"""A mention that is only its own type's name ("tickets" typed Ticket) is not an entity."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    is_type_name_mention,
)
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory
from neo4j_agent_memory.ontology.models import DomainInfo, EntityTypeDef, OntologyDocument

ONTOLOGY = OntologyDocument(
    domain=DomainInfo(id="support-desk", name="support-desk"),
    entity_types=[
        EntityTypeDef(label="SupportCase", pole_type="EVENT", subtype="TICKET"),
        EntityTypeDef(label="Customer", pole_type="PERSON", subtype="CUSTOMER"),
    ],
)


def ticket(name: str, **attributes: str) -> ExtractedEntity:
    return ExtractedEntity(name=name, type="EVENT", subtype="TICKET", attributes=attributes)


@pytest.mark.parametrize(
    "name",
    ["ticket", "Tickets", "my ticket", "the open tickets", "TICKET"],
)
def test_the_subtype_name_is_a_type_name(name):
    assert is_type_name_mention(ticket(name))


@pytest.mark.parametrize("name", ["TK-2210", "ticket TK-2210", "Ticketmaster", "Priya Raman"])
def test_a_named_instance_is_kept(name):
    assert not is_type_name_mention(ticket(name))


def test_the_extractor_label_and_the_ontology_label_count():
    assert is_type_name_mention(ticket("support cases"), ONTOLOGY)
    assert not is_type_name_mention(ticket("support cases"))
    assert is_type_name_mention(ticket("issue", gliner2_label="Issue"))


def test_plurals_of_y_words_and_the_pole_type():
    category = ExtractedEntity(name="categories", type="OBJECT", subtype="CATEGORY")
    assert is_type_name_mention(category)
    assert is_type_name_mention(ExtractedEntity(name="people", type="PERSON")) is False
    assert is_type_name_mention(ExtractedEntity(name="persons", type="PERSON"))


def test_filter_drops_the_mention_and_its_relations():
    result = ExtractionResult(
        entities=[
            ticket("tickets"),
            ticket("TK-2210"),
            ExtractedEntity(name="Grace Liu", type="PERSON", subtype="CUSTOMER"),
        ],
        relations=[
            ExtractedRelation(source="Grace Liu", target="tickets", relation_type="OPENED"),
            ExtractedRelation(source="Grace Liu", target="TK-2210", relation_type="OPENED"),
        ],
    )

    filtered = result.filter_invalid_entities(ONTOLOGY)

    assert [e.name for e in filtered.entities] == ["TK-2210", "Grace Liu"]
    assert [(r.source, r.target) for r in filtered.relations] == [("Grace Liu", "TK-2210")]


class _Extractor:
    async def extract(self, text: str, **_: object) -> ExtractionResult:
        return ExtractionResult(
            entities=[ticket("support cases"), ticket("TK-2210")], relations=[], source_text=text
        )


@pytest.mark.asyncio
async def test_ingestion_drops_type_names_using_the_clients_ontology():
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])
    client.execute_read = AsyncMock(return_value=[])
    memory = ShortTermMemory(client, extractor=_Extractor(), ontology=ONTOLOGY)

    await memory._extract_and_link_entities(
        Message(id=uuid4(), role=MessageRole.USER, content="Show my support cases, TK-2210.")
    )

    written = [
        call.args[1]["name"]
        for call in client.execute_write.call_args_list
        if "MERGE (e:Entity {name: $name, type: $type})" in call.args[0]
    ]
    assert written == ["TK-2210"]
