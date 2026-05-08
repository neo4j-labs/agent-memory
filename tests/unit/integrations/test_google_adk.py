"""Unit tests for Google ADK integration."""

from datetime import datetime
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
