"""Integration tests for entity linking feedback in add_fact/add_preference responses.

Verifies that linking results are surfaced in the metadata of returned
Fact and Preference objects, so callers can see what was linked.

See https://github.com/neo4j-labs/agent-memory/issues/90.
"""

import pytest

from neo4j_agent_memory.memory.long_term import EntityType


@pytest.mark.integration
class TestLinkingFeedback:
    """Test that add_fact/add_preference return linking results in metadata."""

    @pytest.mark.asyncio
    async def test_add_fact_returns_linked_entities(self, clean_memory_client):
        """add_fact should report which entities were linked via metadata."""
        await clean_memory_client.long_term.add_entity(
            name="Alice",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        fact = await clean_memory_client.long_term.add_fact(
            subject="Alice",
            predicate="works_at",
            obj="Acme Corp",
            generate_embedding=False,
        )

        linked = fact.metadata.get("linked_entities")
        assert linked is not None, "linked_entities missing from metadata"
        assert linked["subject"]["name"] == "Alice"
        assert linked["subject"]["linked"] is True
        assert linked["object"]["name"] == "Acme Corp"
        assert linked["object"]["linked"] is False

    @pytest.mark.asyncio
    async def test_add_preference_returns_linked_entity(self, clean_memory_client):
        """add_preference should report whether a matching entity was linked."""
        await clean_memory_client.long_term.add_entity(
            name="Python",
            entity_type="OBJECT",
            resolve=False,
            generate_embedding=False,
        )

        pref = await clean_memory_client.long_term.add_preference(
            category="Python",
            preference="Prefers Python for data science",
            generate_embedding=False,
        )

        linked = pref.metadata.get("linked_entity")
        assert linked is not None, "linked_entity missing from metadata"
        assert linked["name"] == "Python"
        assert linked["linked"] is True

    @pytest.mark.asyncio
    async def test_add_fact_no_match_reports_unlinked(self, clean_memory_client):
        """When no entity matches, linked_entities should show linked=False."""
        fact = await clean_memory_client.long_term.add_fact(
            subject="Nobody",
            predicate="likes",
            obj="Nothing",
            generate_embedding=False,
        )

        linked = fact.metadata.get("linked_entities")
        assert linked is not None
        assert linked["subject"]["linked"] is False
        assert linked["object"]["linked"] is False
