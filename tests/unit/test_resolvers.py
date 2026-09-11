"""Unit tests for entity resolution strategies."""

import pytest

from neo4j_agent_memory.resolution.composite import CompositeResolver
from neo4j_agent_memory.resolution.exact import ExactMatchResolver


class TestExactMatchResolver:
    """Tests for exact match resolver."""

    @pytest.fixture
    def resolver(self):
        return ExactMatchResolver()

    @pytest.mark.asyncio
    async def test_exact_match(self, resolver):
        """Test exact matching."""
        existing = ["John Smith", "Jane Doe"]

        result = await resolver.resolve("John Smith", "PERSON", existing_entities=existing)

        assert result.canonical_name == "John Smith"
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, resolver):
        """Test case-insensitive matching."""
        existing = ["John Smith"]

        result = await resolver.resolve("john smith", "PERSON", existing_entities=existing)

        assert result.canonical_name == "John Smith"

    @pytest.mark.asyncio
    async def test_no_match(self, resolver):
        """Test when no match is found."""
        existing = ["John Smith"]

        result = await resolver.resolve("Alice Johnson", "PERSON", existing_entities=existing)

        assert result.canonical_name == "Alice Johnson"
        assert result.original_name == "Alice Johnson"
        # A miss must not claim an exact match, or the field is unreadable:
        # callers would have to re-compare names to tell hit from miss.
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_empty_existing(self, resolver):
        """Test with no existing entities."""
        result = await resolver.resolve("John Smith", "PERSON")

        assert result.canonical_name == "John Smith"
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_empty_existing_list(self, resolver):
        """An explicitly empty candidate list is also a miss, not an exact match."""
        result = await resolver.resolve("John Smith", "PERSON", existing_entities=[])

        assert result.canonical_name == "John Smith"
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_find_matches(self, resolver):
        """Test finding matches from candidates."""
        matches = await resolver.find_matches(
            "John Smith", "PERSON", ["john smith", "Jane Doe", "John Smith"]
        )

        assert len(matches) == 2  # Case variations
        assert all(m.similarity_score == 1.0 for m in matches)


class TestCompositeResolver:
    """Tests for composite resolver."""

    @pytest.fixture
    def resolver(self):
        return CompositeResolver()

    @pytest.mark.asyncio
    async def test_composite_exact_match(self, resolver):
        """Test composite resolver with exact match."""
        existing = ["John Smith"]

        result = await resolver.resolve("John Smith", "PERSON", existing_entities=existing)

        assert result.canonical_name == "John Smith"

    @pytest.mark.asyncio
    async def test_composite_no_match(self, resolver):
        """Test composite resolver when nothing matches."""
        existing = ["Alice Johnson"]

        result = await resolver.resolve("Bob Wilson", "PERSON", existing_entities=existing)

        assert result.canonical_name == "Bob Wilson"
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_resolve_batch(self, resolver):
        """Test batch resolution."""
        entities = [
            ("John", "PERSON"),
            ("Jane", "PERSON"),
            ("John", "PERSON"),  # Duplicate
        ]

        results = await resolver.resolve_batch(entities)

        assert len(results) == 3
        # Third should resolve to first
        assert results[2].canonical_name == results[0].canonical_name
