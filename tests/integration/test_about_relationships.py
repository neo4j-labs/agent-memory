"""Integration tests for ABOUT relationships between Facts/Preferences and Entities.

Verifies that add_fact() and add_preference() create ABOUT relationships
linking to matching entities in the knowledge graph. These tests directly
validate the fix for https://github.com/neo4j-labs/agent-memory/issues/77.
"""

import pytest

from neo4j_agent_memory.memory.long_term import EntityType


@pytest.mark.integration
class TestAboutRelationships:
    """Test ABOUT relationships between Facts/Preferences and Entities."""

    @pytest.mark.asyncio
    async def test_fact_linked_to_entity_via_about_subject(self, clean_memory_client):
        """Adding a fact whose subject matches an entity creates ABOUT with role=subject."""
        entity, _ = await clean_memory_client.long_term.add_entity(
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

        result = await clean_memory_client._client.execute_read(
            """
            MATCH (f:Fact {id: $fact_id})-[r:ABOUT]->(e:Entity {id: $entity_id})
            RETURN r.role AS role
            """,
            {"fact_id": str(fact.id), "entity_id": str(entity.id)},
        )

        assert len(result) == 1, "ABOUT relationship not created for subject"
        assert result[0]["role"] == "subject"

    @pytest.mark.asyncio
    async def test_fact_linked_to_entity_via_about_object(self, clean_memory_client):
        """Adding a fact whose object matches an entity creates ABOUT with role=object."""
        entity, _ = await clean_memory_client.long_term.add_entity(
            name="Acme Corp",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        fact = await clean_memory_client.long_term.add_fact(
            subject="Bob",
            predicate="works_at",
            obj="Acme Corp",
            generate_embedding=False,
        )

        result = await clean_memory_client._client.execute_read(
            """
            MATCH (f:Fact {id: $fact_id})-[r:ABOUT]->(e:Entity {id: $entity_id})
            RETURN r.role AS role
            """,
            {"fact_id": str(fact.id), "entity_id": str(entity.id)},
        )

        assert len(result) == 1, "ABOUT relationship not created for object"
        assert result[0]["role"] == "object"

    @pytest.mark.asyncio
    async def test_preference_linked_to_entity_via_about(self, clean_memory_client):
        """Adding a preference whose category matches an entity creates ABOUT."""
        entity, _ = await clean_memory_client.long_term.add_entity(
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

        result = await clean_memory_client._client.execute_read(
            """
            MATCH (p:Preference {id: $pref_id})-[r:ABOUT]->(e:Entity {id: $entity_id})
            RETURN r
            """,
            {"pref_id": str(pref.id), "entity_id": str(entity.id)},
        )

        assert len(result) == 1, "ABOUT relationship not created for preference"

    @pytest.mark.asyncio
    async def test_fact_no_matching_entity_no_error(self, clean_memory_client):
        """Adding a fact with no matching entity should not crash and create no ABOUT."""
        fact = await clean_memory_client.long_term.add_fact(
            subject="NonExistentPerson",
            predicate="likes",
            obj="NonExistentThing",
            generate_embedding=False,
        )

        assert fact is not None
        assert fact.subject == "NonExistentPerson"

        result = await clean_memory_client._client.execute_read(
            """
            MATCH (f:Fact {id: $fact_id})-[r:ABOUT]->()
            RETURN r
            """,
            {"fact_id": str(fact.id)},
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fact_case_insensitive_linking(self, clean_memory_client):
        """Entity linking should be case-insensitive."""
        entity, _ = await clean_memory_client.long_term.add_entity(
            name="Rust",
            entity_type="OBJECT",
            resolve=False,
            generate_embedding=False,
        )

        fact = await clean_memory_client.long_term.add_fact(
            subject="rust",
            predicate="is_a",
            obj="programming language",
            generate_embedding=False,
        )

        result = await clean_memory_client._client.execute_read(
            """
            MATCH (f:Fact {id: $fact_id})-[r:ABOUT]->(e:Entity {id: $entity_id})
            RETURN r.role AS role
            """,
            {"fact_id": str(fact.id), "entity_id": str(entity.id)},
        )

        assert len(result) == 1, "Case-insensitive ABOUT relationship not created"
        assert result[0]["role"] == "subject"
