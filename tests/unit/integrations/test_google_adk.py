"""Unit tests for Google ADK integration."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMemoryEntryTypes:
    """Tests for ADK type conversions."""

    def test_memory_entry_creation(self):
        """Test MemoryEntry dataclass creation."""
        from neo4j_agent_memory.integrations.google_adk.types import MemoryEntry

        entry = MemoryEntry(
            id="test-1",
            content="Test content",
            memory_type="message",
            timestamp=datetime.now(),
            metadata={"key": "value"},
            score=0.95,
        )

        assert entry.id == "test-1"
        assert entry.content == "Test content"
        assert entry.memory_type == "message"
        assert entry.score == 0.95

    def test_session_message_creation(self):
        """Test SessionMessage dataclass creation."""
        from neo4j_agent_memory.integrations.google_adk.types import SessionMessage

        msg = SessionMessage(
            role="user",
            content="Hello",
            timestamp=datetime.now(),
            metadata={"source": "test"},
        )

        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_to_memory_entry(self):
        """Test converting Message to MemoryEntry."""
        from neo4j_agent_memory.integrations.google_adk.types import message_to_memory_entry

        mock_msg = MagicMock()
        mock_msg.id = "msg-123"
        mock_msg.content = "Test message"
        mock_msg.role = MagicMock(value="user")
        mock_msg.created_at = datetime.now()
        mock_msg.metadata = {"similarity": 0.9}
        mock_msg.session_id = "session-1"

        entry = message_to_memory_entry(mock_msg)

        assert entry.id == "msg-123"
        assert entry.content == "Test message"
        assert entry.memory_type == "message"
        assert entry.metadata["role"] == "user"
        assert entry.score == 0.9

    def test_entity_to_memory_entry(self):
        """Test converting Entity to MemoryEntry."""
        from neo4j_agent_memory.integrations.google_adk.types import entity_to_memory_entry

        mock_entity = MagicMock()
        mock_entity.id = "entity-123"
        mock_entity.display_name = "Alice"
        mock_entity.type = MagicMock(value="PERSON")
        mock_entity.description = "A software engineer"
        mock_entity.aliases = ["Al"]
        mock_entity.created_at = datetime.now()

        entry = entity_to_memory_entry(mock_entity)

        assert entry.id == "entity-123"
        assert "Alice" in entry.content
        assert "software engineer" in entry.content
        assert entry.memory_type == "entity"
        assert entry.metadata["name"] == "Alice"
        assert entry.metadata["type"] == "PERSON"

    def test_preference_to_memory_entry(self):
        """Test converting Preference to MemoryEntry."""
        from neo4j_agent_memory.integrations.google_adk.types import preference_to_memory_entry

        mock_pref = MagicMock()
        mock_pref.id = "pref-123"
        mock_pref.category = "communication"
        mock_pref.preference = "Prefers email over calls"
        mock_pref.context = "work hours"
        mock_pref.created_at = datetime.now()

        entry = preference_to_memory_entry(mock_pref)

        assert entry.id == "pref-123"
        assert "[communication]" in entry.content
        assert "Prefers email" in entry.content
        assert entry.memory_type == "preference"
        assert entry.metadata["category"] == "communication"

    def test_session_message_from_dict(self):
        """Test creating SessionMessage from dict."""
        from neo4j_agent_memory.integrations.google_adk.types import session_message_from_dict

        data = {
            "role": "assistant",
            "content": "How can I help?",
            "timestamp": datetime.now(),
            "metadata": {"tokens": 10},
        }

        msg = session_message_from_dict(data)

        assert msg.role == "assistant"
        assert msg.content == "How can I help?"
        assert msg.metadata["tokens"] == 10

    def test_memory_entry_to_dict(self):
        """Test converting MemoryEntry to dict."""
        from neo4j_agent_memory.integrations.google_adk.types import (
            MemoryEntry,
            memory_entry_to_dict,
        )

        entry = MemoryEntry(
            id="test-1",
            content="Test",
            memory_type="message",
            timestamp=datetime(2024, 1, 15, 10, 30),
            metadata={"key": "value"},
            score=0.8,
        )

        result = memory_entry_to_dict(entry)

        assert result["id"] == "test-1"
        assert result["content"] == "Test"
        assert result["memory_type"] == "message"
        assert result["timestamp"] == "2024-01-15T10:30:00"
        assert result["score"] == 0.8


class TestNeo4jMemoryService:
    """Tests for Neo4jMemoryService class."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock memory client."""
        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        return client

    @pytest.fixture
    def memory_service(self, mock_memory_client):
        """Create memory service with mock client."""
        from neo4j_agent_memory.integrations.google_adk.memory_service import (
            Neo4jMemoryService,
        )

        return Neo4jMemoryService(
            memory_client=mock_memory_client,
            user_id="test-user",
        )

    def test_initialization(self, memory_service):
        """Test memory service initialization."""
        assert memory_service.user_id == "test-user"
        assert memory_service._include_entities is True
        assert memory_service._include_preferences is True
        assert memory_service._extract_on_store is True

    def test_initialization_custom_settings(self, mock_memory_client):
        """Test memory service with custom settings."""
        from neo4j_agent_memory.integrations.google_adk.memory_service import (
            Neo4jMemoryService,
        )

        service = Neo4jMemoryService(
            memory_client=mock_memory_client,
            user_id="custom-user",
            include_entities=False,
            include_preferences=False,
            extract_on_store=False,
        )

        assert service.user_id == "custom-user"
        assert service._include_entities is False
        assert service._include_preferences is False
        assert service._extract_on_store is False

    @pytest.mark.asyncio
    async def test_add_session_to_memory(self, memory_service, mock_memory_client):
        """Test adding session to memory."""
        mock_message = MagicMock()
        mock_message.id = "msg-1"

        mock_memory_client.short_term.add_message = AsyncMock(return_value=mock_message)

        # Create mock session
        session = {
            "id": "session-123",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        }

        await memory_service.add_session_to_memory(session)

        # Should have stored 2 messages
        assert mock_memory_client.short_term.add_message.call_count == 2

    @pytest.mark.asyncio
    async def test_add_session_with_session_object(self, memory_service, mock_memory_client):
        """Test adding session with ADK-style session object."""
        mock_message = MagicMock()
        mock_memory_client.short_term.add_message = AsyncMock(return_value=mock_message)

        # Create mock ADK session object (spec limits attributes to avoid
        # MagicMock auto-creating .events which would trigger the events path)
        mock_session = MagicMock(spec=["id", "messages"])
        mock_session.id = "adk-session-1"
        mock_msg = MagicMock(spec=["role", "content"])
        mock_msg.role = "user"
        mock_msg.content = "Test message"
        mock_session.messages = [mock_msg]

        await memory_service.add_session_to_memory(mock_session)

        mock_memory_client.short_term.add_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_session_empty_messages(self, memory_service, mock_memory_client):
        """Test adding session with no messages."""
        session = {"id": "empty-session", "messages": []}

        await memory_service.add_session_to_memory(session)

        mock_memory_client.short_term.add_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_memory(self, memory_service, mock_memory_client):
        """Test searching memories."""
        # Setup mock responses
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.content = "Project deadline is next week"
        mock_msg.role = MagicMock(value="user")
        mock_msg.created_at = None
        mock_msg.metadata = {"similarity": 0.9}

        mock_entity = MagicMock()
        mock_entity.id = "entity-1"
        mock_entity.display_name = "Project Alpha"
        mock_entity.type = MagicMock(value="OBJECT")
        mock_entity.description = "A software project"

        mock_pref = MagicMock()
        mock_pref.id = "pref-1"
        mock_pref.category = "work"
        mock_pref.preference = "Likes morning meetings"
        mock_pref.context = None

        mock_memory_client.short_term.search_messages = AsyncMock(return_value=[mock_msg])
        mock_memory_client.long_term.search_entities = AsyncMock(return_value=[mock_entity])
        mock_memory_client.long_term.search_preferences = AsyncMock(return_value=[mock_pref])

        results = await memory_service.search_memory("project deadline")

        assert len(results.memories) == 3

        texts = [r.content.parts[0].text for r in results.memories]

        assert any("Project deadline is next week" in t for t in texts)
        assert any("Project Alpha" in t for t in texts)
        assert any("Likes morning meetings" in t for t in texts)

    @pytest.mark.asyncio
    async def test_search_memory_without_entities(self, mock_memory_client):
        """Test searching with entities disabled."""
        from neo4j_agent_memory.integrations.google_adk.memory_service import (
            Neo4jMemoryService,
        )

        service = Neo4jMemoryService(
            memory_client=mock_memory_client,
            include_entities=False,
            include_preferences=False,
        )

        mock_memory_client.short_term.search_messages = AsyncMock(return_value=[])

        await service.search_memory("test query")

        mock_memory_client.short_term.search_messages.assert_called_once()
        mock_memory_client.long_term.search_entities.assert_not_called()
        mock_memory_client.long_term.search_preferences.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_memories_for_session(self, memory_service, mock_memory_client):
        """Test getting memories for a session."""
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.content = "Hello"
        mock_msg.role = MagicMock(value="user")
        mock_msg.created_at = None
        mock_msg.metadata = None

        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_msg]

        mock_memory_client.short_term.get_conversation = AsyncMock(return_value=mock_conversation)

        results = await memory_service.get_memories_for_session("session-123")

        assert len(results) == 1
        assert results[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_add_memory_message(self, memory_service, mock_memory_client):
        """Test adding a message memory."""
        mock_msg = MagicMock()
        mock_msg.id = "msg-new"
        mock_msg.content = "New message"
        mock_msg.role = MagicMock(value="user")
        mock_msg.created_at = None
        mock_msg.metadata = None

        mock_memory_client.short_term.add_message = AsyncMock(return_value=mock_msg)

        result = await memory_service.add_memory(
            content="New message",
            memory_type="message",
            session_id="session-1",
            role="user",
        )

        assert result is not None
        assert result.memory_type == "message"
        mock_memory_client.short_term.add_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_memory_preference(self, memory_service, mock_memory_client):
        """Test adding a preference memory."""
        mock_pref = MagicMock()
        mock_pref.id = "pref-new"
        mock_pref.category = "ui"
        mock_pref.preference = "Dark mode"
        mock_pref.context = None

        mock_memory_client.long_term.add_preference = AsyncMock(return_value=mock_pref)

        result = await memory_service.add_memory(
            content="Dark mode",
            memory_type="preference",
            category="ui",
        )

        assert result is not None
        assert result.memory_type == "preference"
        mock_memory_client.long_term.add_preference.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_memory_unknown_type(self, memory_service):
        """Test adding memory with unknown type returns None."""
        result = await memory_service.add_memory(
            content="Test",
            memory_type="unknown",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_clear_session(self, memory_service, mock_memory_client):
        """Test clearing a session."""
        mock_memory_client.short_term.clear_session = AsyncMock()

        await memory_service.clear_session("session-to-clear")

        mock_memory_client.short_term.clear_session.assert_called_once_with("session-to-clear")

    def test_extract_messages_from_dict(self, memory_service):
        """Test extracting messages from dict session."""
        session = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ]
        }

        messages = memory_service._extract_messages(session)

        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_extract_messages_from_list(self, memory_service):
        """Test extracting messages from list."""
        session = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        messages = memory_service._extract_messages(session)

        assert len(messages) == 2

    def test_extract_messages_from_object(self, memory_service):
        """Test extracting messages from ADK-style object."""
        mock_msg1 = MagicMock()
        mock_msg1.role = MagicMock(value="user")
        mock_msg1.content = "Hello"

        mock_msg2 = MagicMock()
        mock_msg2.role = "assistant"
        mock_msg2.content = "Hi!"

        mock_session = MagicMock(spec=["messages"])
        mock_session.messages = [mock_msg1, mock_msg2]

        messages = memory_service._extract_messages(mock_session)

        assert len(messages) == 2


class TestIssue130Regression:
    """Regression coverage for #130 — ADK/NAMS incompatibility.

    Three defects:
      1. Unscoped message search hard-rejected by NAMS.
      2. Tool objects (FunctionCall/FunctionResponse) stringified into the
         entity pipeline, producing empty knowledge graphs.
      3. (doc/signature — covered by NAMS short_term alias tests.)
    """

    @pytest.fixture
    def service_factory(self):
        from neo4j_agent_memory.integrations.google_adk.memory_service import (
            Neo4jMemoryService,
        )

        def _make(backend: str):
            client = MagicMock()
            client.backend = backend
            client.short_term = MagicMock()
            client.long_term = MagicMock()
            client.short_term.search_messages = AsyncMock(return_value=[])
            client.long_term.search_entities = AsyncMock(return_value=[])
            client.long_term.search_preferences = AsyncMock(return_value=[])
            return Neo4jMemoryService(memory_client=client), client

        return _make

    # --- Defect 2: tool-event filtering --------------------------------------

    def test_extract_text_ignores_tool_only_parts(self, service_factory):
        service, _ = service_factory("nams")

        text_part = SimpleNamespace(text="real user text")
        tool_call_part = SimpleNamespace(text=None, function_call={"name": "search"})
        mixed = SimpleNamespace(parts=[text_part, tool_call_part])
        assert service._extract_text_from_content(mixed) == "real user text"

        tool_only = SimpleNamespace(parts=[tool_call_part])
        assert service._extract_text_from_content(tool_only) == ""  # never str(content)

    def test_tool_only_event_produces_no_message(self, service_factory):
        service, _ = service_factory("nams")
        tool_event = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=None, function_call={"n": 1})]),
            author="model",
        )
        text_event = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="hello")]),
            author="user",
        )
        session = SimpleNamespace(events=[tool_event, text_event])
        messages = service._extract_messages(session)
        assert len(messages) == 1
        assert messages[0].content == "hello"

    # --- Defect 1: conversation-scoped search --------------------------------

    @pytest.mark.asyncio
    async def test_search_scopes_to_tracked_session_on_nams(self, service_factory):
        service, client = service_factory("nams")
        await service.add_session_to_memory({"id": "sess-7", "messages": []})
        await service.search_memory("q")
        # search_messages must be called WITH the tracked session_id.
        client.short_term.search_messages.assert_awaited_once()
        assert client.short_term.search_messages.await_args.kwargs["session_id"] == "sess-7"

    @pytest.mark.asyncio
    async def test_search_skips_messages_when_no_session_on_nams(self, service_factory):
        service, client = service_factory("nams")
        await service.search_memory("q")  # no session ever tracked
        # Never calls the unscoped search that NAMS rejects...
        client.short_term.search_messages.assert_not_awaited()
        # ...but entity + preference recall still runs (cross-session).
        client.long_term.search_entities.assert_awaited_once()
        client.long_term.search_preferences.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_unscoped_on_bolt(self, service_factory):
        service, client = service_factory("bolt")
        await service.search_memory("q")
        client.short_term.search_messages.assert_awaited_once()
        assert "session_id" not in client.short_term.search_messages.await_args.kwargs

    @pytest.mark.asyncio
    async def test_search_kwarg_session_overrides_tracked(self, service_factory):
        service, client = service_factory("nams")
        await service.add_session_to_memory({"id": "tracked", "messages": []})
        await service.search_memory("q", session_id="explicit")
        assert client.short_term.search_messages.await_args.kwargs["session_id"] == "explicit"


class TestADK2xContract:
    """Conformance with the google-adk 2.x ``BaseMemoryService`` contract.

    google-adk is an optional extra, so every test here skips when it is not
    installed. What is locked in:

    * the service really is a ``BaseMemoryService`` (so ``Runner`` accepts it);
    * ``search_memory(*, app_name, user_id, query)`` returns a
      ``SearchMemoryResponse`` of ADK ``MemoryEntry`` objects whose ``content``
      is a ``google.genai.types.Content`` with ``author``/``timestamp`` set
      (what ``load_memory`` / ``preload_memory`` render);
    * the 2.x-only write paths ``add_events_to_memory`` and the
      ``add_memory(memories=[...])`` form.
    """

    @pytest.fixture
    def adk(self):
        return pytest.importorskip("google.adk", reason="requires the [google-adk] extra")

    @pytest.fixture
    def client(self):
        client = MagicMock()
        client.backend = "bolt"
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.short_term.add_message = AsyncMock(return_value=MagicMock())
        client.short_term.search_messages = AsyncMock(return_value=[])
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def service(self, adk, client):
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        return Neo4jMemoryService(memory_client=client, user_id="u-1")

    def test_is_a_base_memory_service(self, service):
        """Runner(memory_service=...) type-checks against the ADK ABC."""
        from google.adk.memory import BaseMemoryService

        assert isinstance(service, BaseMemoryService)

    def test_runner_accepts_the_service(self, service):
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import load_memory

        runner = Runner(
            app_name="memory-demo",
            agent=LlmAgent(name="memory_demo", model="gemini-2.5-flash", tools=[load_memory]),
            session_service=InMemorySessionService(),
            memory_service=service,
        )
        assert runner.memory_service is service

    @pytest.mark.asyncio
    async def test_add_session_to_memory_ingests_adk_session_events(self, service, client):
        """A real 2.x Session -> one message per text-bearing event."""
        from google.adk.events.event import Event
        from google.adk.sessions.session import Session
        from google.genai import types

        session = Session(
            id="adk-2x-session",
            app_name="memory-demo",
            user_id="u-1",
            events=[
                Event(
                    author="user",
                    content=types.Content(
                        role="user", parts=[types.Part(text="My sister is Sarah.")]
                    ),
                ),
                Event(
                    author="memory_demo",
                    content=types.Content(
                        role="model", parts=[types.Part(text="Noted — Sarah is your sister.")]
                    ),
                ),
                # Tool-only event: must be skipped (#130, defect 2).
                Event(
                    author="memory_demo",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="load_memory", args={"query": "sister"}
                                )
                            )
                        ],
                    ),
                ),
            ],
        )

        await service.add_session_to_memory(session)

        assert client.short_term.add_message.await_count == 2
        sessions = {c.kwargs["session_id"] for c in client.short_term.add_message.await_args_list}
        assert sessions == {"adk-2x-session"}
        # ADK authors the model turn with the *agent name*; the library only
        # accepts MessageRole values, so it is stored as "assistant" with the
        # original author kept in metadata.
        calls = client.short_term.add_message.await_args_list
        roles = [c.kwargs["role"] for c in calls]
        assert roles == ["user", "assistant"]
        assert calls[0].kwargs["metadata"] is None
        assert calls[1].kwargs["metadata"] == {"adk_author": "memory_demo"}

    @pytest.mark.asyncio
    async def test_agent_authored_events_are_stored_not_rejected(self, service, client):
        """Regression: an agent-named author must not reach MessageRole raw.

        ``short_term.add_message`` validates the role against ``MessageRole``,
        so passing ADK's author through verbatim dropped every model turn.
        """
        from google.adk.events.event import Event
        from google.genai import types

        await service.add_events_to_memory(
            app_name="memory-demo",
            user_id="u-1",
            events=[
                Event(
                    author="supervisor",
                    content=types.Content(role="model", parts=[types.Part(text="Routed to KYC")]),
                )
            ],
            session_id="s-agent",
        )

        kwargs = client.short_term.add_message.await_args.kwargs
        assert kwargs["role"] == "assistant"
        assert kwargs["metadata"]["adk_author"] == "supervisor"

    @pytest.mark.asyncio
    async def test_agent_author_survives_the_round_trip(self, service, client):
        """search_memory() re-attributes the entry to the ADK agent name."""
        msg = SimpleNamespace(
            id="msg-2",
            content="Routed to KYC",
            role=SimpleNamespace(value="assistant"),
            created_at=None,
            metadata={"adk_author": "supervisor"},
            session_id="s-agent",
        )
        client.short_term.search_messages = AsyncMock(return_value=[msg])

        response = await service.search_memory(app_name="a", user_id="u-1", query="kyc")

        entry = response.memories[0]
        assert entry.author == "supervisor"
        assert entry.content.role == "model"

    @pytest.mark.asyncio
    async def test_search_memory_keyword_only_adk_call(self, service, client):
        """ADK calls search_memory(app_name=, user_id=, query=)."""
        from google.adk.memory.base_memory_service import SearchMemoryResponse

        response = await service.search_memory(app_name="memory-demo", user_id="u-1", query="q")

        assert isinstance(response, SearchMemoryResponse)
        assert response.memories == []
        assert client.short_term.search_messages.await_args.kwargs["query"] == "q"

    @pytest.mark.asyncio
    async def test_search_memory_returns_adk_memory_entries(self, service, client):
        """Entries carry Content parts plus author/timestamp/custom_metadata."""
        from google.adk.memory.memory_entry import MemoryEntry as ADKMemoryEntry
        from google.adk.tools import _memory_entry_utils
        from google.genai import types

        created = datetime(2026, 9, 10, 12, 0, 0)
        msg = SimpleNamespace(
            id="msg-1",
            content="My sister is Sarah.",
            role=SimpleNamespace(value="user"),
            created_at=created,
            metadata={"similarity": 0.91},
            session_id="s-1",
        )
        pref = SimpleNamespace(
            id="pref-1",
            category="scheduling",
            preference="Prefers morning meetings",
            context=None,
            created_at=created,
        )
        client.short_term.search_messages = AsyncMock(return_value=[msg])
        client.long_term.search_preferences = AsyncMock(return_value=[pref])

        response = await service.search_memory(app_name="a", user_id="u-1", query="sister")

        assert all(isinstance(e, ADKMemoryEntry) for e in response.memories)
        assert all(isinstance(e.content, types.Content) for e in response.memories)
        # preload_memory/load_memory read the text through this helper.
        texts = [_memory_entry_utils.extract_text(e) for e in response.memories]
        assert "My sister is Sarah." in texts
        assert "[scheduling] Prefers morning meetings" in texts

        by_author = {e.author: e for e in response.memories}
        assert by_author["user"].content.role == "user"
        assert by_author["user"].timestamp == created.isoformat()
        assert by_author["user"].custom_metadata["memory_type"] == "message"
        assert by_author["user"].custom_metadata["score"] == 0.91
        # Long-term recall is attributed to its memory type, not a chat role.
        assert by_author["preference"].content.role == "model"
        assert by_author["preference"].custom_metadata["category"] == "scheduling"

    @pytest.mark.asyncio
    async def test_add_events_to_memory_delta(self, service, client):
        """Context.add_events_to_memory(events=...) ingests just those events."""
        from google.adk.events.event import Event
        from google.genai import types

        events = [
            Event(
                author="user",
                content=types.Content(role="user", parts=[types.Part(text="Turn two text")]),
            ),
            Event(author="memory_demo", content=None),
        ]

        await service.add_events_to_memory(
            app_name="memory-demo",
            user_id="u-1",
            events=events,
            session_id="delta-session",
            custom_metadata={"turn": 2},
        )

        client.short_term.add_message.assert_awaited_once()
        kwargs = client.short_term.add_message.await_args.kwargs
        assert kwargs["session_id"] == "delta-session"
        assert kwargs["content"] == "Turn two text"
        assert kwargs["metadata"] == {"turn": 2}

    @pytest.mark.asyncio
    async def test_add_memory_adk_form(self, service, client):
        """add_memory(app_name=, user_id=, memories=[MemoryEntry]) writes messages."""
        from google.adk.memory.memory_entry import MemoryEntry as ADKMemoryEntry
        from google.genai import types

        await service.add_session_to_memory({"id": "tracked-session", "messages": []})
        client.short_term.add_message.reset_mock()

        result = await service.add_memory(
            app_name="memory-demo",
            user_id="u-1",
            memories=[
                ADKMemoryEntry(
                    content=types.Content(
                        role="user", parts=[types.Part(text="Remember the Q3 deadline")]
                    ),
                    author="user",
                ),
            ],
        )

        assert result is None  # the ADK form returns nothing
        kwargs = client.short_term.add_message.await_args.kwargs
        assert kwargs["session_id"] == "tracked-session"  # falls back to the tracked session
        assert kwargs["role"] == "user"
        assert kwargs["content"] == "Remember the Q3 deadline"

    @pytest.mark.asyncio
    async def test_add_memory_convenience_form_still_returns_an_entry(self, service, client):
        """The 0.5.0 convenience form is unchanged."""
        pref = SimpleNamespace(
            id="pref-1",
            category="ui",
            preference="Dark mode",
            context=None,
            created_at=None,
        )
        client.long_term.add_preference = AsyncMock(return_value=pref)

        entry = await service.add_memory(
            content="Dark mode", memory_type="preference", category="ui"
        )

        assert entry is not None
        assert entry.memory_type == "preference"

    @pytest.mark.asyncio
    async def test_add_memory_without_content_or_memories_is_a_noop(self, service, client):
        assert await service.add_memory() is None
        client.short_term.add_message.assert_not_awaited()


class TestWithoutGoogleADKInstalled:
    """The adapter must import and search without the optional extra.

    ``google-adk`` is optional, so ``memory_service`` falls back to local
    stand-ins with the same attribute shape (``SearchMemoryResponse.memories``,
    ``MemoryEntry.content.parts[*].text``, ``author``, ``timestamp``).
    """

    @pytest.fixture
    def no_adk_module(self, monkeypatch):
        import builtins
        import importlib
        import sys

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith(("google.adk", "google.genai")):
                raise ImportError(f"blocked for test: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        for name in [
            m for m in list(sys.modules) if m.startswith("neo4j_agent_memory.integrations")
        ]:
            monkeypatch.delitem(sys.modules, name, raising=False)

        return importlib.import_module("neo4j_agent_memory.integrations.google_adk.memory_service")

    def test_falls_back_to_local_stand_ins(self, no_adk_module):
        assert no_adk_module.SearchMemoryResponse.__module__ == no_adk_module.__name__
        assert no_adk_module.ADKMemoryEntry.__module__ == no_adk_module.__name__

    @pytest.mark.asyncio
    async def test_search_still_returns_the_same_shape(self, no_adk_module):
        client = MagicMock()
        client.backend = "bolt"
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.short_term.search_messages = AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="msg-1",
                    content="No ADK installed here",
                    role=SimpleNamespace(value="user"),
                    created_at=datetime(2026, 9, 10, 12, 0, 0),
                    metadata=None,
                    session_id="s-1",
                )
            ]
        )
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])

        service = no_adk_module.Neo4jMemoryService(memory_client=client)
        response = await service.search_memory(app_name="a", user_id="u", query="adk")

        entry = response.memories[0]
        assert entry.content.parts[0].text == "No ADK installed here"
        assert entry.content.role == "user"
        assert entry.author == "user"
        assert entry.timestamp == "2026-09-10T12:00:00"
        assert entry.custom_metadata["memory_type"] == "message"


class TestLoadMemoryToolThroughRunner:
    """The real ADK 2.x plumbing: Runner -> load_memory -> search_memory.

    Uses a scripted ``BaseLlm`` so no API key or network is needed: turn 1 emits
    a ``load_memory`` function call, turn 2 answers from the tool result.
    """

    @pytest.mark.asyncio
    async def test_load_memory_returns_neo4j_backed_entries(self):
        pytest.importorskip("google.adk", reason="requires the [google-adk] extra")

        from collections.abc import AsyncGenerator

        from google.adk.agents import LlmAgent
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import load_memory
        from google.genai import types

        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        class ScriptedLlm(BaseLlm):
            turn: int = 0

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                self.turn += 1
                if self.turn == 1:
                    yield LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(
                                        name="load_memory", args={"query": "sister"}
                                    )
                                )
                            ],
                        )
                    )
                else:
                    yield LlmResponse(
                        content=types.Content(
                            role="model", parts=[types.Part(text="Your sister is Sarah.")]
                        )
                    )

        client = MagicMock()
        client.backend = "bolt"
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.short_term.add_message = AsyncMock(return_value=MagicMock())
        client.short_term.search_messages = AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="m1",
                    content="My sister is Sarah.",
                    role=SimpleNamespace(value="user"),
                    created_at=None,
                    metadata=None,
                    session_id="s1",
                )
            ]
        )
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])

        session_service = InMemorySessionService()
        runner = Runner(
            app_name="probe-app",
            agent=LlmAgent(name="probe", model=ScriptedLlm(model="scripted"), tools=[load_memory]),
            session_service=session_service,
            memory_service=Neo4jMemoryService(memory_client=client, user_id="u1"),
        )
        session = await session_service.create_session(app_name="probe-app", user_id="u1")

        tool_results = []
        texts = []
        try:
            async for event in runner.run_async(
                user_id="u1",
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text="Who is my sister?")]
                ),
            ):
                for part in event.content.parts if event.content and event.content.parts else []:
                    if part.text:
                        texts.append(part.text)
                    if part.function_response:
                        tool_results.append(part.function_response.response)
        finally:
            await runner.close()

        client.short_term.search_messages.assert_awaited_once()
        assert client.short_term.search_messages.await_args.kwargs["query"] == "sister"
        assert texts == ["Your sister is Sarah."]
        # load_memory wraps our SearchMemoryResponse.memories in its own model.
        memories = tool_results[0]["result"].memories
        assert memories[0].content.parts[0].text == "My sister is Sarah."
        assert memories[0].author == "user"
