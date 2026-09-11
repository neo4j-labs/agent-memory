"""Unit tests for the LangChain 1.x integration.

No network and no Neo4j: the memory client is a mock and the agent run uses
``GenericFakeChatModel``. These tests pin the 1.x contracts the adapters
implement, and the backend tolerance the hosted NAMS backend needs:

* ``Neo4jAgentMemory`` is a ``langchain_core.chat_history.BaseChatMessageHistory``
  with a working async surface (``aget_messages`` / ``aadd_messages`` / ``aclear``)
* ``Neo4jMemoryRetriever`` is a ``BaseRetriever`` whose ``ainvoke`` runs the real
  coroutine on the caller's loop (no worker thread, no nested ``asyncio.run``)
* ``Neo4jMemoryMiddleware`` works inside ``langchain.agents.create_agent``
* all three degrade instead of raising when a layer is bolt-only
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotSupportedError

pytest.importorskip("langchain_core", reason="langchain-core not installed")

from langchain_core.chat_history import BaseChatMessageHistory  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.retrievers import BaseRetriever  # noqa: E402

from neo4j_agent_memory.integrations.langchain import (  # noqa: E402
    Neo4jAgentMemory,
    Neo4jMemoryRetriever,
)


def _memory_message(role: str, content: str) -> SimpleNamespace:
    """A stand-in for ``neo4j_agent_memory.memory.short_term.Message``."""
    return SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(value=role),
        content=content,
        metadata={"similarity": 0.9},
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """A mock MemoryClient with every layer the adapters touch stubbed."""
    client = MagicMock()
    client.short_term = MagicMock()
    client.long_term = MagicMock()
    client.reasoning = MagicMock()

    conversation = SimpleNamespace(
        messages=[
            _memory_message("user", "I prefer spicy food"),
            _memory_message("assistant", "Noted."),
        ]
    )
    client.short_term.get_conversation = AsyncMock(return_value=conversation)
    client.short_term.add_message = AsyncMock(return_value=_memory_message("user", "x"))
    client.short_term.clear_session = AsyncMock()
    client.short_term.search_messages = AsyncMock(
        return_value=[_memory_message("user", "I love Thai curry")]
    )
    client.long_term.get_context = AsyncMock(return_value="- likes spicy food")
    client.long_term.search_entities = AsyncMock(return_value=[])
    client.long_term.search_preferences = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=uuid4(),
                category="food",
                preference="Loves spicy dishes",
                context=None,
                metadata={"similarity": 0.8},
            )
        ]
    )
    client.reasoning.get_similar_traces = AsyncMock(return_value=[])
    client.get_context = AsyncMock(return_value="- likes spicy food")
    return client


def _nams_error(method: str) -> NotSupportedError:
    return NotSupportedError(backend="nams", method=method)


class TestAdapterContracts:
    """The adapters implement ABCs that exist in the installed langchain-core."""

    def test_langchain_core_has_no_memory_module(self) -> None:
        """langchain-core 1.x removed ``BaseMemory``; nothing may target it."""
        with pytest.raises(ImportError):
            __import__("langchain_core.memory")

    def test_chat_history_subclass(self) -> None:
        assert issubclass(Neo4jAgentMemory, BaseChatMessageHistory)

    def test_retriever_subclass(self) -> None:
        assert issubclass(Neo4jMemoryRetriever, BaseRetriever)

    def test_retriever_defines_the_async_hook_langchain_calls(self) -> None:
        """LangChain synthesises ``_aget_relevant_documents`` when absent."""
        assert (
            Neo4jMemoryRetriever._aget_relevant_documents
            is not BaseRetriever._aget_relevant_documents
        )


class TestChatMessageHistory:
    """``BaseChatMessageHistory`` surface."""

    async def test_aget_messages_maps_roles(self, mock_client: MagicMock) -> None:
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        messages = await history.aget_messages()

        assert [type(m) for m in messages] == [HumanMessage, AIMessage]
        assert messages[0].text == "I prefer spicy food"
        mock_client.short_term.get_conversation.assert_awaited_once()

    async def test_aadd_messages_writes_each_role(self, mock_client: MagicMock) -> None:
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        await history.aadd_messages(
            [
                HumanMessage(content="hello"),
                AIMessage(content="hi"),
                SystemMessage(content="be nice"),
                ChatMessage(content="tool output", role="tool"),
            ]
        )

        roles = [call.args[1] for call in mock_client.short_term.add_message.await_args_list]
        assert roles == ["user", "assistant", "system", "tool"]

    async def test_aadd_messages_skips_empty_content(self, mock_client: MagicMock) -> None:
        """A tool-call-only AIMessage has no text to store."""
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        await history.aadd_messages(
            [AIMessage(content="", tool_calls=[{"name": "f", "args": {}, "id": "1"}])]
        )

        mock_client.short_term.add_message.assert_not_awaited()

    async def test_aclear_clears_the_session(self, mock_client: MagicMock) -> None:
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        await history.aclear()

        mock_client.short_term.clear_session.assert_awaited_once_with("s1")

    async def test_sync_surface_refuses_to_block_a_running_loop(
        self, mock_client: MagicMock
    ) -> None:
        """The pre-1.x code scheduled onto the caller's loop and then blocked it."""
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        with pytest.raises(RuntimeError, match="aget_messages"):
            _ = history.messages
        with pytest.raises(RuntimeError, match="aadd_messages"):
            history.add_messages([HumanMessage(content="x")])
        with pytest.raises(RuntimeError, match="aclear"):
            history.clear()

    def test_sync_surface_works_outside_a_loop(self, mock_client: MagicMock) -> None:
        history = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        assert len(history.messages) == 2

    async def test_works_with_runnable_with_message_history(self, mock_client: MagicMock) -> None:
        """The point of the retarget: it plugs into a langchain-core runnable."""
        from langchain_core.runnables import RunnableLambda
        from langchain_core.runnables.history import RunnableWithMessageHistory

        def _echo(payload: dict[str, Any]) -> AIMessage:
            history_len = len(payload["history"])
            return AIMessage(content=f"saw {history_len} remembered messages")

        chain_with_history = RunnableWithMessageHistory(
            RunnableLambda(_echo),
            lambda session_id: Neo4jAgentMemory(memory_client=mock_client, session_id=session_id),
            input_messages_key="input",
            history_messages_key="history",
        )

        result = await chain_with_history.ainvoke(
            {"input": "where should I eat?"},
            config={"configurable": {"session_id": "s1"}},
        )

        assert result.content == "saw 2 remembered messages"
        # The runnable persists both the input and the output turn.
        assert mock_client.short_term.add_message.await_count == 2


class TestContextAssembly:
    """The ``aload_memory_variables`` / ``asave_context`` helper surface."""

    async def test_public_async_names(self, mock_client: MagicMock) -> None:
        memory = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        variables = await memory.aload_memory_variables({"input": "restaurants"})

        assert set(variables) == set(memory.memory_variables)
        assert variables["preferences"] == [
            {"category": "food", "preference": "Loves spicy dishes"}
        ]

    async def test_deprecated_private_aliases_still_work(self, mock_client: MagicMock) -> None:
        memory = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        variables = await memory._load_memory_variables_async({"input": "restaurants"})
        await memory._save_context_async({"input": "q"}, {"output": "a"})

        assert "history" in variables
        assert mock_client.short_term.add_message.await_count == 2

    async def test_asave_context_writes_both_turns(self, mock_client: MagicMock) -> None:
        memory = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        await memory.asave_context({"input": "q"}, {"output": "a"})

        roles = [call.args[1] for call in mock_client.short_term.add_message.await_args_list]
        assert roles == ["user", "assistant"]


class TestNamsTolerance:
    """Bolt-only layers degrade instead of raising on the hosted backend."""

    async def test_preferences_and_traces_degrade(self, mock_client: MagicMock) -> None:
        mock_client.long_term.search_preferences.side_effect = _nams_error("search_preferences")
        mock_client.reasoning.get_similar_traces.side_effect = _nams_error("get_similar_traces")
        memory = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        variables = await memory.aload_memory_variables({"input": "restaurants"})

        assert variables["preferences"] == []
        assert variables["similar_tasks"] == ""

    async def test_long_term_context_falls_back_to_entity_search(
        self, mock_client: MagicMock
    ) -> None:
        """NAMS returns "" from ``long_term.get_context`` but supports entities."""
        mock_client.long_term.get_context.return_value = ""
        mock_client.long_term.search_entities.return_value = [
            SimpleNamespace(
                id=uuid4(),
                display_name="Thai Kitchen",
                full_type="ORGANIZATION:RESTAURANT",
                description="Favourite Thai place",
                type="ORGANIZATION",
                metadata={},
            )
        ]
        memory = Neo4jAgentMemory(memory_client=mock_client, session_id="s1")

        variables = await memory.aload_memory_variables({"input": "restaurants"})

        assert "Thai Kitchen" in variables["context"]

    async def test_retriever_skips_unsupported_layers(self, mock_client: MagicMock) -> None:
        mock_client.long_term.search_preferences.side_effect = _nams_error("search_preferences")
        mock_client.reasoning.get_similar_traces.side_effect = _nams_error("get_similar_traces")
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, session_id="s1")

        docs = await retriever.ainvoke("spicy food")

        assert [d.metadata["type"] for d in docs] == ["message"]

    async def test_retriever_skips_message_search_without_session_id(
        self, mock_client: MagicMock
    ) -> None:
        """NAMS message search is conversation-scoped and raises ValueError."""
        mock_client.short_term.search_messages.side_effect = ValueError("session_id required")
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, search_reasoning=False)

        docs = await retriever.ainvoke("spicy food")

        assert [d.metadata["type"] for d in docs] == ["preference"]


class TestRetriever:
    """``BaseRetriever`` surface."""

    async def test_ainvoke_runs_on_the_callers_loop(self, mock_client: MagicMock) -> None:
        """Regression: the old adapter ran retrieval in a worker thread's loop."""
        caller_thread = threading.get_ident()
        caller_loop = asyncio.get_running_loop()
        observed: dict[str, Any] = {}

        async def _search(query: str, **kwargs: Any) -> list[Any]:
            observed["thread"] = threading.get_ident()
            observed["loop"] = asyncio.get_running_loop()
            return [_memory_message("user", f"hit for {query}")]

        mock_client.short_term.search_messages = AsyncMock(side_effect=_search)
        retriever = Neo4jMemoryRetriever(
            memory_client=mock_client,
            session_id="s1",
            search_long_term=False,
            search_reasoning=False,
        )

        docs = await retriever.ainvoke("spicy food")

        assert observed["thread"] == caller_thread
        assert observed["loop"] is caller_loop
        assert docs[0].page_content == "hit for spicy food"

    async def test_session_id_is_threaded_into_message_search(self, mock_client: MagicMock) -> None:
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, session_id="s1")

        await retriever.ainvoke("spicy food")

        assert mock_client.short_term.search_messages.await_args.kwargs["session_id"] == "s1"

    async def test_invoke_refuses_to_run_inside_a_loop(self, mock_client: MagicMock) -> None:
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, session_id="s1")

        with pytest.raises(RuntimeError, match="ainvoke"):
            retriever.invoke("spicy food")

    async def test_results_are_capped_and_sorted(self, mock_client: MagicMock) -> None:
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, session_id="s1", k=1)

        docs = await retriever.ainvoke("spicy food")

        assert len(docs) == 1
        assert docs[0].metadata["similarity"] == 0.9

    async def test_deprecated_private_coroutine_alias(self, mock_client: MagicMock) -> None:
        retriever = Neo4jMemoryRetriever(memory_client=mock_client, session_id="s1")

        docs = await retriever._get_relevant_documents_async("spicy food")

        assert docs


class TestMiddleware:
    """``Neo4jMemoryMiddleware`` inside ``langchain.agents.create_agent``."""

    @pytest.fixture
    def middleware_cls(self) -> Any:
        pytest.importorskip(
            "langchain.agents.middleware",
            reason="the `langchain` distribution is not installed "
            "(pip install neo4j-agent-memory[langchain-agents])",
        )
        from neo4j_agent_memory.integrations.langchain import Neo4jMemoryMiddleware

        return Neo4jMemoryMiddleware

    async def test_agent_run_injects_context_and_persists_turns(
        self, mock_client: MagicMock, middleware_cls: Any
    ) -> None:
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        prompts: list[list[Any]] = []

        class _Recording(GenericFakeChatModel):
            def _generate(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
                prompts.append(list(messages))
                return super()._generate(messages, *args, **kwargs)

        model = _Recording(messages=iter([AIMessage(content="Try Thai Kitchen.")]))
        agent = create_agent(
            model,
            tools=[],
            system_prompt="You are helpful.",
            middleware=[middleware_cls(mock_client, session_id="s1")],
        )

        result = await agent.ainvoke({"messages": [("user", "Where should I eat?")]})

        assert result["messages"][-1].content == "Try Thai Kitchen."
        # Memory context reached the model via the system message.
        assert "likes spicy food" in prompts[0][0].text
        # Both turns were persisted.
        stored = [
            (call.args[1], call.args[2])
            for call in mock_client.short_term.add_message.await_args_list
        ]
        assert stored == [
            ("user", "Where should I eat?"),
            ("assistant", "Try Thai Kitchen."),
        ]

    async def test_messages_are_persisted_once_across_model_calls(
        self, mock_client: MagicMock, middleware_cls: Any
    ) -> None:
        """A tool loop re-sends the same history on every model call."""
        from langchain.agents.middleware import AgentState

        middleware = middleware_cls(mock_client, session_id="s1")
        state: AgentState[Any] = {
            "messages": [HumanMessage(content="hi", id="h1"), AIMessage(content="yo", id="a1")]
        }

        await middleware.abefore_model(state, None)
        await middleware.aafter_model(state, None)
        await middleware.abefore_model(state, None)
        await middleware.aafter_model(state, None)

        assert mock_client.short_term.add_message.await_count == 2

    async def test_sync_hooks_raise_instead_of_silently_skipping_memory(
        self, mock_client: MagicMock, middleware_cls: Any
    ) -> None:
        middleware = middleware_cls(mock_client, session_id="s1")

        with pytest.raises(NotImplementedError, match="ainvoke"):
            middleware.before_model({"messages": []}, None)
        with pytest.raises(NotImplementedError, match="ainvoke"):
            middleware.after_model({"messages": []}, None)

    async def test_no_injection_when_memory_is_empty(
        self, mock_client: MagicMock, middleware_cls: Any
    ) -> None:
        mock_client.get_context.return_value = ""
        middleware = middleware_cls(mock_client, session_id="s1")
        original = SimpleNamespace(
            messages=[HumanMessage(content="hi")],
            system_message=SystemMessage(content="base"),
        )
        seen: list[Any] = []

        async def _handler(request: Any) -> str:
            seen.append(request)
            return "response"

        assert await middleware.awrap_model_call(original, _handler) == "response"
        assert seen == [original]

    async def test_get_context_unsupported_degrades(
        self, mock_client: MagicMock, middleware_cls: Any
    ) -> None:
        mock_client.get_context.side_effect = _nams_error("get_context")
        middleware = middleware_cls(mock_client, session_id="s1")
        original = SimpleNamespace(messages=[HumanMessage(content="hi")], system_message=None)

        async def _handler(request: Any) -> str:
            return "response"

        assert await middleware.awrap_model_call(original, _handler) == "response"
