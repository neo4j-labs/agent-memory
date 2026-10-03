"""Bolt entity writes add the label the client's ontology declares.

Every write path — message ingestion, ``LongTermMemory.add_entity`` and explicit
mentions — labels a node the same way: the POLE+O type label, a built-in
subtype label, and the ontology's declared label for that exact
``(pole_type, subtype)`` pair, verbatim.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractionResult
from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory
from neo4j_agent_memory.ontology.models import DomainInfo, EntityTypeDef, OntologyDocument
from neo4j_agent_memory.schema.models import EntityRef

ONTOLOGY = OntologyDocument(
    domain=DomainInfo(id="support-desk", name="support-desk"),
    entity_types=[
        EntityTypeDef(label="SupportCase", pole_type="OBJECT", subtype="TICKET"),
        EntityTypeDef(label="Vendor", pole_type="ORGANIZATION"),
    ],
)


class _Extractor:
    def __init__(self, entities: list[ExtractedEntity]) -> None:
        self._entities = entities

    async def extract(self, text: str, **_: object) -> ExtractionResult:
        return ExtractionResult(entities=self._entities, relations=[], source_text=text)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])
    client.execute_read = AsyncMock(return_value=[])
    return client


def _entity_merges(client) -> list[str]:
    return [
        call.args[0]
        for call in client.execute_write.call_args_list
        if "MERGE (e:Entity {name: $name, type: $type})" in call.args[0]
    ]


async def _ingest(client, entity: ExtractedEntity, ontology=ONTOLOGY) -> list[str]:
    memory = ShortTermMemory(client, extractor=_Extractor([entity]), ontology=ontology)
    message = Message(id=uuid4(), role=MessageRole.USER, content="Ticket TK-2210 is open.")
    await memory._extract_and_link_entities(message)
    return _entity_merges(client)


@pytest.mark.asyncio
async def test_ingestion_adds_the_declared_label_verbatim(mock_client):
    (query,) = await _ingest(
        mock_client, ExtractedEntity(name="TK-2210", type="OBJECT", subtype="TICKET")
    )
    assert "SET e:Object, e:SupportCase" in query


@pytest.mark.asyncio
async def test_ingestion_without_an_ontology_writes_the_type_label_only(mock_client):
    (query,) = await _ingest(
        mock_client,
        ExtractedEntity(name="TK-2210", type="OBJECT", subtype="TICKET"),
        ontology=None,
    )
    assert "SET e:Object\n" in query
    assert "SupportCase" not in query


@pytest.mark.asyncio
async def test_a_bare_type_does_not_get_a_subtyped_label(mock_client):
    (query,) = await _ingest(mock_client, ExtractedEntity(name="Lamp", type="OBJECT"))
    assert "SupportCase" not in query


@pytest.mark.asyncio
async def test_add_entity_adds_the_declared_label(mock_client):
    memory = LongTermMemory(mock_client, ontology=ONTOLOGY)

    await memory.add_entity(
        "TK-2210",
        "OBJECT",
        subtype="TICKET",
        generate_embedding=False,
        deduplicate=False,
        geocode=False,
        enrich=False,
    )

    (query,) = _entity_merges(mock_client)
    assert "SET e:Object, e:SupportCase" in query


@pytest.mark.asyncio
async def test_an_explicit_mention_gets_the_type_and_declared_labels(mock_client):
    mock_client.execute_write = AsyncMock(return_value=[{"id": "acme:ORGANIZATION"}])
    memory = ShortTermMemory(mock_client, ontology=ONTOLOGY)
    message = Message(id=uuid4(), role=MessageRole.USER, content="Acme shipped it.")

    await memory._link_explicit_mentions(message, [EntityRef(name="Acme", type="ORGANIZATION")])

    merge = next(
        call.args[0]
        for call in mock_client.execute_write.call_args_list
        if "MERGE (e:Entity" in call.args[0]
    )
    assert "SET e:Organization, e:Vendor" in merge


def _label_writes(client) -> list[tuple[str, dict]]:
    return [
        (call.args[0], call.args[1])
        for call in client.execute_write.call_args_list
        if call.args[0].startswith("MATCH (e:Entity {id: $id})\nSET e:")
    ]


@pytest.mark.asyncio
async def test_a_resolved_mention_labels_the_node_it_merged_onto(mock_client):
    """No MERGE runs on the merge path, so the label must be added explicitly.

    The node may have been written before this ontology revision was active.
    """
    from neo4j_agent_memory.resolution.ontology import EntityResolution

    memory = ShortTermMemory(mock_client, ontology=ONTOLOGY)
    resolution = EntityResolution(
        action="merged", canonical_name="TK-2210", matched_entity_id="node-1"
    )

    node_id = await memory._persist_entity(
        ExtractedEntity(name="TK-2210", type="OBJECT", subtype="TICKET"),
        resolution,
        entity_name_to_id={},
    )

    assert node_id == "node-1"
    assert _label_writes(mock_client) == [
        ("MATCH (e:Entity {id: $id})\nSET e:SupportCase\nRETURN e.id AS id", {"id": "node-1"})
    ]


@pytest.mark.asyncio
async def test_a_merge_under_the_default_ontology_costs_no_label_write(mock_client):
    from neo4j_agent_memory.ontology import POLEO_ONTOLOGY
    from neo4j_agent_memory.resolution.ontology import EntityResolution

    memory = ShortTermMemory(mock_client, ontology=POLEO_ONTOLOGY)
    await memory._persist_entity(
        ExtractedEntity(name="Acme", type="ORGANIZATION"),
        EntityResolution(action="merged", canonical_name="Acme", matched_entity_id="node-2"),
        entity_name_to_id={},
    )

    assert _label_writes(mock_client) == []


@pytest.mark.asyncio
async def test_add_entity_labels_the_entity_it_merged_onto(mock_client):
    from neo4j_agent_memory.memory.long_term import DeduplicationResult, Entity

    existing_id = uuid4()
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    # Deduplication only runs with an embedding.
    memory = LongTermMemory(mock_client, embedder=embedder, ontology=ONTOLOGY)
    memory._check_for_duplicates = AsyncMock(  # type: ignore[method-assign]
        return_value=DeduplicationResult(
            is_duplicate=True, action="merged", matched_entity_id=existing_id
        )
    )
    memory._get_entity_by_id = AsyncMock(  # type: ignore[method-assign]
        return_value=Entity(id=existing_id, name="TK-2210", type="OBJECT", subtype="TICKET")
    )

    entity, result = await memory.add_entity("TK-2210", "OBJECT", subtype="TICKET", enrich=False)

    assert result.action == "merged"
    assert entity.id == existing_id
    assert _label_writes(mock_client) == [
        (
            "MATCH (e:Entity {id: $id})\nSET e:SupportCase\nRETURN e.id AS id",
            {"id": str(existing_id)},
        )
    ]
