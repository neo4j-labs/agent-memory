"""Smoke tests for pydantic_ai_agent.py example.

Four layers of coverage:

* content checks on the script (no imports, no Neo4j)
* ``TestPydanticAIExampleOffline`` — imports the example and runs its agent with
  ``pydantic_ai.models.test.TestModel``, no database and no API key
* ``TestPydanticAIAgentExample`` — the ``MemoryDependency`` / tool surface against
  Neo4j via the mock-backed ``memory_client`` fixture
* ``TestPydanticAIAgentEndToEnd`` — the example's own ``build_agent()`` /
  ``run_turn()`` driven by ``TestModel`` against Neo4j, asserting that the turn
  leaves messages and a reasoning trace (with tool calls) in the graph
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def load_example() -> Any:
    """Import ``examples/pydantic_ai_agent.py`` (conftest puts it on sys.path)."""
    pytest.importorskip("pydantic_ai")
    import pydantic_ai_agent

    return pydantic_ai_agent


@pytest.mark.requires_neo4j
class TestPydanticAIAgentExample:
    """Smoke tests for the Pydantic AI agent example."""

    def test_example_file_exists(self, examples_dir):
        """Verify the example file exists."""
        example_path = examples_dir / "pydantic_ai_agent.py"
        assert example_path.exists(), f"Example file not found: {example_path}"

    def test_pydantic_ai_integration_importable(self):
        """Verify the Pydantic AI integration module is importable."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import (
                MemoryDependency,
                create_memory_tools,
                record_agent_trace,
            )

            assert MemoryDependency is not None
            assert create_memory_tools is not None
            assert record_agent_trace is not None
        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_initialization(self, memory_client):
        """Test MemoryDependency can be initialized."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-{uuid4()}"

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            assert deps is not None
            assert deps.session_id == session_id
            assert deps.client == memory_client

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_get_context(self, memory_client):
        """Test getting context through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-ctx-{uuid4()}"

            # Pre-populate some data
            await memory_client.long_term.add_preference(
                "communication", "Prefers concise responses"
            )

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Get context
            context = await deps.get_context("restaurant recommendation")

            assert isinstance(context, str)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_add_preference(self, memory_client):
        """Test adding preference through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-pref-{uuid4()}"

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Add preference
            await deps.add_preference(
                category="location",
                preference="Prefers downtown area",
            )

            # Verify it was saved
            prefs = await memory_client.long_term.search_preferences("downtown")
            assert len(prefs) >= 1

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_search_preferences(self, memory_client):
        """Test searching preferences through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-search-{uuid4()}"

            # Pre-populate
            await memory_client.long_term.add_preference("food", "Vegetarian, loves Indian cuisine")

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Search
            prefs = await deps.search_preferences("food")

            assert isinstance(prefs, list)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_save_interaction(self, memory_client):
        """``save_interaction`` writes both sides of the exchange to short-term memory."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency
        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

        session_id = f"test-pydantic-interaction-{uuid4()}"
        deps = MemoryDependency(client=memory_client, session_id=session_id)

        await deps.save_interaction("Find me a restaurant", "Try Bella Italia")

        conversation = await memory_client.short_term.get_conversation(session_id)
        roles = [m.role.value for m in conversation.messages]
        assert roles == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_create_memory_tools(self, memory_client):
        """Test creating memory tools."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

            tools = create_memory_tools(memory_client)

            assert isinstance(tools, list)
            assert len(tools) >= 3  # search, save_preference, recall

            # Check tool names
            tool_names = [t.__name__ for t in tools]
            assert "search_memory" in tool_names
            assert "save_preference" in tool_names
            assert "recall_preferences" in tool_names

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_tools_execution(self, memory_client):
        """Test executing memory tools."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

            tools = create_memory_tools(memory_client)

            # Find tools by name
            search_tool = next(t for t in tools if t.__name__ == "search_memory")
            save_tool = next(t for t in tools if t.__name__ == "save_preference")
            recall_tool = next(t for t in tools if t.__name__ == "recall_preferences")

            # Test save_preference
            save_result = await save_tool("cuisine", "Enjoys Mediterranean food")
            assert isinstance(save_result, str)

            # Test search_memory
            search_result = await search_tool("Mediterranean food")
            assert isinstance(search_result, str)

            # Test recall_preferences
            recall_result = await recall_tool("cuisine")
            assert isinstance(recall_result, str)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    def test_record_agent_trace_available(self):
        """Verify record_agent_trace function is available."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace

            # Just verify it's callable
            assert callable(record_agent_trace)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")


class TestPydanticAIExampleContent:
    """Content checks on the script — no imports, no Neo4j."""

    def test_example_sections_present(self, examples_dir):
        """Verify the example covers all documented sections."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        for name in (
            "MemoryDependency",
            "create_memory_tools",
            "get_context",
            "add_preference",
            "search_preferences",
            "record_agent_trace",
        ):
            assert name in content, name

    def test_example_actually_runs_the_agent(self, examples_dir):
        """The demo must call the surface it advertises, not describe it."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        # The agent is built with tools and run for real ...
        assert "tools=tools" in content
        assert "await agent.run(" in content
        # ... the exchange is persisted, the trace recorded and read back ...
        assert "save_interaction(" in content
        assert "await record_agent_trace(" in content
        assert "get_trace_with_steps(" in content
        # ... and the offline path uses a stub model instead of prose.
        assert "TestModel(" in content
        assert "Example usage:" not in content, "the record_agent_trace prose block is back"

    def test_example_pins_the_bolt_backend(self, examples_dir):
        """A stray MEMORY_API_KEY must not silently retarget the hosted backend."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        assert "BoltSettings(" in content
        assert "nams_memory_tools" in content, "the hosted counterpart should be signposted"

    def test_example_uses_shared_env_helper(self, examples_dir):
        """Environment loading lives in examples/_env.py, not in every script."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        assert "from _env import" in content
        assert "def load_env_files(" not in content

    def test_example_uses_pydantic_ai_2x_idioms(self, examples_dir):
        """The example must not use identifiers removed in PydanticAI 1.0/2.0."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        # 2.x idioms
        assert "OpenAIChatModel(" in content
        assert "@agent.instructions" in content
        assert "result.output" in content

        # Removed / legacy identifiers
        for removed in (
            "result_type=",
            "result.data",
            "@agent.system_prompt",
            "OpenAIModel(",
            "GeminiModel(",
            "gpt-4o",
        ):
            assert removed not in content, f"example still uses {removed!r}"

    def test_example_has_proper_structure(self, examples_dir):
        """Verify the example has proper Python structure."""
        content = (examples_dir / "pydantic_ai_agent.py").read_text(encoding="utf-8")

        # Check for main function and entry point
        assert "async def main(" in content
        assert 'if __name__ == "__main__":' in content
        assert "asyncio.run(main())" in content

        # Check for proper imports
        assert "from neo4j_agent_memory" in content
        assert "from neo4j_agent_memory.integrations.pydantic_ai" in content


class TestPydanticAIExampleOffline:
    """Offline checks: no Neo4j, no API key, no network."""

    def test_integration_exports_stable_names(self):
        """The public names the example imports must stay put."""
        pytest.importorskip("pydantic_ai")
        from neo4j_agent_memory.integrations import pydantic_ai as integration

        for name in (
            "MemoryDependency",
            "create_memory_tools",
            "nams_memory_tools",
            "record_agent_trace",
            "llm_provider_from_pydantic_ai",
        ):
            assert hasattr(integration, name), name

    def test_example_exposes_importable_helpers(self):
        """The helpers the docs and these tests drive must keep their names."""
        module = load_example()

        for name in ("build_model", "build_settings", "build_agent", "run_turn", "main"):
            assert callable(getattr(module, name)), name

    def test_build_model_falls_back_to_test_model(self, monkeypatch):
        """Without a key the example still produces a runnable model."""
        module = load_example()
        from pydantic_ai.models.test import TestModel

        # OPENAI_API_KEY is read once, at import, via examples/_env.py.
        monkeypatch.setattr(module, "OPENAI_API_KEY", None)
        model, llm_provider = module.build_model()

        assert isinstance(model, TestModel)
        assert llm_provider is None

    def test_openai_chat_model_maps_to_openai_provider(self, monkeypatch):
        """``llm_provider_from_pydantic_ai`` understands the 2.x class name."""
        pytest.importorskip("pydantic_ai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        from pydantic_ai.models.openai import OpenAIChatModel

        from neo4j_agent_memory.integrations.pydantic_ai import (
            llm_provider_from_pydantic_ai,
        )

        model = OpenAIChatModel("gpt-5-mini")
        provider = llm_provider_from_pydantic_ai(model)

        assert provider is not None
        assert "gpt-5-mini" in repr(getattr(provider, "model", "gpt-5-mini"))

    @pytest.mark.asyncio
    async def test_example_agent_runs_on_test_model(self):
        """The example's own ``build_agent`` runs a turn with no DB and no key."""
        module = load_example()
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.models.test import TestModel

        from neo4j_agent_memory.integrations.pydantic_ai import (
            MemoryDependency,
            create_memory_tools,
        )

        client = MagicMock()
        client.get_context = AsyncMock(return_value="Prefers concise responses")
        client.short_term.search_messages = AsyncMock(return_value=[])
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])
        client.long_term.add_preference = AsyncMock()
        client.reasoning.get_similar_traces = AsyncMock(return_value=[])

        tools = create_memory_tools(client)
        agent = module.build_agent(TestModel(call_tools=["search_memory"]), tools)

        deps = MemoryDependency(client=client, session_id="offline-demo")
        result = await agent.run("Find me a good restaurant", deps=deps)

        # 2.x: ``.output`` (``.data`` is gone) and ``.usage`` is a property.
        assert isinstance(result.output, str)
        assert result.usage.requests >= 1
        assert result.usage.tool_calls >= 1
        # The instructions hook queries memory with *this turn's* prompt, and is
        # re-rendered for every model request, so it may run more than once.
        assert client.get_context.await_count >= 1
        for call in client.get_context.await_args_list:
            assert call.args == ("Find me a good restaurant",)
            assert call.kwargs == {"session_id": "offline-demo"}


@pytest.mark.requires_neo4j
class TestPydanticAIAgentEndToEnd:
    """Run the example's agent turn against Neo4j with a stub model."""

    @pytest.mark.asyncio
    async def test_run_turn_persists_messages_and_trace(self, memory_client):
        """``run_turn`` must leave short-term and reasoning memory populated."""
        module = load_example()
        from pydantic_ai.models.test import TestModel

        from neo4j_agent_memory.integrations.pydantic_ai import (
            MemoryDependency,
            create_memory_tools,
        )

        session_id = f"test-pydantic-run-{uuid4()}"
        tools = create_memory_tools(memory_client)
        # TestModel calls every registered tool by default; two is enough to
        # prove the tool calls land on the trace as ToolCall nodes.
        model = TestModel(call_tools=["search_memory", "recall_preferences"])
        agent = module.build_agent(model, tools)
        deps = MemoryDependency(client=memory_client, session_id=session_id)

        result = await module.run_turn(
            memory_client,
            agent,
            deps,
            "Find me a good restaurant for dinner tonight.",
            task="Find restaurant recommendation",
        )

        assert isinstance(result.output, str)

        # Short-term memory: both sides of the exchange were persisted.
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert [m.role.value for m in conversation.messages] == ["user", "assistant"]

        # Reasoning memory: one completed trace whose steps carry the tool calls.
        traces = await memory_client.reasoning.get_session_traces(session_id)
        assert len(traces) == 1
        stored = await memory_client.reasoning.get_trace_with_steps(traces[0].id)
        assert stored is not None
        assert stored.success is True
        assert len(stored.steps) == 2
        tool_names = {call.tool_name for step in stored.steps for call in step.tool_calls}
        assert tool_names == {"search_memory", "recall_preferences"}
