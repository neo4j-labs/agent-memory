"""Unit tests for provenance tracking."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.memory.long_term import Entity, LongTermMemory


@pytest.fixture
def mock_client():
    """Create a mock Neo4jClient."""
    client = MagicMock()
    client.execute_read = AsyncMock()
    client.execute_write = AsyncMock()
    return client


@pytest.fixture
def mock_embedder():
    """Create a mock embedder."""
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 384)
    return embedder


@pytest.fixture
def long_term_memory(mock_client, mock_embedder):
    """Create a LongTermMemory instance with mocks."""
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
    )


@pytest.fixture
def sample_entity():
    """Create a sample entity for testing."""
    return Entity(
        id=uuid4(),
        name="John Smith",
        type="PERSON",
        subtype="INDIVIDUAL",
        description="A test person",
        confidence=0.95,
    )


class TestRegisterExtractor:
    """Tests for register_extractor method."""

    @pytest.mark.asyncio
    async def test_register_new_extractor(self, long_term_memory, mock_client):
        """Test registering a new extractor."""
        mock_client.execute_write.return_value = [
            {
                "ex": {
                    "id": str(uuid4()),
                    "name": "GLiNEREntityExtractor",
                    "version": "1.0.0",
                }
            }
        ]

        result = await long_term_memory.register_extractor(
            "GLiNEREntityExtractor",
            version="1.0.0",
            config={"threshold": 0.5},
        )

        assert result["name"] == "GLiNEREntityExtractor"
        assert result["version"] == "1.0.0"
        mock_client.execute_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_extractor_minimal(self, long_term_memory, mock_client):
        """Test registering extractor with minimal info."""
        mock_client.execute_write.return_value = [{}]

        result = await long_term_memory.register_extractor("SpacyNER")

        assert result["name"] == "SpacyNER"
        assert result["version"] is None


class TestLinkEntityToMessage:
    """Tests for link_entity_to_message method."""

    @pytest.mark.asyncio
    async def test_link_entity_to_message(self, long_term_memory, mock_client, sample_entity):
        """Test linking entity to source message."""
        mock_client.execute_write.return_value = [{"r": {"confidence": 0.9}}]
        message_id = uuid4()

        result = await long_term_memory.link_entity_to_message(
            sample_entity,
            message_id,
            confidence=0.9,
            start_pos=10,
            end_pos=20,
            context="... John Smith works at ...",
        )

        assert result is True
        mock_client.execute_write.assert_called_once()
        call_args = mock_client.execute_write.call_args[0][1]
        assert call_args["entity_id"] == str(sample_entity.id)
        assert call_args["message_id"] == str(message_id)
        assert call_args["confidence"] == 0.9
        assert call_args["start_pos"] == 10
        assert call_args["end_pos"] == 20

    @pytest.mark.asyncio
    async def test_link_entity_by_uuid(self, long_term_memory, mock_client):
        """Test linking entity using UUID directly."""
        mock_client.execute_write.return_value = [{"r": {}}]
        entity_id = uuid4()
        message_id = uuid4()

        result = await long_term_memory.link_entity_to_message(
            entity_id,
            message_id,
        )

        assert result is True
        call_args = mock_client.execute_write.call_args[0][1]
        assert call_args["entity_id"] == str(entity_id)


class TestLinkEntityToExtractor:
    """Tests for link_entity_to_extractor method."""

    @pytest.mark.asyncio
    async def test_link_entity_to_extractor(self, long_term_memory, mock_client, sample_entity):
        """Test linking entity to extractor."""
        # First call registers extractor, second creates link
        mock_client.execute_write.side_effect = [
            [{"ex": {"name": "GLiNER"}}],  # register_extractor
            [{"r": {}}],  # create link
        ]

        result = await long_term_memory.link_entity_to_extractor(
            sample_entity,
            "GLiNER",
            confidence=0.85,
            extraction_time_ms=150.5,
        )

        assert result is True
        assert mock_client.execute_write.call_count == 2

    @pytest.mark.asyncio
    async def test_link_entity_by_uuid_to_extractor(self, long_term_memory, mock_client):
        """Test linking entity using UUID."""
        mock_client.execute_write.side_effect = [
            [{"ex": {}}],  # register
            [{"r": {}}],  # link
        ]
        entity_id = uuid4()

        result = await long_term_memory.link_entity_to_extractor(
            entity_id,
            "SpacyNER",
        )

        assert result is True


class TestGetEntityProvenance:
    """Tests for get_entity_provenance method."""

    @pytest.mark.asyncio
    async def test_get_provenance_with_sources(self, long_term_memory, mock_client, sample_entity):
        """Test getting provenance with message sources.

        The mocked row mirrors what ``GET_ENTITY_PROVENANCE`` actually
        returns: each source is a flat map of scalar columns
        (``message_id``, ``confidence``, ``start_pos``, ...), not a nested
        ``{"message": ..., "relationship": ...}`` pair -- a relationship
        value, bare or nested inside a map/list, never survives
        ``execute_read``'s ``Result.data()`` round trip.
        """
        mock_client.execute_read.return_value = [
            {
                "e": {"id": str(sample_entity.id), "name": "John"},
                "sources": [
                    {
                        "message_id": str(uuid4()),
                        "content": "John works at Acme",
                        "confidence": 0.9,
                        "start_pos": 0,
                        "end_pos": 4,
                        "context": None,
                        "created_at": None,
                    }
                ],
                "extractors": [],
            }
        ]

        result = await long_term_memory.get_entity_provenance(sample_entity)

        assert len(result["sources"]) == 1
        assert result["sources"][0]["confidence"] == 0.9
        assert result["sources"][0]["start_pos"] == 0
        assert result["sources"][0]["content"] == "John works at Acme"
        assert len(result["extractors"]) == 0

    @pytest.mark.asyncio
    async def test_get_provenance_with_extractors(
        self, long_term_memory, mock_client, sample_entity
    ):
        """Test getting provenance with extractor info.

        As above: the mocked row is the flat scalar-column shape
        ``GET_ENTITY_PROVENANCE`` actually returns, not a nested
        ``{"extractor": ..., "relationship": ...}`` pair.
        """
        mock_client.execute_read.return_value = [
            {
                "e": {"id": str(sample_entity.id)},
                "sources": [],
                "extractors": [
                    {
                        "name": "GLiNER",
                        "version": "1.0",
                        "confidence": 0.85,
                        "extraction_time_ms": 100,
                        "created_at": None,
                    }
                ],
            }
        ]

        result = await long_term_memory.get_entity_provenance(sample_entity)

        assert len(result["extractors"]) == 1
        assert result["extractors"][0]["name"] == "GLiNER"
        assert result["extractors"][0]["version"] == "1.0"
        assert result["extractors"][0]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_get_provenance_no_results(self, long_term_memory, mock_client, sample_entity):
        """Test getting provenance when none exists."""
        mock_client.execute_read.return_value = []

        result = await long_term_memory.get_entity_provenance(sample_entity)

        assert result == {"sources": [], "extractors": []}

    @pytest.mark.asyncio
    async def test_get_provenance_by_uuid(self, long_term_memory, mock_client):
        """Test getting provenance using UUID directly."""
        entity_id = uuid4()
        mock_client.execute_read.return_value = [
            {
                "e": {"id": str(entity_id)},
                "sources": [],
                "extractors": [],
            }
        ]

        result = await long_term_memory.get_entity_provenance(entity_id)

        assert result == {"sources": [], "extractors": []}


class TestGetEntitiesFromMessage:
    """Tests for get_entities_from_message method."""

    @pytest.mark.asyncio
    async def test_get_entities_from_message(self, long_term_memory, mock_client):
        """Test getting entities extracted from a message.

        The mocked rows mirror what ``GET_ENTITIES_FROM_MESSAGE`` actually
        returns: ``confidence``/``start_pos``/``end_pos``/``context`` are
        top-level scalar columns, not nested under an ``"r"`` relationship
        value -- which never survives ``execute_read``'s ``Result.data()``
        round trip.
        """
        message_id = uuid4()
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": str(uuid4()),
                    "name": "John",
                    "type": "PERSON",
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "start_pos": 0,
                "end_pos": 4,
                "context": None,
                "created_at": None,
            },
            {
                "e": {
                    "id": str(uuid4()),
                    "name": "Acme",
                    "type": "ORGANIZATION",
                    "confidence": 0.85,
                },
                "confidence": 0.85,
                "start_pos": 15,
                "end_pos": 19,
                "context": None,
                "created_at": None,
            },
        ]

        result = await long_term_memory.get_entities_from_message(message_id)

        assert len(result) == 2
        assert result[0][0].name == "John"
        assert result[0][1]["start_pos"] == 0
        assert result[1][0].name == "Acme"
        assert result[1][1]["start_pos"] == 15

    @pytest.mark.asyncio
    async def test_get_entities_from_message_empty(self, long_term_memory, mock_client):
        """Test getting entities when none exist."""
        mock_client.execute_read.return_value = []

        result = await long_term_memory.get_entities_from_message(uuid4())

        assert result == []


class TestGetEntitiesByExtractor:
    """Tests for get_entities_by_extractor method."""

    @pytest.mark.asyncio
    async def test_get_entities_by_extractor(self, long_term_memory, mock_client):
        """Test getting entities by extractor name.

        The mocked row mirrors what ``GET_ENTITIES_BY_EXTRACTOR`` actually
        returns: ``confidence``/``extraction_time_ms`` are top-level scalar
        columns, not nested under an ``"r"`` relationship value.
        """
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": str(uuid4()),
                    "name": "John",
                    "type": "PERSON",
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "extraction_time_ms": 50.0,
                "created_at": None,
            },
        ]

        result = await long_term_memory.get_entities_by_extractor("GLiNER", limit=50)

        assert len(result) == 1
        assert result[0][0].name == "John"
        assert result[0][1]["extraction_time_ms"] == 50.0

    @pytest.mark.asyncio
    async def test_get_entities_by_extractor_with_limit(self, long_term_memory, mock_client):
        """Test limit parameter is passed correctly."""
        mock_client.execute_read.return_value = []

        await long_term_memory.get_entities_by_extractor("SpacyNER", limit=25)

        call_args = mock_client.execute_read.call_args[0][1]
        assert call_args["limit"] == 25


class TestListExtractors:
    """Tests for list_extractors method."""

    @pytest.mark.asyncio
    async def test_list_extractors(self, long_term_memory, mock_client):
        """Test listing all extractors."""
        mock_client.execute_read.return_value = [
            {"ex": {"name": "GLiNER", "version": "1.0"}, "entity_count": 100},
            {"ex": {"name": "SpacyNER", "version": "3.7"}, "entity_count": 50},
        ]

        result = await long_term_memory.list_extractors()

        assert len(result) == 2
        assert result[0]["name"] == "GLiNER"
        assert result[0]["entity_count"] == 100
        assert result[1]["name"] == "SpacyNER"

    @pytest.mark.asyncio
    async def test_list_extractors_empty(self, long_term_memory, mock_client):
        """Test listing when no extractors exist."""
        mock_client.execute_read.return_value = []

        result = await long_term_memory.list_extractors()

        assert result == []


class TestGetExtractionStats:
    """Tests for get_extraction_stats method."""

    @pytest.mark.asyncio
    async def test_get_extraction_stats(self, long_term_memory, mock_client):
        """Test getting overall extraction stats."""
        mock_client.execute_read.return_value = [
            {
                "total_entities": 500,
                "source_messages": 100,
                "extractors": ["GLiNER", "SpacyNER"],
            }
        ]

        result = await long_term_memory.get_extraction_stats()

        assert result["total_entities"] == 500
        assert result["source_messages"] == 100
        assert "GLiNER" in result["extractors"]

    @pytest.mark.asyncio
    async def test_get_extraction_stats_empty(self, long_term_memory, mock_client):
        """Test stats when no data exists."""
        mock_client.execute_read.return_value = []

        result = await long_term_memory.get_extraction_stats()

        assert result["total_entities"] == 0
        assert result["source_messages"] == 0
        assert result["extractors"] == []


class TestGetExtractorStats:
    """Tests for get_extractor_stats method."""

    @pytest.mark.asyncio
    async def test_get_extractor_stats(self, long_term_memory, mock_client):
        """Test getting per-extractor stats."""
        mock_client.execute_read.return_value = [
            {
                "name": "GLiNER",
                "version": "1.0",
                "entity_count": 100,
                "avg_confidence": 0.85,
            },
            {
                "name": "SpacyNER",
                "version": "3.7",
                "entity_count": 50,
                "avg_confidence": 0.75,
            },
        ]

        result = await long_term_memory.get_extractor_stats()

        assert len(result) == 2
        assert result[0]["name"] == "GLiNER"
        assert result[0]["entity_count"] == 100
        assert result[0]["avg_confidence"] == 0.85


class TestDeleteEntityProvenance:
    """Tests for delete_entity_provenance method."""

    @pytest.mark.asyncio
    async def test_delete_provenance(self, long_term_memory, mock_client, sample_entity):
        """Test deleting provenance for an entity."""
        mock_client.execute_write.return_value = [{"deleted": 3}]

        result = await long_term_memory.delete_entity_provenance(sample_entity)

        assert result == 3

    @pytest.mark.asyncio
    async def test_delete_provenance_none(self, long_term_memory, mock_client, sample_entity):
        """Test deleting when no provenance exists."""
        mock_client.execute_write.return_value = [{"deleted": 0}]

        result = await long_term_memory.delete_entity_provenance(sample_entity)

        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_provenance_by_uuid(self, long_term_memory, mock_client):
        """Test deleting provenance using UUID."""
        entity_id = uuid4()
        mock_client.execute_write.return_value = [{"deleted": 2}]

        result = await long_term_memory.delete_entity_provenance(entity_id)

        assert result == 2
        call_args = mock_client.execute_write.call_args[0][1]
        assert call_args["entity_id"] == str(entity_id)


class TestProvenanceEdgeCases:
    """Edge case tests for provenance tracking."""

    @pytest.mark.asyncio
    async def test_provenance_source_with_missing_optional_fields(
        self, long_term_memory, mock_client, sample_entity
    ):
        """Test provenance parsing tolerates a source row missing optional fields."""
        mock_client.execute_read.return_value = [
            {
                "e": {"id": str(sample_entity.id)},
                "sources": [
                    {
                        "message_id": str(uuid4()),
                        "content": "Test",
                        "confidence": 0.8,
                        # start_pos/end_pos/context/created_at omitted
                    }
                ],
                "extractors": [],
            }
        ]

        result = await long_term_memory.get_entity_provenance(sample_entity)

        assert len(result["sources"]) == 1
        assert result["sources"][0]["confidence"] == 0.8
        assert result["sources"][0]["start_pos"] is None

    @pytest.mark.asyncio
    async def test_empty_result_handling(self, long_term_memory, mock_client):
        """Test handling of empty results."""
        mock_client.execute_write.return_value = []

        result = await long_term_memory.delete_entity_provenance(uuid4())

        assert result == 0

    @pytest.mark.asyncio
    async def test_link_with_string_message_id(self, long_term_memory, mock_client, sample_entity):
        """Test linking with string message ID."""
        mock_client.execute_write.return_value = [{"r": {}}]
        message_id = str(uuid4())

        result = await long_term_memory.link_entity_to_message(
            sample_entity,
            message_id,  # String instead of UUID
        )

        assert result is True
