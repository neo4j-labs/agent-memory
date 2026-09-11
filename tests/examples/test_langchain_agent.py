"""Smoke tests for the `examples/langchain_agent.py` LangChain 1.x example.

Three layers:

* contract checks — the three adapters subclass base classes that exist in the
  installed LangChain 1.x (no Neo4j, no model);
* Neo4j-backed behaviour — an agent built with ``create_agent`` and
  ``Neo4jMemoryMiddleware``, driven by a scripted ``FakeListChatModel``, plus the
  chat-history and retriever adapters;
* a subprocess run of the example itself, which is the only check that catches
  drift in the parts a unit test does not import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")
pytest.importorskip("langchain", reason="langchain (create_agent) not installed")

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import AgentMiddleware  # noqa: E402
from langchain_core.chat_history import BaseChatMessageHistory  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402

from neo4j_agent_memory.integrations.langchain import (  # noqa: E402
    Neo4jAgentMemory,
    Neo4jMemoryMiddleware,
    Neo4jMemoryRetriever,
)

EXAMPLE_NAME = "langchain_agent.py"

# Vector indexes are dimension-specific. The example's keyless path uses a real
# sentence-transformers embedder (384 dims) while the rest of this suite uses the
# 1536-dim MockEmbedder, so the end-to-end run drops the vector indexes before
# and after itself and lets whoever connects next recreate them. Same treatment
# as tests/examples/test_basic_usage.py.
VECTOR_INDEXES = (
    "entity_embedding_idx",
    "fact_embedding_idx",
    "message_embedding_idx",
    "preference_embedding_idx",
    "step_embedding_idx",
    "task_embedding_idx",
)


def _drop_vector_indexes(connection: dict) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        connection["uri"], auth=(connection["username"], connection["password"])
    )
    try:
        with driver.session() as session:
            for index in VECTOR_INDEXES:
                session.run(f"DROP INDEX {index} IF EXISTS").consume()
    finally:
        driver.close()


class TestLangchainAdapterContracts:
    """Contract checks that need neither Neo4j nor a model."""

    def test_example_file_exists(self, examples_dir):
        assert (examples_dir / EXAMPLE_NAME).exists()

    def test_adapters_implement_live_langchain_base_classes(self):
        """Each adapter targets a base class that exists in LangChain 1.x."""
        assert issubclass(Neo4jAgentMemory, BaseChatMessageHistory)
        assert issubclass(Neo4jMemoryRetriever, BaseRetriever)
        assert issubclass(Neo4jMemoryMiddleware, AgentMiddleware)

    def test_example_uses_the_langchain_1x_idiom(self, examples_dir):
        """The example must teach `create_agent` + middleware, not pre-1.0 memory."""
        content = (examples_dir / EXAMPLE_NAME).read_text(encoding="utf-8")

        assert "create_agent(" in content
        assert "Neo4jMemoryMiddleware" in content
        assert "agent.ainvoke(" in content
        assert "retriever.ainvoke(" in content
        assert "BoltSettings(" in content, "the backend must be pinned, not auto-detected"

        # Retired shapes: BaseMemory is gone from langchain-core 1.x and the
        # chains/executors moved to langchain-classic. (The module docstring
        # names them as history, so assert on the imports/calls instead.)
        assert "ConversationChain(" not in content
        assert "ConversationBufferMemory(" not in content
        assert "AgentExecutor(" not in content
        assert "langchain_core.memory" not in content
        assert "from langchain.chains" not in content
        # The pre-1.x private coroutines must not come back.
        assert "_load_memory_variables_async" not in content
        assert "_save_context_async" not in content
        assert "_get_relevant_documents_async" not in content

    def test_example_records_a_reasoning_trace(self, examples_dir):
        """`include_reasoning` is only honest once something writes traces."""
        content = (examples_dir / EXAMPLE_NAME).read_text(encoding="utf-8")
        assert "start_trace(" in content
        assert "complete_trace(" in content
        assert "TraceOutcome(" in content

    def test_example_does_not_hard_code_a_retired_model_id(self, examples_dir):
        content = (examples_dir / EXAMPLE_NAME).read_text(encoding="utf-8")
        assert "OPENAI_MODEL" in content, "the model id must come from the environment"
        for retired in ("gpt-4o", "gpt-3.5", "text-embedding-004"):
            assert retired not in content, f"retired model id: {retired}"
        assert "ChatOpenAI(model_name=" not in content, (
            "`model=` is the current ChatOpenAI argument; `model_name=` is the legacy alias"
        )

    def test_example_has_proper_structure(self, examples_dir):
        content = (examples_dir / EXAMPLE_NAME).read_text(encoding="utf-8")
        assert "async def main(" in content
        assert 'if __name__ == "__main__":' in content
        assert "asyncio.run(main())" in content
        assert "from neo4j_agent_memory" in content


@pytest.mark.requires_neo4j
class TestMemoryMiddlewareAgent:
    """The shape the example demonstrates: create_agent + Neo4jMemoryMiddleware."""

    async def test_agent_run_persists_both_turns(self, memory_client):
        session_id = f"test-langchain-agent-{uuid4()}"
        model = FakeListChatModel(responses=["Thai Kitchen is your spot."])

        agent = create_agent(
            model,
            tools=[],
            system_prompt="You are a dining assistant.",
            middleware=[
                Neo4jMemoryMiddleware(
                    memory_client,
                    session_id=session_id,
                    include_reasoning=False,
                )
            ],
        )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Where should I eat? Somewhere spicy.")]}
        )
        assert result["messages"][-1].text == "Thai Kitchen is your spot."

        conversation = await memory_client.short_term.get_conversation(session_id)
        roles = [m.role.value for m in conversation.messages]
        assert roles == ["user", "assistant"], f"expected both turns persisted, got {roles}"

    async def test_middleware_does_not_re_persist_on_a_second_call(self, memory_client):
        """A tool loop re-sends the same history; memory must not duplicate it."""
        session_id = f"test-langchain-dedupe-{uuid4()}"
        middleware = Neo4jMemoryMiddleware(
            memory_client, session_id=session_id, include_reasoning=False
        )
        agent = create_agent(
            FakeListChatModel(responses=["one", "two"]),
            tools=[],
            middleware=[middleware],
        )

        first = HumanMessage(content="Remember I like chilli.", id="msg-1")
        await agent.ainvoke({"messages": [first]})
        await agent.ainvoke({"messages": [first]})

        conversation = await memory_client.short_term.get_conversation(session_id)
        contents = [m.content for m in conversation.messages]
        assert contents.count("Remember I like chilli.") == 1, contents

    async def test_sync_hook_refuses_rather_than_skipping_memory(self, memory_client):
        middleware = Neo4jMemoryMiddleware(memory_client, session_id="sync-guard")
        with pytest.raises(NotImplementedError, match="ainvoke"):
            middleware.before_model({"messages": []}, None)  # type: ignore[arg-type]


@pytest.mark.requires_neo4j
class TestLangchainAdaptersAgainstNeo4j:
    """The chat-history and retriever adapters the paired how-to documents."""

    async def test_neo4j_agent_memory_initialization(self, memory_client):
        session_id = f"test-langchain-{uuid4()}"

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_short_term=True,
            include_long_term=True,
            include_reasoning=True,
        )

        assert memory.session_id == session_id
        assert memory.memory_variables == [
            "history",
            "context",
            "preferences",
            "similar_tasks",
        ]

    async def test_aload_memory_variables(self, memory_client):
        session_id = f"test-langchain-load-{uuid4()}"

        await memory_client.short_term.add_message(session_id, "user", "I prefer spicy food")
        await memory_client.long_term.add_preference(
            "food", "Loves spicy dishes", context="Dining preferences"
        )

        memory = Neo4jAgentMemory(memory_client=memory_client, session_id=session_id)

        variables = await memory.aload_memory_variables({"input": "restaurant recommendation"})

        assert isinstance(variables, dict)
        assert set(variables) == set(memory.memory_variables)
        assert "I prefer spicy food" in variables["history"]

    async def test_asave_context(self, memory_client):
        session_id = f"test-langchain-save-{uuid4()}"

        memory = Neo4jAgentMemory(memory_client=memory_client, session_id=session_id)

        await memory.asave_context(
            {"input": "What's a good Thai restaurant?"},
            {"output": "I recommend Thai Kitchen!"},
        )

        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 2

    async def test_chat_message_history_round_trip(self, memory_client):
        """aadd_messages + aget_messages: the BaseChatMessageHistory contract."""
        session_id = f"test-langchain-history-{uuid4()}"

        memory = Neo4jAgentMemory(memory_client=memory_client, session_id=session_id)

        await memory.aadd_messages(
            [
                HumanMessage(content="I love spicy Thai food"),
                AIMessage(content="Noted — spicy Thai it is."),
            ]
        )

        messages = await memory.aget_messages()
        assert [type(m) for m in messages] == [HumanMessage, AIMessage]
        assert messages[0].text == "I love spicy Thai food"

        await memory.aclear()
        assert await memory.aget_messages() == []

    async def test_retriever_ainvoke(self, memory_client):
        """Retrieval through LangChain's own async entry point, on the caller's loop."""
        session_id = f"test-langchain-retrieve-{uuid4()}"

        await memory_client.short_term.add_message(session_id, "user", "I love spicy Thai food")
        await memory_client.long_term.add_preference("food", "Prefers spicy dishes")

        retriever = Neo4jMemoryRetriever(
            memory_client=memory_client,
            session_id=session_id,
            search_short_term=True,
            search_long_term=True,
            k=5,
        )

        docs = await retriever.ainvoke("spicy food")

        assert isinstance(docs, list)
        # May or may not find documents depending on embedding similarity


@pytest.mark.requires_neo4j
@pytest.mark.slow
class TestLangchainExampleScript:
    """Run the example the way a reader would."""

    def test_example_runs_to_completion_without_an_api_key(self, examples_dir, neo4j_connection):
        pytest.importorskip(
            "sentence_transformers",
            reason="the keyless path needs the [sentence-transformers] extra",
        )

        env = {
            **os.environ,
            "NEO4J_URI": neo4j_connection["uri"],
            "NEO4J_USERNAME": neo4j_connection["username"],
            "NEO4J_PASSWORD": neo4j_connection["password"],
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        # Force the keyless path: scripted chat model, local embedder, no LLM.
        env.pop("OPENAI_API_KEY", None)

        script = Path(examples_dir) / EXAMPLE_NAME
        _drop_vector_indexes(neo4j_connection)
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
                cwd=str(examples_dir),
                encoding="utf-8",
            )

            assert result.returncode == 0, (
                f"example exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
            )
            # All five sections ran, and the agent turn produced a reply.
            assert "[5/5]" in result.stdout, result.stdout
            assert "Assistant:" in result.stdout
            # The trace middleware wrote a trace and read it back.
            assert "Trace " in result.stdout
            assert "Done." in result.stdout
        finally:
            # Leave no 384-dim vector indexes behind for the mock-embedder
            # tests that share this database.
            _drop_vector_indexes(neo4j_connection)
