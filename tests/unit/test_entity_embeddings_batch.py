"""Backfilling entity embeddings: ``generate_entity_embeddings_batch`` and the
``generate_embeddings`` switch on ``extract_entities_from_session``.

``long_term.search_entities`` is a vector search, so an entity stored without an
embedding is invisible to it. Auto-extraction embeds names by default;
``generate_embeddings=False`` defers that, and the batch helper repairs it later.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractionResult
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.memory.short_term import ShortTermMemory

VECTOR = [1.0, 0.0, 0.0]


class _Embedder:
    dimensions = 3

    def __init__(self, fail_names: set[str] | None = None) -> None:
        self.fail_names = fail_names or set()
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.single_calls.append(text)
        if text in self.fail_names:
            raise RuntimeError("simulated embed failure")
        return list(VECTOR)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        if self.fail_names & set(texts):
            raise RuntimeError("simulated embed_batch failure")
        return [list(VECTOR) for _ in texts]


class _Extractor:
    async def extract(self, text: str, **_: object) -> ExtractionResult:
        entity = ExtractedEntity(name="Maya Chen", type="PERSON", confidence=0.9)
        return ExtractionResult(entities=[entity], relations=[], source_text=text)


def _long_term_client(pages: list[list[dict[str, str]]]) -> MagicMock:
    """Serve ``pages`` from GET_ENTITIES_WITHOUT_EMBEDDINGS, one page per read."""
    remaining = list(pages)
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])

    async def execute_read(query: str, params: object = None) -> list[dict[str, object]]:
        if query == queries.COUNT_ENTITIES_WITHOUT_EMBEDDINGS:
            return [{"count": sum(len(page) for page in remaining)}]
        if query == queries.GET_ENTITIES_WITHOUT_EMBEDDINGS:
            return list(remaining.pop(0)) if remaining else []
        return []

    client.execute_read = execute_read
    return client


@pytest.mark.asyncio
async def test_batch_helper_embeds_missing_entities_in_one_call_per_page():
    rows = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]
    client = _long_term_client([rows])
    embedder = _Embedder()

    result = await LongTermMemory(client, embedder=embedder).generate_entity_embeddings_batch(
        batch_size=10
    )

    writes = [call.args for call in client.execute_write.call_args_list]
    assert result["embedded"] == 2
    assert result["failed"] == 0
    assert [(q, p["id"], p["embedding"]) for q, p in writes] == [
        (queries.UPDATE_ENTITY_EMBEDDING, "a", VECTOR),
        (queries.UPDATE_ENTITY_EMBEDDING, "b", VECTOR),
    ]
    assert embedder.batch_calls == [["Alpha", "Beta"]]


@pytest.mark.asyncio
async def test_batch_helper_counts_failures_without_raising_or_looping():
    # The same row is served on every read, as when its write never sticks.
    row = {"id": "a", "name": "Alpha"}
    client = _long_term_client([[row], [row]])
    embedder = _Embedder(fail_names={"Alpha"})

    result = await LongTermMemory(client, embedder=embedder).generate_entity_embeddings_batch()

    assert result["embedded"] == 0
    assert result["failed"] == 1
    client.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_batch_helper_without_an_embedder_does_nothing():
    client = _long_term_client([[{"id": "a", "name": "Alpha"}]])

    result = await LongTermMemory(client, embedder=None).generate_entity_embeddings_batch()

    assert result == {"embedded": 0, "failed": 0, "remaining": 0}
    client.execute_write.assert_not_called()


def _session_client() -> MagicMock:
    """One stored message, served once from the session read."""
    served = {"done": False}
    client = MagicMock()
    client.execute_write = AsyncMock(return_value=[])

    async def execute_read(query: str, params: object = None) -> list[dict[str, str]]:
        if query == queries.GET_ALL_MESSAGES_FOR_SESSION and not served["done"]:
            served["done"] = True
            return [{"id": str(uuid4()), "content": "Maya Chen runs the lab."}]
        return []

    client.execute_read = execute_read
    return client


def _entity_embeddings_written(client: MagicMock) -> dict[str, object]:
    written = {}
    for call in client.execute_write.call_args_list:
        query, params = call.args[0], call.args[1] if len(call.args) > 1 else {}
        if "MERGE (e:Entity {name: $name, type: $type})" in query:
            written[params["name"]] = params["embedding"]
    return written


@pytest.mark.asyncio
async def test_extract_entities_from_session_embeds_names_by_default():
    client = _session_client()
    embedder = _Embedder()
    memory = ShortTermMemory(client, embedder=embedder, extractor=_Extractor())

    stats = await memory.extract_entities_from_session(
        "session-1", skip_existing=False, extract_relations=False
    )

    assert stats["entities_extracted"] == 1
    assert embedder.batch_calls == [["Maya Chen"]]
    assert _entity_embeddings_written(client) == {"Maya Chen": VECTOR}


@pytest.mark.asyncio
async def test_extract_entities_from_session_can_defer_embedding():
    client = _session_client()
    embedder = _Embedder()
    memory = ShortTermMemory(client, embedder=embedder, extractor=_Extractor())

    stats = await memory.extract_entities_from_session(
        "session-1",
        skip_existing=False,
        extract_relations=False,
        generate_embeddings=False,
    )

    assert stats["entities_extracted"] == 1
    assert embedder.batch_calls == []
    assert embedder.single_calls == []
    assert _entity_embeddings_written(client) == {"Maya Chen": None}
