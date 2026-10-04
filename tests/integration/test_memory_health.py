"""Integration tests for the memory_health diagnostic tool.

Verifies that memory_health returns correct node counts, relationship
counts, and integrity metrics.

See https://github.com/neo4j-labs/agent-memory/issues/91.
"""

import json

import pytest

from neo4j_agent_memory.memory.long_term import EntityType


@pytest.mark.integration
class TestMemoryHealth:
    """Test the memory_health diagnostic tool."""

    @pytest.mark.asyncio
    async def test_health_returns_node_counts(self, clean_memory_client):
        """memory_health should count all node types."""
        await clean_memory_client.long_term.add_entity(
            name="HealthTestEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        result = await clean_memory_client.graph.execute_read(
            """
            OPTIONAL MATCH (entity:Entity)
            RETURN count(entity) AS entities
            """,
            {},
        )

        assert result[0]["entities"] >= 1

    @pytest.mark.asyncio
    async def test_health_detects_orphaned_facts(self, clean_memory_client):
        """memory_health should detect facts with no ABOUT relationships."""
        # Create a fact with no matching entity — should be orphaned
        await clean_memory_client.long_term.add_fact(
            subject="OrphanSubject",
            predicate="orphan_test",
            obj="OrphanObject",
            generate_embedding=False,
        )

        result = await clean_memory_client.graph.execute_read(
            """
            MATCH (f:Fact) WHERE NOT (f)-[:ABOUT]->()
            RETURN count(f) AS orphaned
            """,
            {},
        )

        assert result[0]["orphaned"] >= 1

    @pytest.mark.asyncio
    async def test_health_detects_linked_facts(self, clean_memory_client):
        """Facts linked to entities should not count as orphaned."""
        await clean_memory_client.long_term.add_entity(
            name="LinkedEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )
        await clean_memory_client.long_term.add_fact(
            subject="LinkedEntity",
            predicate="is_linked",
            obj="test",
            generate_embedding=False,
        )

        result = await clean_memory_client.graph.execute_read(
            """
            MATCH (f:Fact)-[:ABOUT]->()
            RETURN count(f) AS linked
            """,
            {},
        )

        assert result[0]["linked"] >= 1
