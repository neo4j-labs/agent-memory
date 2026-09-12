"""Tests for long-term write-path integrity.

Covers the cases where a successful-looking call left the graph without the
data the caller believed it had written:

* aliases were stored inside the ``metadata`` JSON blob while alias lookup
  read a top-level ``aliases`` property, so ``get_entity_by_name`` could
  never find an entity by its aliases;
* ``add_entity`` returned a client-minted id instead of the id the MERGE
  actually stored, so ids handed to ``add_relationship`` matched no node;
* ``add_relationship`` reported success after writing zero rows;
* ``merge_duplicate_entities`` dropped the merged-away entity's edges.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotFoundError
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import build_create_entity_query
from neo4j_agent_memory.memory.long_term import (
    DeduplicationConfig,
    LongTermMemory,
)

# Edge types that merge_duplicate_entities documents (and must therefore
# migrate) for an entity.
MERGED_EDGE_TYPES = {
    "MENTIONS",
    "RELATED_TO",
    "SAME_AS",
    "EXTRACTED_FROM",
    "EXTRACTED_BY",
    "APPLIES_TO",
    "TOUCHED",
}


def stored_node(**overrides: object) -> dict[str, object]:
    """Build a node payload as ``execute_write`` returns it (``RETURN e``)."""
    node: dict[str, object] = {
        "id": str(uuid4()),
        "name": "John Smith",
        "canonical_name": "John Smith",
        "type": "PERSON",
        "subtype": None,
        "description": None,
        "embedding": [0.1] * 384,
        "confidence": 1.0,
        "metadata": None,
    }
    node.update(overrides)
    return node


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_read = AsyncMock(return_value=[])
    client.execute_write = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 384)
    return embedder


@pytest.fixture
def memory(mock_client: MagicMock, mock_embedder: MagicMock) -> LongTermMemory:
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
        deduplication=DeduplicationConfig(),
    )


@pytest.fixture
def memory_unique(mock_client: MagicMock, mock_embedder: MagicMock) -> LongTermMemory:
    """Deduplication off, so ``add_entity`` always runs the create path."""
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
        deduplication=DeduplicationConfig(enabled=False),
    )


class TestAliasesAreTopLevel:
    """``aliases`` must live where the alias lookup query reads it."""

    def test_create_query_writes_top_level_aliases(self) -> None:
        query = build_create_entity_query("PERSON", None, include_aliases=True)

        assert "e.aliases = coalesce($aliases, [])" in query
        # ON MATCH appends only aliases not already present, atomically.
        assert "e.aliases = coalesce(e.aliases, [])" in query

    def test_aliases_are_opt_in_so_hand_built_params_keep_working(self) -> None:
        """The builder gained a parameter; existing callers must not break.

        Cypher errors on a referenced-but-absent parameter, so a query built
        without opting in must not mention ``$aliases`` at all — otherwise
        every caller supplying its own parameter dict starts failing with
        ``ParameterMissing``.
        """
        assert "$aliases" not in build_create_entity_query("PERSON", None)
        assert "e.aliases" not in build_create_entity_query("PERSON", None)

    def test_alias_lookup_reads_the_same_property_the_writer_sets(self) -> None:
        """Guard against the write path and the lookup drifting apart again."""
        assert "e.aliases" in queries.GET_ENTITY_BY_NAME
        assert "e.aliases" in build_create_entity_query("PERSON", None, include_aliases=True)

    def test_merge_entities_writes_the_same_property(self) -> None:
        assert "target.aliases" in queries.MERGE_ENTITIES

    @pytest.mark.asyncio
    async def test_add_entity_passes_aliases_as_a_parameter(
        self, memory_unique: LongTermMemory, mock_client: MagicMock
    ) -> None:
        await memory_unique.add_entity("John Smith", "PERSON", aliases=["Jon Smith"])

        params = mock_client.execute_write.call_args[0][1]
        assert params["aliases"] == ["Jon Smith"]
        # Not buried in the metadata blob — that is the split being fixed.
        stored_metadata = json.loads(params["metadata"]) if params["metadata"] else {}
        assert "aliases" not in stored_metadata

    @pytest.mark.asyncio
    async def test_add_entity_without_aliases_sends_an_empty_list(
        self, memory_unique: LongTermMemory, mock_client: MagicMock
    ) -> None:
        await memory_unique.add_entity("John Smith", "PERSON")

        assert mock_client.execute_write.call_args[0][1]["aliases"] == []

    def test_parse_entity_reads_top_level_aliases(self, memory: LongTermMemory) -> None:
        entity = memory._parse_entity(
            stored_node(
                aliases=["Jon Smith", "J. Smith"], metadata='{"attributes": {"role": "ceo"}}'
            )
        )

        assert entity.aliases == ["Jon Smith", "J. Smith"]
        assert entity.attributes == {"role": "ceo"}
        # Aliases must not be duplicated into metadata.
        assert "aliases" not in entity.metadata

    def test_parse_entity_falls_back_to_legacy_metadata_aliases(
        self, memory: LongTermMemory
    ) -> None:
        """Rows written before aliases moved to a property still read back."""
        entity = memory._parse_entity(
            stored_node(metadata='{"aliases": ["Jon Smith"], "attributes": {}}')
        )

        assert entity.aliases == ["Jon Smith"]
        assert "aliases" not in entity.metadata

    @pytest.mark.asyncio
    async def test_add_alias_appends_without_reading_metadata_back(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        entity_id = uuid4()

        await memory._add_alias_to_entity(entity_id, "Jon Smith")

        # A single write, and no read-modify-write of the metadata blob.
        mock_client.execute_read.assert_not_called()
        assert mock_client.execute_write.call_count == 1
        query, params = mock_client.execute_write.call_args[0]
        assert "e.aliases" in query
        assert "e.metadata" not in query
        assert params == {"id": str(entity_id), "alias": "Jon Smith"}


class TestAddEntityReturnsStoredNode:
    """``add_entity`` must return what the MERGE stored, not what it minted."""

    @pytest.mark.asyncio
    async def test_returns_the_stored_id_when_merge_matched_an_existing_node(
        self, memory_unique: LongTermMemory, mock_client: MagicMock
    ) -> None:
        stored_id = str(uuid4())
        # ON MATCH keeps the pre-existing node: its id and properties win.
        mock_client.execute_write.return_value = [
            {"e": stored_node(id=stored_id, name="John Smith", description="pre-existing")}
        ]

        entity, _ = await memory_unique.add_entity("John Smith", "PERSON")

        assert entity.id == UUID(stored_id)
        assert entity.description == "pre-existing"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_local_entity_when_nothing_is_returned(
        self, memory_unique: LongTermMemory
    ) -> None:
        """An empty result set (e.g. a stub client) must not break the call."""
        entity, _ = await memory_unique.add_entity("John Smith", "PERSON")

        assert isinstance(entity.id, UUID)
        assert entity.name == "John Smith"

    @pytest.mark.asyncio
    async def test_node_without_an_id_does_not_break_the_write_path(
        self, memory_unique: LongTermMemory, mock_client: MagicMock
    ) -> None:
        """A node written by another tool has no ``id``; don't KeyError on it."""
        mock_client.execute_write.return_value = [{"e": {"name": "John Smith", "type": "PERSON"}}]

        entity, _ = await memory_unique.add_entity("John Smith", "PERSON")

        assert isinstance(entity.id, UUID)
        assert entity.name == "John Smith"

    @pytest.mark.asyncio
    async def test_flagged_entity_still_writes_same_as_and_enrichment_after_readback(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        """Reading the node back must not skip the post-write steps."""
        stored_id = str(uuid4())
        matched_id = str(uuid4())
        # Raw scores only, so the candidate lands inside the flag band.
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(use_fuzzy_matching=False),
        )
        mock_client.execute_read.return_value = [
            {
                "e": {
                    "id": matched_id,
                    "name": "John Smith",
                    "canonical_name": "John Smith",
                    "type": "PERSON",
                    "metadata": None,
                },
                "score": 0.90,
            }
        ]
        mock_client.execute_write.return_value = [{"e": stored_node(id=stored_id)}]
        enrichment = MagicMock()
        enrichment.is_running = True
        enrichment.enqueue = AsyncMock()
        memory._enrichment_service = enrichment

        entity, dedup_result = await memory.add_entity("Jon Smith", "PERSON", enrich=True)

        assert dedup_result.action == "flagged"
        assert entity.id == UUID(stored_id)

        # The SAME_AS edge and the enrichment queue entry both still happen, and
        # the edge is keyed on the id the graph stored.
        relationship_writes = [
            call
            for call in mock_client.execute_write.call_args_list
            if call[0][0] == queries.CREATE_SAME_AS_RELATIONSHIP
        ]
        assert len(relationship_writes) == 1
        assert relationship_writes[0][0][1]["source_id"] == stored_id
        assert relationship_writes[0][0][1]["target_id"] == matched_id
        enrichment.enqueue.assert_awaited_once()
        assert enrichment.enqueue.call_args.kwargs["entity_id"] == UUID(stored_id)


class TestAddRelationshipSurfacesFailure:
    """A write that matched nothing is not a success."""

    @pytest.mark.asyncio
    async def test_raises_when_endpoints_do_not_exist(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = []  # both MATCH clauses missed

        with pytest.raises(NotFoundError, match="no :Entity node"):
            await memory.add_relationship(uuid4(), uuid4(), "KNOWS")

    @pytest.mark.asyncio
    async def test_error_names_both_endpoint_ids(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = []
        source_id, target_id = uuid4(), uuid4()

        with pytest.raises(NotFoundError) as excinfo:
            await memory.add_relationship(source_id, target_id, "KNOWS")

        message = str(excinfo.value)
        assert str(source_id) in message
        assert str(target_id) in message
        assert "KNOWS" in message

    @pytest.mark.asyncio
    async def test_returns_the_stored_relationship_on_re_add(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        """Re-adding an edge keeps the original id/confidence, not the new ones."""
        stored_id = str(uuid4())
        # This is the shape the query projects; the driver's ``data()``
        # flattens a bare ``RETURN r`` to a tuple with no properties.
        mock_client.execute_write.return_value = [
            {"id": stored_id, "description": "original", "confidence": 0.4}
        ]
        source_id, target_id = uuid4(), uuid4()

        relationship = await memory.add_relationship(
            source_id, target_id, "KNOWS", confidence=0.9, description="retry"
        )

        assert relationship.id == UUID(stored_id)
        assert relationship.confidence == 0.4
        assert relationship.description == "original"
        assert relationship.source_id == source_id
        assert relationship.target_id == target_id

    @pytest.mark.asyncio
    async def test_null_stored_confidence_falls_back_to_the_argument(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        """Edges written before confidence was recorded must not yield None."""
        mock_client.execute_write.return_value = [
            {"id": str(uuid4()), "description": None, "confidence": None}
        ]

        relationship = await memory.add_relationship(uuid4(), uuid4(), "KNOWS", confidence=0.9)

        assert relationship.confidence == 0.9
        assert relationship.description is None

    @pytest.mark.asyncio
    async def test_keeps_the_local_id_when_the_query_projects_nothing(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        """A zero-row/no-projection result must not crash or drop the edge type."""
        mock_client.execute_write.return_value = [{}]

        relationship = await memory.add_relationship(uuid4(), uuid4(), "KNOWS")

        assert isinstance(relationship.id, UUID)
        assert relationship.type == "KNOWS"

    @pytest.mark.asyncio
    async def test_accepts_entity_objects_and_passes_through_attributes(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = [{"id": str(uuid4())}]
        entity_a, _ = await self._entities()

        relationship = await memory.add_relationship(
            entity_a, uuid4(), "KNOWS", attributes={"source": "extraction"}
        )

        assert relationship.attributes == {"source": "extraction"}

    @staticmethod
    async def _entities() -> tuple[object, object]:
        from neo4j_agent_memory.memory.long_term import Entity

        return Entity(id=uuid4(), name="A", type="PERSON"), Entity(
            id=uuid4(), name="B", type="PERSON"
        )


class TestMergeMigratesEntityEdges:
    """``merge_duplicate_entities`` must not orphan the source's edges."""

    @pytest.mark.parametrize("edge_type", sorted(MERGED_EDGE_TYPES))
    def test_merge_query_migrates_each_documented_edge_type(self, edge_type: str) -> None:
        assert edge_type in queries.MERGE_ENTITIES

    def test_merge_query_migrates_related_to_in_both_directions(self) -> None:
        query = queries.MERGE_ENTITIES.replace(" ", "")

        # Outgoing: (source)-[:RELATED_TO]->(other)
        assert "(source)-[r:RELATED_TO]->(other:Entity)" in query
        # Incoming: (other)-[:RELATED_TO]->(source)
        assert "(other:Entity)-[r:RELATED_TO]->(source)" in query

    def test_still_adds_the_source_name_as_an_alias(self) -> None:
        assert "target.aliases" in queries.MERGE_ENTITIES

    def test_still_marks_the_source_as_merged(self) -> None:
        assert "source.merged_into = target.id" in queries.MERGE_ENTITIES
        assert "source.merged_at" in queries.MERGE_ENTITIES

    def test_documented_edge_list_matches_the_query(self) -> None:
        """The docstring enumerates what migrates; keep it honest."""
        doc = LongTermMemory.merge_duplicate_entities.__doc__ or ""
        documented = set()
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped.startswith("* ") and "``" in stripped:
                documented.add(stripped.split("``")[1])
            elif stripped.startswith("* ") and "`" in stripped:
                documented.add(stripped.split("`")[1])

        assert documented == MERGED_EDGE_TYPES

    def test_migrated_edges_are_tagged_with_their_origin(self) -> None:
        """Every copied edge carries where it came from, so the merge is auditable."""
        assert queries.MERGE_ENTITIES.count("nr.migrated_from = source.id") == 6

    def test_edge_properties_survive_the_copy(self) -> None:
        """A bare MERGE would silently strip the edge's own properties."""
        query = queries.MERGE_ENTITIES
        assert "nr.type = r.type" not in query  # type is the merge key, not copied
        assert "nr.recorded_at = r.recorded_at" in query  # TOUCHED
        assert "nr.context = r.context" in query  # EXTRACTED_FROM
        assert "nr.extraction_time_ms = r.extraction_time_ms" in query  # EXTRACTED_BY
        assert "nr.valid_until = r.valid_until" in query  # RELATED_TO bi-temporality

    @pytest.mark.asyncio
    async def test_merge_returns_the_stored_node_pair(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        source_id, target_id = uuid4(), uuid4()
        mock_client.execute_write.return_value = [
            {
                "source": stored_node(
                    id=str(source_id), name="Jon Smith", metadata='{"merged_into": "x"}'
                ),
                "target": stored_node(id=str(target_id), name="John Smith", aliases=["Jon Smith"]),
            }
        ]

        result = await memory.merge_duplicate_entities(source_id, target_id)

        assert result is not None
        source, target = result
        assert source.id == source_id
        assert target.id == target_id
        assert target.aliases == ["Jon Smith"]

    @pytest.mark.asyncio
    async def test_merge_returns_none_when_entities_are_missing(
        self, memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = []

        assert await memory.merge_duplicate_entities(uuid4(), uuid4()) is None
