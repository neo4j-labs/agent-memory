#!/usr/bin/env python3
"""Entity enrichment: Wikipedia and Diffbot facts attached to your entities.

Five numbered sections, each printing its own banner:

    1. ``WikimediaProvider`` on its own        — network only, no Neo4j
    2. ``DiffbotProvider``                     (optional: DIFFBOT_API_KEY)
    3. Caching in front of a provider chain    — the composition to copy
    4. Background enrichment via ``MemoryClient`` — read back off ``entity.metadata``
    5. Enriching what extraction produced     — your own ``BackgroundEnrichmentService``

Every section is skippable: anything whose prerequisite is missing prints a
``SKIPPED`` line and the tour continues, so the script always reaches
``Enrichment demo complete!``.

Bolt-only. Background enrichment runs client-side against a bolt connection; it
is not available on the hosted NAMS backend, where ``MemoryClient`` reports
``enrichment (not available on NAMS)`` and ignores the config. Sections 4 and 5
therefore pin ``BoltSettings``.

Where the data lands, and how to read it back:
``BackgroundEnrichmentService`` writes the result onto the ``:Entity`` node as
``enriched_description``, ``wikipedia_url``, ``wikidata_id``, ``image_url``,
``enrichment_provider`` and ``enriched_at``, plus the whole result as an
``enrichment_data`` JSON blob. The Python API folds all of that into
``Entity.metadata``, so the read path is::

    entity = await client.long_term.get_entity_by_name("Marie Curie")
    entity.metadata["enriched_description"]
    entity.metadata["wikipedia_url"]

Enrichment is fire-and-forget: ``add_entity()`` returns as soon as the node is
written and the task is queued, so both Neo4j sections poll for the result
instead of sleeping a fixed number of seconds.

Requirements:
    - ``httpx``, the HTTP client every provider uses. ``uv sync`` at the repo
      root installs it; from PyPI: ``pip install neo4j-agent-memory httpx``
    - A Neo4j for sections 4-5: ``make neo4j-start`` (or set NEO4J_URI /
      NEO4J_PASSWORD)
    - ``uv sync --extra sentence-transformers`` (local embeddings, no API key),
      or ``uv sync --extra openai`` plus OPENAI_API_KEY
    - Optional, section 2: DIFFBOT_API_KEY
    - Optional, section 5: the ``[spacy]`` extra plus
      ``uv run python -m spacy download en_core_web_sm``

Configuration comes from ``examples/.env`` — copy ``examples/.env.example``.

Run:
    uv run python examples/enrichment_example.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from _env import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME, OPENAI_API_KEY
from pydantic import SecretStr

from neo4j_agent_memory import (
    Entity,
    ExtractionConfig,
    ExtractorType,
    Neo4jConfig,
    connect,
)
from neo4j_agent_memory.config.settings import (
    BoltSettings,
    EnrichmentConfig,
    EnrichmentProvider,
)
from neo4j_agent_memory.enrichment import (
    BackgroundEnrichmentService,
    CachedEnrichmentProvider,
    CompositeEnrichmentProvider,
    DiffbotProvider,
    EnrichmentResult,
    EnrichmentStatus,
    WikimediaProvider,
)

if TYPE_CHECKING:
    from neo4j_agent_memory import BoltMemoryClient
    from neo4j_agent_memory.enrichment import EnrichmentProvider as EnrichmentProviderProtocol

SESSION_ID = "enrichment-demo-session"
TOTAL_SECTIONS = 5

#: Types we let through to the enrichment queue (``EnrichmentConfig.entity_types``
#: in section 4, the service's own filter in section 5). Both sections feed it
#: entities outside this list so you can see one being declined.
ENRICHED_TYPES = ["PERSON", "ORGANIZATION", "LOCATION"]

#: Section titles shared between the section banners and the skip messages.
SECTION_1_TITLE = "WikimediaProvider on its own (no Neo4j)"
SECTION_2_TITLE = "DiffbotProvider"
SECTION_3_TITLE = "CachedEnrichmentProvider in front of a CompositeEnrichmentProvider"
SECTION_4_TITLE = "Background enrichment through MemoryClient"
SECTION_5_TITLE = "Enriching the entities extraction produced"

#: How long the two Neo4j sections wait for background enrichment. Wikimedia is
#: rate-limited to ~2 req/sec by default, so a handful of entities takes seconds.
ENRICHMENT_DEADLINE_SECONDS = 30.0


# =====================================================================
# Output helpers — each section announces itself so a skipped section is
# obvious in the transcript.
# =====================================================================
def section(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)


def skipped(number: int, title: str, reason: str) -> None:
    section(number, title)
    print(f"SKIPPED — {reason}")


def print_result(label: str, result: EnrichmentResult) -> None:
    """Print an ``EnrichmentResult`` whatever its status."""
    print(f"\n   {label} -> {result.status.value} (provider: {result.provider})")
    if result.status != EnrichmentStatus.SUCCESS:
        if result.error_message:
            print(f"      error: {result.error_message}")
        return
    if result.description:
        print(f"      description: {result.description[:120]}")
    if result.wikipedia_url:
        print(f"      wikipedia:   {result.wikipedia_url}")
    if result.wikidata_id:
        print(f"      wikidata:    {result.wikidata_id}")
    if result.image_url:
        print(f"      image:       {result.image_url[:70]}")
    if result.related_entities:
        print(f"      related:     {result.related_entities[:3]}")


def print_enrichment_metadata(name: str, metadata: dict[str, Any]) -> None:
    """Print the enrichment fields the library surfaces on ``Entity.metadata``."""
    print(f"\n   {name}:")
    print(f"      enriched_at:          {metadata.get('enriched_at')}")
    print(f"      enrichment_provider:  {metadata.get('enrichment_provider')}")
    description = str(metadata.get("enriched_description", ""))
    print(f"      enriched_description: {description[:100]}")
    print(f"      wikipedia_url:        {metadata.get('wikipedia_url')}")
    print(f"      wikidata_id:          {metadata.get('wikidata_id')}")


# =====================================================================
# 1. A provider on its own — no Neo4j, no API key
# =====================================================================
async def direct_provider() -> None:
    section(1, SECTION_1_TITLE)

    # rate_limit is the minimum gap between requests. Wikimedia asks for ~2
    # req/sec at most, and the default (0.5s) already respects that.
    provider = WikimediaProvider(rate_limit=0.5, language="en")
    print(f"Provider {provider.name!r} supports: {', '.join(provider.supported_entity_types)}")

    for name, entity_type in (
        ("Albert Einstein", "PERSON"),
        ("Apple Inc", "ORGANIZATION"),
        ("Paris", "LOCATION"),
    ):
        print_result(f"{name} ({entity_type})", await provider.enrich(name, entity_type))

    # A type the provider declines outright: no request is made at all.
    declined = await provider.enrich("Quarterly review", "MEETING")
    print(f"\n   Quarterly review (MEETING) -> {declined.status.value} (type not supported)")


# =====================================================================
# 2. Diffbot — structured knowledge-graph data behind an API key
# =====================================================================
async def diffbot_provider() -> None:
    api_key = os.getenv("DIFFBOT_API_KEY")
    if not api_key:
        skipped(2, SECTION_2_TITLE, "DIFFBOT_API_KEY not set (see examples/.env.example)")
        return

    section(2, SECTION_2_TITLE)
    # The key comes from the environment and is never printed.
    provider = DiffbotProvider(api_key=api_key, rate_limit=0.2)
    result = await provider.enrich("Microsoft", "ORGANIZATION")
    print_result("Microsoft (ORGANIZATION)", result)
    if result.status == EnrichmentStatus.SUCCESS:
        print(f"      types:      {result.metadata.get('types', [])}")
        print(f"      importance: {result.metadata.get('importance', 'n/a')}")


# =====================================================================
# 3. Caching in front of a provider chain
# =====================================================================
async def caching_and_composite() -> None:
    section(3, SECTION_3_TITLE)

    providers: list[Any] = [WikimediaProvider()]
    diffbot_api_key = os.getenv("DIFFBOT_API_KEY")
    if diffbot_api_key:
        providers.insert(0, DiffbotProvider(api_key=diffbot_api_key))
        print("Chain: Diffbot -> Wikimedia (fallback)")
    else:
        print("Chain: Wikimedia only (set DIFFBOT_API_KEY to put Diffbot in front)")

    # Wrap the *chain*, not one leaf: the cache then fronts every provider in
    # it, so a cache hit skips the whole fallback sequence. Wrapping a single
    # provider and building a second, unwrapped one for the chain — the easy
    # mistake — leaves the chain making live calls it does not need.
    chain = CachedEnrichmentProvider(
        CompositeEnrichmentProvider(providers),
        ttl_hours=24,
    )

    # perf_counter, not time(): it is monotonic and high-resolution, so a
    # sub-millisecond cache hit is still measurable (time() has ~16ms
    # granularity on Windows, which made this division by zero).
    start = time.perf_counter()
    cold_result = await chain.enrich("Elon Musk", "PERSON")
    cold = time.perf_counter() - start

    start = time.perf_counter()
    warm_result = await chain.enrich("Elon Musk", "PERSON")
    warm = time.perf_counter() - start

    print(f"\n   cold call (network): {cold * 1000:8.2f} ms -> {cold_result.status.value}")
    print(f"   warm call (cached):  {warm * 1000:8.2f} ms -> {warm_result.status.value}")
    if warm > 0:
        print(f"   speedup: {cold / warm:.0f}x")
    else:
        print("   speedup: instant (cache hit below the clock's resolution)")

    # Two more names through the same cache. A bare ambiguous name lands on a
    # Wikipedia disambiguation page, which the provider reports as not_found
    # rather than guessing; the fix is to enrich the exact title.
    print_result("Amazon (ORGANIZATION)", await chain.enrich("Amazon", "ORGANIZATION"))
    print_result(
        "Amazon (company) (ORGANIZATION)",
        await chain.enrich("Amazon (company)", "ORGANIZATION"),
    )


# =====================================================================
# 4. Background enrichment through MemoryClient
# =====================================================================
async def wait_for_enrichment(
    client: BoltMemoryClient,
    entities: list[Entity],
    *,
    deadline_seconds: float = ENRICHMENT_DEADLINE_SECONDS,
    poll_interval: float = 0.5,
) -> dict[str, dict[str, Any]]:
    """Poll until every entity carries enrichment metadata, or time runs out.

    Returns ``{entity name: entity.metadata}`` for the entities that finished.
    Reading ``entity.metadata`` is the read path that works regardless of who
    owns the enrichment service — when you own it yourself, its
    ``pending_count`` is the more direct signal (see section 5).
    """
    pending = {entity.name for entity in entities}
    finished: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + deadline_seconds

    while pending and time.monotonic() < deadline:
        for name in sorted(pending):
            stored = await client.long_term.get_entity_by_name(name)
            if stored is not None and stored.metadata.get("enriched_description"):
                finished[name] = stored.metadata
                pending.discard(name)
        if pending:
            await asyncio.sleep(poll_interval)

    return finished


async def background_enrichment(client: BoltMemoryClient) -> None:
    section(4, SECTION_4_TITLE)

    # One enrichable entity plus one the filter declines. `add_entity()` always
    # queues at the default priority, and two equal-priority tasks sitting in
    # the queue together raise `TypeError: '<' not supported between instances
    # of 'EnrichmentTask'` — asyncio.PriorityQueue falls back to comparing the
    # tasks themselves — so one at a time through this path. Section 5 enqueues
    # several entities with distinct priorities, which is the shape that works.
    # The OBJECT row never reaches the provider at all: the type filter stops it
    # before any HTTP call, and the entity is still stored normally.
    to_add = [
        ("Marie Curie", "PERSON", None, "Nobel Prize-winning physicist"),
        ("Ford F-150", "OBJECT", "VEHICLE", "The user's pickup truck"),
    ]

    queued: list[Entity] = []
    declined: list[Entity] = []
    for name, entity_type, subtype, description in to_add:
        # add_entity returns (entity, dedup_result); dedup_result.action is
        # "none", "merged" (folded into a near-identical existing entity) or
        # "flagged" (a SAME_AS edge was written for review).
        entity, dedup_result = await client.long_term.add_entity(
            name=name,
            entity_type=entity_type,
            subtype=subtype,
            description=description,
        )
        print(f"   stored {entity.name} ({entity.full_type}) — dedup: {dedup_result.action}")
        (queued if entity_type in ENRICHED_TYPES else declined).append(entity)

    print(f"\nPolling for background enrichment (up to {ENRICHMENT_DEADLINE_SECONDS:.0f}s)...")
    finished = await wait_for_enrichment(client, queued)

    for entity in queued:
        metadata = finished.get(entity.name)
        if metadata is None:
            print(
                f"\n   {entity.name}: no enrichment yet — either still in flight, "
                "or the provider found no match"
            )
        else:
            print_enrichment_metadata(entity.name, metadata)

    for entity in declined:
        stored = await client.long_term.get_entity_by_name(entity.name)
        enriched = bool(stored and stored.metadata.get("enriched_description"))
        print(
            f"\n   {entity.name} ({entity.full_type}): not enriched — "
            f"enrichment.entity_types={ENRICHED_TYPES} excludes {entity.type}. "
            f"Enrichment metadata present: {enriched}"
        )


# =====================================================================
# 5. Enriching the entities extraction produced
# =====================================================================
async def enrich_extracted_entities(
    client: BoltMemoryClient,
    *,
    provider: EnrichmentProviderProtocol | None = None,
) -> None:
    """Enrich entities that came out of message extraction.

    ``add_message(extract_entities=True)`` writes entity nodes directly rather
    than going through ``long_term.add_entity()``, so those entities are *not*
    queued for enrichment. Running your own ``BackgroundEnrichmentService`` is
    how you close that gap — and it gives you the queue counters
    (``pending_count`` / ``queue_size``) to wait on.

    ``provider`` overrides the enrichment provider; the smoke test passes a
    fake so it can exercise this path with no network access.
    """
    section(5, SECTION_5_TITLE)

    await client.short_term.add_message(
        SESSION_ID,
        "user",
        "I just read a biography of Ada Lovelace and I am flying to Lisbon next week.",
        extract_entities=True,
    )

    # Message-level extraction links its entities with MENTIONS edges, not the
    # EXTRACTED_FROM provenance edges that `long_term.get_entities_from_message()`
    # reads, so this read goes through the read-only Cypher escape hatch. It is
    # scoped to the whole conversation rather than the one message just added, so
    # a re-run still sees what the first run extracted.
    extracted = await client.query.cypher(
        """
        MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
        MATCH (m)-[r:MENTIONS]->(e:Entity)
        RETURN DISTINCT e.id AS id, e.name AS name, e.type AS type,
               coalesce(r.confidence, 1.0) AS confidence
        ORDER BY name
        """,
        {"session_id": SESSION_ID},
    )
    if not extracted:
        print(
            "SKIPPED — no extractor is configured, so the message produced no "
            "entities. Install the [spacy] extra plus "
            "`python -m spacy download en_core_web_sm`, or set OPENAI_API_KEY."
        )
        return

    print(f"Extraction found {len(extracted)} entities in this conversation")

    # A cache in front of the provider keeps repeated names (very common in
    # conversation) down to one HTTP call each.
    service = BackgroundEnrichmentService(
        client.graph,
        CachedEnrichmentProvider(provider or WikimediaProvider(), ttl_hours=24),
        entity_types=ENRICHED_TYPES,
        min_confidence=0.5,
    )
    await service.start()
    try:
        accepted: list[str] = []
        # Descending priorities keep the queue in mention order (higher priority
        # is processed first) and keep every queued task distinct, which the
        # priority queue needs in order to never compare two tasks directly.
        for rank, row in enumerate(extracted):
            was_queued = await service.enqueue(
                UUID(row["id"]),
                row["name"],
                row["type"],
                priority=len(extracted) - rank,
                confidence=row["confidence"],
            )
            verdict = "queued" if was_queued else "declined (type filter or confidence)"
            print(f"   {row['name']} ({row['type']}, {row['confidence']:.2f}): {verdict}")
            if was_queued:
                accepted.append(row["name"])

        # Own the service, wait on its own counters. queue_size drops as soon
        # as a task is picked up; pending_count only clears once the write has
        # landed, so that is the one to wait on.
        deadline = time.monotonic() + ENRICHMENT_DEADLINE_SECONDS
        while service.pending_count and time.monotonic() < deadline:
            print(f"   waiting — {service.pending_count} pending, {service.queue_size} queued")
            await asyncio.sleep(1.0)
        if service.pending_count:
            print(f"   timed out with {service.pending_count} still pending")
    finally:
        # stop() drains in-flight work before returning.
        await service.stop()

    for name in accepted:
        stored = await client.long_term.get_entity_by_name(name)
        if stored is not None and stored.metadata.get("enriched_description"):
            print_enrichment_metadata(name, stored.metadata)
        else:
            print(f"\n   {name}: no enrichment (the provider found no match)")


# =====================================================================
# Configuration
# =====================================================================
def spacy_model_available() -> bool:
    """True when spaCy and the small English model are both importable."""
    return all(importlib.util.find_spec(name) is not None for name in ("spacy", "en_core_web_sm"))


def build_settings() -> BoltSettings | None:
    """Build ``BoltSettings`` with enrichment on, or None if no embedder exists."""
    if OPENAI_API_KEY:
        embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    else:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            print("ERROR: no embedding provider available. Either:")
            print("  1. set OPENAI_API_KEY (see examples/.env.example), or")
            print("  2. uv sync --extra sentence-transformers")
            return None
        embedding_model = os.getenv(
            "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )

    # Section 5 needs an extractor. spaCy is the cheap local option; without it
    # extraction is off and that section reports why it skipped.
    if spacy_model_available():
        extraction = ExtractionConfig(extractor_type=ExtractorType.SPACY)
        extractor_note = "spaCy"
    else:
        extraction = ExtractionConfig(extractor_type=ExtractorType.NONE)
        extractor_note = "off (no spaCy model)"
    print(f"Embeddings: {embedding_model} | entity extraction: {extractor_note}")

    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=SecretStr(NEO4J_PASSWORD),
        ),
        embedding=embedding_model,
        extraction=extraction,
        enrichment=EnrichmentConfig(
            enabled=True,
            providers=[EnrichmentProvider.WIKIMEDIA],
            background_enabled=True,
            cache_results=True,
            entity_types=ENRICHED_TYPES,
            min_confidence=0.5,
        ),
    )


async def neo4j_sections() -> None:
    """Run sections 4 and 5, or report why they were skipped."""
    settings = build_settings()
    if settings is None:
        reason = "no embedding provider (set OPENAI_API_KEY or install sentence-transformers)"
        skipped(4, SECTION_4_TITLE, reason)
        skipped(5, SECTION_5_TITLE, reason)
        return

    # Bolt-only sections, so connect through the typed factory to get a
    # concrete BoltMemoryClient rather than the base-Protocol MemoryClient.
    try:
        client = await connect(settings)
    except Exception as exc:  # noqa: BLE001 — any driver/auth failure is a skip
        reason = f"could not connect to {NEO4J_URI}: {type(exc).__name__}: {exc}"
        skipped(4, SECTION_4_TITLE, reason)
        skipped(5, SECTION_5_TITLE, "no Neo4j connection")
        return

    try:
        await background_enrichment(client)
        await enrich_extracted_entities(client)
    finally:
        await client.close()


async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory — entity enrichment (bolt backend)")
    print("=" * 60)

    # One guard for the whole script: every provider needs httpx, and without
    # it each enrich() call would come back as an ERROR result rather than data.
    if importlib.util.find_spec("httpx") is None:
        reason = "httpx is not installed — pip install httpx"
        for number, title in enumerate(
            (
                SECTION_1_TITLE,
                SECTION_2_TITLE,
                SECTION_3_TITLE,
                SECTION_4_TITLE,
                SECTION_5_TITLE,
            ),
            start=1,
        ):
            skipped(number, title, reason)
    else:
        await direct_provider()
        await diffbot_provider()
        await caching_and_composite()
        await neo4j_sections()

    print("\nEnrichment demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
