"""Unit tests for MemoryIntegration convenience layer."""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy


@pytest.fixture
def mock_client():
    """Create a mock MemoryClient with async methods."""
    client = MagicMock()
    client.short_term = MagicMock()
    client.long_term = MagicMock()
    client.reasoning = MagicMock()
    client.graph = MagicMock()
    return client


@pytest.fixture
def integration(mock_client):
    """Create a MemoryIntegration with a mock client."""
    return MemoryIntegration(mock_client)


class TestSessionStrategy:
    """Tests for session ID resolution strategies."""

    def test_per_conversation_generates_uuid(self, mock_client):
        integration = MemoryIntegration(
            mock_client, session_strategy=SessionStrategy.PER_CONVERSATION
        )
        sid = integration.resolve_session_id()
        # Should be a valid UUID
        UUID(sid)

    def test_per_conversation_reuses_uuid(self, mock_client):
        integration = MemoryIntegration(
            mock_client, session_strategy=SessionStrategy.PER_CONVERSATION
        )
        sid1 = integration.resolve_session_id()
        sid2 = integration.resolve_session_id()
        assert sid1 == sid2

    def test_per_day_includes_date(self, mock_client):
        integration = MemoryIntegration(
            mock_client,
            session_strategy=SessionStrategy.PER_DAY,
            user_id="alice",
        )
        sid = integration.resolve_session_id()
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        assert sid == f"alice-{today}"

    def test_per_day_default_user(self, mock_client):
        integration = MemoryIntegration(mock_client, session_strategy=SessionStrategy.PER_DAY)
        sid = integration.resolve_session_id()
        assert sid.startswith("default-")

    def test_persistent_uses_user_id(self, mock_client):
        integration = MemoryIntegration(
            mock_client,
            session_strategy=SessionStrategy.PERSISTENT,
            user_id="bob",
        )
        sid = integration.resolve_session_id()
        assert sid == "bob"

    def test_persistent_default_user(self, mock_client):
        integration = MemoryIntegration(mock_client, session_strategy=SessionStrategy.PERSISTENT)
        sid = integration.resolve_session_id()
        assert sid == "default"

    def test_hint_overrides_strategy(self, mock_client):
        integration = MemoryIntegration(
            mock_client, session_strategy=SessionStrategy.PERSISTENT, user_id="bob"
        )
        sid = integration.resolve_session_id(hint="explicit-session")
        assert sid == "explicit-session"

    def test_string_strategy_accepted(self, mock_client):
        integration = MemoryIntegration(mock_client, session_strategy="per_conversation")
        assert integration._strategy == SessionStrategy.PER_CONVERSATION


class TestStoreMessage:
    """Tests for store_message method."""

    @pytest.mark.asyncio
    async def test_store_message_success(self, integration, mock_client):
        mock_msg = MagicMock()
        mock_msg.id = "msg-123"
        mock_client.short_term.add_message = AsyncMock(return_value=mock_msg)

        result = await integration.store_message("user", "Hello world")
        assert result["stored"] is True
        assert result["type"] == "message"
        assert result["id"] == "msg-123"
        assert "session_id" in result

    @pytest.mark.asyncio
    async def test_store_message_with_session_id(self, integration, mock_client):
        mock_msg = MagicMock()
        mock_msg.id = "msg-456"
        mock_client.short_term.add_message = AsyncMock(return_value=mock_msg)

        result = await integration.store_message("user", "Hello", session_id="my-session")
        assert result["session_id"] == "my-session"

    @pytest.mark.asyncio
    async def test_store_message_error(self, integration, mock_client):
        mock_client.short_term.add_message = AsyncMock(side_effect=Exception("DB error"))
        result = await integration.store_message("user", "Hello")
        assert "error" in result
        assert "DB error" in result["error"]


class TestGetContext:
    """Tests for get_context method."""

    @pytest.mark.asyncio
    async def test_get_context_success(self, integration, mock_client):
        mock_client.get_context = AsyncMock(return_value="Some context")

        result = await integration.get_context()
        assert result["has_context"] is True
        assert result["context"] == "Some context"
        assert "session_id" in result

    @pytest.mark.asyncio
    async def test_get_context_empty(self, integration, mock_client):
        mock_client.get_context = AsyncMock(return_value="")

        result = await integration.get_context()
        assert result["has_context"] is False

    @pytest.mark.asyncio
    async def test_get_context_error(self, integration, mock_client):
        mock_client.get_context = AsyncMock(side_effect=Exception("timeout"))
        result = await integration.get_context()
        assert "error" in result


class TestSearch:
    """Tests for search method."""

    @pytest.mark.asyncio
    async def test_search_messages(self, integration, mock_client):
        mock_msg = MagicMock()
        mock_msg.id = "m1"
        mock_msg.role = MagicMock(value="user")
        mock_msg.content = "test"
        mock_msg.created_at = None
        mock_msg.metadata = None
        mock_client.short_term.search_messages = AsyncMock(return_value=[mock_msg])
        mock_client.long_term.search_entities = AsyncMock(return_value=[])
        mock_client.long_term.search_preferences = AsyncMock(return_value=[])

        result = await integration.search("test query")
        assert "results" in result
        assert len(result["results"]["messages"]) == 1

    @pytest.mark.asyncio
    async def test_search_specific_types(self, integration, mock_client):
        mock_client.long_term.search_entities = AsyncMock(return_value=[])

        result = await integration.search("test", memory_types=["entities"])
        assert "entities" in result["results"]
        assert "messages" not in result["results"]


class TestAddEntity:
    """Tests for add_entity method."""

    @pytest.mark.asyncio
    async def test_add_entity_success(self, integration, mock_client):
        mock_entity = MagicMock()
        mock_entity.id = "e-1"
        mock_entity.display_name = "John Smith"
        mock_entity.type = MagicMock(value="PERSON")
        mock_dedup = MagicMock()
        mock_dedup.action = "created"
        mock_dedup.matched_entity_name = None
        mock_dedup.similarity_score = 0.0

        mock_client.long_term.add_entity = AsyncMock(return_value=(mock_entity, mock_dedup))

        result = await integration.add_entity("John Smith", "PERSON")
        assert result["stored"] is True
        assert result["name"] == "John Smith"
        assert result["entity_type"] == "PERSON"

    @pytest.mark.asyncio
    async def test_add_entity_nams_result(self, integration, mock_client):
        mock_entity = MagicMock()
        mock_entity.id = "e-2"
        mock_entity.display_name = "Acme"
        mock_entity.type = MagicMock(value="ORGANIZATION")
        mock_client.long_term.add_entity = AsyncMock(return_value=mock_entity)

        result = await integration.add_entity("Acme", "ORGANIZATION")

        assert result["stored"] is True
        assert result["name"] == "Acme"
        assert result["entity_type"] == "ORGANIZATION"
        assert "deduplication" not in result

    @pytest.mark.asyncio
    async def test_add_entity_error(self, integration, mock_client):
        mock_client.long_term.add_entity = AsyncMock(side_effect=Exception("duplicate"))
        result = await integration.add_entity("Test", "PERSON")
        assert "error" in result


class TestAddPreference:
    """Tests for add_preference method."""

    @pytest.mark.asyncio
    async def test_add_preference_success(self, integration, mock_client):
        mock_pref = MagicMock()
        mock_pref.id = "p-1"
        mock_client.long_term.add_preference = AsyncMock(return_value=mock_pref)

        result = await integration.add_preference("food", "Loves pasta")
        assert result["stored"] is True
        assert result["category"] == "food"


class TestAddFact:
    """Tests for add_fact method."""

    @pytest.mark.asyncio
    async def test_add_fact_success(self, integration, mock_client):
        mock_fact = MagicMock()
        mock_fact.id = "f-1"
        mock_client.long_term.add_fact = AsyncMock(return_value=mock_fact)

        result = await integration.add_fact("Earth", "has_radius_km", "6371")
        assert result["stored"] is True
        assert result["triple"] == "Earth -> has_radius_km -> 6371"


class TestClientProperty:
    """Tests for client property access."""

    def test_client_raises_without_connection(self):
        integration = MemoryIntegration()
        with pytest.raises(RuntimeError, match="not connected"):
            _ = integration.client

    def test_client_returns_when_connected(self, mock_client):
        integration = MemoryIntegration(mock_client)
        assert integration.client is mock_client
