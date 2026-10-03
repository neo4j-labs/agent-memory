"""Tests for entity deduplication on ingest."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from neo4j_agent_memory.config.settings import ResolutionConfig
from neo4j_agent_memory.memory.long_term import (
    DeduplicationConfig,
    DeduplicationResult,
    DeduplicationStats,
    DuplicateCandidate,
    LongTermMemory,
)
from neo4j_agent_memory.resolution.base import ResolvedEntity
from neo4j_agent_memory.resolution.ontology import EntityResolution, OntologyResolver


class TestDeduplicationConfig:
    """Tests for DeduplicationConfig."""

    def test_default_config(self):
        """Defaults mirror ResolutionConfig -- one set of v0.7 thresholds.

        They used to be 0.95/0.85/0.9/10, so the same pair of names merged on
        ``add_entity`` and only got flagged on the ingestion path.
        """
        config = DeduplicationConfig()

        assert config.enabled is True
        assert config.auto_merge_threshold == 0.90
        assert config.flag_threshold == 0.85
        assert config.use_fuzzy_matching is True
        assert config.fuzzy_threshold == 0.85
        assert config.max_candidates == 12
        assert config.match_same_type_only is True

    def test_defaults_match_the_resolution_config(self):
        """The two shapes must not drift apart again."""
        resolution = ResolutionConfig()
        config = DeduplicationConfig()

        assert config.auto_merge_threshold == resolution.auto_merge_threshold
        assert config.flag_threshold == resolution.review_threshold
        assert config.fuzzy_threshold == resolution.fuzzy_threshold
        assert config.max_candidates == resolution.candidate_limit
        assert config == DeduplicationConfig.from_resolution_config(resolution)

    def test_custom_config(self):
        """Test custom configuration."""
        config = DeduplicationConfig(
            enabled=False,
            auto_merge_threshold=0.98,
            flag_threshold=0.80,
            use_fuzzy_matching=False,
            fuzzy_threshold=0.85,
            max_candidates=20,
            match_same_type_only=False,
        )

        assert config.enabled is False
        assert config.auto_merge_threshold == 0.98
        assert config.flag_threshold == 0.80
        assert config.use_fuzzy_matching is False
        assert config.fuzzy_threshold == 0.85
        assert config.max_candidates == 20
        assert config.match_same_type_only is False

    def test_invalid_threshold_order(self):
        """Test that auto_merge_threshold must be >= flag_threshold."""
        with pytest.raises(ValueError, match="auto_merge_threshold must be >= flag_threshold"):
            DeduplicationConfig(
                auto_merge_threshold=0.80,
                flag_threshold=0.90,
            )

    def test_invalid_flag_threshold_range(self):
        """Test flag_threshold must be between 0 and 1."""
        with pytest.raises(ValueError, match="flag_threshold must be between 0 and 1"):
            DeduplicationConfig(flag_threshold=1.5)

        with pytest.raises(ValueError, match="flag_threshold must be between 0 and 1"):
            DeduplicationConfig(flag_threshold=-0.1)

    def test_invalid_auto_merge_threshold_range(self):
        """Test auto_merge_threshold must be between 0 and 1."""
        with pytest.raises(ValueError, match="auto_merge_threshold must be between 0 and 1"):
            DeduplicationConfig(auto_merge_threshold=1.5)

    def test_invalid_fuzzy_threshold_range(self):
        """Test fuzzy_threshold must be between 0 and 1."""
        with pytest.raises(ValueError, match="fuzzy_threshold must be between 0 and 1"):
            DeduplicationConfig(fuzzy_threshold=-0.1)


class TestDeduplicationResult:
    """Tests for DeduplicationResult."""

    def test_default_result(self):
        """Test default result values."""
        result = DeduplicationResult()

        assert result.is_duplicate is False
        assert result.action == "none"
        assert result.matched_entity_id is None
        assert result.matched_entity_name is None
        assert result.similarity_score == 0.0
        assert result.match_type is None

    def test_merged_result(self):
        """Test result for merged entity."""
        entity_id = uuid4()
        result = DeduplicationResult(
            is_duplicate=True,
            action="merged",
            matched_entity_id=entity_id,
            matched_entity_name="John Smith",
            similarity_score=0.96,
            match_type="embedding",
        )

        assert result.is_duplicate is True
        assert result.action == "merged"
        assert result.matched_entity_id == entity_id
        assert result.matched_entity_name == "John Smith"
        assert result.similarity_score == 0.96
        assert result.match_type == "embedding"

    def test_flagged_result(self):
        """Test result for flagged entity."""
        entity_id = uuid4()
        result = DeduplicationResult(
            is_duplicate=True,
            action="flagged",
            matched_entity_id=entity_id,
            matched_entity_name="J. Smith",
            similarity_score=0.88,
            match_type="both",
        )

        assert result.is_duplicate is True
        assert result.action == "flagged"
        assert result.similarity_score == 0.88
        assert result.match_type == "both"


class TestDeduplicationStats:
    """Tests for DeduplicationStats."""

    def test_default_stats(self):
        """Test default stats values."""
        stats = DeduplicationStats()

        assert stats.total_entities == 0
        assert stats.merged_entities == 0
        assert stats.same_as_relationships == 0
        assert stats.pending_reviews == 0

    def test_custom_stats(self):
        """Test custom stats values."""
        stats = DeduplicationStats(
            total_entities=100,
            merged_entities=10,
            same_as_relationships=15,
            pending_reviews=5,
        )

        assert stats.total_entities == 100
        assert stats.merged_entities == 10
        assert stats.same_as_relationships == 15
        assert stats.pending_reviews == 5


class TestDuplicateCandidate:
    """Tests for DuplicateCandidate."""

    def test_duplicate_candidate(self):
        """Test DuplicateCandidate creation."""
        entity_id = uuid4()
        candidate = DuplicateCandidate(
            entity_id=entity_id,
            entity_name="John Smith",
            canonical_name="John Q. Smith",
            entity_type="PERSON",
            similarity_score=0.92,
            fuzzy_score=0.95,
            relationship_status="pending",
        )

        assert candidate.entity_id == entity_id
        assert candidate.entity_name == "John Smith"
        assert candidate.canonical_name == "John Q. Smith"
        assert candidate.entity_type == "PERSON"
        assert candidate.similarity_score == 0.92
        assert candidate.fuzzy_score == 0.95
        assert candidate.relationship_status == "pending"


class TestLongTermMemoryDeduplication:
    """Tests for LongTermMemory deduplication functionality."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_read = AsyncMock(return_value=[])
        client.execute_write = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_embedder(self):
        """Create a mock embedder."""
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 384)
        return embedder

    @pytest.fixture
    def memory(self, mock_client, mock_embedder):
        """Create a LongTermMemory instance with mocked dependencies."""
        return LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(),
        )

    @pytest.fixture
    def memory_disabled_dedup(self, mock_client, mock_embedder):
        """Create a LongTermMemory instance with deduplication disabled."""
        return LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(enabled=False),
        )

    @pytest.mark.asyncio
    async def test_add_entity_no_duplicates(self, memory, mock_client):
        """Test adding entity when no duplicates exist."""
        mock_client.execute_read.return_value = []

        entity, dedup_result = await memory.add_entity(
            name="John Smith",
            entity_type="PERSON",
        )

        assert entity.name == "John Smith"
        assert entity.type == "PERSON"
        assert dedup_result.is_duplicate is False
        assert dedup_result.action == "none"

    @pytest.mark.asyncio
    async def test_add_entity_auto_merge(self, memory, mock_client):
        """Test adding entity that triggers auto-merge."""
        existing_entity_id = str(uuid4())

        # Create a function to return different values for each call
        call_count = 0

        async def mock_execute_read(query, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: find similar entities
                return [
                    {
                        "e": {
                            "id": existing_entity_id,
                            "name": "John Smith",
                            "canonical_name": "John Smith",
                            "type": "PERSON",
                            "subtype": None,
                            "description": None,
                            "embedding": [0.1] * 384,
                            "confidence": 1.0,
                            "metadata": None,
                        },
                        "score": 0.96,  # Above auto_merge_threshold
                    }
                ]
            else:
                # Second call: get entity by ID
                return [
                    {
                        "e": {
                            "id": existing_entity_id,
                            "name": "John Smith",
                            "canonical_name": "John Smith",
                            "type": "PERSON",
                            "subtype": None,
                            "description": None,
                            "embedding": [0.1] * 384,
                            "confidence": 1.0,
                            "metadata": '{"aliases": []}',
                        }
                    }
                ]

        mock_client.execute_read = mock_execute_read

        entity, dedup_result = await memory.add_entity(
            name="Jon Smith",  # Slightly different name
            entity_type="PERSON",
        )

        assert dedup_result.is_duplicate is True
        assert dedup_result.action == "merged"
        assert dedup_result.matched_entity_id == UUID(existing_entity_id)
        # The contractual property is "above auto_merge_threshold (0.90)".
        # The exact value depends on whether fuzzy matching is enabled (the
        # default DeduplicationConfig averages vector + fuzzy scores, so the
        # combined score ≠ the raw 0.96 vector score from the mock).
        assert dedup_result.similarity_score >= 0.90
        # The returned entity should be the existing one
        assert entity.name == "John Smith"

    @pytest.mark.asyncio
    async def test_add_entity_flagged_for_review(self, memory, mock_client):
        """Test adding entity that gets flagged for review."""
        existing_entity_id = str(uuid4())

        # Mock finding a similar entity with medium confidence
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": existing_entity_id,
                    "name": "John Smith",
                    "canonical_name": "John Smith",
                    "type": "PERSON",
                    "subtype": None,
                    "description": None,
                    "embedding": [0.1] * 384,
                    "confidence": 1.0,
                    "metadata": None,
                },
                "score": 0.88,  # Between flag_threshold and auto_merge_threshold
            }
        ]

        entity, dedup_result = await memory.add_entity(
            name="J. Smith",
            entity_type="PERSON",
        )

        assert dedup_result.is_duplicate is True
        assert dedup_result.action == "flagged"
        assert dedup_result.matched_entity_id == UUID(existing_entity_id)
        assert dedup_result.similarity_score == 0.88

        # Entity should still be created
        assert entity.name == "J. Smith"

        # Verify SAME_AS relationship was created
        write_calls = mock_client.execute_write.call_args_list
        same_as_call = [c for c in write_calls if "SAME_AS" in str(c)]
        assert len(same_as_call) == 1

    @pytest.mark.asyncio
    async def test_add_entity_deduplication_disabled(self, memory_disabled_dedup, mock_client):
        """Test adding entity with deduplication disabled."""
        entity, dedup_result = await memory_disabled_dedup.add_entity(
            name="John Smith",
            entity_type="PERSON",
        )

        assert entity.name == "John Smith"
        assert dedup_result.is_duplicate is False
        assert dedup_result.action == "none"

        # Should not check for duplicates
        # Only the entity creation write should happen
        assert mock_client.execute_write.call_count == 1

    @pytest.mark.asyncio
    async def test_add_entity_deduplicate_param_false(self, memory, mock_client):
        """Test adding entity with deduplicate=False parameter."""
        entity, dedup_result = await memory.add_entity(
            name="John Smith",
            entity_type="PERSON",
            deduplicate=False,
        )

        assert entity.name == "John Smith"
        assert dedup_result.is_duplicate is False
        assert dedup_result.action == "none"

    @pytest.mark.asyncio
    async def test_add_entity_skip_merged_entities(self, memory, mock_client):
        """Test that merged entities are skipped during deduplication."""
        merged_entity_id = str(uuid4())
        active_entity_id = str(uuid4())

        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": merged_entity_id,
                    "name": "John Smith",
                    "type": "PERSON",
                    "merged_into": active_entity_id,  # This entity is merged
                    "metadata": None,
                },
                "score": 0.98,
            },
            {
                "e": {
                    "id": active_entity_id,
                    "name": "John Q. Smith",
                    "type": "PERSON",
                    "merged_into": None,  # Active entity
                    "metadata": None,
                },
                "score": 0.87,  # Lower score but not merged
            },
        ]

        entity, dedup_result = await memory.add_entity(
            name="Jon Smith",
            entity_type="PERSON",
        )

        # Should match the active entity, not the merged one
        assert dedup_result.matched_entity_id == UUID(active_entity_id)
        assert dedup_result.action == "flagged"  # 0.87 is between thresholds

    @pytest.mark.asyncio
    async def test_find_potential_duplicates(self, memory, mock_client):
        """Test finding potential duplicate pairs."""
        entity1_id = str(uuid4())
        entity2_id = str(uuid4())

        mock_client.execute_read.return_value = [
            {
                "e1": {
                    "id": entity1_id,
                    "name": "John Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "e2": {
                    "id": entity2_id,
                    "name": "Jon Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "confidence": 0.88,
                "match_type": "embedding",
                "status": "pending",
                "created_at": None,
            }
        ]

        duplicates = await memory.find_potential_duplicates(limit=10)

        assert len(duplicates) == 1
        entity1, entity2, confidence = duplicates[0]
        assert entity1.name == "John Smith"
        assert entity2.name == "Jon Smith"
        assert confidence == 0.88

    @pytest.mark.asyncio
    async def test_merge_duplicate_entities(self, memory, mock_client):
        """Test merging two duplicate entities."""
        source_id = uuid4()
        target_id = uuid4()

        mock_client.execute_write.return_value = [
            {
                "source": {
                    "id": str(source_id),
                    "name": "Jon Smith",
                    "type": "PERSON",
                    "merged_into": str(target_id),
                    "metadata": None,
                },
                "target": {
                    "id": str(target_id),
                    "name": "John Smith",
                    "type": "PERSON",
                    "metadata": '{"aliases": ["Jon Smith"]}',
                },
            }
        ]

        result = await memory.merge_duplicate_entities(source_id, target_id)

        assert result is not None
        source, target = result
        assert source.name == "Jon Smith"
        assert target.name == "John Smith"

    @pytest.mark.asyncio
    async def test_review_duplicate_confirm(self, memory, mock_client):
        """Test confirming a duplicate pair."""
        source_id = uuid4()
        target_id = uuid4()

        mock_client.execute_write.return_value = [
            {
                "source": {
                    "id": str(source_id),
                    "name": "Jon Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "target": {
                    "id": str(target_id),
                    "name": "John Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
            }
        ]

        result = await memory.review_duplicate(source_id, target_id, confirm=True)

        assert result is True
        # Should have called merge and update status
        assert mock_client.execute_write.call_count >= 2

    @pytest.mark.asyncio
    async def test_review_duplicate_reject(self, memory, mock_client):
        """Test rejecting a duplicate pair."""
        source_id = uuid4()
        target_id = uuid4()

        result = await memory.review_duplicate(source_id, target_id, confirm=False)

        assert result is True
        # Should have updated the SAME_AS status to rejected
        mock_client.execute_write.assert_called()

    @pytest.mark.asyncio
    async def test_get_same_as_cluster(self, memory, mock_client):
        """Test getting entities in a SAME_AS cluster."""
        entity_id = uuid4()
        related_id1 = str(uuid4())
        related_id2 = str(uuid4())

        mock_client.execute_read.side_effect = [
            # First call: get cluster
            [
                {
                    "entity": {
                        "id": related_id1,
                        "name": "Jon Smith",
                        "type": "PERSON",
                        "metadata": None,
                    },
                    "distance": 1,
                },
                {
                    "entity": {
                        "id": related_id2,
                        "name": "J. Smith",
                        "type": "PERSON",
                        "metadata": None,
                    },
                    "distance": 2,
                },
            ],
            # Second call: get original entity
            [
                {
                    "e": {
                        "id": str(entity_id),
                        "name": "John Smith",
                        "type": "PERSON",
                        "metadata": None,
                    }
                }
            ],
        ]

        # Need to fix the order - _get_entity_by_id is called first
        mock_client.execute_read.side_effect = [
            # get_same_as_cluster query
            [
                {
                    "entity": {
                        "id": related_id1,
                        "name": "Jon Smith",
                        "type": "PERSON",
                        "metadata": None,
                    },
                    "distance": 1,
                },
                {
                    "entity": {
                        "id": related_id2,
                        "name": "J. Smith",
                        "type": "PERSON",
                        "metadata": None,
                    },
                    "distance": 2,
                },
            ],
            # _get_entity_by_id for original
            [
                {
                    "e": {
                        "id": str(entity_id),
                        "name": "John Smith",
                        "type": "PERSON",
                        "metadata": None,
                    }
                }
            ],
        ]

        entities = await memory.get_same_as_cluster(entity_id)

        assert len(entities) == 3
        names = [e.name for e in entities]
        assert "John Smith" in names
        assert "Jon Smith" in names
        assert "J. Smith" in names

    @pytest.mark.asyncio
    async def test_get_deduplication_stats(self, memory, mock_client):
        """Test getting deduplication statistics."""
        mock_client.execute_read.return_value = [
            {
                "total_entities": 100,
                "merged_entities": 15,
                "same_as_relationships": 20,
                "pending_reviews": 5,
            }
        ]

        stats = await memory.get_deduplication_stats()

        assert stats.total_entities == 100
        assert stats.merged_entities == 15
        assert stats.same_as_relationships == 20
        assert stats.pending_reviews == 5

    @pytest.mark.asyncio
    async def test_get_deduplication_stats_empty(self, memory, mock_client):
        """Test getting stats when no data exists."""
        mock_client.execute_read.return_value = []

        stats = await memory.get_deduplication_stats()

        assert stats.total_entities == 0
        assert stats.merged_entities == 0


class TestDeduplicationWithFuzzyMatching:
    """Tests for deduplication with fuzzy string matching."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_read = AsyncMock(return_value=[])
        client.execute_write = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_embedder(self):
        """Create a mock embedder."""
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 384)
        return embedder

    @pytest.fixture
    def memory_with_fuzzy(self, mock_client, mock_embedder):
        """Create memory with fuzzy matching enabled."""
        return LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(
                use_fuzzy_matching=True,
                fuzzy_threshold=0.85,
            ),
        )

    @pytest.fixture
    def memory_without_fuzzy(self, mock_client, mock_embedder):
        """Create memory with fuzzy matching disabled."""
        return LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(
                use_fuzzy_matching=False,
            ),
        )

    @pytest.mark.asyncio
    async def test_fuzzy_matching_boosts_score(self, memory_with_fuzzy, mock_client):
        """Test that fuzzy matching can boost the combined score."""
        existing_entity_id = str(uuid4())

        # Entity with medium embedding similarity but high fuzzy similarity
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": existing_entity_id,
                    "name": "John Smith",
                    "canonical_name": "John Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "score": 0.88,  # Medium embedding score
            }
        ]

        # "Jon Smith" has high fuzzy similarity to "John Smith"
        entity, dedup_result = await memory_with_fuzzy.add_entity(
            name="Jon Smith",  # Very similar string
            entity_type="PERSON",
        )

        # With fuzzy matching, the combined score should be boosted
        assert dedup_result.is_duplicate is True
        # The match type should indicate both methods matched
        assert dedup_result.match_type in ["embedding", "both"]

    @pytest.mark.asyncio
    async def test_no_fuzzy_matching_when_disabled(self, memory_without_fuzzy, mock_client):
        """Test that fuzzy matching is not used when disabled."""
        existing_entity_id = str(uuid4())

        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": existing_entity_id,
                    "name": "John Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "score": 0.88,
            }
        ]

        entity, dedup_result = await memory_without_fuzzy.add_entity(
            name="Jon Smith",
            entity_type="PERSON",
        )

        # Should only use embedding matching
        assert dedup_result.match_type == "embedding"


class TestDeduplicationConfigurationOptions:
    """Tests for various deduplication configuration options."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_read = AsyncMock(return_value=[])
        client.execute_write = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_embedder(self):
        """Create a mock embedder."""
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 384)
        return embedder

    @pytest.mark.asyncio
    async def test_match_same_type_only_true(self, mock_client, mock_embedder):
        """Test matching only same entity types."""
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(match_same_type_only=True),
        )

        await memory.add_entity(name="Apple", entity_type="ORGANIZATION")

        # Check the query was called with type parameter
        read_calls = mock_client.execute_read.call_args_list
        if read_calls:
            last_call = read_calls[-1]
            params = (
                last_call[0][1] if len(last_call[0]) > 1 else last_call[1].get("parameters", {})
            )
            # Type should be passed when match_same_type_only is True
            assert "type" in params

    @pytest.mark.asyncio
    async def test_custom_thresholds(self, mock_client, mock_embedder):
        """Test custom threshold values."""
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(
                auto_merge_threshold=0.99,
                flag_threshold=0.90,
            ),
        )

        existing_entity_id = str(uuid4())
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": existing_entity_id,
                    "name": "Test Entity",
                    "type": "OBJECT",
                    "metadata": None,
                },
                "score": 0.95,  # Above default but below custom auto_merge
            }
        ]

        entity, dedup_result = await memory.add_entity(
            name="Test Entity 2",
            entity_type="OBJECT",
        )

        # With custom thresholds, 0.95 should only flag, not merge
        assert dedup_result.action == "flagged"

    @pytest.mark.asyncio
    async def test_max_candidates_limit(self, mock_client, mock_embedder):
        """Test that max_candidates limits the search."""
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(max_candidates=5),
        )

        await memory.add_entity(name="Test", entity_type="OBJECT")

        # Check the limit parameter
        read_calls = mock_client.execute_read.call_args_list
        if read_calls:
            last_call = read_calls[-1]
            params = last_call[0][1] if len(last_call[0]) > 1 else {}
            assert params.get("limit") == 5


class TestDeduplicationConfigFromResolutionConfig:
    """``DeduplicationConfig`` mirrors the one set of v0.7 thresholds."""

    def test_maps_the_resolution_bands(self):
        config = DeduplicationConfig.from_resolution_config(
            ResolutionConfig(
                auto_merge_threshold=0.93,
                review_threshold=0.81,
                candidate_limit=7,
                fuzzy_threshold=0.77,
            )
        )

        assert config.enabled is True
        assert config.auto_merge_threshold == 0.93
        assert config.flag_threshold == 0.81
        assert config.max_candidates == 7
        assert config.fuzzy_threshold == 0.77

    def test_defaults_round_trip(self):
        config = DeduplicationConfig.from_resolution_config(ResolutionConfig())

        assert config.auto_merge_threshold == 0.90
        assert config.flag_threshold == 0.85


class TestCheckForDuplicatesAdapter:
    """``_check_for_duplicates`` delegates to the ontology resolver.

    One dedup mechanism: ``add_entity`` and the message-ingestion path must
    band identically, so the resolver decides and this method only translates
    the decision into the older ``DeduplicationResult`` shape.
    """

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.execute_read = AsyncMock(return_value=[])
        client.execute_write = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_embedder(self):
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 384)
        return embedder

    def _resolver(self, client, resolution):
        resolver = OntologyResolver(client, config=ResolutionConfig())
        resolver.resolve_one = AsyncMock(return_value=resolution)  # type: ignore[method-assign]
        return resolver

    @pytest.mark.asyncio
    async def test_merged_resolution_becomes_a_merge(self, mock_client, mock_embedder):
        matched_id = str(uuid4())
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            resolver=self._resolver(
                mock_client,
                EntityResolution(
                    action="merged",
                    entity_id=matched_id,
                    canonical_name="John Smith",
                    score=0.97,
                    match_type="exact",
                    matched_entity_id=matched_id,
                    matched_entity_name="John Smith",
                ),
            ),
        )

        result = await memory._check_for_duplicates("Jon Smith", "PERSON", [0.1] * 384)

        assert result.is_duplicate is True
        assert result.action == "merged"
        assert result.matched_entity_id == UUID(matched_id)
        assert result.matched_entity_name == "John Smith"
        assert result.similarity_score == 0.97
        assert result.match_type == "exact"
        # The resolver, not the old embedding path, produced the decision.
        assert mock_client.execute_read.await_count == 0

    @pytest.mark.asyncio
    async def test_review_resolution_becomes_a_flag(self, mock_client, mock_embedder):
        matched_id = str(uuid4())
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            resolver=self._resolver(
                mock_client,
                EntityResolution(
                    action="review",
                    canonical_name="J. Smith",
                    score=0.87,
                    match_type="prefix",
                    matched_entity_id=matched_id,
                    matched_entity_name="John Smith",
                ),
            ),
        )

        result = await memory._check_for_duplicates("J. Smith", "PERSON", [0.1] * 384)

        assert result.action == "flagged"
        assert result.is_duplicate is True
        assert result.matched_entity_id == UUID(matched_id)

    @pytest.mark.asyncio
    async def test_created_resolution_is_not_a_duplicate(self, mock_client, mock_embedder):
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            resolver=self._resolver(
                mock_client,
                EntityResolution(action="created", canonical_name="Grace Hopper"),
            ),
        )

        result = await memory._check_for_duplicates("Grace Hopper", "PERSON", [0.1] * 384)

        assert result.is_duplicate is False
        assert result.action == "none"

    @pytest.mark.asyncio
    async def test_add_entity_auto_merges_through_the_adapter(self, mock_client, mock_embedder):
        matched_id = str(uuid4())
        mock_client.execute_read = AsyncMock(
            return_value=[
                {
                    "e": {
                        "id": matched_id,
                        "name": "John Smith",
                        "canonical_name": "John Smith",
                        "type": "PERSON",
                        "subtype": None,
                        "description": None,
                        "embedding": [0.1] * 384,
                        "confidence": 1.0,
                        "metadata": None,
                    }
                }
            ]
        )
        resolver = self._resolver(
            mock_client,
            EntityResolution(
                action="merged",
                entity_id=matched_id,
                canonical_name="John Smith",
                score=0.99,
                match_type="exact",
                matched_entity_id=matched_id,
                matched_entity_name="John Smith",
            ),
        )
        # ``add_entity`` also calls ``resolve`` for canonicalisation.
        resolver.resolve = AsyncMock(  # type: ignore[method-assign]
            return_value=ResolvedEntity(
                original_name="Jon Smith",
                canonical_name="John Smith",
                entity_type="PERSON",
                confidence=0.99,
                match_type="exact",
            )
        )
        memory = LongTermMemory(client=mock_client, embedder=mock_embedder, resolver=resolver)

        entity, dedup_result = await memory.add_entity(name="Jon Smith", entity_type="PERSON")

        assert dedup_result.action == "merged"
        assert entity.name == "John Smith"
        alias_writes = [
            call for call in mock_client.execute_write.call_args_list if "e.aliases" in str(call[0])
        ]
        assert alias_writes, "the merged-away surface form becomes an alias"

    @pytest.mark.asyncio
    async def test_a_non_ontology_resolver_keeps_the_embedding_path(
        self, mock_client, mock_embedder
    ):
        from neo4j_agent_memory.resolution.fuzzy import FuzzyMatchResolver

        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            resolver=FuzzyMatchResolver(),
        )

        result = await memory._check_for_duplicates("Jon Smith", "PERSON", [0.1] * 384)

        assert result.action == "none"
        # The original embedding-similarity query ran.
        assert mock_client.execute_read.await_count == 1
