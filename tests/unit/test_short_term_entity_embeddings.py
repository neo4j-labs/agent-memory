"""Entities written by short-term auto-extraction carry name embeddings.

``long_term.search_entities`` is a vector search, so an extracted entity stored
without an embedding can never be found by it.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.config.settings import ResolutionConfig
from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractionResult
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory
from neo4j_agent_memory.resolution.ontology import OntologyResolver


class _Extractor:
    def __init__(self, entities: list[ExtractedEntity]) -> None:
        self._entities = entities

    async def extract(self, text: str, **_: object) -> ExtractionResult:
        return ExtractionResult(entities=self._entities, relations=[], source_text=text)


class _Embedder:
    dimensions = 3

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, text: str) -> list[float]:
        return [float(len(text)), 0.0, 1.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(text)), 0.0, 1.0] for text in texts]


ENTITIES = [
    ExtractedEntity(name="Maya Chen", type="PERSON", confidence=0.9),
    ExtractedEntity(name="Northstar Robotics", type="ORGANIZATION", confidence=0.9),
]


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])
    client.execute_read = AsyncMock(return_value=[])
    return client


def _entity_writes(client) -> dict[str, object]:
    """Map entity name -> embedding parameter for every entity MERGE."""
    writes = {}
    for call in client.execute_write.call_args_list:
        query, params = call.args[0], call.args[1] if len(call.args) > 1 else {}
        if "MERGE (e:Entity {name: $name, type: $type})" in query:
            writes[params["name"]] = params["embedding"]
    return writes


@pytest.mark.asyncio
async def test_auto_extracted_entities_are_embedded_in_one_batch(mock_client):
    embedder = _Embedder()
    memory = ShortTermMemory(mock_client, embedder=embedder, extractor=_Extractor(ENTITIES))
    message = Message(id=uuid4(), role=MessageRole.USER, content="Maya Chen runs Northstar.")

    await memory._extract_and_link_entities(message)

    assert embedder.batches == [["Maya Chen", "Northstar Robotics"]]
    assert _entity_writes(mock_client) == {
        "Maya Chen": [9.0, 0.0, 1.0],
        "Northstar Robotics": [18.0, 0.0, 1.0],
    }


@pytest.mark.asyncio
async def test_add_message_auto_extraction_embeds_entities(mock_client):
    memory = ShortTermMemory(mock_client, embedder=_Embedder(), extractor=_Extractor(ENTITIES))

    await memory.add_message(
        "session-1",
        "user",
        "Maya Chen runs Northstar Robotics.",
        extract_entities=True,
    )

    writes = _entity_writes(mock_client)
    assert set(writes) == {"Maya Chen", "Northstar Robotics"}
    assert all(embedding is not None for embedding in writes.values())


class _FailingEmbedder(_Embedder):
    async def embed(self, text: str) -> list[float]:
        raise AssertionError("embedder called with embeddings disabled")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("embedder called with embeddings disabled")


@pytest.mark.asyncio
async def test_add_message_without_embeddings_never_calls_the_embedder(mock_client):
    memory = ShortTermMemory(
        mock_client, embedder=_FailingEmbedder(), extractor=_Extractor(ENTITIES)
    )

    await memory.add_message(
        "session-1",
        "user",
        "Maya Chen runs Northstar Robotics.",
        extract_entities=True,
        generate_embedding=False,
    )

    assert _entity_writes(mock_client) == {"Maya Chen": None, "Northstar Robotics": None}


@pytest.mark.asyncio
async def test_add_messages_batch_without_embeddings_never_calls_the_embedder(mock_client):
    memory = ShortTermMemory(
        mock_client, embedder=_FailingEmbedder(), extractor=_Extractor(ENTITIES)
    )

    await memory.add_messages_batch(
        "session-1",
        [{"role": "user", "content": "Maya Chen runs Northstar Robotics."}],
        generate_embeddings=False,
        extract_entities=True,
    )

    assert _entity_writes(mock_client) == {"Maya Chen": None, "Northstar Robotics": None}


@pytest.mark.asyncio
async def test_add_messages_batch_embeds_extracted_entities(mock_client):
    embedder = _Embedder()
    memory = ShortTermMemory(mock_client, embedder=embedder, extractor=_Extractor(ENTITIES))

    await memory.add_messages_batch(
        "session-1",
        [{"role": "user", "content": "Maya Chen runs Northstar Robotics."}],
        extract_entities=True,
    )

    assert ["Maya Chen", "Northstar Robotics"] in embedder.batches
    assert _entity_writes(mock_client) == {
        "Maya Chen": [9.0, 0.0, 1.0],
        "Northstar Robotics": [18.0, 0.0, 1.0],
    }


@pytest.mark.asyncio
async def test_extract_entities_from_session_embeds_entities(mock_client):
    mock_client.execute_read = AsyncMock(
        return_value=[{"id": "msg-1", "content": "Maya Chen runs Northstar Robotics."}]
    )
    memory = ShortTermMemory(mock_client, embedder=_Embedder(), extractor=_Extractor(ENTITIES))

    result = await memory.extract_entities_from_session("session-1")

    assert result["entities_extracted"] == 2
    assert _entity_writes(mock_client) == {
        "Maya Chen": [9.0, 0.0, 1.0],
        "Northstar Robotics": [18.0, 0.0, 1.0],
    }


@pytest.mark.asyncio
async def test_without_an_embedder_entities_are_stored_without_embeddings(mock_client):
    memory = ShortTermMemory(mock_client, extractor=_Extractor(ENTITIES))
    message = Message(id=uuid4(), role=MessageRole.USER, content="Maya Chen runs Northstar.")

    await memory._extract_and_link_entities(message)

    assert _entity_writes(mock_client) == {"Maya Chen": None, "Northstar Robotics": None}


def _resolver(client, embedder) -> OntologyResolver:
    return OntologyResolver(client, embedder=embedder, config=ResolutionConfig())


@pytest.mark.asyncio
async def test_ingest_resolution_reuses_the_name_embeddings(mock_client):
    embedder = _Embedder()
    memory = ShortTermMemory(
        mock_client,
        embedder=embedder,
        extractor=_Extractor(ENTITIES),
        resolver=_resolver(mock_client, embedder),
    )
    message = Message(id=uuid4(), role=MessageRole.USER, content="Maya Chen runs Northstar.")

    await memory._extract_and_link_entities(message)

    # One embed_batch for the episode: resolution blocks on the same vectors
    # the entity writes store, instead of embedding the names a second time.
    assert embedder.batches == [["Maya Chen", "Northstar Robotics"]]
    assert _entity_writes(mock_client) == {
        "Maya Chen": [9.0, 0.0, 1.0],
        "Northstar Robotics": [18.0, 0.0, 1.0],
    }


@pytest.mark.asyncio
async def test_ingest_resolution_without_embeddings_never_calls_the_embedder(mock_client):
    # A recording embedder, not _FailingEmbedder: resolution swallows embedder
    # errors so they never gate a write, which would hide a call here.
    embedder = _Embedder()
    memory = ShortTermMemory(
        mock_client,
        embedder=embedder,
        extractor=_Extractor(ENTITIES),
        resolver=_resolver(mock_client, embedder),
    )

    await memory.add_message(
        "session-1",
        "user",
        "Maya Chen runs Northstar Robotics.",
        extract_entities=True,
        generate_embedding=False,
    )

    assert embedder.batches == []
    assert _entity_writes(mock_client) == {"Maya Chen": None, "Northstar Robotics": None}
