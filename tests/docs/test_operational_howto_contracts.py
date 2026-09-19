"""Offline checks for operational how-to snippets against the checked-out SDK.

No model downloads, live providers, or Neo4j service are used. Selected authored
programs run with the real extraction pipeline and API-signature-checked storage
substitutes; the migration helper runs against a deterministic graph substitute.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec
from uuid import uuid4

import pytest

from neo4j_agent_memory.extraction import ExtractionPipeline
from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractedRelation, ExtractionResult
from neo4j_agent_memory.extraction.gliner_extractor import GLiNEREntityExtractor
from neo4j_agent_memory.graph.client import Neo4jClient
from neo4j_agent_memory.memory.buffered import BufferedWriter
from neo4j_agent_memory.memory.long_term import Entity, LongTermMemory
from neo4j_agent_memory.memory.short_term import ShortTermMemory

ROOT = Path(__file__).resolve().parents[2]
HOWTO = ROOT / "docs/modules/ROOT/pages/how-to"
PAGES = (
    "batch-processing",
    "buffered-writes",
    "audit-reasoning",
    "privacy-and-audit",
    "evaluation",
    "consolidation",
    "configure-embedding-provider",
    "configure-llm-provider",
    "bring-your-own-model",
    "migrate-to-providers",
    "migrate-embedding-model",
    "running-without-an-llm",
)


#: `example$` includes resolve two ways at build time: files under the Antora
#: examples dir, and maintained top-level programs mapped in by the
#: `docs/extensions/example-files` extension. Resolving them here means these
#: blocks are compiled as the page renders them, not skipped.
ANTORA_EXAMPLES = ROOT / "docs/modules/ROOT/examples"
EXAMPLE_FILES = json.loads((ROOT / "docs/extensions/example-files.json").read_text())


def resolve_example(relative):
    mapped = EXAMPLE_FILES.get(relative)
    path = ROOT / mapped if mapped else ANTORA_EXAMPLES / relative
    assert path.is_file(), (
        f"include::example${relative}[] resolves to no file ({path}); Antora would "
        "render an unresolved-directive error on the page"
    )
    return path.read_text().rstrip("\n")


def blocks(name):
    found = re.findall(
        r"\[source,python\]\n----\n(.*?)\n----", (HOWTO / f"{name}.adoc").read_text(), re.S
    )
    return [
        re.sub(r"include::example\$(.+?)\[\]", lambda m: resolve_example(m[1]), block)
        for block in found
    ]


def program(name, contains):
    matches = [block for block in blocks(name) if contains in block]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("name", PAGES)
def test_operational_python_blocks_compile(name):
    for block in blocks(name):
        compile(block, f"{name}.adoc", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


class FixtureExtractor:
    async def extract(self, text, **kwargs):
        if "Jane" in text:
            raise RuntimeError("fixture extraction failure")
        return ExtractionResult(
            entities=[
                ExtractedEntity(name="Sara", type="PERSON", start_pos=0, end_pos=4),
                ExtractedEntity(name="Anthem", type="ORGANIZATION", start_pos=10, end_pos=16),
            ],
            relations=[ExtractedRelation(source="Sara", target="Anthem", relation_type="WORKS_AT")],
        )


def storage():
    long_term = create_autospec(LongTermMemory, instance=True)
    long_term.add_entity.side_effect = lambda **kwargs: (
        Entity(id=uuid4(), name=kwargs["name"], type=kwargs["entity_type"]),
        SimpleNamespace(action="none"),
    )
    short_term = create_autospec(ShortTermMemory, instance=True)
    short_term.add_message.side_effect = lambda **_kwargs: SimpleNamespace(id=uuid4())
    long_term.link_entity_to_message.return_value = True
    ids = {}

    async def find_persisted(query, params):
        key = (params["name"], params["type"])
        return [{"id": ids.setdefault(key, str(uuid4()))}]

    query = SimpleNamespace(cypher=AsyncMock(side_effect=find_persisted))
    return SimpleNamespace(long_term=long_term, short_term=short_term, query=query)


async def run_block(text, scope):
    result = eval(
        compile(text, "authored-howto", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), scope
    )
    if result is not None:
        await result


@pytest.mark.asyncio
async def test_batch_program_and_provenance_use_real_pipeline_results(monkeypatch):
    monkeypatch.setattr(GLiNEREntityExtractor, "for_schema", lambda *_args: FixtureExtractor())
    scope = {}
    await run_block(program("batch-processing", "# List of documents"), scope)
    result = scope["result"]
    assert (result.total_items, result.successful_items, result.failed_items) == (3, 2, 1)
    assert result.get_errors()[0][0] == 1
    client = storage()
    scope["client"] = client
    await run_block(program("batch-processing", "async def store_entity"), scope)
    await run_block(program("batch-processing", "# Register the extractor for provenance"), scope)
    assert client.short_term.add_message.await_count == 2
    assert client.long_term.add_entity.await_count == 4
    calls = client.short_term.add_message.await_args_list
    assert [call.kwargs["content"] for call in calls] == [
        scope["documents"][0],
        scope["documents"][2],
    ]
    assert all(call.kwargs["extraction_mode"] == "skip" for call in calls)
    first = client.long_term.link_entity_to_message.await_args_list[0].kwargs
    assert (first["start_pos"], first["end_pos"]) == (0, 4)
    links = client.long_term.link_entity_to_message.await_args_list
    # The substitute storage returns a fresh ID on every write, while the
    # exact graph read returns the existing ID for repeated names.
    assert links[0].kwargs["entity"].id == links[2].kwargs["entity"].id
    assert links[1].kwargs["entity"].id == links[3].kwargs["entity"].id


@pytest.mark.asyncio
async def test_financial_program_uses_stored_ids_for_relationships(monkeypatch):
    monkeypatch.setattr(GLiNEREntityExtractor, "for_schema", lambda *_args: FixtureExtractor())
    client = storage()
    scope = {"client": client}
    await run_block(program("batch-processing", "async def store_entity"), scope)
    await run_block(program("batch-processing", "async def process_financial_documents"), scope)
    assert client.long_term.search_entities.await_count == 0
    relations = client.long_term.add_relationship.await_args_list
    assert sum(call.kwargs["relationship_type"] == "WORKS_AT" for call in relations) == 2
    assert all("attributes" not in call.kwargs for call in relations)
    assert all(call.kwargs["source"] != call.kwargs["target"] for call in relations)


@pytest.mark.asyncio
async def test_flush_finishes_failed_attempt_without_reporting_persistence():
    graph = create_autospec(Neo4jClient, instance=True)
    graph.execute_write.side_effect = RuntimeError("fixture write failed")
    writer = BufferedWriter(graph, write_mode="buffered")
    await writer.submit("RETURN $value", {"value": 1})
    await writer.flush()
    assert writer.pending == 0
    assert len(writer.errors) == 1
    assert str(writer.errors[0].error) == "fixture write failed"
    await writer.stop()


@pytest.mark.asyncio
async def test_documented_entity_backfill_visits_pages_and_validates_vectors():
    scope = {}
    exec(program("migrate-embedding-model", "async def reembed_entities"), scope)
    graph = create_autospec(Neo4jClient, instance=True)
    graph.execute_read.side_effect = [
        [{"id": "a", "text": "Sara"}],
        [{"id": "b", "text": "Anthem"}],
        [],
    ]
    embedder = SimpleNamespace(dimensions=2, embed=AsyncMock(return_value=[[0.2, 0.8]]))
    count = await scope["reembed_entities"](SimpleNamespace(graph=graph), embedder, batch_size=1)
    assert count == 2
    assert [call.args[1]["after_id"] for call in graph.execute_read.await_args_list] == [
        "",
        "a",
        "b",
    ]
    assert graph.execute_write.await_count == 2
    assert embedder.embed.await_args_list[0].args[0] == ["Sara"]
    graph.execute_read.side_effect = [[{"id": "a", "text": "Sara"}]]
    graph.execute_write.reset_mock()
    embedder.embed.return_value = [[0.1]]
    with pytest.raises(ValueError, match="unexpected shape"):
        await scope["reembed_entities"](SimpleNamespace(graph=graph), embedder)
    graph.execute_write.assert_not_awaited()


def test_unknown_local_model_requires_declared_dimensions():
    from neo4j_agent_memory.llm.adapters.sentence_transformers import SentenceTransformersProvider

    with pytest.raises(ValueError, match="Pass dimensions"):
        SentenceTransformersProvider("my-org/unknown-fixture-model", device="cpu")
    provider = SentenceTransformersProvider(
        "my-org/unknown-fixture-model", device="cpu", dimensions=512
    )
    assert provider.dimensions == 512


@pytest.mark.asyncio
async def test_vertex_wrapper_dimension_limit_is_explicit(monkeypatch):
    from neo4j_agent_memory.embeddings import vertex_ai
    from neo4j_agent_memory.llm.adapters.vertex_ai import VertexAIEmbeddingProvider

    constructed = []

    class FixtureVertexEmbedder:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        async def embed(self, text):
            return [0.0] * 768

    monkeypatch.setattr(vertex_ai, "VertexAIEmbedder", FixtureVertexEmbedder)
    provider = VertexAIEmbeddingProvider("vertex_ai/gemini-embedding-001", dimensions=1536)
    vector = await provider.embed_one("fixture")
    assert provider.dimensions == 1536
    assert len(vector) == 768
    assert "output_dimensionality" not in constructed[0]
