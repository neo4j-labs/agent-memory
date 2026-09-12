"""Integration tests for long-term write-path integrity against a real Neo4j.

These exercise the failure modes that only show up once a real MERGE, a real
``ON MATCH`` branch and a real relationship write are involved:

* ``aliases`` written into the ``metadata`` blob while alias lookup read a
  top-level property;
* ``add_entity`` returning an id that no node carries;
* ``add_relationship`` acknowledging a write that matched nothing;
* ``merge_duplicate_entities`` orphaning the merged-away entity's edges.
"""

from uuid import UUID, uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotFoundError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _edges(neo4j_client, query: str, **params) -> list[dict]:
    return await neo4j_client.execute_read(query, params)


class TestAliasesRoundTrip:
    """``add_entity(aliases=...)`` must be findable by that alias."""

    async def test_entity_is_findable_by_alias(self, clean_memory_client):
        entity, _ = await clean_memory_client.long_term.add_entity(
            "Jonathan Smith",
            "PERSON",
            aliases=["Jon Smith", "J. Smith"],
            resolve=False,
            generate_embedding=False,
        )

        found = await clean_memory_client.long_term.get_entity_by_name("Jon Smith")

        assert found is not None
        assert found.id == entity.id
        assert "Jon Smith" in found.aliases

    async def test_aliases_survive_a_round_trip(self, clean_memory_client):
        await clean_memory_client.long_term.add_entity(
            "Jonathan Smith",
            "PERSON",
            aliases=["Jon Smith"],
            resolve=False,
            generate_embedding=False,
        )

        found = await clean_memory_client.long_term.get_entity_by_name("Jonathan Smith")

        assert found is not None
        assert found.aliases == ["Jon Smith"]

    async def test_alias_is_not_duplicated_into_metadata(self, clean_memory_client):
        entity, _ = await clean_memory_client.long_term.add_entity(
            "Jonathan Smith",
            "PERSON",
            aliases=["Jon Smith"],
            metadata={"source": "test"},
            resolve=False,
            generate_embedding=False,
        )

        assert "aliases" not in (entity.metadata or {})
        assert entity.metadata.get("source") == "test"

    async def test_adding_the_same_alias_twice_does_not_duplicate_it(self, clean_memory_client):
        entity, _ = await clean_memory_client.long_term.add_entity(
            "Jonathan Smith", "PERSON", resolve=False, generate_embedding=False
        )

        await clean_memory_client.long_term._add_alias_to_entity(entity.id, "Jon Smith")
        await clean_memory_client.long_term._add_alias_to_entity(entity.id, "Jon Smith")

        found = await clean_memory_client.long_term.get_entity_by_name("Jonathan Smith")
        assert found is not None
        assert found.aliases == ["Jon Smith"]

    async def test_legacy_row_with_metadata_aliases_still_reads_back(self, clean_memory_client):
        """Rows written before the move to a property must keep working."""
        entity_id = str(uuid4())
        await clean_memory_client.graph.execute_write(
            """
            MERGE (e:Entity {name: $name, type: $type})
            ON CREATE SET e.id = $id,
                          e.metadata = $metadata,
                          e.created_at = datetime()
            """,
            {
                "id": entity_id,
                "name": "Legacy Person",
                "type": "PERSON",
                "metadata": '{"aliases": ["Old Alias"], "attributes": {}}',
            },
        )

        found = await clean_memory_client.long_term._get_entity_by_id(UUID(entity_id))

        assert found is not None
        assert found.aliases == ["Old Alias"]
        assert "aliases" not in (found.metadata or {})


class TestAddEntityReturnsStoredId:
    """Every id ``add_entity`` hands back must address a real node."""

    async def test_re_add_returns_the_id_the_graph_kept(self, clean_memory_client):
        first, _ = await clean_memory_client.long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        second, _ = await clean_memory_client.long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )

        # The MERGE matched, so ON CREATE did not run and the original id won.
        assert second.id == first.id

    async def test_returned_id_is_backed_by_a_node(self, clean_memory_client):
        entity, _ = await clean_memory_client.long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        # Force the ON MATCH branch on the next call.
        re_added, _ = await clean_memory_client.long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (e:Entity {id: $id}) RETURN count(e) AS n",
            id=str(re_added.id),
        )

        assert rows[0]["n"] == 1
        assert re_added.id == entity.id

    async def test_relationship_between_returned_ids_actually_lands(self, clean_memory_client):
        """The reported symptom: the edge is silently never created."""
        source, _ = await clean_memory_client.long_term.add_entity(
            "Alice", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await clean_memory_client.long_term.add_entity(
            "Bob", "PERSON", resolve=False, generate_embedding=False
        )

        await clean_memory_client.long_term.add_relationship(source.id, target.id, "KNOWS")

        rows = await _edges(
            clean_memory_client.graph,
            """
            MATCH (a:Entity {id: $source})-[r:RELATED_TO]->(b:Entity {id: $target})
            RETURN r.type AS relation_type
            """,
            source=str(source.id),
            target=str(target.id),
        )

        assert len(rows) == 1
        assert rows[0]["relation_type"] == "KNOWS"

    async def test_relationship_survives_an_on_match_re_add(self, clean_memory_client):
        """Ids captured before a repeat add must still address the node."""
        source, _ = await clean_memory_client.long_term.add_entity(
            "Alice", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await clean_memory_client.long_term.add_entity(
            "Bob", "PERSON", resolve=False, generate_embedding=False
        )
        await clean_memory_client.long_term.add_entity(
            "Alice", "PERSON", resolve=False, generate_embedding=False
        )

        await clean_memory_client.long_term.add_relationship(source.id, target.id, "KNOWS")

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $source})-[:RELATED_TO]->(:Entity {id: $target}) "
            "RETURN count(*) AS n",
            source=str(source.id),
            target=str(target.id),
        )

        assert rows[0]["n"] == 1


class TestAddRelationshipSurfacesFailure:
    """A relationship write that matches no node is an error, not a success."""

    async def test_missing_source_raises_and_writes_nothing(self, clean_memory_client):
        target, _ = await clean_memory_client.long_term.add_entity(
            "Bob", "PERSON", resolve=False, generate_embedding=False
        )

        with pytest.raises(NotFoundError):
            await clean_memory_client.long_term.add_relationship(uuid4(), target.id, "KNOWS")

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS n",
        )
        assert rows[0]["n"] == 0

    async def test_missing_target_raises(self, clean_memory_client):
        source, _ = await clean_memory_client.long_term.add_entity(
            "Alice", "PERSON", resolve=False, generate_embedding=False
        )

        with pytest.raises(NotFoundError):
            await clean_memory_client.long_term.add_relationship(source.id, uuid4(), "KNOWS")

    async def test_both_endpoints_missing_raises(self, clean_memory_client):
        with pytest.raises(NotFoundError):
            await clean_memory_client.long_term.add_relationship(uuid4(), uuid4(), "KNOWS")

    async def test_re_adding_an_edge_is_idempotent(self, clean_memory_client):
        source, _ = await clean_memory_client.long_term.add_entity(
            "Alice", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await clean_memory_client.long_term.add_entity(
            "Bob", "PERSON", resolve=False, generate_embedding=False
        )

        first = await clean_memory_client.long_term.add_relationship(
            source.id, target.id, "KNOWS", confidence=0.9
        )
        second = await clean_memory_client.long_term.add_relationship(
            source.id, target.id, "KNOWS", confidence=0.2
        )

        assert second.id == first.id
        # ON CREATE kept the original confidence; the retry did not overwrite it.
        assert second.confidence == first.confidence

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $s})-[r:RELATED_TO]->(:Entity {id: $t}) RETURN count(r) AS n",
            s=str(source.id),
            t=str(target.id),
        )
        assert rows[0]["n"] == 1


class TestMergeMigratesEdges:
    """The surviving entity must inherit the merged-away entity's context."""

    async def test_related_to_edges_are_copied_in_both_directions(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        outgoing, _ = await long_term.add_entity(
            "Acme Corp", "ORGANIZATION", resolve=False, generate_embedding=False
        )
        incoming, _ = await long_term.add_entity(
            "Bob", "PERSON", resolve=False, generate_embedding=False
        )
        await long_term.add_relationship(source.id, outgoing.id, "WORKS_AT", confidence=0.8)
        await long_term.add_relationship(incoming.id, source.id, "KNOWS", confidence=0.7)

        merged = await long_term.merge_duplicate_entities(source.id, target.id)
        assert merged is not None

        out_rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $t})-[r:RELATED_TO]->(:Entity {id: $o}) "
            "RETURN r.type AS relation_type, r.confidence AS confidence, "
            "       r.migrated_from AS migrated_from",
            t=str(target.id),
            o=str(outgoing.id),
        )
        assert len(out_rows) == 1
        assert out_rows[0]["relation_type"] == "WORKS_AT"
        assert out_rows[0]["confidence"] == 0.8
        assert out_rows[0]["migrated_from"] == str(source.id)

        in_rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $i})-[r:RELATED_TO]->(:Entity {id: $t}) "
            "RETURN r.type AS relation_type",
            i=str(incoming.id),
            t=str(target.id),
        )
        assert len(in_rows) == 1
        assert in_rows[0]["relation_type"] == "KNOWS"

    async def test_mentions_are_copied(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        # MENTIONS is written by the short-term extraction path, not by
        # link_entity_to_message (which writes EXTRACTED_FROM), so create it
        # the way a real extraction would.
        message = await clean_memory_client.short_term.add_message(
            f"session-{uuid4()}", "user", "Jon Smith was here.", extract_entities=False
        )
        await clean_memory_client.graph.execute_write(
            "MATCH (m:Message {id: $m}), (e:Entity {id: $e}) MERGE (m)-[:MENTIONS]->(e)",
            {"m": str(message.id), "e": str(source.id)},
        )

        await long_term.merge_duplicate_entities(source.id, target.id)

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (m:Message {id: $m})-[:MENTIONS]->(e:Entity {id: $t}) RETURN count(*) AS n",
            m=str(message.id),
            t=str(target.id),
        )
        assert rows[0]["n"] == 1

    async def test_provenance_edges_are_copied(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        message = await clean_memory_client.short_term.add_message(
            f"session-{uuid4()}", "user", "Jon Smith was here.", extract_entities=False
        )
        await long_term.link_entity_to_message(source, message.id, confidence=0.9, context="ctx")
        await long_term.register_extractor("TestExtractor", version="1.0.0")
        await long_term.link_entity_to_extractor(source, "TestExtractor", confidence=0.9)

        await long_term.merge_duplicate_entities(source.id, target.id)

        provenance = await long_term.get_entity_provenance(target)
        assert any(s["message_id"] == str(message.id) for s in provenance["sources"])
        assert any(e["name"] == "TestExtractor" for e in provenance["extractors"])

    async def test_merge_is_idempotent(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        other, _ = await long_term.add_entity(
            "Acme Corp", "ORGANIZATION", resolve=False, generate_embedding=False
        )
        await long_term.add_relationship(source.id, other.id, "WORKS_AT")

        await long_term.merge_duplicate_entities(source.id, target.id)
        await long_term.merge_duplicate_entities(source.id, target.id)

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $t})-[r:RELATED_TO]->(:Entity {id: $o}) RETURN count(r) AS n",
            t=str(target.id),
            o=str(other.id),
        )
        assert rows[0]["n"] == 1

    async def test_source_keeps_its_own_edges_for_audit(self, clean_memory_client):
        """Edges are copied, not moved, so the merge stays reversible."""
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )
        other, _ = await long_term.add_entity(
            "Acme Corp", "ORGANIZATION", resolve=False, generate_embedding=False
        )
        await long_term.add_relationship(source.id, other.id, "WORKS_AT")

        await long_term.merge_duplicate_entities(source.id, target.id)

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (:Entity {id: $s})-[:RELATED_TO]->(:Entity {id: $o}) RETURN count(*) AS n",
            s=str(source.id),
            o=str(other.id),
        )
        assert rows[0]["n"] == 1

    async def test_source_name_becomes_a_findable_alias(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )

        await long_term.merge_duplicate_entities(source.id, target.id)

        found = await long_term.get_entity_by_name("Jon Smith")
        assert found is not None
        assert found.id == target.id

    async def test_source_is_marked_as_merged(self, clean_memory_client):
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity(
            "Jon Smith", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await long_term.add_entity(
            "John Smith", "PERSON", resolve=False, generate_embedding=False
        )

        await long_term.merge_duplicate_entities(source.id, target.id)

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH (e:Entity {id: $id}) RETURN e.merged_into AS merged_into, "
            "       e.merged_at AS merged_at",
            id=str(source.id),
        )
        assert rows[0]["merged_into"] == str(target.id)
        assert rows[0]["merged_at"] is not None


class TestIssue79:
    """Reproduces https://github.com/neo4j-labs/agent-memory/issues/79.

    The report's sequence, in library terms: an entity exists, and a later
    write links *to* it by calling ``add_entity`` for the target with
    ``deduplicate=False, resolve=False`` — which is what the reporting bridge
    does. On the pre-fix code that call returns an id no node carries, so the
    following ``add_relationship`` writes zero rows and returns silently:
    ``{"status": "stored"}`` with no edge in the graph.

    The report describes the returned id as "a new ghost entity". There is no
    second node — the ``MERGE`` is keyed on ``(name, type)`` and reuses the
    existing one — which is why the fix belongs in ``add_entity``'s return
    value rather than in a bridge-side name lookup.
    """

    async def test_relationship_to_an_existing_entity_lands(self, clean_memory_client):
        long_term = clean_memory_client.long_term

        # 1. The entity that already exists.
        await long_term.add_entity(
            "Charles Brubaker",
            "PERSON",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )

        # 2. A new entity carrying a relationship to it.
        scout, _ = await long_term.add_entity(
            "Scout",
            "OBJECT",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )

        # The bridge's target lookup: add_entity again, dedup and resolution off.
        target, _ = await long_term.add_entity(
            "Charles Brubaker",
            "PERSON",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )

        await long_term.add_relationship(scout, target, "AUTHORED_BY")

        rows = await _edges(
            clean_memory_client.graph,
            "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS n",
        )
        assert rows[0]["n"] == 1

    async def test_the_relationship_connects_the_two_real_nodes(self, clean_memory_client):
        """Not just an edge — an edge between the nodes the caller meant."""
        long_term = clean_memory_client.long_term

        stored, _ = await long_term.add_entity(
            "Charles Brubaker",
            "PERSON",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )
        scout, _ = await long_term.add_entity(
            "Scout",
            "OBJECT",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )
        # Re-add, exactly as the bridge does for a relationship target.
        target, _ = await long_term.add_entity(
            "Charles Brubaker",
            "PERSON",
            deduplicate=False,
            resolve=False,
            generate_embedding=False,
        )

        await long_term.add_relationship(scout, target, "AUTHORED_BY")

        rows = await _edges(
            clean_memory_client.graph,
            """
            MATCH (a:Entity {id: $source})-[r:RELATED_TO]->(b:Entity {id: $target})
            RETURN b.name AS target_name, r.type AS rel_type
            """,
            source=str(scout.id),
            target=str(stored.id),
        )
        assert len(rows) == 1
        assert rows[0]["target_name"] == "Charles Brubaker"
        assert rows[0]["rel_type"] == "AUTHORED_BY"
