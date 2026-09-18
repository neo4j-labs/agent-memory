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
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotFoundError, ValidationError
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import build_create_entity_query
from neo4j_agent_memory.memory.long_term import (
    DeduplicationConfig,
    LongTermMemory,
)

# Edge types that merge_duplicate_entities documents (and must therefore
# migrate) for an entity.
if TYPE_CHECKING:
    from neo4j_agent_memory.ontology.models import OntologyDocument

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
                # Inside the flag band (>= 0.85) but under the auto-merge
                # line, which DeduplicationConfig now shares with
                # ResolutionConfig at 0.90.
                "score": 0.87,
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
        """Every copied edge carries where it came from, so the merge is auditable.

        Six ``ON CREATE SET`` blocks plus the two ``ON MATCH SET`` blocks the
        RELATED_TO transfers gained when they started folding provenance into
        an edge the surviving entity already had.
        """
        assert queries.MERGE_ENTITIES.count("nr.migrated_from = source.id") == 8

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


# -----------------------------------------------------------------------------
# Strict ontology enforcement on the write paths (v0.7)
# -----------------------------------------------------------------------------


def support_ontology() -> OntologyDocument:
    """Customer -[BUYS_FROM]-> Vendor, and nothing else."""
    from neo4j_agent_memory.ontology.models import (
        DomainInfo,
        EntityTypeDef,
        OntologyDocument,
        RelationshipDef,
    )

    return OntologyDocument(
        domain=DomainInfo(id="support", name="support"),
        entity_types=[
            EntityTypeDef(label="Customer", pole_type="PERSON", subtype="INDIVIDUAL"),
            EntityTypeDef(label="Vendor", pole_type="ORGANIZATION", subtype="COMPANY"),
        ],
        relationships=[
            RelationshipDef(type="BUYS_FROM", source="Customer", target="Vendor"),
        ],
    )


@pytest.fixture
def strict_memory(mock_client: MagicMock, mock_embedder: MagicMock) -> LongTermMemory:
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
        deduplication=DeduplicationConfig(enabled=False),
        ontology=support_ontology(),
        validation_mode="strict",
    )


@pytest.fixture
def permissive_memory(mock_client: MagicMock, mock_embedder: MagicMock) -> LongTermMemory:
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
        deduplication=DeduplicationConfig(enabled=False),
        ontology=support_ontology(),
        validation_mode="permissive",
    )


def entity_type_rows(
    source_id: object,
    target_id: object,
    *,
    target_type: str = "ORGANIZATION",
    target_subtype: str | None = "COMPANY",
) -> list[dict[str, object]]:
    """Rows as ``GET_ENTITY_TYPES_FOR_PAIR`` returns them."""
    return [
        {"id": str(source_id), "type": "PERSON", "subtype": "INDIVIDUAL"},
        {"id": str(target_id), "type": target_type, "subtype": target_subtype},
    ]


class TestStrictAddEntity:
    @pytest.mark.asyncio
    async def test_a_declared_type_is_written(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = [
            stored_node(name="Ada", type="PERSON", subtype="INDIVIDUAL")
        ]

        entity, _ = await strict_memory.add_entity(
            "Ada", "PERSON", subtype="INDIVIDUAL", geocode=False, enrich=False
        )

        assert entity.name == "Ada"

    @pytest.mark.asyncio
    async def test_an_undeclared_subtype_raises_when_there_is_no_base_label(
        self, strict_memory: LongTermMemory
    ) -> None:
        # Every PERSON label in this ontology carries a subtype, so PERSON:ALIAS
        # has no base declaration to fall back on.
        with pytest.raises(ValidationError, match="PERSON:ALIAS"):
            await strict_memory.add_entity("Ada", "PERSON", subtype="ALIAS")

    @pytest.mark.asyncio
    async def test_an_undeclared_subtype_falls_back_to_a_declared_base_label(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        from neo4j_agent_memory.ontology.models import (
            DomainInfo,
            EntityTypeDef,
            OntologyDocument,
        )

        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(enabled=False),
            ontology=OntologyDocument(
                domain=DomainInfo(id="flat", name="flat"),
                entity_types=[EntityTypeDef(label="Person", pole_type="PERSON")],
            ),
            validation_mode="strict",
        )
        mock_client.execute_write.return_value = [
            stored_node(name="Ada", type="PERSON", subtype="ALIAS")
        ]

        entity, _ = await memory.add_entity("Ada", "PERSON", subtype="ALIAS", enrich=False)

        assert entity.subtype == "ALIAS"

    @pytest.mark.asyncio
    async def test_an_undeclared_type_raises(self, strict_memory: LongTermMemory) -> None:
        with pytest.raises(ValidationError) as excinfo:
            await strict_memory.add_entity("Launch", "EVENT")

        assert "EVENT" in str(excinfo.value)
        assert "support" in str(excinfo.value)
        assert excinfo.value.details["entity_type"] == "EVENT"
        assert excinfo.value.details["declared_labels"] == ["Customer", "Vendor"]

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_validation_fails(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        with pytest.raises(ValidationError):
            await strict_memory.add_entity("Launch", "EVENT")

        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_permissive_mode_writes_an_undeclared_type(
        self, permissive_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = [stored_node(name="Launch", type="EVENT")]

        entity, _ = await permissive_memory.add_entity("Launch", "EVENT", enrich=False)

        assert entity.type == "EVENT"

    @pytest.mark.asyncio
    async def test_strict_mode_without_an_ontology_is_a_no_op(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        memory = LongTermMemory(
            client=mock_client,
            embedder=mock_embedder,
            deduplication=DeduplicationConfig(enabled=False),
            validation_mode="strict",
        )
        mock_client.execute_write.return_value = [stored_node(name="Launch", type="EVENT")]

        entity, _ = await memory.add_entity("Launch", "EVENT", enrich=False)

        assert entity.type == "EVENT"


class TestStrictAddRelationship:
    @pytest.mark.asyncio
    async def test_a_permitted_pair_is_written(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        source_id, target_id = uuid4(), uuid4()
        mock_client.execute_read.return_value = entity_type_rows(source_id, target_id)
        mock_client.execute_write.return_value = [{"id": str(uuid4()), "confidence": 1.0}]

        relationship = await strict_memory.add_relationship(source_id, target_id, "BUYS_FROM")

        assert relationship.type == "BUYS_FROM"
        assert mock_client.execute_read.await_args[0][0] == queries.GET_ENTITY_TYPES_FOR_PAIR

    @pytest.mark.asyncio
    async def test_an_undeclared_relationship_type_raises(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        with pytest.raises(ValidationError, match="SELLS_TO"):
            await strict_memory.add_relationship(uuid4(), uuid4(), "SELLS_TO")

        # Rejected before any lookup or write.
        mock_client.execute_read.assert_not_called()
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_forbidden_endpoint_pair_raises(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        source_id, target_id = uuid4(), uuid4()
        # Reversed: Vendor -[BUYS_FROM]-> Customer is not declared.
        mock_client.execute_read.return_value = [
            {"id": str(source_id), "type": "ORGANIZATION", "subtype": "COMPANY"},
            {"id": str(target_id), "type": "PERSON", "subtype": "INDIVIDUAL"},
        ]

        with pytest.raises(ValidationError, match="Vendor -\\[BUYS_FROM\\]-> Customer"):
            await strict_memory.add_relationship(source_id, target_id, "BUYS_FROM")

        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_undeclared_endpoint_type_raises(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        source_id, target_id = uuid4(), uuid4()
        mock_client.execute_read.return_value = entity_type_rows(
            source_id, target_id, target_type="EVENT", target_subtype=None
        )

        with pytest.raises(ValidationError, match="does not declare"):
            await strict_memory.add_relationship(source_id, target_id, "BUYS_FROM")

    @pytest.mark.asyncio
    async def test_a_missing_endpoint_still_raises_not_found(
        self, strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        # Validation must not pre-empt the clearer NotFoundError for an id
        # that matches no node.
        mock_client.execute_read.return_value = []
        mock_client.execute_write.return_value = []

        with pytest.raises(NotFoundError):
            await strict_memory.add_relationship(uuid4(), uuid4(), "BUYS_FROM")

    @pytest.mark.asyncio
    async def test_permissive_mode_writes_a_forbidden_pair(
        self, permissive_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        mock_client.execute_write.return_value = [{"id": str(uuid4()), "confidence": 1.0}]

        relationship = await permissive_memory.add_relationship(uuid4(), uuid4(), "SELLS_TO")

        assert relationship.type == "SELLS_TO"
        # No validation lookup happened at all.
        mock_client.execute_read.assert_not_called()


# -----------------------------------------------------------------------------
# Strict enforcement against a CUSTOM (non-POLE+O) schema
# -----------------------------------------------------------------------------


def custom_ontology() -> OntologyDocument:
    """The ad-hoc document ``SchemaModel.CUSTOM`` builds for a Movies graph.

    ``MemoryClient._resolve_ontology`` maps each ``schema_config.entity_types``
    name through ``map_label_to_poleo`` so the GLiNER2.5 compile has a POLE+O
    type to work with, and keeps the custom name as the *label*. Nodes,
    however, are written with the custom name as their ``type`` — an adopted
    ``:Movie`` node is ``(:Entity:Movie {type: 'MOVIE'})``.
    """
    from neo4j_agent_memory.extraction.label_mapping import map_label_to_poleo
    from neo4j_agent_memory.ontology.models import (
        DomainInfo,
        EntityTypeDef,
        OntologyDocument,
    )

    entity_types = []
    for name in ("PERSON", "MOVIE", "GENRE"):
        pole_type, subtype = map_label_to_poleo(name)
        entity_types.append(EntityTypeDef(label=name, pole_type=pole_type, subtype=subtype))
    return OntologyDocument(
        domain=DomainInfo(id="custom", name="custom"),
        entity_types=entity_types,
    )


@pytest.fixture
def custom_strict_memory(mock_client: MagicMock, mock_embedder: MagicMock) -> LongTermMemory:
    return LongTermMemory(
        client=mock_client,
        embedder=mock_embedder,
        deduplication=DeduplicationConfig(enabled=False),
        ontology=custom_ontology(),
        validation_mode="strict",
    )


class TestStrictCustomSchema:
    """A custom domain type is the node's ``type``; strict mode must accept it."""

    @pytest.mark.parametrize(
        ("name", "entity_type"),
        [("Bob Singh", "PERSON"), ("Inception", "MOVIE"), ("Science Fiction", "GENRE")],
    )
    @pytest.mark.asyncio
    async def test_every_declared_custom_type_is_written(
        self,
        custom_strict_memory: LongTermMemory,
        mock_client: MagicMock,
        name: str,
        entity_type: str,
    ) -> None:
        """Each declared label reaches the write, through the public API."""
        mock_client.execute_write.return_value = [
            {"e": stored_node(name=name, type=entity_type, subtype=None)}
        ]

        entity, _ = await custom_strict_memory.add_entity(name, entity_type)

        assert entity.type == entity_type
        assert entity.name == name

    @pytest.mark.asyncio
    async def test_an_undeclared_custom_type_still_raises(
        self, custom_strict_memory: LongTermMemory
    ) -> None:
        with pytest.raises(ValidationError, match="STUDIO"):
            await custom_strict_memory.add_entity("A24", "STUDIO")

    @pytest.mark.asyncio
    async def test_an_ontology_with_no_relationships_cannot_forbid_an_edge(
        self, custom_strict_memory: LongTermMemory, mock_client: MagicMock
    ) -> None:
        """A schema silent about edges says nothing, so it rejects nothing.

        The ad-hoc CUSTOM document has no way to declare a relationship, so
        enforcing here would make every ``add_relationship`` under
        ``strict_types=True`` unreachable — which is what broke the
        existing-graph example's ``DIRECTED`` / ``IN_GENRE`` writes.
        """
        mock_client.execute_write.return_value = [{"id": str(uuid4()), "confidence": 1.0}]

        relationship = await custom_strict_memory.add_relationship(uuid4(), uuid4(), "IN_GENRE")

        assert relationship.type == "IN_GENRE"
        # No endpoint lookup: there was nothing to check.
        mock_client.execute_read.assert_not_called()


# -----------------------------------------------------------------------------
# Embedding backfill on an auto-merge
# -----------------------------------------------------------------------------


class TestMergedEntityKeepsAUsableEmbedding:
    """A merged-into node with no embedding is invisible to vector search.

    ``add_entity`` returns early on an auto-merge, skipping the MERGE whose
    ``ON MATCH SET e.embedding = COALESCE($embedding, e.embedding)`` would have
    supplied one. Merges are reached by exact name, alias and gazetteer hits as
    well as by vector similarity, so the node can easily have none.
    """

    @staticmethod
    def _merging_memory(client: MagicMock, embedder: MagicMock) -> LongTermMemory:
        memory = LongTermMemory(
            client=client,
            embedder=embedder,
            deduplication=DeduplicationConfig(),
        )
        return memory

    @pytest.mark.asyncio
    async def test_a_merge_backfills_a_missing_embedding(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        from neo4j_agent_memory.memory.long_term import DeduplicationResult

        existing_id = uuid4()
        memory = self._merging_memory(mock_client, mock_embedder)
        mock_client.execute_read.return_value = [
            {"e": stored_node(id=str(existing_id), name="Acme Corp", embedding=None)}
        ]
        memory._check_for_duplicates = AsyncMock(  # type: ignore[method-assign]
            return_value=DeduplicationResult(
                is_duplicate=True,
                action="merged",
                matched_entity_id=existing_id,
                matched_entity_name="Acme Corp",
                similarity_score=1.0,
                match_type="exact",
            )
        )

        entity, dedup = await memory.add_entity("Acme Corp", "ORGANIZATION")

        assert dedup.action == "merged"
        assert entity.id == existing_id
        assert entity.embedding == [0.1] * 384
        writes = [call[0] for call in mock_client.execute_write.call_args_list]
        assert any(call[0] == queries.UPDATE_ENTITY_EMBEDDING for call in writes), writes
        backfill = next(c for c in writes if c[0] == queries.UPDATE_ENTITY_EMBEDDING)
        assert backfill[1] == {"id": str(existing_id), "embedding": [0.1] * 384}

    @pytest.mark.asyncio
    async def test_a_usable_embedding_is_left_alone(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        from neo4j_agent_memory.memory.long_term import DeduplicationResult

        existing_id = uuid4()
        memory = self._merging_memory(mock_client, mock_embedder)
        mock_client.execute_read.return_value = [
            {"e": stored_node(id=str(existing_id), name="Acme Corp", embedding=[0.9] * 384)}
        ]
        memory._check_for_duplicates = AsyncMock(  # type: ignore[method-assign]
            return_value=DeduplicationResult(
                is_duplicate=True,
                action="merged",
                matched_entity_id=existing_id,
                matched_entity_name="Acme Corp",
                similarity_score=1.0,
                match_type="exact",
            )
        )

        entity, _ = await memory.add_entity("Acme Corp", "ORGANIZATION")

        assert entity.embedding == [0.9] * 384
        writes = [call[0][0] for call in mock_client.execute_write.call_args_list]
        assert queries.UPDATE_ENTITY_EMBEDDING not in writes

    @pytest.mark.asyncio
    async def test_an_embedding_of_the_wrong_width_is_replaced(
        self, mock_client: MagicMock, mock_embedder: MagicMock
    ) -> None:
        """The vector index is sized from ``embedder.dimensions``."""
        from neo4j_agent_memory.memory.long_term import DeduplicationResult

        existing_id = uuid4()
        memory = self._merging_memory(mock_client, mock_embedder)
        mock_client.execute_read.return_value = [
            {"e": stored_node(id=str(existing_id), name="Acme Corp", embedding=[0.9] * 1536)}
        ]
        memory._check_for_duplicates = AsyncMock(  # type: ignore[method-assign]
            return_value=DeduplicationResult(
                is_duplicate=True,
                action="merged",
                matched_entity_id=existing_id,
                matched_entity_name="Acme Corp",
                similarity_score=1.0,
                match_type="exact",
            )
        )

        entity, _ = await memory.add_entity("Acme Corp", "ORGANIZATION")

        assert entity.embedding == [0.1] * 384
        writes = [call[0][0] for call in mock_client.execute_write.call_args_list]
        assert queries.UPDATE_ENTITY_EMBEDDING in writes
