"""Integration tests for typed relation storage with provenance (v0.7).

Covers the ``RELATED_TO`` merge-key change (now keyed on
``(source, type, target)`` instead of just ``(source, target)``) and the
new provenance properties written by
``ShortTermMemory._store_relations`` / ``LongTermMemory.add_relationship``:
``support``, ``derived``, ``extractor``, ``source_message_ids``,
``evidence`` — plus the idempotent ``r.relation_type`` -> ``r.type``
backfill run from ``SchemaManager.setup_all()``.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.extraction.base import ExtractedRelation
from neo4j_agent_memory.graph.schema import RELATION_TYPE_BACKFILL, SchemaManager


async def _related_to_rows(client: Any, source_id: str, target_id: str) -> list[dict[str, Any]]:
    """Fetch every RELATED_TO edge between two entities, properties projected.

    Projects properties explicitly rather than ``RETURN r`` — the driver's
    ``Result.data()`` serializes a relationship as a
    ``(start_props, type, end_props)`` tuple and drops its own properties
    (see the comment on ``queries.CREATE_ENTITY_RELATIONSHIP``).
    """
    return await client._client.execute_read(
        """
        MATCH (a:Entity {id: $source_id})-[r:RELATED_TO]->(b:Entity {id: $target_id})
        RETURN r.type AS type,
               r.relation_type AS relation_type,
               r.confidence AS confidence,
               r.support AS support,
               r.derived AS derived,
               r.extractor AS extractor,
               r.source_message_ids AS source_message_ids,
               r.evidence AS evidence
        ORDER BY r.type
        """,
        {"source_id": source_id, "target_id": target_id},
    )


async def _add_pair(client: Any) -> tuple[str, str]:
    """Create a PERSON and an ORGANIZATION entity, returning their (str) ids."""
    source, _ = await client.long_term.add_entity(
        "Brian Chesky", "PERSON", resolve=False, generate_embedding=False
    )
    target, _ = await client.long_term.add_entity(
        "Airbnb", "ORGANIZATION", resolve=False, generate_embedding=False
    )
    return str(source.id), str(target.id)


@pytest.mark.integration
class TestTypedRelationStorage:
    """``_store_relations`` / ``CREATE_ENTITY_RELATION_BY_ID`` merge + provenance."""

    @pytest.mark.asyncio
    async def test_two_typed_relations_between_same_pair_both_survive(self, clean_memory_client):
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)
        entity_name_to_id = {"brian chesky": source_id, "airbnb": target_id}

        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky",
                    target="Airbnb",
                    relation_type="FOUNDED",
                    confidence=0.9,
                )
            ],
            entity_name_to_id,
            message_id="msg-1",
            extractor="test",
        )
        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky",
                    target="Airbnb",
                    relation_type="WORKS_AT",
                    confidence=0.8,
                )
            ],
            entity_name_to_id,
            message_id="msg-1",
            extractor="test",
        )

        rows = await _related_to_rows(client, source_id, target_id)
        # Both typed edges survive as distinct relationships, not an
        # overwrite of a single (source, target) edge.
        assert [row["type"] for row in rows] == ["FOUNDED", "WORKS_AT"]

    @pytest.mark.asyncio
    async def test_storing_same_typed_relation_twice_increments_support(self, clean_memory_client):
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)
        entity_name_to_id = {"brian chesky": source_id, "airbnb": target_id}

        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky",
                    target="Airbnb",
                    relation_type="FOUNDED",
                    confidence=0.7,
                )
            ],
            entity_name_to_id,
            message_id="msg-1",
            evidence="Brian Chesky founded Airbnb.",
            extractor="test",
        )
        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky",
                    target="Airbnb",
                    relation_type="FOUNDED",
                    confidence=0.95,
                )
            ],
            entity_name_to_id,
            message_id="msg-2",
            evidence="Airbnb was founded by Brian Chesky.",
            extractor="test",
        )

        rows = await _related_to_rows(client, source_id, target_id)
        assert len(rows) == 1  # one edge, not two
        row = rows[0]
        assert row["support"] == 2
        assert row["confidence"] == pytest.approx(0.95)  # keeps the max observed
        assert set(row["source_message_ids"]) == {"msg-1", "msg-2"}
        assert len(row["evidence"]) == 2

    @pytest.mark.asyncio
    async def test_derived_semantics_asserted_observation_wins(self, clean_memory_client):
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)
        entity_name_to_id = {"brian chesky": source_id, "airbnb": target_id}

        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky", target="Airbnb", relation_type="MENTIONS", derived=True
                )
            ],
            entity_name_to_id,
        )
        rows = await _related_to_rows(client, source_id, target_id)
        assert rows[0]["derived"]

        # An asserted (non-derived) observation permanently clears the flag.
        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky",
                    target="Airbnb",
                    relation_type="MENTIONS",
                    derived=False,
                )
            ],
            entity_name_to_id,
        )
        rows = await _related_to_rows(client, source_id, target_id)
        assert not rows[0]["derived"]

        # A later derived observation doesn't flip it back to True.
        await client.short_term._store_relations(
            [
                ExtractedRelation(
                    source="Brian Chesky", target="Airbnb", relation_type="MENTIONS", derived=True
                )
            ],
            entity_name_to_id,
        )
        rows = await _related_to_rows(client, source_id, target_id)
        assert not rows[0]["derived"]

    @pytest.mark.asyncio
    async def test_add_relationship_shares_the_same_provenance_mechanics(self, clean_memory_client):
        client = clean_memory_client
        source, _ = await client.long_term.add_entity(
            "Jane Doe", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await client.long_term.add_entity(
            "Acme Corp", "ORGANIZATION", resolve=False, generate_embedding=False
        )

        await client.long_term.add_relationship(
            source,
            target,
            "EMPLOYED_BY",
            confidence=0.6,
            message_id="msg-1",
            extractor="manual",
        )
        await client.long_term.add_relationship(
            source,
            target,
            "EMPLOYED_BY",
            confidence=0.9,
            message_id="msg-2",
            extractor="manual",
        )

        rows = await _related_to_rows(client, str(source.id), str(target.id))
        assert len(rows) == 1
        assert rows[0]["support"] == 2
        assert rows[0]["confidence"] == pytest.approx(0.9)
        assert set(rows[0]["source_message_ids"]) == {"msg-1", "msg-2"}

    @pytest.mark.asyncio
    async def test_get_related_entities_reads_back_typed_relation_provenance(
        self, clean_memory_client
    ):
        """``get_related_entities`` surfaces the stored type/support/confidence.

        ``GET_ENTITY_RELATIONSHIPS`` used to ``RETURN e, r, other``: a bare
        relationship never survives ``execute_read``'s ``Result.data()``
        (it flattens to a ``(start_props, type, end_props)`` tuple and drops
        properties), so every hit silently fell back to
        ``type="RELATED_TO"``, ``confidence=1.0``, and a fresh random id
        instead of what was actually stored.
        """
        client = clean_memory_client
        source, _ = await client.long_term.add_entity(
            "Brian Chesky", "PERSON", resolve=False, generate_embedding=False
        )
        target, _ = await client.long_term.add_entity(
            "Airbnb", "ORGANIZATION", resolve=False, generate_embedding=False
        )

        stored = await client.long_term.add_relationship(
            source,
            target,
            "FOUNDED",
            description="Brian Chesky founded Airbnb",
            confidence=0.87,
        )

        related = await client.long_term.get_related_entities(source.id)

        assert len(related) == 1
        other_entity, relationship = related[0]
        assert other_entity.name == "Airbnb"
        assert relationship.id == stored.id
        assert relationship.type == "FOUNDED"
        assert relationship.confidence == pytest.approx(0.87)
        assert relationship.description == "Brian Chesky founded Airbnb"
        # Provenance fields introduced alongside the typed-relation rework:
        # not part of the Relationship model's own fields, so they land in
        # attributes.
        assert relationship.attributes["support"] == 1
        assert relationship.attributes["derived"] is False


@pytest.mark.integration
class TestRelationTypeBackfill:
    """One-shot ``r.relation_type`` -> ``r.type`` backfill (``setup_all``)."""

    @staticmethod
    async def _clear_marker(client: Any) -> None:
        """Drop the once-per-database marker so the scan runs again.

        ``connect()`` already ran the backfill for this database and recorded
        it; these tests create their legacy edge afterwards, so they have to
        reset the marker rather than rely on the scan repeating.
        """
        await client._client.execute_write(
            f"MATCH (m:SchemaMigration {{name: '{RELATION_TYPE_BACKFILL}'}}) DELETE m"
        )

    @staticmethod
    async def _marker_rows(client: Any) -> list[dict[str, Any]]:
        return await client._client.execute_read(
            "MATCH (m:SchemaMigration) RETURN m.name AS name, m.completed_at AS completed_at"
        )

    @pytest.mark.asyncio
    async def test_backfill_migrates_legacy_edge_without_duplicating_it(self, clean_memory_client):
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)

        # Simulate a pre-v0.7 edge: only relation_type, no r.type/support —
        # what CREATE_ENTITY_RELATION_BY_ID/_BY_NAME wrote before this PR.
        await client._client.execute_write(
            """
            MATCH (a:Entity {id: $source_id}), (b:Entity {id: $target_id})
            CREATE (a)-[r:RELATED_TO]->(b)
            SET r.relation_type = 'LEGACY_REL', r.confidence = 0.8
            """,
            {"source_id": source_id, "target_id": target_id},
        )

        await self._clear_marker(client)
        schema_manager = SchemaManager(client._client)
        await schema_manager.setup_all()

        rows = await _related_to_rows(client, source_id, target_id)
        assert len(rows) == 1  # backfill updates the edge in place
        assert rows[0]["type"] == "LEGACY_REL"
        assert rows[0]["support"] == 1

        # Completion is recorded, so the next connect() does not re-scan.
        markers = await self._marker_rows(client)
        assert [m["name"] for m in markers] == [RELATION_TYPE_BACKFILL]
        assert markers[0]["completed_at"] is not None

        # Re-running setup_all() (e.g. on the next connect()) is a no-op:
        # still one edge, unchanged values.
        await schema_manager.setup_all()
        rows = await _related_to_rows(client, source_id, target_id)
        assert len(rows) == 1
        assert rows[0]["type"] == "LEGACY_REL"
        assert rows[0]["support"] == 1

    @pytest.mark.asyncio
    async def test_the_marker_makes_setup_all_skip_the_scan(self, clean_memory_client):
        """A legacy edge created *after* the marker is deliberately left alone.

        That is the point of the marker: the scan is a write over every
        ``RELATED_TO`` edge, so it is not paid for on every connection. The
        backfill can still be driven explicitly.
        """
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)

        schema_manager = SchemaManager(client._client)
        await self._clear_marker(client)
        await schema_manager.setup_all()  # records the marker

        await client._client.execute_write(
            """
            MATCH (a:Entity {id: $source_id}), (b:Entity {id: $target_id})
            CREATE (a)-[r:RELATED_TO]->(b)
            SET r.relation_type = 'LATE_REL'
            """,
            {"source_id": source_id, "target_id": target_id},
        )

        await schema_manager.setup_all()
        rows = await _related_to_rows(client, source_id, target_id)
        assert rows[0]["type"] is None  # skipped

        await schema_manager._backfill_relation_type(force=True)
        rows = await _related_to_rows(client, source_id, target_id)
        assert rows[0]["type"] == "LATE_REL"

    @pytest.mark.asyncio
    async def test_the_setting_off_skips_the_scan_entirely(self, clean_memory_client):
        client = clean_memory_client
        source_id, target_id = await _add_pair(client)
        await client._client.execute_write(
            """
            MATCH (a:Entity {id: $source_id}), (b:Entity {id: $target_id})
            CREATE (a)-[r:RELATED_TO]->(b)
            SET r.relation_type = 'LEGACY_REL'
            """,
            {"source_id": source_id, "target_id": target_id},
        )
        await self._clear_marker(client)

        await SchemaManager(client._client, backfill_relation_types=False).setup_all()

        rows = await _related_to_rows(client, source_id, target_id)
        assert rows[0]["type"] is None
        assert await self._marker_rows(client) == []
