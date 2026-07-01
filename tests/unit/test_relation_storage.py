"""Unit tests for relation storage in short-term memory."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory


class MockExtractorWithRelations:
    """Mock extractor that returns both entities and relations."""

    def __init__(self, entities: list[ExtractedEntity], relations: list[ExtractedRelation]):
        self._entities = entities
        self._relations = relations

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        return ExtractionResult(
            entities=self._entities,
            relations=self._relations if extract_relations else [],
            source_text=text,
        )


class TestRelationStorage:
    """Tests for storing extracted relations."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()

        # Faithful mock: the entity-create query (`... RETURN e`) returns the
        # node so callers can read back its real id; other writes return [].
        async def _execute_write(query, params=None):
            if "RETURN e" in query:
                return [{"e": {"id": (params or {}).get("id")}}]
            return []

        client.execute_write = AsyncMock(side_effect=_execute_write)
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def sample_entities(self):
        """Sample entities for testing."""
        return [
            ExtractedEntity(
                name="Brian Chesky",
                type="PERSON",
                confidence=0.95,
            ),
            ExtractedEntity(
                name="Airbnb",
                type="ORGANIZATION",
                confidence=0.92,
            ),
        ]

    @pytest.fixture
    def sample_relations(self):
        """Sample relations for testing."""
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                confidence=0.9,
            ),
        ]

    @pytest.mark.asyncio
    async def test_extract_and_link_entities_stores_relations(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that _extract_and_link_entities stores relations."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        message = Message(
            id=uuid4(),
            role=MessageRole.USER,
            content="Brian Chesky founded Airbnb",
        )

        await memory._extract_and_link_entities(message, extract_relations=True)

        # Check that execute_write was called
        calls = mock_client.execute_write.call_args_list

        # Should have calls for:
        # - 2 entity creations
        # - 2 MENTIONS relationships
        # - 1 RELATED_TO relationship
        assert len(calls) >= 5

        # Find the relation creation call
        relation_calls = [
            call
            for call in calls
            if "CREATE_ENTITY_RELATION_BY_ID" in str(call) or "relation_type" in str(call)
        ]
        assert len(relation_calls) >= 1

    @pytest.mark.asyncio
    async def test_extract_and_link_entities_skips_relations_when_disabled(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that relations are not stored when extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        message = Message(
            id=uuid4(),
            role=MessageRole.USER,
            content="Brian Chesky founded Airbnb",
        )

        await memory._extract_and_link_entities(message, extract_relations=False)

        # Check that execute_write was called
        calls = mock_client.execute_write.call_args_list

        # Should have calls for:
        # - 2 entity creations
        # - 2 MENTIONS relationships
        # - NO RELATED_TO relationship
        assert len(calls) == 4  # 2 entities + 2 MENTIONS

    @pytest.mark.asyncio
    async def test_store_relations_uses_id_based_query_for_local_entities(
        self, mock_client, sample_relations
    ):
        """Test that _store_relations uses ID-based query when both entities are local."""
        memory = ShortTermMemory(mock_client)

        entity_name_to_id = {
            "brian chesky": "entity-1",
            "airbnb": "entity-2",
        }

        stored = await memory._store_relations(sample_relations, entity_name_to_id)

        assert stored == 1

        # Check that the ID-based query was used
        call = mock_client.execute_write.call_args
        query = call[0][0]
        params = call[0][1]

        assert "source_id" in params
        assert "target_id" in params
        assert params["source_id"] == "entity-1"
        assert params["target_id"] == "entity-2"
        assert params["relation_type"] == "FOUNDED"
        assert params["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_store_relations_uses_name_based_query_for_cross_message_entities(
        self, mock_client, sample_relations
    ):
        """Test that _store_relations uses name-based query for cross-message relations."""
        mock_client.execute_write = AsyncMock(return_value=[{"r": {}}])
        memory = ShortTermMemory(mock_client)

        # Only one entity is in local mapping
        entity_name_to_id = {
            "brian chesky": "entity-1",
            # "airbnb" is not in local mapping - simulating cross-message relation
        }

        stored = await memory._store_relations(sample_relations, entity_name_to_id)

        assert stored == 1

        # Check that the name-based query was used
        call = mock_client.execute_write.call_args
        params = call[0][1]

        assert "source_name" in params
        assert "target_name" in params
        assert params["source_name"] == "Brian Chesky"
        assert params["target_name"] == "Airbnb"

    @pytest.mark.asyncio
    async def test_store_relations_returns_zero_for_empty_relations(self, mock_client):
        """Test that _store_relations returns 0 for empty relations list."""
        memory = ShortTermMemory(mock_client)

        stored = await memory._store_relations([], {})

        assert stored == 0
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_message_with_extract_relations_true(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test add_message with extract_relations=True."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock conversation exists
        mock_client.execute_read = AsyncMock(
            return_value=[{"c": {"id": str(uuid4()), "session_id": "test"}}]
        )

        await memory.add_message(
            "test-session",
            MessageRole.USER,
            "Brian Chesky founded Airbnb",
            extract_entities=True,
            extract_relations=True,
        )

        # Verify that relations were stored
        calls = mock_client.execute_write.call_args_list
        relation_calls = [
            call for call in calls if len(call[0]) > 1 and "relation_type" in str(call[0][1])
        ]
        assert len(relation_calls) >= 1

    @pytest.mark.asyncio
    async def test_add_message_with_extract_relations_false(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test add_message with extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock conversation exists
        mock_client.execute_read = AsyncMock(
            return_value=[{"c": {"id": str(uuid4()), "session_id": "test"}}]
        )

        await memory.add_message(
            "test-session",
            MessageRole.USER,
            "Brian Chesky founded Airbnb",
            extract_entities=True,
            extract_relations=False,
        )

        # Count calls - should not include relation storage calls
        calls = mock_client.execute_write.call_args_list

        # Should have: 1 message creation + 2 entity creations + 2 MENTIONS = 5
        # Should NOT have RELATED_TO call
        relation_calls = [
            call for call in calls if len(call[0]) > 1 and "relation_type" in str(call[0][1])
        ]
        assert len(relation_calls) == 0


class TestExtractEntitiesFromSessionWithRelations:
    """Tests for extract_entities_from_session with relation support."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()

        # Faithful mock: the entity-create query (`... RETURN e`) returns the
        # node so callers can read back its real id; other writes return [].
        async def _execute_write(query, params=None):
            if "RETURN e" in query:
                return [{"e": {"id": (params or {}).get("id")}}]
            return []

        client.execute_write = AsyncMock(side_effect=_execute_write)
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def sample_entities(self):
        return [
            ExtractedEntity(name="Brian Chesky", type="PERSON", confidence=0.95),
            ExtractedEntity(name="Airbnb", type="ORGANIZATION", confidence=0.92),
        ]

    @pytest.fixture
    def sample_relations(self):
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                confidence=0.9,
            ),
        ]

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_returns_relation_count(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that extract_entities_from_session returns relations_extracted count."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock messages to process
        mock_client.execute_read = AsyncMock(
            return_value=[
                {"id": "msg-1", "content": "Brian Chesky founded Airbnb"},
            ]
        )

        result = await memory.extract_entities_from_session(
            "test-session",
            extract_relations=True,
        )

        assert "relations_extracted" in result
        assert result["relations_extracted"] >= 1
        assert result["messages_processed"] == 1
        assert result["entities_extracted"] == 2

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_without_relations(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test extract_entities_from_session with extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock messages to process
        mock_client.execute_read = AsyncMock(
            return_value=[
                {"id": "msg-1", "content": "Brian Chesky founded Airbnb"},
            ]
        )

        result = await memory.extract_entities_from_session(
            "test-session",
            extract_relations=False,
        )

        assert result["relations_extracted"] == 0
        assert result["messages_processed"] == 1
        assert result["entities_extracted"] == 2

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_no_extractor(self, mock_client):
        """Test extract_entities_from_session returns zeros when no extractor."""
        memory = ShortTermMemory(mock_client)  # No extractor

        result = await memory.extract_entities_from_session("test-session")

        assert result == {
            "messages_processed": 0,
            "entities_extracted": 0,
            "relations_extracted": 0,
        }


class TestCypherQueries:
    """Tests for the new Cypher queries."""

    def test_create_entity_relation_by_id_query_exists(self):
        """Test that CREATE_ENTITY_RELATION_BY_ID query is defined."""
        from neo4j_agent_memory.graph import queries

        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_ID")
        query = queries.CREATE_ENTITY_RELATION_BY_ID

        # Check query contains expected elements
        assert "MATCH (source:Entity {id: $source_id})" in query
        assert "MATCH (target:Entity {id: $target_id})" in query
        assert "MERGE (source)-[r:RELATED_TO]->(target)" in query
        assert "relation_type" in query
        assert "confidence" in query

    def test_create_entity_relation_by_name_query_exists(self):
        """Test that CREATE_ENTITY_RELATION_BY_NAME query is defined."""
        from neo4j_agent_memory.graph import queries

        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_NAME")
        query = queries.CREATE_ENTITY_RELATION_BY_NAME

        # Check query contains expected elements
        assert "toLower(source.name)" in query or "source.name" in query
        assert "toLower(target.name)" in query or "target.name" in query
        assert "MERGE (source)-[r:RELATED_TO]->(target)" in query
        assert "relation_type" in query
        assert "confidence" in query
