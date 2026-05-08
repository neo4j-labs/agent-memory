"""Integration tests for ``SchemaManager.adopt_existing_graph``.

These tests seed a small pre-existing domain graph (no ``:Entity`` labels,
no library-managed ``id``/``type`` properties), call the adoption helper,
and verify that subsequent library writes that MERGE on
``(:Entity {name, type})`` link to the pre-existing nodes instead of
creating duplicates.
"""

from __future__ import annotations

import pytest

from neo4j_agent_memory.core.exceptions import SchemaError
from neo4j_agent_memory.schema.models import AdoptionReport


@pytest.mark.integration
@pytest.mark.asyncio
class TestAdoptExistingGraph:
    """Verify that an existing domain graph can be adopted as long-term memory."""

    async def _seed_movies_domain(self, client) -> None:
        """Seed a tiny Movies-style domain graph.

        Three :Person nodes (``name``), three :Movie nodes (``title``), one
        :Genre, and a :Movie with no ``title`` to exercise the skip path.
        """
        await client._client.execute_write(
            """
            CREATE (alice:Person {name: 'Alice'})
            CREATE (bob:Person   {name: 'Bob'})
            CREATE (carol:Person {name: 'Carol'})
            CREATE (m1:Movie {title: 'The Matrix'})
            CREATE (m2:Movie {title: 'Inception'})
            CREATE (m3:Movie {title: 'Arrival'})
            CREATE (g:Genre {name: 'Sci-Fi'})
            // Edge case: a Movie with no title.
            CREATE (broken:Movie {released: 1999})
            // Some pre-existing relations to confirm we don't disturb them.
            CREATE (alice)-[:ACTED_IN]->(m1)
            CREATE (bob)-[:DIRECTED]->(m2)
            """
        )

    async def test_adopts_labels_to_entity(self, clean_memory_client):
        client = clean_memory_client
        await self._seed_movies_domain(client)

        report = await client.schema.adopt_existing_graph(
            label_to_type={
                "Person": "PERSON",
                "Movie": "OBJECT",
                "Genre": "EVENT",
            },
            name_property_per_label={"Movie": "title"},
        )

        assert isinstance(report, AdoptionReport)
        assert report.dry_run is False
        # 3 Persons + 3 Movies + 1 Genre = 7 migrated.
        assert report.total_migrated == 7
        # 1 Movie with no title is skipped.
        assert report.total_skipped == 1

        # Spot-check the per-label breakdown.
        by_label = {r.label: r for r in report.by_label}
        assert by_label["Person"].migrated_count == 3
        assert by_label["Person"].skipped_count == 0
        assert by_label["Movie"].migrated_count == 3
        assert by_label["Movie"].skipped_count == 1
        assert by_label["Genre"].migrated_count == 1

        # Verify the super-label, type, and id were applied.
        rows = await client._client.execute_read(
            """
            MATCH (n:Person:Entity)
            WHERE n.name = 'Alice'
            RETURN n.type AS type, n.id AS id, n.name AS name
            """
        )
        assert len(rows) == 1
        assert rows[0]["type"] == "PERSON"
        assert rows[0]["id"] == "person:Alice"
        assert rows[0]["name"] == "Alice"

        # Verify the Movie used `title` as the name.
        rows = await client._client.execute_read(
            """
            MATCH (m:Movie:Entity)
            WHERE m.title = 'The Matrix'
            RETURN m.type AS type, m.name AS name, m.id AS id
            """
        )
        assert len(rows) == 1
        assert rows[0]["type"] == "OBJECT"
        assert rows[0]["name"] == "The Matrix"
        assert rows[0]["id"] == "movie:The Matrix"

        # Verify the broken Movie (no title) was *not* adopted.
        rows = await client._client.execute_read(
            """
            MATCH (m:Movie)
            WHERE m.title IS NULL
            RETURN m:Entity AS is_entity
            """
        )
        assert len(rows) == 1
        assert rows[0]["is_entity"] is False

    async def test_idempotent_on_re_run(self, clean_memory_client):
        client = clean_memory_client
        await self._seed_movies_domain(client)

        first = await client.schema.adopt_existing_graph(
            label_to_type={"Person": "PERSON"},
        )
        second = await client.schema.adopt_existing_graph(
            label_to_type={"Person": "PERSON"},
        )

        assert first.total_migrated == 3
        # Re-running migrates zero further nodes; everything is already adopted.
        assert second.total_migrated == 0
        assert second.by_label[0].already_adopted_count == 3

    async def test_dry_run_does_not_mutate(self, clean_memory_client):
        client = clean_memory_client
        await self._seed_movies_domain(client)

        report = await client.schema.adopt_existing_graph(
            label_to_type={"Person": "PERSON"},
            dry_run=True,
        )
        assert report.dry_run is True
        assert report.total_migrated == 3  # projected count

        # Confirm the graph was not actually mutated.
        rows = await client._client.execute_read(
            """
            MATCH (p:Person:Entity)
            RETURN count(p) AS cnt
            """
        )
        assert rows[0]["cnt"] == 0

    async def test_preserves_existing_id_property(self, clean_memory_client):
        client = clean_memory_client
        await client._client.execute_write(
            "CREATE (:Person {name: 'PreExisting', id: 'custom-id-42'})"
        )

        await client.schema.adopt_existing_graph(label_to_type={"Person": "PERSON"})

        rows = await client._client.execute_read(
            "MATCH (p:Person:Entity {name: 'PreExisting'}) RETURN p.id AS id"
        )
        assert rows[0]["id"] == "custom-id-42"

    async def test_rejects_unsafe_label(self, clean_memory_client):
        client = clean_memory_client
        with pytest.raises(SchemaError):
            await client.schema.adopt_existing_graph(
                label_to_type={"Person`); DROP DATABASE; //": "PERSON"},
            )

    async def test_rejects_unsafe_name_property(self, clean_memory_client):
        client = clean_memory_client
        with pytest.raises(SchemaError):
            await client.schema.adopt_existing_graph(
                label_to_type={"Person": "PERSON"},
                name_property_per_label={"Person": "name`); MATCH"},
            )

    async def test_subsequent_mention_links_to_existing_node(self, clean_memory_client, session_id):
        """The whole point of adoption: extracted MENTIONS edges should point
        at the pre-existing domain node, not duplicate it."""
        client = clean_memory_client
        # Seed a Person matching the MockExtractor's behavior (capitalized
        # word becomes a PERSON entity).
        await client._client.execute_write("CREATE (:Person {name: 'Alice'})")
        await client.schema.adopt_existing_graph(label_to_type={"Person": "PERSON"})

        # Confirm exactly one Alice exists post-adoption.
        rows = await client._client.execute_read(
            "MATCH (p:Person {name: 'Alice'}) RETURN count(p) AS cnt"
        )
        assert rows[0]["cnt"] == 1

        # Add a message that should produce a MENTIONS edge into Alice.
        await client.short_term.add_message(session_id, "user", "I had lunch with Alice yesterday.")

        # There should still be exactly one Alice node — the library
        # MERGE'd on (:Entity {name:'Alice', type:'PERSON'}) and found the
        # adopted domain node.
        rows = await client._client.execute_read("MATCH (p {name: 'Alice'}) RETURN count(p) AS cnt")
        assert rows[0]["cnt"] == 1
