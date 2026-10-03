"""Unit tests for relation storage in short-term memory."""

import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.memory.short_term import (
    Message,
    MessageRole,
    ShortTermMemory,
    _relation_evidence,
    _sentence_window,
)


def _placeholder_names(query: str) -> set[str]:
    """Extract every ``$param`` placeholder referenced in a Cypher query."""
    return set(re.findall(r"\$(\w+)", query))


class MockExtractorWithRelations:
    """Mock extractor that returns both entities and relations."""

    def __init__(self, entities: list[ExtractedEntity], relations: list[ExtractedRelation]):
        self._entities = entities
        self._relations = relations

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        return ExtractionResult(
            entities=self._entities,
            relations=self._relations if extract_relations else [],
            source_text=text,
        )


class TestRelationStorage:
    """Tests for storing extracted relations."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_write = AsyncMock(return_value=[])
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def sample_entities(self):
        """Sample entities for testing."""
        return [
            ExtractedEntity(
                name="Brian Chesky",
                type="PERSON",
                confidence=0.95,
            ),
            ExtractedEntity(
                name="Airbnb",
                type="ORGANIZATION",
                confidence=0.92,
            ),
        ]

    @pytest.fixture
    def sample_relations(self):
        """Sample relations for testing."""
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                confidence=0.9,
            ),
        ]

    @pytest.mark.asyncio
    async def test_extract_and_link_entities_stores_relations(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that _extract_and_link_entities stores relations."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        message = Message(
            id=uuid4(),
            role=MessageRole.USER,
            content="Brian Chesky founded Airbnb",
        )

        await memory._extract_and_link_entities(message, extract_relations=True)

        # Check that execute_write was called
        calls = mock_client.execute_write.call_args_list

        # Should have calls for:
        # - 2 entity creations
        # - 2 MENTIONS relationships
        # - 1 RELATED_TO relationship
        assert len(calls) >= 5

        # Find the relation creation call
        relation_calls = [
            call
            for call in calls
            if "CREATE_ENTITY_RELATION_BY_ID" in str(call) or "relation_type" in str(call)
        ]
        assert len(relation_calls) >= 1

    @pytest.mark.asyncio
    async def test_extract_and_link_entities_skips_relations_when_disabled(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that relations are not stored when extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        message = Message(
            id=uuid4(),
            role=MessageRole.USER,
            content="Brian Chesky founded Airbnb",
        )

        await memory._extract_and_link_entities(message, extract_relations=False)

        # Check that execute_write was called
        calls = mock_client.execute_write.call_args_list

        # Should have calls for:
        # - 2 entity creations
        # - 2 MENTIONS relationships
        # - NO RELATED_TO relationship
        assert len(calls) == 4  # 2 entities + 2 MENTIONS

    @pytest.mark.asyncio
    async def test_store_relations_uses_id_based_query_for_local_entities(
        self, mock_client, sample_relations
    ):
        """Test that _store_relations uses ID-based query when both entities are local."""
        memory = ShortTermMemory(mock_client)

        entity_name_to_id = {
            "brian chesky": "entity-1",
            "airbnb": "entity-2",
        }

        stored = await memory._store_relations(sample_relations, entity_name_to_id)

        assert stored == 1

        # Check that the ID-based query was used
        call = mock_client.execute_write.call_args
        query = call[0][0]
        params = call[0][1]

        assert "source_id" in params
        assert "target_id" in params
        assert params["source_id"] == "entity-1"
        assert params["target_id"] == "entity-2"
        assert params["relation_type"] == "FOUNDED"
        assert params["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_store_relations_uses_name_based_query_for_cross_message_entities(
        self, mock_client, sample_relations
    ):
        """Test that _store_relations uses name-based query for cross-message relations."""
        mock_client.execute_write = AsyncMock(return_value=[{"r": {}}])
        memory = ShortTermMemory(mock_client)

        # Only one entity is in local mapping
        entity_name_to_id = {
            "brian chesky": "entity-1",
            # "airbnb" is not in local mapping - simulating cross-message relation
        }

        stored = await memory._store_relations(sample_relations, entity_name_to_id)

        assert stored == 1

        # Check that the name-based query was used
        call = mock_client.execute_write.call_args
        params = call[0][1]

        assert "source_name" in params
        assert "target_name" in params
        assert params["source_name"] == "Brian Chesky"
        assert params["target_name"] == "Airbnb"

    @pytest.mark.asyncio
    async def test_store_relations_returns_zero_for_empty_relations(self, mock_client):
        """Test that _store_relations returns 0 for empty relations list."""
        memory = ShortTermMemory(mock_client)

        stored = await memory._store_relations([], {})

        assert stored == 0
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_message_with_extract_relations_true(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test add_message with extract_relations=True."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock conversation exists
        mock_client.execute_read = AsyncMock(
            return_value=[{"c": {"id": str(uuid4()), "session_id": "test"}}]
        )

        await memory.add_message(
            "test-session",
            MessageRole.USER,
            "Brian Chesky founded Airbnb",
            extract_entities=True,
            extract_relations=True,
        )

        # Verify that relations were stored
        calls = mock_client.execute_write.call_args_list
        relation_calls = [
            call for call in calls if len(call[0]) > 1 and "relation_type" in str(call[0][1])
        ]
        assert len(relation_calls) >= 1

    @pytest.mark.asyncio
    async def test_add_message_with_extract_relations_false(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test add_message with extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock conversation exists
        mock_client.execute_read = AsyncMock(
            return_value=[{"c": {"id": str(uuid4()), "session_id": "test"}}]
        )

        await memory.add_message(
            "test-session",
            MessageRole.USER,
            "Brian Chesky founded Airbnb",
            extract_entities=True,
            extract_relations=False,
        )

        # Count calls - should not include relation storage calls
        calls = mock_client.execute_write.call_args_list

        # Should have: 1 message creation + 2 entity creations + 2 MENTIONS = 5
        # Should NOT have RELATED_TO call
        relation_calls = [
            call for call in calls if len(call[0]) > 1 and "relation_type" in str(call[0][1])
        ]
        assert len(relation_calls) == 0


class TestExtractEntitiesFromSessionWithRelations:
    """Tests for extract_entities_from_session with relation support."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_write = AsyncMock(return_value=[])
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def sample_entities(self):
        return [
            ExtractedEntity(name="Brian Chesky", type="PERSON", confidence=0.95),
            ExtractedEntity(name="Airbnb", type="ORGANIZATION", confidence=0.92),
        ]

    @pytest.fixture
    def sample_relations(self):
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                confidence=0.9,
            ),
        ]

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_returns_relation_count(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test that extract_entities_from_session returns relations_extracted count."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock messages to process
        mock_client.execute_read = AsyncMock(
            return_value=[
                {"id": "msg-1", "content": "Brian Chesky founded Airbnb"},
            ]
        )

        result = await memory.extract_entities_from_session(
            "test-session",
            extract_relations=True,
        )

        assert "relations_extracted" in result
        assert result["relations_extracted"] >= 1
        assert result["messages_processed"] == 1
        # ``entities_extracted`` is the mention count, kept for compatibility;
        # the node counts are reported separately because resolution routinely
        # lands several mentions on one node.
        assert result["entities_extracted"] == 2
        assert result["entities_created"] == 2
        assert result["entities_merged"] == 0

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_without_relations(
        self, mock_client, sample_entities, sample_relations
    ):
        """Test extract_entities_from_session with extract_relations=False."""
        extractor = MockExtractorWithRelations(sample_entities, sample_relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        # Mock messages to process
        mock_client.execute_read = AsyncMock(
            return_value=[
                {"id": "msg-1", "content": "Brian Chesky founded Airbnb"},
            ]
        )

        result = await memory.extract_entities_from_session(
            "test-session",
            extract_relations=False,
        )

        assert result["relations_extracted"] == 0
        assert result["messages_processed"] == 1
        assert result["entities_extracted"] == 2

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_no_extractor(self, mock_client):
        """Test extract_entities_from_session returns zeros when no extractor."""
        memory = ShortTermMemory(mock_client)  # No extractor

        result = await memory.extract_entities_from_session("test-session")

        assert result == {
            "messages_processed": 0,
            "entities_extracted": 0,
            "entities_created": 0,
            "entities_merged": 0,
            "relations_extracted": 0,
        }

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_counts_merged_nodes(self):
        """Mentions that resolve onto one node must not inflate the node count."""
        from neo4j_agent_memory.config.settings import ResolutionConfig
        from neo4j_agent_memory.resolution.ontology import OntologyResolver
        from tests.unit.test_ontology_resolver import RecordingClient

        client = RecordingClient(
            {
                queries.GET_MESSAGES_FOR_ENTITY_EXTRACTION: [
                    {"id": "msg-1", "content": "Airbnb, or Airbnb Inc"}
                ]
            }
        )
        entities = [
            ExtractedEntity(name="Airbnb", type="ORGANIZATION"),
            ExtractedEntity(name="Airbnb Inc", type="ORGANIZATION"),
        ]
        extractor = MockExtractorWithRelations(entities, [])
        memory = ShortTermMemory(
            client,
            extractor=extractor,
            resolver=OntologyResolver(client, config=ResolutionConfig()),
            resolution_config=ResolutionConfig(),
        )

        result = await memory.extract_entities_from_session("test-session")

        assert result["entities_extracted"] == 2, "both mentions were seen"
        # "Airbnb Inc" normalizes onto "Airbnb": one node, not two.
        assert result["entities_created"] == 1
        assert result["entities_merged"] == 1


class TestCypherQueries:
    """Tests for the new Cypher queries."""

    def test_create_entity_relation_by_id_query_exists(self):
        """Test that CREATE_ENTITY_RELATION_BY_ID query is defined."""
        from neo4j_agent_memory.graph import queries

        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_ID")
        query = queries.CREATE_ENTITY_RELATION_BY_ID

        # Check query contains expected elements
        assert "MATCH (source:Entity {id: $source_id})" in query
        assert "MATCH (target:Entity {id: $target_id})" in query
        # The merge key includes the type so two differently-typed
        # relations between the same pair of entities both survive (v0.7).
        assert "MERGE (source)-[r:RELATED_TO {type: $relation_type}]->(target)" in query
        assert "relation_type" in query
        assert "confidence" in query
        # Provenance additions (v0.7): support/derived/extractor/evidence.
        assert "r.id = $id" in query
        assert "r.support" in query
        assert "r.derived = $derived" in query
        assert "r.extractor = $extractor" in query
        assert "r.source_message_ids" in query
        assert "r.evidence" in query
        assert "ON MATCH SET" in query
        # No self-loops: an edge from an entity to itself is never what the
        # extractor meant.
        assert "elementId(source) <> elementId(target)" in query

    def test_create_entity_relation_by_name_query_exists(self):
        """Test that CREATE_ENTITY_RELATION_BY_NAME query is defined."""
        from neo4j_agent_memory.graph import queries

        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_NAME")
        query = queries.CREATE_ENTITY_RELATION_BY_NAME

        # Check query contains expected elements
        assert "toLower(source.name)" in query or "source.name" in query
        assert "toLower(target.name)" in query or "target.name" in query
        assert "MERGE (source)-[r:RELATED_TO {type: $relation_type}]->(target)" in query
        assert "relation_type" in query
        assert "confidence" in query
        assert "r.id = $id" in query
        assert "r.support" in query
        assert "r.derived = $derived" in query
        assert "r.extractor = $extractor" in query
        assert "r.source_message_ids" in query
        assert "r.evidence" in query
        assert "ON MATCH SET" in query
        assert "elementId(source) <> elementId(target)" in query

    def test_create_entity_relationship_query_has_provenance(self):
        """CREATE_ENTITY_RELATIONSHIP (add_relationship path) gains the same provenance."""
        from neo4j_agent_memory.graph import queries

        query = queries.CREATE_ENTITY_RELATIONSHIP
        assert "MERGE (e1)-[r:RELATED_TO {type: $relation_type}]->(e2)" in query
        assert "r.support" in query
        assert "r.derived = $derived" in query
        assert "r.extractor = $extractor" in query
        assert "r.source_message_ids" in query
        assert "r.evidence" in query
        assert "ON MATCH SET" in query

    @pytest.mark.parametrize(
        "query_name",
        [
            "CREATE_ENTITY_RELATION_BY_ID",
            "CREATE_ENTITY_RELATION_BY_NAME",
            "CREATE_ENTITY_RELATIONSHIP",
        ],
    )
    def test_evidence_append_dedupes_like_source_message_ids(self, query_name):
        """Re-observing a relation must not stack the same snippet three deep."""
        from neo4j_agent_memory.graph import queries

        query = getattr(queries, query_name)
        evidence_clause = next(
            line for line in query.splitlines() if "r.evidence = (coalesce" in line
        )
        # The clause spans two lines after the guard was added; take the block.
        block = query[query.index(evidence_clause) :][:220]
        assert "NOT x IN coalesce(r.evidence, [])" in block

    def test_merge_entities_relation_transfer_is_null_safe_and_keeps_provenance(self):
        """A pre-v0.7 edge has a null ``type``; Neo4j rejects a null merge key."""
        from neo4j_agent_memory.graph import queries

        query = queries.MERGE_ENTITIES
        assert "coalesce(r.type, r.relation_type, 'RELATED_TO') AS rel_type" in query
        assert query.count("MERGE (target)-[nr:RELATED_TO {type: rel_type}]->(other)") == 1
        assert query.count("MERGE (other)-[nr:RELATED_TO {type: rel_type}]->(target)") == 1
        # The old ON CREATE SET dropped every provenance property.
        for prop in ("nr.support", "nr.derived", "nr.source_message_ids", "nr.evidence"):
            assert prop in query
        assert "nr.support = coalesce(nr.support, 1) + coalesce(r.support, 1)" in query

    def test_backfill_relation_type_query_exists(self):
        """BACKFILL_RELATION_TYPE is defined and matches the plan's shape."""
        from neo4j_agent_memory.graph import queries

        assert hasattr(queries, "BACKFILL_RELATION_TYPE")
        query = queries.BACKFILL_RELATION_TYPE

        assert "RELATED_TO" in query
        assert "r.type IS NULL" in query
        assert "r.relation_type IS NOT NULL" in query
        assert "SET r.type = r.relation_type" in query
        assert "r.support = coalesce(r.support, 1)" in query


class TestStoreRelationsProvenance:
    """Tests for mention-id resolution and provenance parameters (v0.7)."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_write = AsyncMock(return_value=[{"r": {}}])
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def sample_relations(self):
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                confidence=0.9,
            ),
        ]

    @pytest.mark.asyncio
    async def test_prefers_mention_ids_over_local_name_mapping(self, mock_client):
        """When source_id/target_id resolve, they win over entity_name_to_id."""
        memory = ShortTermMemory(mock_client)
        relation = ExtractedRelation(
            source="Brian Chesky",
            target="Airbnb",
            relation_type="FOUNDED",
            confidence=0.9,
            source_id="mention-1",
            target_id="mention-2",
        )
        # Deliberately different/absent from the name mapping, to prove the
        # mention-id path is the one actually used.
        entity_name_to_id: dict[str, str] = {}
        mention_id_to_node_id = {"mention-1": "node-1", "mention-2": "node-2"}

        stored = await memory._store_relations(
            [relation],
            entity_name_to_id,
            mention_id_to_node_id=mention_id_to_node_id,
        )

        assert stored == 1
        query, params = mock_client.execute_write.call_args[0]
        assert query == queries.CREATE_ENTITY_RELATION_BY_ID
        assert params["source_id"] == "node-1"
        assert params["target_id"] == "node-2"

    @pytest.mark.asyncio
    async def test_falls_back_to_names_when_mention_ids_unresolved(self, mock_client):
        """Unresolvable mention ids fall back to entity_name_to_id, not by-name lookup."""
        memory = ShortTermMemory(mock_client)
        relation = ExtractedRelation(
            source="Brian Chesky",
            target="Airbnb",
            relation_type="FOUNDED",
            confidence=0.9,
            source_id="unknown-mention-1",
            target_id="unknown-mention-2",
        )
        entity_name_to_id = {"brian chesky": "entity-1", "airbnb": "entity-2"}

        stored = await memory._store_relations(
            [relation], entity_name_to_id, mention_id_to_node_id={}
        )

        assert stored == 1
        query, params = mock_client.execute_write.call_args[0]
        assert query == queries.CREATE_ENTITY_RELATION_BY_ID
        assert params["source_id"] == "entity-1"
        assert params["target_id"] == "entity-2"

    @pytest.mark.asyncio
    async def test_falls_back_to_names_when_relation_has_no_mention_ids(
        self, mock_client, sample_relations
    ):
        """Relations without source_id/target_id behave exactly as before."""
        memory = ShortTermMemory(mock_client)
        entity_name_to_id = {"brian chesky": "entity-1", "airbnb": "entity-2"}

        stored = await memory._store_relations(
            sample_relations,
            entity_name_to_id,
            mention_id_to_node_id={"unrelated": "node-x"},
        )

        assert stored == 1
        query, params = mock_client.execute_write.call_args[0]
        assert query == queries.CREATE_ENTITY_RELATION_BY_ID
        assert params["source_id"] == "entity-1"
        assert params["target_id"] == "entity-2"

    @pytest.mark.asyncio
    async def test_by_id_write_supplies_every_query_parameter(self, mock_client, sample_relations):
        """Every $placeholder in CREATE_ENTITY_RELATION_BY_ID is supplied.

        Neo4j rejects a query referencing a parameter the caller never
        passed, so this guards against a missing-parameter regression
        without needing a live database.
        """
        memory = ShortTermMemory(mock_client)
        entity_name_to_id = {"brian chesky": "entity-1", "airbnb": "entity-2"}

        await memory._store_relations(
            sample_relations,
            entity_name_to_id,
            message_id="msg-1",
            evidence="Brian Chesky founded Airbnb in San Francisco.",
            extractor="gliner2",
        )

        query, params = mock_client.execute_write.call_args[0]
        assert query == queries.CREATE_ENTITY_RELATION_BY_ID
        assert _placeholder_names(query) <= params.keys()

    @pytest.mark.asyncio
    async def test_by_name_write_supplies_every_query_parameter(self, mock_client):
        """Every $placeholder in CREATE_ENTITY_RELATION_BY_NAME is supplied."""
        memory = ShortTermMemory(mock_client)
        relation = ExtractedRelation(
            source="Brian Chesky",
            target="Airbnb",
            relation_type="FOUNDED",
            confidence=0.9,
        )

        # Neither entity is in the local mapping, forcing the by-name path.
        await memory._store_relations(
            [relation],
            {},
            message_id="msg-1",
            evidence="Brian Chesky founded Airbnb.",
            extractor="gliner2",
        )

        query, params = mock_client.execute_write.call_args[0]
        assert query == queries.CREATE_ENTITY_RELATION_BY_NAME
        assert _placeholder_names(query) <= params.keys()

    @pytest.mark.asyncio
    async def test_derived_flag_is_read_from_the_relation(self, mock_client):
        """params['derived'] reflects ExtractedRelation.derived, defaulting False."""
        memory = ShortTermMemory(mock_client)
        entity_name_to_id = {"brian chesky": "entity-1", "airbnb": "entity-2"}

        derived_relation = ExtractedRelation(
            source="Brian Chesky", target="Airbnb", relation_type="FOUNDED", derived=True
        )
        await memory._store_relations([derived_relation], entity_name_to_id)
        assert mock_client.execute_write.call_args[0][1]["derived"] is True

        asserted_relation = ExtractedRelation(
            source="Brian Chesky", target="Airbnb", relation_type="FOUNDED"
        )
        await memory._store_relations([asserted_relation], entity_name_to_id)
        assert mock_client.execute_write.call_args[0][1]["derived"] is False

    @pytest.mark.asyncio
    async def test_returns_zero_and_skips_write_for_empty_relations(self, mock_client):
        """Unchanged short-circuit behaviour for an empty relation list."""
        memory = ShortTermMemory(mock_client)

        stored = await memory._store_relations([], {}, message_id="msg-1")

        assert stored == 0
        mock_client.execute_write.assert_not_called()


class TestRelationEvidence:
    """Tests for the deterministic evidence-snippet helpers."""

    def test_sentence_window_expands_to_sentence_boundaries(self):
        content = "Intro sentence. Brian Chesky founded Airbnb in 2008. Trailing sentence."
        start = content.index("Brian Chesky")
        end = start + len("Brian Chesky founded Airbnb")

        window = _sentence_window(content, start, end)

        assert window == "Brian Chesky founded Airbnb in 2008."

    def test_sentence_window_clamps_out_of_range_offsets(self):
        content = "Short sentence."
        # Should not raise even with wildly out-of-range offsets.
        assert _sentence_window(content, -10, 1000) == "Short sentence."

    def test_relation_evidence_uses_sentence_window_when_mentions_found(self):
        content = "Intro. Brian Chesky founded Airbnb in San Francisco. Outro."

        snippet = _relation_evidence(content, "Brian Chesky", "Airbnb")

        assert snippet is not None
        assert "Brian Chesky" in snippet
        assert "Airbnb" in snippet
        assert "Intro." not in snippet
        assert "Outro." not in snippet

    def test_relation_evidence_is_order_independent(self):
        """Finding target before source in the text still produces one window."""
        content = "Airbnb was founded by Brian Chesky in 2008."

        snippet = _relation_evidence(content, "Brian Chesky", "Airbnb")

        assert snippet == content.strip()

    def test_relation_evidence_falls_back_to_first_200_chars_when_unmatched(self):
        content = "x" * 500

        snippet = _relation_evidence(content, "not present", "also not present")

        assert snippet == "x" * 200

    def test_relation_evidence_returns_none_without_content(self):
        assert _relation_evidence(None, "a", "b") is None
        assert _relation_evidence("", "a", "b") is None


# -----------------------------------------------------------------------------
# Ontology enforcement on the ingestion path (v0.7)
# -----------------------------------------------------------------------------


def ingest_ontology():
    """Person -[EMPLOYED_BY]-> Company; no EVENT type, no FOUNDED relation."""
    from neo4j_agent_memory.ontology.models import (
        DomainInfo,
        EntityTypeDef,
        OntologyDocument,
        RelationshipDef,
    )

    return OntologyDocument(
        domain=DomainInfo(id="ingest", name="ingest"),
        entity_types=[
            EntityTypeDef(label="Person", pole_type="PERSON"),
            EntityTypeDef(label="Company", pole_type="ORGANIZATION"),
        ],
        relationships=[
            RelationshipDef(type="EMPLOYED_BY", source="Person", target="Company"),
        ],
    )


class TestOntologyEnforcementOnIngest:
    """``_extract_and_link_entities`` applies the ontology before storing."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.execute_write = AsyncMock(return_value=[])
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def entities(self):
        return [
            ExtractedEntity(id="e1", name="Brian Chesky", type="PERSON", confidence=0.95),
            ExtractedEntity(id="e2", name="Airbnb", type="ORGANIZATION", confidence=0.92),
        ]

    @pytest.fixture
    def relations(self):
        return [
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="EMPLOYED_BY",
                source_id="e1",
                target_id="e2",
                confidence=0.9,
            ),
            ExtractedRelation(
                source="Brian Chesky",
                target="Airbnb",
                relation_type="FOUNDED",
                source_id="e1",
                target_id="e2",
                confidence=0.9,
            ),
        ]

    @staticmethod
    def _message():
        return Message(
            id=uuid4(),
            role=MessageRole.USER,
            content="Brian Chesky founded Airbnb",
        )

    @staticmethod
    def _stored_relation_types(mock_client):
        return [
            call[0][1]["relation_type"]
            for call in mock_client.execute_write.call_args_list
            if "relation_type" in call[0][1]
        ]

    @staticmethod
    def _stored_entity_names(mock_client):
        return [
            call[0][1]["name"]
            for call in mock_client.execute_write.call_args_list
            if "canonical_name" in call[0][1]
        ]

    @pytest.mark.asyncio
    async def test_a_forbidden_relation_is_not_stored(self, mock_client, entities, relations):
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(mock_client, extractor=extractor, ontology=ingest_ontology())

        await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert self._stored_relation_types(mock_client) == ["EMPLOYED_BY"]

    @pytest.mark.asyncio
    async def test_without_an_ontology_both_relations_are_stored(
        self, mock_client, entities, relations
    ):
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(mock_client, extractor=extractor)

        await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert self._stored_relation_types(mock_client) == ["EMPLOYED_BY", "FOUNDED"]

    @pytest.mark.asyncio
    async def test_permissive_mode_keeps_an_undeclared_entity(self, mock_client):
        entities = [
            ExtractedEntity(id="e1", name="Brian Chesky", type="PERSON"),
            ExtractedEntity(id="e2", name="IPO", type="EVENT"),
        ]
        extractor = MockExtractorWithRelations(entities, [])
        memory = ShortTermMemory(
            mock_client,
            extractor=extractor,
            ontology=ingest_ontology(),
            validation_mode="permissive",
        )

        await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert self._stored_entity_names(mock_client) == ["Brian Chesky", "IPO"]

    @pytest.mark.asyncio
    async def test_strict_mode_drops_an_undeclared_entity(self, mock_client, caplog):
        entities = [
            ExtractedEntity(id="e1", name="Brian Chesky", type="PERSON"),
            ExtractedEntity(id="e2", name="IPO", type="EVENT"),
        ]
        extractor = MockExtractorWithRelations(entities, [])
        memory = ShortTermMemory(
            mock_client,
            extractor=extractor,
            ontology=ingest_ontology(),
            validation_mode="strict",
        )

        with caplog.at_level("WARNING", logger="neo4j_agent_memory.memory.short_term"):
            await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert self._stored_entity_names(mock_client) == ["Brian Chesky"]
        assert "EVENT" in caplog.text

    @pytest.mark.asyncio
    async def test_strict_mode_also_drops_relations_onto_dropped_entities(self, mock_client):
        entities = [
            ExtractedEntity(id="e1", name="Brian Chesky", type="PERSON"),
            ExtractedEntity(id="e2", name="IPO", type="EVENT"),
        ]
        relations = [
            ExtractedRelation(
                source="Brian Chesky",
                target="IPO",
                relation_type="EMPLOYED_BY",
                source_id="e1",
                target_id="e2",
            )
        ]
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(
            mock_client,
            extractor=extractor,
            ontology=ingest_ontology(),
            validation_mode="strict",
        )

        await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert self._stored_entity_names(mock_client) == ["Brian Chesky"]
        assert self._stored_relation_types(mock_client) == []

    @pytest.mark.asyncio
    async def test_a_gliner2_violation_is_logged_at_warning(self, mock_client, relations, caplog):
        entities = [
            ExtractedEntity(id="e1", name="Brian Chesky", type="PERSON", extractor="gliner2"),
            ExtractedEntity(id="e2", name="Airbnb", type="ORGANIZATION", extractor="gliner2"),
        ]
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(mock_client, extractor=extractor, ontology=ingest_ontology())

        with caplog.at_level("WARNING", logger="neo4j_agent_memory.memory.short_term"):
            await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert "mis-compiled schema" in caplog.text

    @pytest.mark.asyncio
    async def test_a_non_gliner2_violation_stays_at_debug(
        self, mock_client, entities, relations, caplog
    ):
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(mock_client, extractor=extractor, ontology=ingest_ontology())

        with caplog.at_level("WARNING", logger="neo4j_agent_memory.memory.short_term"):
            await memory._extract_and_link_entities(self._message(), extract_relations=True)

        assert "mis-compiled schema" not in caplog.text

    @pytest.mark.asyncio
    async def test_extract_entities_from_session_applies_the_ontology(
        self, mock_client, entities, relations
    ):
        mock_client.execute_read = AsyncMock(
            return_value=[{"id": str(uuid4()), "content": "Brian Chesky founded Airbnb"}]
        )
        extractor = MockExtractorWithRelations(entities, relations)
        memory = ShortTermMemory(mock_client, extractor=extractor, ontology=ingest_ontology())

        stats = await memory.extract_entities_from_session("s1", extract_relations=True)

        assert stats["relations_extracted"] == 1
        assert self._stored_relation_types(mock_client) == ["EMPLOYED_BY"]


class TestRelationsUseResolvedNodeIds:
    """Resolution and relation storage agree on which node an edge points at."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Neo4j client."""
        client = MagicMock()
        client.execute_write = AsyncMock(return_value=[])
        client.execute_read = AsyncMock(return_value=[])
        return client

    @pytest.mark.asyncio
    async def test_a_merged_mention_anchors_the_relation(self):
        """A variant that merged onto a stored node must not create a new edge endpoint."""
        from neo4j_agent_memory.config.settings import ResolutionConfig
        from neo4j_agent_memory.resolution.ontology import OntologyResolver
        from tests.unit.test_ontology_resolver import RecordingClient, entity_row

        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Acme Corp", entity_id="e1")]}
        )
        extractor = MockExtractorWithRelations(
            [
                ExtractedEntity(name="ACME Corporation", type="ORGANIZATION", confidence=0.9),
                ExtractedEntity(name="Ada Lovelace", type="PERSON", confidence=0.9),
            ],
            [
                ExtractedRelation(
                    source="Ada Lovelace",
                    target="ACME Corporation",
                    relation_type="EMPLOYED_BY",
                    confidence=0.9,
                )
            ],
        )
        memory = ShortTermMemory(
            client,
            extractor=extractor,
            resolver=OntologyResolver(client, config=ResolutionConfig()),
            resolution_config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(
            Message(id=uuid4(), role=MessageRole.USER, content="Ada Lovelace works at ACME")
        )

        relation_writes = [
            params
            for query, params in client.writes
            if query == queries.CREATE_ENTITY_RELATION_BY_ID
        ]
        assert len(relation_writes) == 1
        assert relation_writes[0]["target_id"] == "e1"
        assert relation_writes[0]["relation_type"] == "EMPLOYED_BY"

    @pytest.mark.asyncio
    async def test_two_mentions_on_one_node_store_no_self_loop(self, mock_client, caplog):
        """The normal outcome for "X is not affiliated with Y" after a merge."""
        memory = ShortTermMemory(mock_client)
        relations = [
            ExtractedRelation(
                source="Apple Bank",
                target="Apple",
                relation_type="AFFILIATED_WITH",
                confidence=0.9,
            )
        ]
        # Both surface forms resolved onto the same node.
        entity_name_to_id = {"apple bank": "node-1", "apple": "node-1"}

        with caplog.at_level("DEBUG", logger="neo4j_agent_memory.memory.short_term"):
            stored = await memory._store_relations(relations, entity_name_to_id)

        assert stored == 0
        mock_client.execute_write.assert_not_called()
        assert "self-referential relation" in caplog.text

    @pytest.mark.asyncio
    async def test_a_self_loop_via_mention_ids_is_skipped(self, mock_client):
        """The precise mention-id path needs the same guard."""
        memory = ShortTermMemory(mock_client)
        relations = [
            ExtractedRelation(
                source="Apple Bank",
                target="Apple",
                relation_type="AFFILIATED_WITH",
                source_id="m1",
                target_id="m2",
            )
        ]

        stored = await memory._store_relations(
            relations,
            {},
            mention_id_to_node_id={"m1": "node-1", "m2": "node-1"},
        )

        assert stored == 0
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_distinct_nodes_still_store_the_edge(self, mock_client):
        memory = ShortTermMemory(mock_client)
        relations = [
            ExtractedRelation(
                source="Apple Bank",
                target="Apple",
                relation_type="AFFILIATED_WITH",
            )
        ]

        stored = await memory._store_relations(
            relations, {"apple bank": "node-1", "apple": "node-2"}
        )

        assert stored == 1
