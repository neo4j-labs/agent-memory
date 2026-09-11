"""Enrichment results must reach ``Entity.metadata``.

Background enrichment writes its results as node properties plus an
``enrichment_data`` JSON blob. Before this was covered, ``_parse_entity``
dropped every one of those fields, so ``entity.metadata["wikipedia_url"]`` was
always missing even after a successful enrichment and the enrichment example's
demo 4 could never report success (see the enrichment-example review, F01).

These tests drive the real writer (``BackgroundEnrichmentService``) with a fake
provider and a fake Neo4j client, then feed the captured write back through the
real reader (``LongTermMemory._parse_entity``), so the two halves cannot drift
apart silently.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from neo4j_agent_memory.enrichment.background import BackgroundEnrichmentService
from neo4j_agent_memory.enrichment.base import EnrichmentResult, EnrichmentStatus
from neo4j_agent_memory.memory.long_term import LongTermMemory

ENTITY_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeEnrichmentProvider:
    """Minimal in-memory provider: no network, deterministic payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def supported_entity_types(self) -> list[str]:
        return ["PERSON", "ORGANIZATION", "LOCATION"]

    def supports_entity_type(self, entity_type: str) -> bool:
        return entity_type.upper() in self.supported_entity_types

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> EnrichmentResult:
        self.calls.append((entity_name, entity_type))
        return EnrichmentResult(
            entity_name=entity_name,
            entity_type=entity_type,
            provider=self.name,
            status=EnrichmentStatus.SUCCESS,
            description="Theoretical physicist.",
            summary="Developed the theory of relativity.",
            wikipedia_url="https://en.wikipedia.org/wiki/Albert_Einstein",
            wikidata_id="Q937",
            image_url="https://example.invalid/einstein.jpg",
            source_url="https://en.wikipedia.org/wiki/Albert_Einstein",
            metadata={"birth_year": 1879},
            retrieved_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        )


class CapturingClient:
    """Stand-in for ``Neo4jClient`` that records write calls."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def execute_write(self, query: str, params: dict[str, Any]) -> list[Any]:
        self.writes.append((query, params))
        return []


def _node_from_write(params: dict[str, Any]) -> dict[str, Any]:
    """Build the entity node a later read would return for this write."""
    node: dict[str, Any] = {
        "id": str(ENTITY_ID),
        "name": "Albert Einstein",
        "type": "PERSON",
        "metadata": json.dumps({"aliases": ["Einstein"], "source": "unit-test"}),
        # ``SET e.enriched_at = datetime()`` — the driver hands back a datetime.
        "enriched_at": datetime(2026, 9, 10, 12, 0, 1, tzinfo=timezone.utc),
    }
    for prop in (
        "enriched_description",
        "enrichment_data",
        "wikipedia_url",
        "wikidata_id",
        "image_url",
    ):
        node[prop] = params[prop]
    node["enrichment_provider"] = params["provider"]
    return node


@pytest.fixture
def long_term() -> LongTermMemory:
    return LongTermMemory(client=MagicMock())


@pytest.mark.asyncio
async def test_enrichment_write_then_parse_lands_in_entity_metadata(long_term):
    """The headline fields survive the write → read round trip."""
    client = CapturingClient()
    provider = FakeEnrichmentProvider()
    service = BackgroundEnrichmentService(client, provider)  # type: ignore[arg-type]

    result = await provider.enrich("Albert Einstein", "PERSON")
    await service._update_entity(ENTITY_ID, result)

    assert len(client.writes) == 1
    _, params = client.writes[0]

    entity = long_term._parse_entity(_node_from_write(params))

    metadata = entity.metadata
    assert metadata["enriched_description"] == "Theoretical physicist."
    assert metadata["wikipedia_url"] == "https://en.wikipedia.org/wiki/Albert_Einstein"
    assert metadata["wikidata_id"] == "Q937"
    assert metadata["image_url"] == "https://example.invalid/einstein.jpg"
    assert metadata["enrichment_provider"] == "fake"
    assert metadata["enriched_at"].startswith("2026-09-10T12:00:01")
    # Non-enrichment metadata is preserved, and aliases still get promoted.
    assert metadata["source"] == "unit-test"
    assert entity.aliases == ["Einstein"]


@pytest.mark.asyncio
async def test_enrichment_write_sets_node_properties_cypher_reads():
    """``get_locations`` selects ``e.wikipedia_url``, so the writer must set it."""
    client = CapturingClient()
    provider = FakeEnrichmentProvider()
    service = BackgroundEnrichmentService(client, provider)  # type: ignore[arg-type]

    result = await provider.enrich("Albert Einstein", "PERSON")
    await service._update_entity(ENTITY_ID, result)

    query, params = client.writes[0]
    for prop in ("wikipedia_url", "wikidata_id", "image_url", "enriched_description"):
        assert f"e.{prop}" in query, f"{prop} should be written as a node property"
    assert params["wikipedia_url"] == "https://en.wikipedia.org/wiki/Albert_Einstein"


def test_parse_entity_reads_nested_enrichment_data(long_term):
    """The JSON blob alone is enough — node properties are a fast path, not the source."""
    node = {
        "id": str(uuid4()),
        "name": "Acme Corporation",
        "type": "ORGANIZATION",
        "enrichment_data": json.dumps(
            {
                "description": "A fictional company.",
                "summary": "Makes everything.",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Acme_Corporation",
                "wikidata_id": "Q286041",
                "image_url": "https://example.invalid/acme.png",
                "confidence": 0.9,
                "metadata": {"industry": "conglomerate"},
            }
        ),
    }

    entity = long_term._parse_entity(node)

    assert entity.metadata["enriched_description"] == "A fictional company."
    assert entity.metadata["enriched_summary"] == "Makes everything."
    assert entity.metadata["wikipedia_url"] == "https://en.wikipedia.org/wiki/Acme_Corporation"
    assert entity.metadata["wikidata_id"] == "Q286041"
    assert entity.metadata["image_url"] == "https://example.invalid/acme.png"
    assert entity.metadata["enrichment_confidence"] == 0.9
    assert entity.metadata["enrichment_metadata"] == {"industry": "conglomerate"}


def test_parse_entity_without_enrichment_has_no_enrichment_keys(long_term):
    """An un-enriched entity gains nothing — no ``None`` placeholders."""
    entity = long_term._parse_entity(
        {
            "id": str(uuid4()),
            "name": "Unknown Person",
            "type": "PERSON",
            "metadata": None,
        }
    )

    assert entity.metadata == {}


def test_parse_entity_tolerates_corrupt_enrichment_data(long_term):
    """A malformed blob must not break entity reads."""
    entity = long_term._parse_entity(
        {
            "id": str(uuid4()),
            "name": "Broken",
            "type": "PERSON",
            "enrichment_data": "{not json",
            "enriched_description": "still readable",
        }
    )

    assert entity.metadata["enriched_description"] == "still readable"
    assert "wikipedia_url" not in entity.metadata
