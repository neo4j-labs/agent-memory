"""Unit tests for Microsoft Agent Framework integration.

These tests verify the integration components work correctly without
requiring an actual Neo4j database or Microsoft Agent Framework installation.
"""

from __future__ import annotations

import inspect
import json
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests if agent_framework is not installed
pytest.importorskip("agent_framework", reason="Microsoft Agent Framework not installed")


def _release_tuple(raw: str) -> tuple[int, ...]:
    """Parse the release segment of a version into a comparable tuple."""
    match = re.match(r"\d+(?:\.\d+)*", raw.strip())
    assert match is not None, f"unparseable version: {raw!r}"
    return tuple(int(part) for part in match.group(0).split("."))


class TestAgentFrameworkGAContract:
    """Guard the Agent Framework 1.x GA surface the adapter is written against.

    The 1.0.0b2 preview exported ``BaseContextProvider`` / ``BaseHistoryProvider``;
    the GA line renamed both (keeping the keyword-only ``before_run`` /
    ``after_run`` signatures). Nothing in the old test suite noticed, because
    every test imported the adapter classes without ever checking what they
    extend — the integration simply stopped importing. These tests fail loudly
    on the next such rename.
    """

    def test_context_provider_extends_ga_base(self) -> None:
        """Neo4jContextProvider must extend agent_framework.ContextProvider."""
        from agent_framework import ContextProvider

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        assert issubclass(Neo4jContextProvider, ContextProvider)

    def test_chat_store_extends_ga_history_provider(self) -> None:
        """Neo4jChatMessageStore must extend agent_framework.HistoryProvider."""
        from agent_framework import HistoryProvider

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        assert issubclass(Neo4jChatMessageStore, HistoryProvider)

    @pytest.mark.parametrize("hook", ["before_run", "after_run"])
    def test_context_hook_signature_matches_base(self, hook: str) -> None:
        """The provider hooks must keep the framework's keyword-only signature."""
        from agent_framework import ContextProvider

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        base_params = inspect.signature(getattr(ContextProvider, hook)).parameters
        ours = inspect.signature(getattr(Neo4jContextProvider, hook)).parameters

        assert list(ours) == list(base_params)
        for name, param in base_params.items():
            assert ours[name].kind == param.kind

    def test_history_provider_hooks_are_inherited(self) -> None:
        """The chat store relies on HistoryProvider's before_run/after_run."""
        from agent_framework import HistoryProvider

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        assert Neo4jChatMessageStore.before_run is HistoryProvider.before_run
        assert Neo4jChatMessageStore.after_run is HistoryProvider.after_run

    @pytest.mark.parametrize("method", ["get_messages", "save_messages"])
    def test_history_methods_accept_state_keyword(self, method: str) -> None:
        """HistoryProvider calls these with ``state=``; both must accept it."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        params = inspect.signature(getattr(Neo4jChatMessageStore, method)).parameters
        assert "state" in params
        assert params["state"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_installed_framework_satisfies_declared_minimum(self) -> None:
        """The installed agent-framework must be at least MIN_VERSION."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION,
        )

        for distribution in ("agent-framework-core", "agent-framework"):
            try:
                installed = package_version(distribution)
            except PackageNotFoundError:
                continue
            assert _release_tuple(installed) >= _release_tuple(
                MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION
            ), (
                f"{distribution} {installed} is older than the adapter's declared "
                f"minimum {MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION}"
            )
            return
        pytest.skip("no agent-framework distribution metadata found")


class TestNeo4jContextProvider:
    """Tests for Neo4jContextProvider."""

    @pytest.fixture
    def mock_memory_client(self) -> MagicMock:
        """Create a mock MemoryClient."""
        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        client._client = MagicMock()
        return client

    @pytest.fixture
    def provider(self, mock_memory_client: MagicMock) -> Any:
        """Create a Neo4jContextProvider instance."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        return Neo4jContextProvider(
            memory_client=mock_memory_client,
            session_id="test-session-123",
            user_id="user-456",
            include_short_term=True,
            include_long_term=True,
            include_reasoning=True,
        )

    def test_initialization(self, provider: Any, mock_memory_client: MagicMock) -> None:
        """Test provider initializes with correct settings."""
        assert provider.session_id == "test-session-123"
        assert provider.user_id == "user-456"
        assert provider.memory_client == mock_memory_client
        assert provider._include_short_term is True
        assert provider._include_long_term is True
        assert provider._include_reasoning is True
        assert provider.source_id == "neo4j-context"

    def test_initialization_custom_source_id(self, mock_memory_client: MagicMock) -> None:
        """Test provider initializes with custom source_id."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=mock_memory_client,
            session_id="test-session",
            source_id="custom-source",
        )
        assert provider.source_id == "custom-source"

    def test_initialization_validates_session_id(self, mock_memory_client: MagicMock) -> None:
        """Test that empty session_id raises ValueError."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        with pytest.raises(ValueError, match="session_id must be a non-empty string"):
            Neo4jContextProvider(
                memory_client=mock_memory_client,
                session_id="",
            )

    @pytest.mark.asyncio
    async def test_before_run_returns_context(
        self, provider: Any, mock_memory_client: MagicMock
    ) -> None:
        """Test before_run() injects context from memory."""
        from agent_framework import Message, SessionContext

        # Mock conversation response
        mock_conv = MagicMock()
        mock_msg = MagicMock()
        mock_msg.role = MagicMock(value="user")
        mock_msg.content = "Hello, I need help"
        mock_msg.metadata = None
        mock_conv.messages = [mock_msg]
        mock_memory_client.short_term.get_conversation = AsyncMock(return_value=mock_conv)
        mock_memory_client.short_term.search_messages = AsyncMock(return_value=[])
        mock_memory_client.long_term.search_preferences = AsyncMock(return_value=[])
        mock_memory_client.long_term.search_entities = AsyncMock(return_value=[])
        mock_memory_client.reasoning.get_similar_traces = AsyncMock(return_value=[])

        input_messages = [Message("user", ["What products do you recommend?"])]
        context = SessionContext(input_messages=input_messages)
        mock_session = MagicMock()
        mock_agent = MagicMock()

        await provider.before_run(
            agent=mock_agent,
            session=mock_session,
            context=context,
            state={},
        )

        # Should have called memory methods
        mock_memory_client.short_term.get_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_before_run_empty_messages_no_context(self, provider: Any) -> None:
        """Test before_run() with no user messages adds no context."""
        from agent_framework import SessionContext

        context = SessionContext(input_messages=[])

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        assert len(context.instructions) == 0

    @pytest.mark.asyncio
    async def test_after_run_saves_messages(
        self, provider: Any, mock_memory_client: MagicMock
    ) -> None:
        """Test after_run() saves messages to short-term memory."""
        from agent_framework import AgentResponse, Message, SessionContext

        mock_memory_client.short_term.add_message = AsyncMock()

        input_messages = [Message("user", ["Hello"])]
        context = SessionContext(input_messages=input_messages)

        # Mock the response
        mock_response = MagicMock(spec=AgentResponse)
        mock_response.messages = [Message("assistant", ["Hi there!"])]
        context._response = mock_response

        await provider.after_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        # Should have saved both messages
        assert mock_memory_client.short_term.add_message.call_count == 2

    def test_serialize_returns_json(self, provider: Any) -> None:
        """Test serialize() returns valid JSON."""
        serialized = provider.serialize()
        data = json.loads(serialized)

        assert data["session_id"] == "test-session-123"
        assert data["user_id"] == "user-456"
        assert data["source_id"] == "neo4j-context"
        assert data["include_short_term"] is True
        assert data["include_long_term"] is True
        assert data["include_reasoning"] is True

    def test_deserialize_restores_provider(self, mock_memory_client: MagicMock) -> None:
        """Test deserialize() creates provider from saved state."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        state = json.dumps(
            {
                "session_id": "restored-session",
                "source_id": "custom-source",
                "user_id": "restored-user",
                "include_short_term": False,
                "include_long_term": True,
                "include_reasoning": False,
                "max_context_items": 15,
                "max_recent_messages": 3,
                "extract_entities": True,
                "extract_entities_async": False,
                "similarity_threshold": 0.8,
                "gds_enabled": False,
            }
        )

        restored = Neo4jContextProvider.deserialize(state, mock_memory_client)

        assert restored.session_id == "restored-session"
        assert restored.source_id == "custom-source"
        assert restored.user_id == "restored-user"
        assert restored._include_short_term is False
        assert restored._include_reasoning is False
        assert restored._max_context_items == 15


class TestNeo4jChatMessageStore:
    """Tests for Neo4jChatMessageStore."""

    @pytest.fixture
    def mock_memory_client(self) -> MagicMock:
        """Create a mock MemoryClient."""
        client = MagicMock()
        client.short_term = MagicMock()
        return client

    @pytest.fixture
    def chat_store(self, mock_memory_client: MagicMock) -> Any:
        """Create a Neo4jChatMessageStore instance."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        return Neo4jChatMessageStore(
            memory_client=mock_memory_client,
            session_id="chat-session-123",
        )

    def test_initialization(self, chat_store: Any) -> None:
        """Test chat store initializes correctly."""
        assert chat_store.session_id == "chat-session-123"
        assert chat_store.source_id == "neo4j-history"

    @pytest.mark.asyncio
    async def test_add_messages(self, chat_store: Any, mock_memory_client: MagicMock) -> None:
        """Test add_messages() saves messages to Neo4j."""
        from agent_framework import Message

        mock_memory_client.short_term.add_message = AsyncMock()

        messages = [
            Message("user", ["Hello"]),
            Message("assistant", ["Hi!"]),
        ]

        await chat_store.add_messages(messages)

        assert mock_memory_client.short_term.add_message.call_count == 2

    @pytest.mark.asyncio
    async def test_list_messages(self, chat_store: Any, mock_memory_client: MagicMock) -> None:
        """Test list_messages() retrieves messages from Neo4j."""
        from agent_framework import Message

        mock_msg1 = MagicMock()
        mock_msg1.role = MagicMock(value="user")
        mock_msg1.content = "Hello"
        mock_msg1.metadata = None

        mock_msg2 = MagicMock()
        mock_msg2.role = MagicMock(value="assistant")
        mock_msg2.content = "Hi there!"
        mock_msg2.metadata = None

        mock_conv = MagicMock()
        mock_conv.messages = [mock_msg1, mock_msg2]
        mock_memory_client.short_term.get_conversation = AsyncMock(return_value=mock_conv)

        messages = await chat_store.list_messages()

        assert len(messages) == 2
        assert all(isinstance(m, Message) for m in messages)

    @pytest.mark.asyncio
    async def test_before_run_loads_history_into_context(
        self, chat_store: Any, mock_memory_client: MagicMock
    ) -> None:
        """The inherited before_run must load Neo4j history into the context.

        This drives the real ``HistoryProvider.before_run``, which calls
        ``get_messages(session_id, state=state)`` — so it fails if the
        framework's loading contract drifts away from our signature.
        """
        from agent_framework import SessionContext

        stored = MagicMock()
        stored.role = MagicMock(value="user")
        stored.content = "Earlier question"
        stored.metadata = None
        mock_conv = MagicMock()
        mock_conv.messages = [stored]
        mock_memory_client.short_term.get_conversation = AsyncMock(return_value=mock_conv)

        context = SessionContext(session_id="chat-session-123", input_messages=[])

        await chat_store.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        loaded = context.get_messages(sources={chat_store.source_id})
        assert [m.text for m in loaded] == ["Earlier question"]

    @pytest.mark.asyncio
    async def test_after_run_persists_inputs_and_outputs(
        self, chat_store: Any, mock_memory_client: MagicMock
    ) -> None:
        """The inherited after_run must persist both input and response messages."""
        from agent_framework import AgentResponse, Message, SessionContext

        mock_memory_client.short_term.add_message = AsyncMock()

        context = SessionContext(
            session_id="chat-session-123",
            input_messages=[Message("user", ["Hello"])],
        )
        response = MagicMock(spec=AgentResponse)
        response.messages = [Message("assistant", ["Hi!"])]
        context._response = response

        await chat_store.after_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        roles = [
            call.kwargs["role"] for call in mock_memory_client.short_term.add_message.call_args_list
        ]
        assert roles == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_serialize_deserialize(
        self, chat_store: Any, mock_memory_client: MagicMock
    ) -> None:
        """Test serialize/deserialize roundtrip."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        serialized = chat_store.serialize()
        assert serialized["session_id"] == "chat-session-123"
        assert serialized["source_id"] == "neo4j-history"

        restored = Neo4jChatMessageStore.deserialize(serialized, mock_memory_client)
        assert restored.session_id == "chat-session-123"
        assert restored.source_id == "neo4j-history"


class TestGDSIntegration:
    """Tests for GDSIntegration."""

    @pytest.fixture
    def mock_memory_client(self) -> MagicMock:
        """Create a mock MemoryClient with session support."""
        client = MagicMock()
        # Mock the Neo4j client
        mock_neo4j = MagicMock()
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_neo4j.session.return_value = mock_session
        client._client = mock_neo4j
        return client

    @pytest.fixture
    def gds_config(self) -> Any:
        """Create a GDS config."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSConfig

        return GDSConfig(
            enabled=True,
            fallback_to_basic=True,
            warn_on_fallback=False,
        )

    @pytest.fixture
    def gds(self, mock_memory_client: MagicMock, gds_config: Any) -> Any:
        """Create a GDSIntegration instance."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSIntegration

        return GDSIntegration(mock_memory_client, gds_config)

    @pytest.mark.asyncio
    async def test_is_gds_available_false_when_not_installed(
        self, gds: Any, mock_memory_client: MagicMock
    ) -> None:
        """Test is_gds_available() returns False when GDS not installed."""
        # Make the query fail
        mock_session = mock_memory_client._client.session.return_value.__aenter__.return_value
        mock_session.run = AsyncMock(side_effect=Exception("gds.version() not found"))

        available = await gds.is_gds_available()
        assert available is False

    @pytest.mark.asyncio
    async def test_pagerank_fallback_when_gds_unavailable(
        self, gds: Any, mock_memory_client: MagicMock
    ) -> None:
        """Test PageRank uses fallback when GDS unavailable."""
        # Mark GDS as unavailable
        gds._gds_available = False

        # Mock fallback query
        mock_session = mock_memory_client._client.session.return_value.__aenter__.return_value
        mock_result = MagicMock()
        mock_result.data = AsyncMock(
            return_value=[
                {"entity_id": "e1", "score": 0.8},
                {"entity_id": "e2", "score": 0.6},
            ]
        )
        mock_session.run = AsyncMock(return_value=mock_result)

        scores = await gds.get_pagerank_scores(["e1", "e2"])

        # Should return scores from fallback
        assert len(scores) == 2

    @pytest.mark.asyncio
    async def test_find_shortest_path(self, gds: Any, mock_memory_client: MagicMock) -> None:
        """Test find_shortest_path() returns path."""
        mock_session = mock_memory_client._client.session.return_value.__aenter__.return_value
        mock_result = MagicMock()
        mock_result.single = AsyncMock(
            return_value={
                "nodes": [
                    {"id": "1", "name": "Alice", "type": "Person"},
                    {"id": "2", "name": "Bob", "type": "Person"},
                ],
                "relationships": [{"type": "KNOWS"}],
            }
        )
        mock_session.run = AsyncMock(return_value=mock_result)

        path = await gds.find_shortest_path("Alice", "Bob")

        assert path is not None
        assert len(path["nodes"]) == 2


class TestMemoryTools:
    """Tests for memory tools."""

    @pytest.fixture
    def mock_memory(self) -> MagicMock:
        """Create a mock Neo4jMicrosoftMemory."""
        memory = MagicMock()
        memory.gds = None
        memory._client = MagicMock()
        return memory

    def test_create_memory_tools_returns_list(self, mock_memory: MagicMock) -> None:
        """Test create_memory_tools() returns tool definitions."""
        from neo4j_agent_memory.integrations.microsoft_agent import create_memory_tools

        tools = create_memory_tools(mock_memory)

        assert isinstance(tools, list)
        assert len(tools) >= 6  # At least 6 basic tools

        # Check tool structure — FunctionTool objects have name/description attrs
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")

    def test_create_memory_tools_includes_search_memory(self, mock_memory: MagicMock) -> None:
        """Test tools include search_memory."""
        from neo4j_agent_memory.integrations.microsoft_agent import create_memory_tools

        tools = create_memory_tools(mock_memory)
        tool_names = [t.name for t in tools]

        assert "search_memory" in tool_names
        assert "remember_preference" in tool_names
        assert "recall_preferences" in tool_names
        assert "search_knowledge" in tool_names
        assert "remember_fact" in tool_names
        assert "find_similar_tasks" in tool_names

    @pytest.mark.asyncio
    async def test_execute_memory_tool_search(self, mock_memory: MagicMock) -> None:
        """Test execute_memory_tool for search_memory."""
        from neo4j_agent_memory.integrations.microsoft_agent import execute_memory_tool

        mock_memory.search_memory = AsyncMock(
            return_value={
                "messages": [{"role": "user", "content": "test"}],
                "entities": [],
                "preferences": [],
            }
        )

        result = await execute_memory_tool(
            mock_memory,
            "search_memory",
            {"query": "test query", "limit": 5},
        )

        data = json.loads(result)
        assert "results" in data

    @pytest.mark.asyncio
    async def test_execute_memory_tool_remember_preference(self, mock_memory: MagicMock) -> None:
        """Test execute_memory_tool for remember_preference."""
        from neo4j_agent_memory.integrations.microsoft_agent import execute_memory_tool

        mock_memory.add_preference = AsyncMock()

        result = await execute_memory_tool(
            mock_memory,
            "remember_preference",
            {"category": "shopping", "preference": "Prefers blue colors"},
        )

        data = json.loads(result)
        assert data["status"] == "saved"
        assert data["category"] == "shopping"

    @pytest.mark.asyncio
    async def test_execute_memory_tool_unknown(self, mock_memory: MagicMock) -> None:
        """Test execute_memory_tool returns error for unknown tool."""
        from neo4j_agent_memory.integrations.microsoft_agent import execute_memory_tool

        result = await execute_memory_tool(
            mock_memory,
            "unknown_tool",
            {},
        )

        data = json.loads(result)
        assert "error" in data


class TestGDSConfig:
    """Tests for GDSConfig."""

    def test_default_config(self) -> None:
        """Test default GDS configuration."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSConfig

        config = GDSConfig()

        assert config.enabled is False
        assert config.fallback_to_basic is True
        assert config.use_pagerank_for_ranking is True
        assert config.pagerank_weight == 0.3

    def test_custom_config(self) -> None:
        """Test custom GDS configuration."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            GDSAlgorithm,
            GDSConfig,
        )

        config = GDSConfig(
            enabled=True,
            use_pagerank_for_ranking=False,
            pagerank_weight=0.5,
            expose_as_tools=[GDSAlgorithm.SHORTEST_PATH, GDSAlgorithm.NODE_SIMILARITY],
            fallback_to_basic=False,
        )

        assert config.enabled is True
        assert config.use_pagerank_for_ranking is False
        assert config.pagerank_weight == 0.5
        assert len(config.expose_as_tools) == 2
        assert config.fallback_to_basic is False


class TestTracing:
    """Tests for reasoning trace recording."""

    @pytest.fixture
    def mock_memory(self) -> MagicMock:
        """Create a mock Neo4jMicrosoftMemory."""
        memory = MagicMock()
        memory.session_id = "test-session"
        memory.memory_client = MagicMock()
        memory.memory_client.reasoning = MagicMock()
        return memory

    @pytest.mark.asyncio
    async def test_record_agent_trace(self, mock_memory: MagicMock) -> None:
        """Test record_agent_trace() creates a trace."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import record_agent_trace

        # Mock trace operations
        mock_trace = MagicMock()
        mock_trace.id = "trace-123"
        mock_memory.memory_client.reasoning.start_trace = AsyncMock(return_value=mock_trace)
        mock_memory.memory_client.reasoning.add_step = AsyncMock(
            return_value=MagicMock(id="step-1")
        )
        mock_memory.memory_client.reasoning.record_tool_call = AsyncMock()
        mock_memory.memory_client.reasoning.complete_trace = AsyncMock()
        mock_memory.memory_client.reasoning.get_trace = AsyncMock(return_value=mock_trace)

        messages = [
            Message("user", ["Find me running shoes"]),
            Message("assistant", ["Here are some options..."]),
        ]

        trace = await record_agent_trace(
            memory=mock_memory,
            messages=messages,
            task="Find running shoes",
            outcome="Found 3 options",
            success=True,
        )

        assert trace == mock_trace
        mock_memory.memory_client.reasoning.start_trace.assert_called_once()
        mock_memory.memory_client.reasoning.complete_trace.assert_called_once()

    def test_format_traces_for_prompt(self) -> None:
        """Test format_traces_for_prompt() formats traces."""
        from neo4j_agent_memory.integrations.microsoft_agent import format_traces_for_prompt

        mock_trace = MagicMock()
        mock_trace.task = "Find products"
        mock_trace.outcome = "Found 5 products"
        mock_trace.success = True
        mock_trace.steps = []

        formatted = format_traces_for_prompt([mock_trace])

        assert "Past experience" in formatted
        assert "Find products" in formatted
        assert "Found 5 products" in formatted
        assert "Succeeded" in formatted

    def test_format_traces_empty_list(self) -> None:
        """Test format_traces_for_prompt() with empty list."""
        from neo4j_agent_memory.integrations.microsoft_agent import format_traces_for_prompt

        formatted = format_traces_for_prompt([])
        assert formatted == ""
