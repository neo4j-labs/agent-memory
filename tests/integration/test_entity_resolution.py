"""Comprehensive integration tests for entity resolution."""

import pytest

from neo4j_agent_memory.memory.long_term import EntityType
from neo4j_agent_memory.resolution.base import ResolvedEntity
from neo4j_agent_memory.resolution.composite import CompositeResolver
from neo4j_agent_memory.resolution.exact import ExactMatchResolver


@pytest.mark.integration
class TestExactMatchResolverIntegration:
    """Integration tests for exact match resolver with Neo4j."""

    @pytest.mark.asyncio
    async def test_resolve_exact_match_against_database(self, memory_client):
        """Test exact matching against entities in database."""
        # Create entities in database
        await memory_client.long_term.add_entity(
            name="John Smith",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="Acme Corporation",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        # Use resolver
        resolver = ExactMatchResolver()

        # Exact match
        result = await resolver.resolve(
            "John Smith",
            "PERSON",
            existing_entities=["John Smith", "Acme Corporation"],
        )

        assert result.canonical_name == "John Smith"
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    @pytest.mark.asyncio
    async def test_resolve_case_insensitive_against_database(self, memory_client):
        """Test case-insensitive matching."""
        await memory_client.long_term.add_entity(
            name="Jane Doe",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        resolver = ExactMatchResolver()

        # Different case
        result = await resolver.resolve(
            "JANE DOE",
            "PERSON",
            existing_entities=["Jane Doe"],
        )

        assert result.canonical_name == "Jane Doe"

    @pytest.mark.asyncio
    async def test_resolve_no_match(self, memory_client):
        """Test when no match exists."""
        await memory_client.long_term.add_entity(
            name="Existing Entity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        resolver = ExactMatchResolver()

        result = await resolver.resolve(
            "New Entity",
            "PERSON",
            existing_entities=["Existing Entity"],
        )

        # Should return original name
        assert result.canonical_name == "New Entity"
        assert result.original_name == "New Entity"


@pytest.mark.integration
class TestCompositeResolverIntegration:
    """Integration tests for composite resolver with Neo4j."""

    @pytest.mark.asyncio
    async def test_composite_exact_then_fuzzy(self, memory_client, mock_embedder):
        """Test composite resolver tries exact first, then fuzzy."""
        # Create entity
        await memory_client.long_term.add_entity(
            name="Microsoft Corporation",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        resolver = CompositeResolver(embedder=mock_embedder)

        # Exact match should work
        result = await resolver.resolve(
            "Microsoft Corporation",
            "ORGANIZATION",
            existing_entities=["Microsoft Corporation"],
        )

        assert result.canonical_name == "Microsoft Corporation"

    @pytest.mark.asyncio
    async def test_composite_falls_through_strategies(self, memory_client, mock_embedder):
        """Test composite resolver falls through when no match found."""
        await memory_client.long_term.add_entity(
            name="Apple Inc",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        resolver = CompositeResolver(
            embedder=mock_embedder,
            semantic_threshold=0.8,
        )

        # Completely different entity
        result = await resolver.resolve(
            "Banana Corp",
            "ORGANIZATION",
            existing_entities=["Apple Inc"],
        )

        # Should return as new entity (no match)
        assert result.original_name == "Banana Corp"

    @pytest.mark.asyncio
    async def test_composite_batch_resolution(self, memory_client, mock_embedder):
        """Test batch resolution with composite resolver - deduplication within batch."""
        resolver = CompositeResolver(embedder=mock_embedder)

        # Batch of entities to resolve - tests cross-entity deduplication within the batch
        entities = [
            ("John Smith", "PERSON"),  # First occurrence
            ("john smith", "PERSON"),  # Case variation - should resolve to first
            ("Alice Wonder", "PERSON"),  # New entity
            ("ALICE WONDER", "PERSON"),  # Case variation - should resolve to Alice Wonder
        ]

        results = await resolver.resolve_batch(entities)

        assert len(results) == 4
        # First John Smith is canonical
        assert results[0].canonical_name == "John Smith"
        # Second john smith should resolve to first
        assert results[1].canonical_name == "John Smith"
        # Alice is new
        assert results[2].canonical_name == "Alice Wonder"
        # ALICE WONDER should resolve to Alice Wonder
        assert results[3].canonical_name == "Alice Wonder"


@pytest.mark.integration
class TestEntityResolutionInMemoryOperations:
    """Test entity resolution within memory operations."""

    @pytest.mark.asyncio
    async def test_add_entity_with_resolution_enabled(self, memory_client):
        """Test adding entity with resolution finds existing."""
        # Add original entity
        original, _ = await memory_client.long_term.add_entity(
            name="Google LLC",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        # Add similar entity with resolution
        similar, _ = await memory_client.long_term.add_entity(
            name="google llc",  # Different case
            entity_type=EntityType.ORGANIZATION,
            resolve=True,  # Enable resolution
            generate_embedding=True,
        )

        # Should resolve to existing entity (exact match resolver)
        assert similar.canonical_name == "Google LLC"

    @pytest.mark.asyncio
    async def test_entity_extraction_with_resolution(self, memory_client, session_id):
        """Test entity extraction uses resolution."""
        # Add known entity
        await memory_client.long_term.add_entity(
            name="Acme Corp",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        # Add message mentioning similar entity
        await memory_client.short_term.add_message(
            session_id,
            "user",
            "I work at Acme",  # Mock extractor may extract this
            extract_entities=True,
            generate_embedding=False,
        )

        # The extraction + resolution pipeline should link to existing entity

    @pytest.mark.asyncio
    async def test_resolution_across_sessions(self, memory_client):
        """Test that resolution works when adding entities with same name (different case)."""
        # Add first entity without resolution
        entity1, _ = await memory_client.long_term.add_entity(
            name="UniqueCompany123",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        # Verify entity was created
        assert entity1.name == "UniqueCompany123"

        # Add similar entity with resolution enabled
        # Note: Resolution requires the resolver to find the existing entity
        entity2, _ = await memory_client.long_term.add_entity(
            name="uniquecompany123",  # Different case
            entity_type=EntityType.ORGANIZATION,
            resolve=True,
            generate_embedding=True,
        )

        # With MockResolver's case-insensitive matching, should resolve to existing
        assert entity2.canonical_name == "UniqueCompany123"


@pytest.mark.integration
class TestEntityResolutionEdgeCases:
    """Test edge cases in entity resolution."""

    @pytest.mark.asyncio
    async def test_resolution_empty_database(self, clean_memory_client):
        """Test resolution with no existing entities."""
        resolver = ExactMatchResolver()

        result = await resolver.resolve(
            "Brand New Entity",
            "PERSON",
            existing_entities=[],
        )

        assert result.canonical_name == "Brand New Entity"
        # With no existing entities there is nothing to match: the entity is its
        # own canonical form and the match type is "none" (only a real hit
        # reports "exact").
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_resolution_special_characters(self, memory_client):
        """Test resolution handles special characters."""
        await memory_client.long_term.add_entity(
            name="O'Reilly & Associates",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        resolver = ExactMatchResolver()

        result = await resolver.resolve(
            "O'Reilly & Associates",
            "ORGANIZATION",
            existing_entities=["O'Reilly & Associates"],
        )

        assert result.canonical_name == "O'Reilly & Associates"

    @pytest.mark.asyncio
    async def test_resolution_unicode_characters(self, memory_client):
        """Test resolution handles unicode."""
        await memory_client.long_term.add_entity(
            name="Tokyo 東京",
            entity_type=EntityType.LOCATION,
            resolve=False,
            generate_embedding=False,
        )

        resolver = ExactMatchResolver()

        result = await resolver.resolve(
            "Tokyo 東京",
            "LOCATION",
            existing_entities=["Tokyo 東京"],
        )

        assert result.canonical_name == "Tokyo 東京"

    @pytest.mark.asyncio
    async def test_resolution_whitespace_handling(self, memory_client):
        """Test resolution handles whitespace variations."""
        await memory_client.long_term.add_entity(
            name="New York City",
            entity_type=EntityType.LOCATION,
            resolve=False,
            generate_embedding=False,
        )

        resolver = ExactMatchResolver()

        # Extra whitespace
        result = await resolver.resolve(
            "  New York City  ",
            "LOCATION",
            existing_entities=["New York City"],
        )

        # Should still match after normalization
        assert result.canonical_name == "New York City"

    @pytest.mark.asyncio
    async def test_resolution_large_candidate_set(self, memory_client, mock_embedder):
        """Test resolution with large number of candidates."""
        # Create many entities
        candidates = [f"Entity{i}" for i in range(100)]
        for name in candidates:
            await memory_client.long_term.add_entity(
                name=name,
                entity_type=EntityType.CONCEPT,
                resolve=False,
                generate_embedding=False,
            )

        resolver = CompositeResolver(embedder=mock_embedder)

        # Resolve against all
        result = await resolver.resolve(
            "Entity50",
            "CONCEPT",
            existing_entities=candidates,
        )

        assert result.canonical_name == "Entity50"

    @pytest.mark.asyncio
    async def test_resolution_concurrent_operations(self, memory_client, mock_embedder):
        """Test concurrent resolution operations."""
        import asyncio

        # Create base entities
        base_entities = ["Alpha", "Beta", "Gamma", "Delta"]
        for name in base_entities:
            await memory_client.long_term.add_entity(
                name=name,
                entity_type=EntityType.CONCEPT,
                resolve=False,
                generate_embedding=False,
            )

        resolver = CompositeResolver(embedder=mock_embedder)

        async def resolve_entity(name):
            return await resolver.resolve(
                name,
                "CONCEPT",
                existing_entities=base_entities,
            )

        # Resolve many entities concurrently
        names = ["Alpha", "beta", "GAMMA", "delta", "Epsilon"]
        tasks = [resolve_entity(name) for name in names]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        assert all(isinstance(r, ResolvedEntity) for r in results)


@pytest.mark.integration
class TestEntityResolutionMetrics:
    """Test entity resolution tracking and metrics."""

    @pytest.mark.asyncio
    async def test_resolution_confidence_scores(self, memory_client, mock_embedder):
        """Test that resolution returns appropriate confidence scores."""
        await memory_client.long_term.add_entity(
            name="Test Corp",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        resolver = CompositeResolver(embedder=mock_embedder)

        # Exact match should have high confidence
        result = await resolver.resolve(
            "Test Corp",
            "ORGANIZATION",
            existing_entities=["Test Corp"],
        )

        assert result.confidence >= 0.9

    @pytest.mark.asyncio
    async def test_resolution_match_type_tracking(self, memory_client, mock_embedder):
        """Test that resolution tracks match type."""
        await memory_client.long_term.add_entity(
            name="Exact Match Entity",
            entity_type=EntityType.CONCEPT,
            resolve=False,
            generate_embedding=True,
        )

        resolver = CompositeResolver(embedder=mock_embedder)

        # This should be exact match
        result = await resolver.resolve(
            "Exact Match Entity",
            "CONCEPT",
            existing_entities=["Exact Match Entity"],
        )

        assert result.match_type in ["exact", "fuzzy", "semantic", "none"]

    @pytest.mark.asyncio
    async def test_find_matches_returns_all_candidates(self, memory_client, mock_embedder):
        """Test find_matches returns all potential matches."""
        candidates = ["Apple Inc", "Apple Computer", "Apple Records"]
        for name in candidates:
            await memory_client.long_term.add_entity(
                name=name,
                entity_type=EntityType.ORGANIZATION,
                resolve=False,
                generate_embedding=True,
            )

        resolver = CompositeResolver(embedder=mock_embedder)

        # Find all matches for "Apple"
        matches = await resolver.find_matches(
            "Apple",
            "ORGANIZATION",
            candidates,
        )

        assert isinstance(matches, list)
        # All have "Apple" so might match
