"""Tests for the entity extraction-to-storage pipeline improvements."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.extraction.pipeline import (
    _entity_key,
    _merge_entities_confidence,
    DEFAULT_SOURCE_WEIGHTS,
    CROSS_EXTRACTOR_BOOST,
)


class TestExtractionResultDeduplicate:
    """Tests for ExtractionResult.deduplicate_entities()."""

    def test_deduplicate_removes_exact_duplicates(self):
        """Duplicate entities with same name+type keep highest confidence."""
        entities = [
            ExtractedEntity(name="John Smith", type="PERSON", confidence=0.8),
            ExtractedEntity(name="John Smith", type="PERSON", confidence=0.95),
            ExtractedEntity(name="Acme Corp", type="ORGANIZATION", confidence=0.9),
        ]
        result = ExtractionResult(entities=entities)
        deduped = result.deduplicate_entities()

        assert len(deduped.entities) == 2
        john = next(e for e in deduped.entities if e.name == "John Smith")
        assert john.confidence == 0.95

    def test_deduplicate_keeps_different_types(self):
        """Same name with different types are kept separate."""
        entities = [
            ExtractedEntity(name="Apple", type="ORGANIZATION", confidence=0.9),
            ExtractedEntity(name="Apple", type="OBJECT", confidence=0.8),
        ]
        result = ExtractionResult(entities=entities)
        deduped = result.deduplicate_entities()

        assert len(deduped.entities) == 2

    def test_deduplicate_filters_orphaned_relations(self):
        """Relations referencing removed entities are filtered."""
        entities = [
            ExtractedEntity(name="John", type="PERSON", confidence=0.9),
            ExtractedEntity(name="John", type="PERSON", confidence=0.7),
            ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.8),
        ]
        relations = [
            ExtractedRelation(
                source="John", target="Acme", relation_type="WORKS_AT", confidence=0.8
            ),
            ExtractedRelation(
                source="John", target="Unknown", relation_type="KNOWS", confidence=0.5
            ),
        ]
        result = ExtractionResult(entities=entities, relations=relations)
        deduped = result.deduplicate_entities()

        # "Unknown" entity doesn't exist, so that relation should be filtered
        assert len(deduped.relations) == 1
        assert deduped.relations[0].target == "Acme"

    def test_deduplicate_preserves_preferences(self):
        """Preferences are preserved during deduplication."""
        from neo4j_agent_memory.extraction.base import ExtractedPreference

        entities = [
            ExtractedEntity(name="John", type="PERSON", confidence=0.9),
            ExtractedEntity(name="John", type="PERSON", confidence=0.7),
        ]
        prefs = [ExtractedPreference(category="food", preference="likes pizza")]
        result = ExtractionResult(entities=entities, preferences=prefs)
        deduped = result.deduplicate_entities()

        assert len(deduped.preferences) == 1

    def test_deduplicate_empty_result(self):
        """Empty result deduplicates to empty result."""
        result = ExtractionResult()
        deduped = result.deduplicate_entities()
        assert len(deduped.entities) == 0


class TestEntityKeyWithSubtype:
    """Tests for _entity_key including subtype."""

    def test_key_without_subtype(self):
        """Key without subtype matches old behavior."""
        entity = ExtractedEntity(name="John Smith", type="PERSON", confidence=0.9)
        assert _entity_key(entity) == "john smith::PERSON"

    def test_key_with_subtype(self):
        """Key includes subtype when present."""
        entity = ExtractedEntity(
            name="John Smith", type="PERSON", subtype="individual", confidence=0.9
        )
        assert _entity_key(entity) == "john smith::PERSON::INDIVIDUAL"

    def test_different_subtypes_different_keys(self):
        """Same name+type but different subtypes produce different keys."""
        e1 = ExtractedEntity(
            name="John", type="PERSON", subtype="individual", confidence=0.9
        )
        e2 = ExtractedEntity(
            name="John", type="PERSON", subtype="alias", confidence=0.8
        )
        assert _entity_key(e1) != _entity_key(e2)


class TestWeightedConfidenceMerge:
    """Tests for source-weighted confidence merge."""

    def test_spacy_lower_weight_than_gliner(self):
        """spaCy's fixed confidence is weighted lower than GLiNER."""
        spacy_entities = [
            ExtractedEntity(
                name="John Smith", type="PERSON", confidence=0.85, extractor="spacy"
            ),
        ]
        gliner_entities = [
            ExtractedEntity(
                name="John Smith", type="PERSON", confidence=0.7, extractor="gliner"
            ),
        ]
        result = _merge_entities_confidence([spacy_entities, gliner_entities])

        assert len(result) == 1
        # GLiNER should win: 0.7 * 0.9 = 0.63, spaCy: 0.85 * 0.7 = 0.595
        assert result[0].extractor == "gliner"

    def test_cross_extractor_boost(self):
        """Entities found by multiple extractors get confidence boost."""
        entities1 = [
            ExtractedEntity(
                name="Acme Corp", type="ORGANIZATION", confidence=0.8, extractor="spacy"
            ),
        ]
        entities2 = [
            ExtractedEntity(
                name="Acme Corp", type="ORGANIZATION", confidence=0.75, extractor="gliner"
            ),
        ]
        result = _merge_entities_confidence([entities1, entities2])

        assert len(result) == 1
        # Should get a boost since found by 2 extractors
        acme = result[0]
        assert acme.confidence > 0.75  # Original confidence + boost

    def test_single_extractor_no_boost(self):
        """Single extractor entities don't get boosted."""
        entities = [
            ExtractedEntity(
                name="John", type="PERSON", confidence=0.9, extractor="gliner"
            ),
        ]
        result = _merge_entities_confidence([entities])

        assert len(result) == 1
        assert result[0].confidence == 0.9  # No boost

    def test_unknown_extractor_uses_default_weight(self):
        """Unknown extractor sources use default weight of 0.8."""
        entities = [
            ExtractedEntity(
                name="John", type="PERSON", confidence=0.9, extractor="custom_extractor"
            ),
        ]
        result = _merge_entities_confidence([entities])

        assert len(result) == 1


class TestIngestExtractedEntity:
    """Tests for LongTermMemory.ingest_extracted_entity()."""

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.execute_read = AsyncMock(return_value=[])
        client.execute_write = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_embedder(self):
        embedder = AsyncMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 768)
        embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768, [0.2] * 768])
        return embedder

    @pytest.fixture
    def long_term_memory(self, mock_client, mock_embedder):
        from neo4j_agent_memory.memory.long_term import LongTermMemory

        return LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
        )

    @pytest.mark.asyncio
    async def test_ingest_creates_new_entity(self, long_term_memory, mock_client):
        """New entity is created with embedding."""
        entity = ExtractedEntity(name="John Smith", type="PERSON", confidence=0.9)
        entity_id, is_new = await long_term_memory.ingest_extracted_entity(entity)

        assert is_new is True
        assert entity_id is not None
        # Should have called execute_write to create entity
        mock_client.execute_write.assert_called()

    @pytest.mark.asyncio
    async def test_ingest_generates_embedding(self, long_term_memory, mock_embedder):
        """Embedding is generated for the entity."""
        entity = ExtractedEntity(name="John Smith", type="PERSON", confidence=0.9)
        await long_term_memory.ingest_extracted_entity(entity)

        mock_embedder.embed.assert_called_once_with("John Smith")

    @pytest.mark.asyncio
    async def test_ingest_skips_embedding_when_disabled(
        self, long_term_memory, mock_embedder
    ):
        """No embedding when generate_embedding=False."""
        entity = ExtractedEntity(name="John Smith", type="PERSON", confidence=0.9)
        await long_term_memory.ingest_extracted_entity(
            entity, generate_embedding=False
        )

        mock_embedder.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_ingest_batch_uses_embed_batch(
        self, long_term_memory, mock_embedder
    ):
        """Batch ingest uses embed_batch for efficiency."""
        entities = [
            ExtractedEntity(name="John", type="PERSON", confidence=0.9),
            ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.8),
        ]
        results = await long_term_memory.ingest_extracted_entities_batch(entities)

        assert len(results) == 2
        mock_embedder.embed_batch.assert_called_once_with(["John", "Acme"])

    @pytest.mark.asyncio
    async def test_ingest_merges_duplicate(self, long_term_memory, mock_client):
        """Duplicate entity returns existing ID."""
        from neo4j_agent_memory.memory.long_term import DeduplicationConfig

        long_term_memory._deduplication = DeduplicationConfig(enabled=True)

        # Mock vector search to return a similar entity
        existing_id = "12345678-1234-5678-1234-567812345678"
        mock_client.execute_read = AsyncMock(
            return_value=[
                {
                    "e": {
                        "id": existing_id,
                        "name": "John Smith",
                        "type": "PERSON",
                        "canonical_name": "John Smith",
                        "embedding": [0.1] * 768,
                    },
                    "score": 0.96,  # Above auto_merge_threshold
                }
            ]
        )

        entity = ExtractedEntity(name="Jon Smith", type="PERSON", confidence=0.9)
        entity_id, is_new = await long_term_memory.ingest_extracted_entity(entity)

        assert is_new is False
        assert entity_id == existing_id


class TestShortTermLongTermWiring:
    """Tests that ShortTermMemory routes through LongTermMemory."""

    def test_short_term_accepts_long_term(self):
        """ShortTermMemory accepts long_term parameter."""
        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        mock_client = MagicMock()
        mock_long_term = MagicMock()
        st = ShortTermMemory(client=mock_client, long_term=mock_long_term)
        assert st._long_term is mock_long_term

    def test_short_term_long_term_optional(self):
        """ShortTermMemory works without long_term (backward compat)."""
        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        mock_client = MagicMock()
        st = ShortTermMemory(client=mock_client)
        assert st._long_term is None
