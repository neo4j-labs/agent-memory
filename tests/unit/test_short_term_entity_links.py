"""Auto-extraction links messages to the entity its MERGE actually wrote.

``build_create_entity_query`` MERGEs on ``(name, type)`` and sets ``id`` only
ON CREATE. When the entity already exists, the id generated for the write names
no node, so MENTIONS links and relations must use the id the query returned.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory

EXISTING_IDS = {"Maya Chen": "existing-maya", "Northstar Robotics": "existing-northstar"}


class _Extractor:
    async def extract(self, text: str, **_: object) -> ExtractionResult:
        return ExtractionResult(
            entities=[
                ExtractedEntity(name="Maya Chen", type="PERSON", confidence=0.9),
                ExtractedEntity(name="Northstar Robotics", type="ORGANIZATION", confidence=0.9),
            ],
            relations=[
                ExtractedRelation(
                    source="Maya Chen", target="Northstar Robotics", relation_type="WORKS_AT"
                )
            ],
            source_text=text,
        )


def _is_entity_merge(query: str) -> bool:
    return "MERGE (e:Entity {name: $name, type: $type})" in query


@pytest.fixture
def mock_client():
    """A client whose entity MERGE matches an entity that already exists."""

    async def execute_write(query: str, params: dict | None = None):
        if _is_entity_merge(query):
            assert params is not None
            return [{"e": {"id": EXISTING_IDS[params["name"]], "name": params["name"]}}]
        return []

    client = MagicMock()
    client.execute_write = AsyncMock(side_effect=execute_write)
    client.execute_read = AsyncMock(return_value=[])
    return client


def _calls(client, query: str) -> list[dict]:
    return [c.args[1] for c in client.execute_write.call_args_list if c.args[0] == query]


def _assert_links_use_existing_ids(client) -> None:
    links = _calls(client, queries.LINK_MESSAGE_TO_ENTITY)
    assert sorted(link["entity_id"] for link in links) == sorted(EXISTING_IDS.values())
    relations = _calls(client, queries.CREATE_ENTITY_RELATION_BY_ID)
    assert [(r["source_id"], r["target_id"]) for r in relations] == [
        ("existing-maya", "existing-northstar")
    ]


@pytest.mark.asyncio
async def test_message_extraction_links_to_the_merged_entity_id(mock_client):
    memory = ShortTermMemory(mock_client, extractor=_Extractor())
    message = Message(id=uuid4(), role=MessageRole.USER, content="Maya Chen runs Northstar.")

    await memory._extract_and_link_entities(message)

    _assert_links_use_existing_ids(mock_client)


@pytest.mark.asyncio
async def test_session_extraction_links_to_the_merged_entity_id(mock_client):
    mock_client.execute_read = AsyncMock(
        return_value=[{"id": "msg-1", "content": "Maya Chen runs Northstar Robotics."}]
    )
    memory = ShortTermMemory(mock_client, extractor=_Extractor())

    await memory.extract_entities_from_session("session-1")

    _assert_links_use_existing_ids(mock_client)


@pytest.mark.asyncio
async def test_generated_id_is_used_when_the_write_returns_no_row():
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])
    memory = ShortTermMemory(client, extractor=_Extractor())
    message = Message(id=uuid4(), role=MessageRole.USER, content="Maya Chen runs Northstar.")

    await memory._extract_and_link_entities(message)

    merges = [c.args[1] for c in client.execute_write.call_args_list if _is_entity_merge(c.args[0])]
    links = _calls(client, queries.LINK_MESSAGE_TO_ENTITY)
    assert [link["entity_id"] for link in links] == [merge["id"] for merge in merges]
