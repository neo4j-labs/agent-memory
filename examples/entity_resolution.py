#!/usr/bin/env python3
"""Entity resolution strategies in `neo4j-agent-memory` — no Neo4j, no API key.

Resolution is the step that decides whether an incoming entity reference names
something memory already knows about. Six numbered sections, each printing its
own banner, against one fixed set of six "already stored" entities:

    1. Exact matching — normalized, case-insensitive
    2. Fuzzy matching — typo tolerance via rapidfuzz        (needs [fuzzy])
    3. Composite resolution — exact -> fuzzy -> semantic, with type filtering
    4. The type-aware guarantee — a PERSON never merges into a LOCATION
    5. Semantic matching — the abbreviations fuzzy cannot reach   (needs an embedder)
    6. Batch resolution — cross-entity deduplication in one pass

Nothing here touches a database: the resolvers work on an in-memory list of
names, which is why this is the one example that runs with no backend at all.
The closing "Where this plugs in" block shows the same resolvers configured on a
real client, where `client.long_term.add_entity()` runs them on every write.

Every section is skippable: a section whose prerequisite is missing prints a
`SKIPPED` line and the tour continues, so the script always reaches
`Demo complete!`.

Requirements:
    - sections 1, 3, 4 and 6: nothing, pure Python
    - section 2: `uv sync --extra fuzzy` (rapidfuzz)
    - section 5: an embedder — `uv sync --extra sentence-transformers` for the
      local model (free, offline once cached) or `uv sync --extra openai` plus
      OPENAI_API_KEY

Configuration comes from `examples/.env` — copy `examples/.env.example`.
Thresholds are tunable from the environment: FUZZY_THRESHOLD (default 0.85, the
library default) and SEMANTIC_THRESHOLD (default 0.8). Both defaults below were
measured against `all-MiniLM-L6-v2`; cosine scores are model-dependent, so
expect to re-tune SEMANTIC_THRESHOLD when you change embedding models.

Docs: `how-to/deduplication.adoc` (the database-backed SAME_AS review loop) and
`explanation/resolution-deduplication.adoc` (why resolution and deduplication
are two different steps).

Run:
    uv run python examples/entity_resolution.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from typing import cast

import _env  # noqa: F401  — loads examples/.env (OPENAI_API_KEY, EMBEDDING_MODEL, ...)

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import Embedder, adapt_to_legacy_embedder
from neo4j_agent_memory.resolution import (
    CompositeResolver,
    ExactMatchResolver,
    FuzzyMatchResolver,
    ResolvedEntity,
)

TOTAL_SECTIONS = 6

FUZZY_THRESHOLD = float(os.getenv("FUZZY_THRESHOLD", "0.85"))
SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.8"))

# The entities memory already holds. Resolution is always relative to a known
# world, so both the names and their POLE+O types are needed — the types are
# what section 4's guarantee is built on.
EXISTING: list[tuple[str, str]] = [
    ("John Smith", "PERSON"),
    ("Acme Corporation", "ORGANIZATION"),
    ("New York City", "LOCATION"),
    ("Microsoft", "ORGANIZATION"),
    ("San Francisco", "LOCATION"),
    ("Paris", "LOCATION"),  # a city — section 6 also resolves a *person* named Paris
]
EXISTING_NAMES = [name for name, _ in EXISTING]
EXISTING_TYPES = dict(EXISTING)


# =====================================================================
# Output helpers — each section announces itself so a skipped section is
# obvious in the transcript.
# =====================================================================
def section(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)


def skipped(reason: str) -> None:
    print(f"   SKIPPED: {reason}")


def report(name: str, entity_type: str, result: ResolvedEntity) -> None:
    """Print one resolution outcome.

    A resolution matched when the canonical name the resolver chose is one of
    the names already stored. `CompositeResolver` also reports `match_type
    == "none"` for a miss, but the single-strategy resolvers tag every result
    with their own strategy name (`"fuzzy"`, `"semantic"`) whether or not
    anything matched, so the canonical name is the reliable signal.
    """
    matched = result.canonical_name in EXISTING_NAMES and result.match_type != "none"
    if not matched:
        print(f"   {name!r} ({entity_type}) -> new entity [match_type={result.match_type}]")
        return
    print(
        f"   {name!r} ({entity_type}) -> {result.canonical_name!r} "
        f"[match_type={result.match_type}, confidence={result.confidence:.2f}]"
    )


# =====================================================================
# Embedder for the optional semantic stage
# =====================================================================
def build_embedder() -> tuple[Embedder, str] | None:
    """Resolve an embedder for section 5, or None when none is available.

    The local sentence-transformers model is preferred over OpenAI here (unlike
    the other examples): it costs nothing, needs no key, and the cosine
    thresholds in this file were measured against it.
    """
    from neo4j_agent_memory.llm import from_provider

    if importlib.util.find_spec("sentence_transformers") is not None:
        model = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    elif os.getenv("OPENAI_API_KEY"):
        model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    else:
        return None

    # `from_provider` returns the new EmbeddingProvider Protocol; the resolution
    # layer still takes the older single-text `Embedder` shape, and
    # `adapt_to_legacy_embedder` bridges the two.
    provider = from_provider(model, kind="embedding")
    return cast(Embedder, adapt_to_legacy_embedder(provider)), model


# =====================================================================
# 1. Exact matching
# =====================================================================
async def demo_exact() -> None:
    section(1, "Exact matching — ExactMatchResolver")

    resolver = ExactMatchResolver()  # case_sensitive=False by default

    cases = [
        ("John Smith", "PERSON"),  # exact
        ("john smith", "PERSON"),  # case-insensitive, canonicalized to the stored name
        ("John  Smith", "PERSON"),  # whitespace-normalized
        ("Jon Smith", "PERSON"),  # typo — out of reach for exact matching
        ("Microsoft", "ORGANIZATION"),  # exact
    ]

    for name, entity_type in cases:
        result = await resolver.resolve(name, entity_type, existing_entities=EXISTING_NAMES)
        report(name, entity_type, result)


# =====================================================================
# 2. Fuzzy matching
# =====================================================================
async def demo_fuzzy() -> None:
    section(2, f"Fuzzy matching — FuzzyMatchResolver(threshold={FUZZY_THRESHOLD})")

    # The constructor never raises when rapidfuzz is missing; it records the
    # fact on `is_available` instead, so ask rather than catching.
    resolver = FuzzyMatchResolver(threshold=FUZZY_THRESHOLD)
    if not resolver.is_available:
        skipped("rapidfuzz not installed — uv sync --extra fuzzy")
        return

    cases = [
        ("Jon Smith", "PERSON"),  # missing letter
        ("Jhon Smith", "PERSON"),  # transposition
        ("Microsft", "ORGANIZATION"),  # missing letter
        ("ACME CORPORATION", "ORGANIZATION"),  # case only
        ("Acme Corp", "ORGANIZATION"),  # abbreviation — too far for edit distance
        ("New York", "LOCATION"),  # prefix — also too far
        ("Random Company", "ORGANIZATION"),  # genuinely new
    ]

    for name, entity_type in cases:
        result = await resolver.resolve(name, entity_type, existing_entities=EXISTING_NAMES)
        report(name, entity_type, result)

    print(
        "   note: 'Acme Corp' and 'New York' score below the threshold — string distance"
        "\n         cannot see that they mean the same thing. Section 5 can."
    )
    print(
        "   note: a miss above still reads match_type=fuzzy — a single-strategy resolver"
        "\n         stamps every result with its own strategy. CompositeResolver (section 3)"
        "\n         is the one that reports match_type='none' for a miss."
    )


# =====================================================================
# 3. Composite resolution
# =====================================================================
async def demo_composite(resolver: CompositeResolver, fuzzy_available: bool) -> None:
    section(3, f"Composite resolution — exact -> fuzzy(threshold={FUZZY_THRESHOLD}) -> semantic")

    print(f"   fuzzy stage: {'enabled' if fuzzy_available else 'unavailable (exact only)'}")
    print("   semantic stage: off in this section (no embedder passed) — see section 5")

    cases = [
        ("john smith", "PERSON"),  # exact stage wins
        ("Jhon Smith", "PERSON"),  # falls through to the fuzzy stage
        ("Totally Different", "PERSON"),  # no stage matches
    ]

    for name, entity_type in cases:
        result = await resolver.resolve(
            name,
            entity_type,
            existing_entities=EXISTING_NAMES,
            # Pass the type map: type filtering only engages when the resolver
            # knows what the stored entities are (see section 4).
            existing_entity_types=EXISTING_TYPES,
        )
        report(name, entity_type, result)


# =====================================================================
# 4. The type-aware guarantee
# =====================================================================
async def demo_type_awareness() -> None:
    section(4, "The type-aware guarantee — type_strict")

    # The football club, not the city: two different things whose names are one
    # fuzzy edit apart. This is the merge a type-blind resolver gets wrong.
    incoming, incoming_type = "New York City FC", "ORGANIZATION"
    print(f"   stored: 'New York City' (LOCATION). Incoming: {incoming!r} ({incoming_type}).")

    strict = CompositeResolver(fuzzy_threshold=FUZZY_THRESHOLD)  # type_strict=True default
    loose = CompositeResolver(fuzzy_threshold=FUZZY_THRESHOLD, type_strict=False)

    for label, resolver in (("type_strict=True ", strict), ("type_strict=False", loose)):
        result = await resolver.resolve(
            incoming,
            incoming_type,
            existing_entities=EXISTING_NAMES,
            existing_entity_types=EXISTING_TYPES,
        )
        if result.canonical_name == incoming:
            print(f"   {label}: kept separate -> new {incoming_type} {incoming!r}")
        else:
            stored_type = EXISTING_TYPES.get(result.canonical_name, "?")
            print(
                f"   {label}: merged into the stored {stored_type} "
                f"{result.canonical_name!r} [match_type={result.match_type}] <- wrong"
            )

    print("   type filtering only engages when existing_entity_types is supplied:")
    blind = await strict.resolve(incoming, incoming_type, existing_entities=EXISTING_NAMES)
    print(
        f"   no type map: {incoming!r} -> {blind.canonical_name!r} "
        f"[match_type={blind.match_type}] — the resolver cannot see the type clash"
    )


# =====================================================================
# 5. Semantic matching (optional — needs an embedder)
# =====================================================================
async def demo_semantic() -> None:
    section(5, f"Semantic matching — CompositeResolver(embedder=..., {SEMANTIC_THRESHOLD})")

    built = build_embedder()
    if built is None:
        skipped(
            "no embedder available — uv sync --extra sentence-transformers, "
            "or set OPENAI_API_KEY with uv sync --extra openai"
        )
        return
    embedder, model = built
    print(f"   embeddings: {model}")

    resolver = CompositeResolver(
        embedder=embedder,
        fuzzy_threshold=FUZZY_THRESHOLD,
        semantic_threshold=SEMANTIC_THRESHOLD,
    )

    cases = [
        ("Acme Corp", "ORGANIZATION"),  # fuzzy missed this in section 2
        ("New York", "LOCATION"),  # and this
        ("NYC", "LOCATION"),  # no shared characters at all
        ("IBM", "ORGANIZATION"),  # a different company — must stay separate
    ]

    try:
        for name, entity_type in cases:
            result = await resolver.resolve(
                name,
                entity_type,
                existing_entities=EXISTING_NAMES,
                existing_entity_types=EXISTING_TYPES,
            )
            report(name, entity_type, result)
    except (EmbeddingError, OSError) as exc:
        # Only reachable when the model cannot be loaded (first run offline, or
        # a rejected API key). Resolution errors are not caught.
        skipped(f"embedding backend unavailable: {exc}")


# =====================================================================
# 6. Batch resolution
# =====================================================================
async def demo_batch(resolver: CompositeResolver) -> None:
    section(6, "Batch resolution — resolve_batch() deduplicates within the batch")

    batch = [
        ("John Smith", "PERSON"),
        ("Jane Doe", "PERSON"),
        ("john smith", "PERSON"),  # duplicate of the first, different case
        ("Jhon Smith", "PERSON"),  # duplicate of the first, typo
        ("Jane Doe", "PERSON"),  # exact duplicate
        ("Paris", "LOCATION"),
        ("Paris", "PERSON"),  # same string, different type — stays separate
    ]

    print(f"   input ({len(batch)}): {[f'{n} ({t})' for n, t in batch]}")

    results = await resolver.resolve_batch(batch)

    unique = {(r.canonical_name, r.entity_type) for r in results}
    print(f"   {len(batch)} references -> {len(unique)} entities: {sorted(unique)}")

    for (name, _), result in zip(batch, results):
        if name != result.canonical_name:
            print(f"   {name!r} folded into {result.canonical_name!r} [{result.match_type}]")

    print(
        "   resolve_batch() keys its deduplication on (name, type), so the two "
        "'Paris' references stay apart with no type map passed in."
    )


async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory — entity resolution strategies")
    print("=" * 60)
    print(f"\nStored entities ({len(EXISTING)}):")
    for name, entity_type in EXISTING:
        print(f"   {name} ({entity_type})")

    # One composite instance for sections 3 and 6 so the two sections are
    # directly comparable — thresholds differing between them would invite the
    # wrong conclusion about resolve_batch().
    composite = CompositeResolver(fuzzy_threshold=FUZZY_THRESHOLD)
    fuzzy_available = FuzzyMatchResolver().is_available

    await demo_exact()
    await demo_fuzzy()
    await demo_composite(composite, fuzzy_available)
    await demo_type_awareness()
    await demo_semantic()
    await demo_batch(composite)

    # =================================================================
    # Where this plugs in
    # =================================================================
    # Nothing above touched a database. On a real client the resolver is
    # configured once and runs inside long-term memory on every write:
    #
    #     from neo4j_agent_memory import (
    #         BoltSettings, Neo4jConfig, ResolutionConfig, ResolverStrategy, connect,
    #     )
    #
    #     settings = BoltSettings(
    #         neo4j=Neo4jConfig(uri=..., username=..., password=...),
    #         embedding="sentence-transformers/all-MiniLM-L6-v2",
    #         resolution=ResolutionConfig(
    #             strategy=ResolverStrategy.COMPOSITE,   # exact -> fuzzy -> semantic
    #             fuzzy_threshold=0.85,
    #             semantic_threshold=0.8,
    #         ),
    #     )
    #     client = await connect(settings)
    #
    #     # resolve=True (the default) sends the name through that resolver; the
    #     # DeduplicationResult says whether it merged, flagged, or created.
    #     entity, dedup = await client.long_term.add_entity("Jhon Smith", "PERSON")
    #
    #     # Pairs too close to ignore but too far to merge become pending
    #     # SAME_AS edges for a human to confirm or reject:
    #     for left, right, score in await client.long_term.find_potential_duplicates():
    #         await client.long_term.review_duplicate(left.id, right.id, confirm=True)
    #
    # To hand-build the resolver instead of configuring one, pass it to connect():
    #
    #     client = await connect(settings, resolver=CompositeResolver(embedder=embedder))
    print("\nWhere this plugs in")
    print("-" * 60)
    print("   ResolutionConfig(strategy=ResolverStrategy.COMPOSITE, ...) on your settings,")
    print("   or connect(settings, resolver=CompositeResolver(...)) to pass one in. Then")
    print("   client.long_term.add_entity() resolves every name before it is written, and")
    print("   find_potential_duplicates() / review_duplicate() handle the flagged pairs.")
    print("   Docs: how-to/deduplication.adoc, explanation/resolution-deduplication.adoc")

    print("\nDemo complete!")


if __name__ == "__main__":
    asyncio.run(main())
