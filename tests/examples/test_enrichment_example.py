"""Smoke tests for the enrichment_example.py example.

Two layers:

* static checks — the file compiles, the names it imports resolve, and the
  read path it documents is the one the library actually writes to. These run
  in ``example-tests-quick`` with no Neo4j and no network.
* behavioural checks — an enrichment round-trip against Neo4j with a *fake*
  provider injected through ``MemoryClient(..., enrichment_provider=...)``, so
  nothing in CI depends on Wikipedia being up. These cover what the static
  assertions cannot: that ``add_entity()`` queues the entity, that the result
  lands on the node, and that it comes back out through ``entity.metadata``
  (which it silently did not before the 0.6 parser fix — the example reported
  "not enriched" on every successful run).

Demos 1-3 of the example talk to Wikipedia and are deliberately *not* exercised
here.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_FILE = EXAMPLES_DIR / "enrichment_example.py"


# =============================================================================
# Static checks
# =============================================================================


@pytest.mark.syntax
class TestEnrichmentExampleStructure:
    def test_example_file_exists(self):
        assert EXAMPLE_FILE.exists(), f"Missing: {EXAMPLE_FILE}"

    def test_example_compiles(self):
        ast.parse(EXAMPLE_FILE.read_text(encoding="utf-8"))


@pytest.mark.imports
class TestEnrichmentExampleImports:
    """Every library name the example imports must resolve."""

    def test_required_imports_resolve(self):
        from neo4j_agent_memory import (  # noqa: F401
            Entity,
            ExtractionConfig,
            ExtractorType,
            Neo4jConfig,
            connect,
        )
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            BoltSettings,
            EnrichmentConfig,
            EnrichmentProvider,
        )
        from neo4j_agent_memory.enrichment import (  # noqa: F401
            BackgroundEnrichmentService,
            CachedEnrichmentProvider,
            CompositeEnrichmentProvider,
            DiffbotProvider,
            EnrichmentResult,
            EnrichmentStatus,
            WikimediaProvider,
        )


@pytest.mark.syntax
class TestEnrichmentExampleContent:
    """Guards against the drift that has broken this example before.

    History: it called a phantom ``long_term.get_entity()``, then read
    ``entity.enriched_description`` / ``entity.wikipedia_url`` — attributes no
    code path has ever produced — so demo 4 reported failure even when
    enrichment had succeeded.
    """

    @staticmethod
    def source() -> str:
        return EXAMPLE_FILE.read_text(encoding="utf-8")

    def test_reads_enrichment_from_entity_metadata(self):
        source = self.source()
        assert 'metadata.get("enriched_description")' in source, (
            "enrichment is surfaced through Entity.metadata, not as attributes"
        )

    def test_does_not_read_phantom_entity_attributes(self):
        source = self.source()
        for phantom in (
            'getattr(updated, "enriched_description"',
            "entity.enriched_description",
            "entity.wikipedia_url",
            "updated.wikipedia_url",
        ):
            assert phantom not in source, f"Entity has no such attribute: {phantom}"

    def test_does_not_call_phantom_get_entity(self):
        assert ".long_term.get_entity(" not in self.source(), (
            "long_term.get_entity() does not exist; use get_entity_by_name()"
        )

    def test_pins_the_bolt_backend(self):
        source = self.source()
        assert "BoltSettings(" in source, (
            "enrichment is bolt-only; the example must pin the backend"
        )

    def test_polls_instead_of_sleeping_a_fixed_time(self):
        source = self.source()
        assert "asyncio.sleep(3)" not in source, "do not guess how long enrichment takes"
        assert "pending_count" in source, "wait on the service's own queue counters"
        assert "queue_size" in source

    def test_cache_timing_uses_a_monotonic_clock_and_guards_the_ratio(self):
        source = self.source()
        assert "time.perf_counter()" in source
        assert "time.time()" not in source, "time.time() is coarse enough to divide by zero"
        assert "if warm > 0:" in source, "guard the speedup division"

    def test_demonstrates_the_enrichment_building_blocks(self):
        source = self.source()
        for name in (
            "BackgroundEnrichmentService(",
            "CachedEnrichmentProvider(",
            "CompositeEnrichmentProvider(",
            "EnrichmentConfig(",
            "EnrichmentProvider.WIKIMEDIA",
        ):
            assert name in source, f"example should demonstrate {name}"

    def test_uses_tuple_return_from_add_entity(self):
        source = self.source()
        assert "add_entity(" in source
        assert "dedup_result" in source, (
            "add_entity returns (entity, dedup_result) — use the second element"
        )


# =============================================================================
# Behavioural checks: enrichment round-trip with a fake provider
# =============================================================================


class FakeEnrichmentProvider:
    """Offline stand-in for WikimediaProvider.

    Satisfies ``neo4j_agent_memory.enrichment.EnrichmentProvider`` and records
    how many times it was asked, so cache behaviour is observable.
    """

    SUPPORTED = ("PERSON", "ORGANIZATION", "LOCATION")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self.SUPPORTED)

    def supports_entity_type(self, entity_type: str) -> bool:
        return entity_type.upper() in self.SUPPORTED

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> Any:
        from neo4j_agent_memory.enrichment import EnrichmentResult, EnrichmentStatus

        self.calls.append((entity_name, entity_type))
        slug = entity_name.replace(" ", "_")
        return EnrichmentResult(
            entity_name=entity_name,
            entity_type=entity_type,
            provider=self.name,
            status=EnrichmentStatus.SUCCESS,
            description=f"Fake encyclopedia entry for {entity_name}",
            wikipedia_url=f"https://en.wikipedia.org/wiki/{slug}",
            wikidata_id="Q42",
            image_url=f"https://upload.example.invalid/{slug}.jpg",
            metadata={"source": "fake"},
        )


def _vector_dimensions(connection: dict) -> int:
    """Dimensions of the vector indexes already in the database, else 1536.

    The example-test database is shared; matching whatever the indexes were
    created with keeps this test from tripping the dimension guard.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        connection["uri"], auth=(connection["username"], connection["password"])
    )
    try:
        with driver.session() as session:
            for record in session.run(
                "SHOW INDEXES YIELD name, type, options WHERE type = 'VECTOR' "
                "RETURN options.indexConfig['vector.dimensions'] AS dimensions"
            ):
                dimensions = record["dimensions"]
                if isinstance(dimensions, int):
                    return dimensions
    finally:
        driver.close()
    return 1536


@pytest.fixture
def fake_provider() -> FakeEnrichmentProvider:
    return FakeEnrichmentProvider()


@pytest.fixture
async def enrichment_client(neo4j_connection, fake_provider):
    """A connected client with enrichment on and the fake provider injected."""
    from neo4j_agent_memory import MemoryClient, Neo4jConfig
    from neo4j_agent_memory.config.settings import BoltSettings, EnrichmentConfig
    from tests.examples.conftest import MockEmbedder

    example = _load_example_module()
    settings = BoltSettings(
        neo4j=Neo4jConfig(
            uri=neo4j_connection["uri"],
            username=neo4j_connection["username"],
            password=SecretStr(neo4j_connection["password"]),
        ),
        enrichment=EnrichmentConfig(
            enabled=True,
            background_enabled=True,
            entity_types=example.ENRICHED_TYPES,
            min_confidence=0.5,
        ),
    )

    client = MemoryClient(
        settings,
        embedder=MockEmbedder(dimensions=_vector_dimensions(neo4j_connection)),
        enrichment_provider=fake_provider,
    )
    try:
        await client.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Could not connect to Neo4j: {exc}")

    created: list[str] = []
    try:
        yield client, created
    finally:
        if created:
            await client.graph.execute_write(
                "MATCH (e:Entity) WHERE e.id IN $ids DETACH DELETE e",
                {"ids": created},
            )
        await client.close()


def _load_example_module():
    """Import enrichment_example.py so its helpers can be exercised directly."""
    cached = sys.modules.get("enrichment_example")
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location("enrichment_example", EXAMPLE_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["enrichment_example"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("enrichment_example", None)
        raise
    return module


@pytest.mark.requires_neo4j
class TestEnrichmentRoundTrip:
    """The path the example's section 4 documents, with no network access."""

    @pytest.mark.asyncio
    async def test_add_entity_enrichment_surfaces_on_entity_metadata(
        self, enrichment_client, fake_provider
    ):
        client, created = enrichment_client
        example = _load_example_module()

        name = f"Enrichment Example Person {uuid4().hex[:8]}"
        entity, dedup_result = await client.long_term.add_entity(
            name=name,
            entity_type="PERSON",
            description="Subject of the enrichment smoke test",
        )
        created.append(str(entity.id))
        assert dedup_result.action in {"none", "merged", "flagged"}

        # The example's own poll helper — this is the read path under test.
        finished = await example.wait_for_enrichment(
            client, [entity], deadline_seconds=20.0, poll_interval=0.2
        )
        assert name in finished, "background enrichment never landed"

        metadata = finished[name]
        # These keys are what BackgroundEnrichmentService._update_entity writes;
        # the test fails if either side renames them.
        assert metadata["enriched_description"] == f"Fake encyclopedia entry for {name}"
        assert metadata["wikipedia_url"].startswith("https://en.wikipedia.org/wiki/")
        assert metadata["wikidata_id"] == "Q42"
        assert metadata["enrichment_provider"] == "fake"
        assert metadata["enriched_at"]
        assert (name, "PERSON") in fake_provider.calls

    @pytest.mark.asyncio
    async def test_entity_type_outside_the_filter_is_never_enriched(
        self, enrichment_client, fake_provider
    ):
        client, created = enrichment_client

        name = f"Enrichment Example Truck {uuid4().hex[:8]}"
        entity, _ = await client.long_term.add_entity(
            name=name,
            entity_type="OBJECT",
            subtype="VEHICLE",
            description="Outside enrichment.entity_types",
        )
        created.append(str(entity.id))

        # Nothing to wait for: the queue declines OBJECT before any call.
        await asyncio.sleep(1.0)
        stored = await client.long_term.get_entity_by_name(name)
        assert stored is not None
        assert "enriched_description" not in stored.metadata
        assert all(called_name != name for called_name, _ in fake_provider.calls)


@pytest.mark.requires_neo4j
class TestBackgroundEnrichmentServiceDirectly:
    """The path the example's section 5 documents: own the service."""

    @pytest.mark.asyncio
    async def test_cached_provider_serves_two_entities_with_one_call(
        self, enrichment_client, fake_provider
    ):
        from neo4j_agent_memory.enrichment import (
            BackgroundEnrichmentService,
            CachedEnrichmentProvider,
        )

        client, created = enrichment_client
        example = _load_example_module()

        # Same name, two nodes: the cache should collapse them to one call.
        name = f"Enrichment Example City {uuid4().hex[:8]}"
        for _ in range(2):
            await client.graph.execute_write(
                "CREATE (e:Entity {id: $id, name: $name, type: 'LOCATION', "
                "created_at: datetime()})",
                {"id": str(uuid4()), "name": name},
            )
        rows = await client.query.cypher(
            "MATCH (e:Entity {name: $name}) RETURN e.id AS id", {"name": name}
        )
        ids = [UUID(row["id"]) for row in rows]
        created.extend(str(i) for i in ids)
        assert len(ids) == 2

        service = BackgroundEnrichmentService(
            client.graph,
            CachedEnrichmentProvider(fake_provider, ttl_hours=1),
            entity_types=example.ENRICHED_TYPES,
            min_confidence=0.5,
        )
        await service.start()
        try:
            # Distinct priorities: the queue compares the tasks themselves when
            # priorities tie (see the xfail below).
            for rank, entity_id in enumerate(ids):
                assert await service.enqueue(entity_id, name, "LOCATION", priority=len(ids) - rank)

            deadline = time.monotonic() + 20.0
            while service.pending_count and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            assert service.pending_count == 0, "enrichment queue never drained"
            assert service.queue_size == 0
        finally:
            await service.stop()
            assert not service.is_running

        enriched = await client.query.cypher(
            "MATCH (e:Entity {name: $name}) "
            "RETURN e.enriched_description AS description, e.wikipedia_url AS url",
            {"name": name},
        )
        assert len(enriched) == 2
        assert all(row["description"] and row["url"] for row in enriched)
        # One network call for two entities — that is what the cache buys.
        assert len(fake_provider.calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason=(
            "library bug: EnrichmentTask is not orderable, so asyncio.PriorityQueue "
            "raises TypeError when two equal-priority tasks are queued together. "
            "add_entity() always enqueues at the default priority, so this hits any "
            "caller adding two enrichable entities in a row. Remove the xfail once "
            "EnrichmentTask orders (or the queue carries a tiebreaker)."
        ),
        strict=False,
    )
    async def test_two_default_priority_tasks_can_share_the_queue(
        self, enrichment_client, fake_provider
    ):
        from neo4j_agent_memory.enrichment import BackgroundEnrichmentService

        client, _created = enrichment_client

        # A stalled provider keeps the first task in flight so the second one
        # has to be pushed onto a non-empty heap.
        class SlowProvider(FakeEnrichmentProvider):
            async def enrich(self, entity_name: str, entity_type: str, **kwargs: Any) -> Any:
                await asyncio.sleep(0.5)
                return await super().enrich(entity_name, entity_type, **kwargs)

        service = BackgroundEnrichmentService(client.graph, SlowProvider())
        await service.start()
        try:
            for index in range(3):
                await service.enqueue(uuid4(), f"Queued Entity {index}", "PERSON")
        finally:
            await service.stop(timeout=5.0)
