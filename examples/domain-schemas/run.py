#!/usr/bin/env python3
"""Domain-schema entity extraction with neo4j-agent-memory.

One runner, eight built-in GLiNER domain schemas. Each schema ships a synthetic
corpus under ``samples/`` and the runner does the same five things for all of
them:

1. Build a GLiNER extractor from ``ExtractionConfig`` (the same path
   ``MemorySettings.extraction`` takes internally, so it transfers to apps).
2. Extract entities per document and print them grouped by POLE+O type.
3. Summarise the run with per-domain highlight sections.
4. Optionally demo GLiREL relations, native batch inference or streaming
   extraction (``--relations`` / ``--batch`` / ``--streaming``).
5. Optionally store the result in Neo4j with provenance, relationships and a
   read-back (``--store``, automatic when ``NEO4J_URI`` is set).

Usage::

    python run.py --list
    python run.py --schema medical
    python run.py --schema news --relations --store
    python run.py --schema podcast --device mps --threshold 0.5

Everything runs locally: GLiNER for extraction, sentence-transformers for the
embeddings used by the storage step. No LLM and no API key are involved. The
GLiNER model (~500 MB) downloads once on first use.

All sample documents are synthetic. Quotes, figures and events are invented for
demonstration; nothing in them is attributable to any real person or company.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

# ``samples`` is the package next to this file. Running ``python run.py`` puts
# that directory on ``sys.path`` automatically; importing this module from
# elsewhere (a test, say) needs the directory added first.
from samples import BATCH, RELATIONS, STREAMING, Highlight, SampleSet, get_sample, list_sample_names

from neo4j_agent_memory import ExtractionConfig, MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractorType
from neo4j_agent_memory.extraction import (
    ExtractedEntity,
    ExtractedRelation,
    GLiNEREntityExtractor,
    GLiNERWithRelationsExtractor,
    create_gliner_extractor,
    create_streaming_extractor,
    get_schema,
    is_gliner_available,
    is_glirel_available,
    list_schemas,
)
from neo4j_agent_memory.memory.long_term import Entity, LongTermMemory

EXAMPLE_DIR = Path(__file__).resolve().parent
EXTRACTOR_NAME = "GLiNEREntityExtractor"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_STORE_LIMIT = 30
RULE = "=" * 70

SYNTHETIC_BANNER = (
    "All sample documents are synthetic: quotes, figures and events are invented\n"
    "for demonstration. Nothing here is attributable to a real person or company."
)

RELATIONS_NOTE = """
    Relationships can be extracted two ways, neither of which needs this
    runner's entity pass to change:

      * GLiREL, locally and with no LLM call — `python run.py --schema {schema}
        --relations` (install the optional `glirel` package first).
      * The LLM extractor stage, when you need free-form relation types —
        `ExtractionConfig(extractor_type=ExtractorType.PIPELINE,
        enable_llm_fallback=True)`.

    With `--store`, whatever relations were found are written as
    `(:Entity)-[:RELATED_TO {{relation_type}}]->(:Entity)` edges.
"""


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def load_env() -> None:
    """Load ``examples/.env`` if present, so the documented setup step works."""
    env_file = EXAMPLE_DIR.parent / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional; parse the file ourselves
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_file)
    print(f"Loaded environment from {env_file}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Extract entities from a synthetic corpus with a GLiNER domain schema.",
    )
    parser.add_argument(
        "--schema",
        choices=list_sample_names(),
        help="Domain schema (and corpus) to run.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the available schemas and the demos each one showcases.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="GLiNER confidence threshold (default: the schema's tuned value).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="GLiNER model id (default: the library default).",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("GLINER_DEVICE", "cpu"),
        help="Device for GLiNER inference: cpu, cuda or mps (default: cpu).",
    )
    parser.add_argument(
        "--relations",
        action="store_true",
        help="Run the GLiREL relation-extraction demo (no LLM call).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run the native GLiNER batch-inference demo.",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Run the chunked streaming-extraction demo.",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Skip every optional demo, even the ones this schema showcases.",
    )
    store = parser.add_mutually_exclusive_group()
    store.add_argument(
        "--store",
        dest="store",
        action="store_true",
        default=None,
        help="Store the extracted graph in Neo4j (default: when NEO4J_URI is set).",
    )
    store.add_argument(
        "--no-store",
        dest="store",
        action="store_false",
        help="Never touch Neo4j, even when NEO4J_URI is set.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_STORE_LIMIT,
        help=f"Maximum entities to store (default: {DEFAULT_STORE_LIMIT}).",
    )
    return parser


def resolve_demos(args: argparse.Namespace, sample: SampleSet) -> tuple[str, ...]:
    """Pick the optional demos to run.

    Explicit flags win; otherwise the schema's own showcase list is used.
    """
    if args.extract_only:
        return ()
    explicit = tuple(
        name
        for name, enabled in (
            (RELATIONS, args.relations),
            (BATCH, args.batch),
            (STREAMING, args.streaming),
        )
        if enabled
    )
    return explicit or sample.demos


def create_extractor(
    sample: SampleSet, args: argparse.Namespace
) -> tuple[GLiNEREntityExtractor, ExtractionConfig]:
    """Build the extractor via the config-driven factory.

    ``create_gliner_extractor`` is the same code path ``MemorySettings.extraction``
    uses internally, so this transfers unchanged to an application. The terse
    alternative is ``GLiNEREntityExtractor.for_schema(sample.schema_name)``.
    """
    config = ExtractionConfig(
        gliner_schema=sample.schema_name,
        gliner_threshold=args.threshold if args.threshold is not None else sample.threshold,
        gliner_device=args.device,
        **({"gliner_model": args.model} if args.model else {}),
    )
    extractor = create_gliner_extractor(config)
    if not isinstance(extractor, GLiNEREntityExtractor):  # pragma: no cover - factory contract
        raise TypeError(f"Expected a GLiNEREntityExtractor, got {type(extractor).__name__}")
    return extractor, config


def create_relation_extractor(
    sample: SampleSet, args: argparse.Namespace, config: ExtractionConfig
) -> GLiNERWithRelationsExtractor:
    """Build the combined GLiNER + GLiREL extractor for this schema."""
    return GLiNERWithRelationsExtractor.for_schema(
        sample.schema_name,
        gliner_model=config.gliner_model,
        entity_threshold=config.gliner_threshold,
        device=args.device,
    )


# --------------------------------------------------------------------------- #
# Printing helpers
# --------------------------------------------------------------------------- #
def print_schema_table() -> None:
    """Print the available schemas, their corpora and their default demos."""
    print("Available domain schemas:\n")
    for name in list_sample_names():
        sample = get_sample(name)
        demos = ", ".join(sample.demos) if sample.demos else "-"
        print(f"  {name:<14} {sample.blurb}")
        print(f"  {'':<14} threshold {sample.threshold} | demos: {demos}")
    print("\nRun one with: python run.py --schema <name>")


def print_header(sample: SampleSet, config: ExtractionConfig, demos: tuple[str, ...]) -> None:
    """Print the run header."""
    print(RULE)
    print(f"{sample.schema_name.upper()} schema — {sample.title}")
    print(RULE)
    print(SYNTHETIC_BANNER)
    print()
    print(f"  Schema:       {sample.schema_name} ({len(sample.documents)} documents)")
    print(f"  Model:        {config.gliner_model}")
    print(f"  Threshold:    {config.gliner_threshold}")
    print(f"  Device:       {config.gliner_device}")
    print(f"  Entity types: {list(get_schema(sample.schema_name).entity_types)}")
    print(f"  Demos:        {', '.join(demos) if demos else '(none)'}")
    print()


def print_entities_by_type(entities: list[ExtractedEntity], limit: int) -> None:
    """Print extracted entities grouped by POLE+O type."""
    by_type: dict[str, list[ExtractedEntity]] = {}
    for entity in entities:
        by_type.setdefault(entity.type, []).append(entity)
    for entity_type, group in sorted(by_type.items()):
        print(f"\n  {entity_type}:")
        ranked = sorted(group, key=lambda e: e.confidence or 0, reverse=True)
        for entity in ranked[:limit]:
            conf = f"({entity.confidence:.0%})" if entity.confidence else ""
            subtype = f" [{entity.subtype}]" if entity.subtype else ""
            print(f"    - {entity.name}{subtype} {conf}")


def print_highlight(highlight: Highlight, entities: list[ExtractedEntity]) -> None:
    """Print one summary section."""
    selected = [e for e in entities if highlight.matches(e.type, e.subtype)]
    print(f"\n{highlight.label}:")
    if not selected:
        print("  (none found — try a lower --threshold)")
        return
    ranked = sorted(selected, key=lambda e: e.confidence or 0, reverse=True)
    for entity in ranked[: highlight.limit]:
        subtype = f" [{entity.subtype}]" if highlight.show_subtype and entity.subtype else ""
        print(f"  - {entity.name}{subtype}")


def print_summary(sample: SampleSet, entities: list[ExtractedEntity]) -> None:
    """Print the deduplicated run summary."""
    print(RULE)
    print(f"{sample.schema_name.upper()} KNOWLEDGE GRAPH SUMMARY")
    print(RULE)
    print(f"\nTotal unique entities: {len(entities)}")

    counts: dict[str, int] = {}
    for entity in entities:
        counts[entity.type] = counts.get(entity.type, 0) + 1
    print("\nEntity breakdown:")
    for entity_type, count in sorted(counts.items()):
        print(f"  {entity_type}: {count}")

    for highlight in sample.highlights:
        print_highlight(highlight, entities)
    print()


def print_use_cases(sample: SampleSet) -> None:
    """Print the domain's use cases, the relations note and any caveat."""
    print(RULE)
    print(f"{sample.title}: use cases")
    print(RULE)
    print(sample.use_cases)
    print(RELATIONS_NOTE.format(schema=sample.schema_name))
    if sample.note:
        print(sample.note)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def collapse_whitespace(entity: ExtractedEntity) -> ExtractedEntity:
    """Flatten a span that wrapped across source lines ("Acme\\n  Corp")."""
    flat = " ".join(entity.name.split())
    return entity if flat == entity.name else entity.model_copy(update={"name": flat})


def deduplicate(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Keep the highest-confidence mention of each (normalized name, type)."""
    best: dict[tuple[str, str], ExtractedEntity] = {}
    for entity in entities:
        key = (entity.normalized_name, entity.type)
        current = best.get(key)
        if current is None or (entity.confidence or 0) > (current.confidence or 0):
            best[key] = entity
    return list(best.values())


async def extract_documents(
    extractor: GLiNEREntityExtractor, sample: SampleSet
) -> list[ExtractedEntity]:
    """Extract from every document in the corpus, printing as it goes."""
    all_entities: list[ExtractedEntity] = []
    for doc in sample.documents:
        print(doc.title)
        if doc.meta:
            print(doc.meta)
        print("-" * 50)

        result = await extractor.extract(doc.content)
        filtered = result.filter_invalid_entities()
        entities = [collapse_whitespace(entity) for entity in filtered.entities]
        print(f"  Entities extracted: {len(entities)}")
        print_entities_by_type(entities, sample.per_type_limit)

        all_entities.extend(entities)
        print()
    return all_entities


async def demo_relations(
    sample: SampleSet, args: argparse.Namespace, config: ExtractionConfig
) -> list[ExtractedRelation]:
    """Extract relations with GLiREL — no LLM call involved."""
    print(RULE)
    print("RELATION EXTRACTION (GLiREL — no LLM required)")
    print(RULE)
    if not is_glirel_available():
        print("\n  GLiREL is not installed, so this demo is skipped.")
        print("  It is opt-in (last released 2025-04): pip install glirel")
        print("  GLiREL extracts relationships locally, without LLM calls.\n")
        return []

    extractor = create_relation_extractor(sample, args, config)
    doc = sample.documents[0]
    print(f"\nExtracting relations from: {doc.title}")
    result = (await extractor.extract(doc.content)).filter_invalid_entities()
    if not result.relations:
        print("  No relationships extracted (try a lower --threshold)\n")
        return []

    print(f"\n  Relationships found: {len(result.relations)}")
    for rel in result.relations:
        conf = f" ({rel.confidence:.0%})" if rel.confidence else ""
        print(f"    {rel.source} -[{rel.relation_type}]-> {rel.target}{conf}")
    print()
    return list(result.relations)


async def demo_batch(extractor: GLiNEREntityExtractor, sample: SampleSet) -> None:
    """Process the whole corpus with GLiNER's native batch inference."""
    print(RULE)
    print("BATCH EXTRACTION (native GLiNER batch inference)")
    print(RULE)
    print()

    results = await extractor.extract_batch(
        sample.texts,
        batch_size=10,
        on_progress=lambda done, total: print(f"  Batch progress: {done}/{total}"),
    )
    total_entities = sum(result.entity_count for result in results)
    print(f"  Processed: {len(results)} documents")
    print(f"  Total entities (batch): {total_entities}")
    print(
        "\n  Note: GLiNEREntityExtractor.extract_batch returns a plain\n"
        "  list[ExtractionResult] (one per input, GPU-efficient). The\n"
        "  multi-stage ExtractionPipeline.extract_batch returns a\n"
        "  BatchExtractionResult with success/failure bookkeeping instead.\n"
    )


async def demo_streaming(extractor: GLiNEREntityExtractor, sample: SampleSet) -> None:
    """Chunk a long document and extract chunk by chunk."""
    print(RULE)
    print("STREAMING EXTRACTION (for long documents)")
    print(RULE)
    print()

    long_document = sample.long_document
    print(f"  Combined document length: {len(long_document)} characters")

    streamer = create_streaming_extractor(extractor, chunk_size=2000, overlap=200)

    chunk_count = 0
    streamed_entities = 0
    async for chunk_result in streamer.extract_streaming(long_document):
        chunk_count += 1
        streamed_entities += chunk_result.entity_count
        error = "" if chunk_result.success else f" (error: {chunk_result.error})"
        print(
            f"  Chunk {chunk_result.chunk.index + 1}: {chunk_result.entity_count} entities{error}"
        )

    print(f"\n  Total: {chunk_count} chunks, {streamed_entities} entities (before dedup)")

    complete = await streamer.extract(long_document, deduplicate=True)
    print(
        f"  After deduplication: {complete.stats.deduplicated_entities} entities "
        f"(from {complete.stats.total_entities} raw)\n"
    )


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def build_settings(uri: str) -> MemorySettings:
    """Settings for the storage step: local, bolt-pinned, no LLM.

    * ``backend="bolt"`` — a ``MEMORY_API_KEY`` in the environment must not
      silently redirect these writes to the hosted service (which stores neither
      ``subtype`` nor ``attributes``).
    * ``llm=None`` — no LLM client is constructed, so no OpenAI key is needed.
    * a sentence-transformers embedder — entity embeddings stay on this machine.
      Swap in ``embedding="openai/text-embedding-3-small"`` if you prefer the API.
    * ``ExtractorType.NONE`` — this script already extracted; the client must not
      extract again on write.
    """
    return MemorySettings(
        backend="bolt",
        neo4j=Neo4jConfig(
            uri=uri,
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        ),
        llm=None,
        embedding=LOCAL_EMBEDDING_MODEL,
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )


def bolt_long_term(client: MemoryClient) -> LongTermMemory:
    """Narrow ``client.long_term`` to the bolt implementation.

    ``MemoryClient.long_term`` is typed as the backend-agnostic protocol, which
    covers ``add_entity``/``search_entities`` but not the provenance surface
    (``register_extractor``, ``link_entity_to_extractor``,
    ``get_entities_by_extractor``) — on NAMS, extraction runs server-side and
    those edges are not part of the hosted API. This example pins
    ``backend="bolt"``, so the narrowing always succeeds.
    """
    long_term = client.long_term
    if not isinstance(long_term, LongTermMemory):  # pragma: no cover - bolt is pinned
        raise TypeError("the storage step needs the bolt backend (backend='bolt')")
    return long_term


def _extractor_version() -> str | None:
    """Version of the installed gliner package, when available."""
    try:
        from importlib.metadata import version

        return version("gliner")
    except Exception:  # pragma: no cover - metadata is best-effort
        return None


async def store_graph(
    sample: SampleSet,
    entities: list[ExtractedEntity],
    relations: list[ExtractedRelation],
    config: ExtractionConfig,
    *,
    uri: str,
    limit: int,
) -> None:
    """Store entities, provenance and relations, then read the graph back."""
    print(RULE)
    print("STORING IN NEO4J")
    print(RULE)

    settings = build_settings(uri)
    async with MemoryClient(settings) as client:
        print(f"\n  backend: {client.backend} ({uri})")
        long_term = bolt_long_term(client)

        await long_term.register_extractor(
            EXTRACTOR_NAME,
            version=_extractor_version(),
            config={
                "schema": sample.schema_name,
                "threshold": config.gliner_threshold,
                "model": config.gliner_model,
            },
        )

        stored: dict[str, Entity] = {}
        actions: dict[str, int] = {}
        for extracted in entities[:limit]:
            entity, dedup = await long_term.add_entity(
                name=extracted.name,
                entity_type=extracted.type,
                subtype=extracted.subtype,
                attributes={
                    "source": sample.source_tag,
                    "confidence": extracted.confidence,
                    "gliner_schema": sample.schema_name,
                },
            )
            actions[dedup.action] = actions.get(dedup.action, 0) + 1
            stored.setdefault(extracted.name.casefold(), entity)
            stored.setdefault(extracted.normalized_name, entity)
            # Provenance: which extractor produced this entity.
            await long_term.link_entity_to_extractor(
                entity,
                EXTRACTOR_NAME,
                confidence=extracted.confidence or 1.0,
            )

        unique_stored = {entity.id: entity for entity in stored.values()}
        submitted = len(entities[:limit])
        print(
            f"  Stored {len(unique_stored)} entity nodes from {submitted} extracted mentions "
            f"(dedup actions: {actions or 'none'})"
        )
        print(f"  Linked all of them to the :Extractor node '{EXTRACTOR_NAME}'")

        stored_relations = 0
        for rel in relations:
            source = stored.get(rel.source.casefold())
            target = stored.get(rel.target.casefold())
            if source is None or target is None:
                continue
            await long_term.add_relationship(
                source,
                target,
                rel.relation_type,
                confidence=rel.confidence,
            )
            stored_relations += 1
        if relations:
            print(f"  Stored {stored_relations}/{len(relations)} GLiREL relations as RELATED_TO")

        await read_back(long_term, sample, unique_stored)


async def read_back(
    long_term: LongTermMemory,
    sample: SampleSet,
    stored: dict[UUID, Entity],
) -> None:
    """Read the graph back, so the run ends with a graph rather than a list."""
    print("\n  Read-back:")

    by_extractor = await long_term.get_entities_by_extractor(EXTRACTOR_NAME, limit=5)
    print(f"    entities tagged with {EXTRACTOR_NAME}: {len(by_extractor)} (first 5)")
    for entity, info in by_extractor:
        confidence = info.get("confidence")
        conf = f" ({confidence:.0%})" if isinstance(confidence, float) else ""
        print(f"      - {entity.name} ({entity.full_type}){conf}")

    matches = await long_term.search_entities(sample.readback_query, limit=3, threshold=0.2)
    print(f'    search_entities("{sample.readback_query}"): {len(matches)} hits')
    for entity in matches:
        score = entity.metadata.get("similarity")
        score_text = f" ({score:.2f})" if isinstance(score, float) else ""
        print(f"      - {entity.name} ({entity.full_type}){score_text}")

    for entity in stored.values():
        related = await long_term.get_related_entities(entity)
        if related:
            print(f"    get_related_entities({entity.name}):")
            for other, relationship in related:
                print(f"      - -[{relationship.type}]-> {other.name}")
            break
    else:
        print("    no RELATED_TO edges yet — run with --relations --store to add some")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def main(argv: list[str] | None = None) -> int:
    """Run one domain schema end to end. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    load_env()

    if args.list or not args.schema:
        print_schema_table()
        if not args.schema and not args.list:
            print("\nNo --schema given.")
            return 2
        return 0

    sample = get_sample(args.schema)
    if sample.schema_name not in list_schemas():  # pragma: no cover - registry guard
        print(f"Schema '{sample.schema_name}' is not a built-in GLiNER schema: {list_schemas()}")
        return 2

    if not is_gliner_available():
        print("  ERROR: GLiNER is not installed.")
        print("\n  To run this example, install GLiNER:")
        print("    uv sync --all-extras")
        print('    # or: pip install "neo4j-agent-memory[gliner]"')
        print("\n  The GLiNER model (~500 MB) downloads on first use.")
        return 0

    extractor, config = create_extractor(sample, args)
    demos = resolve_demos(args, sample)
    print_header(sample, config, demos)

    entities = deduplicate(await extract_documents(extractor, sample))
    print_summary(sample, entities)

    relations: list[ExtractedRelation] = []
    if RELATIONS in demos:
        relations = await demo_relations(sample, args, config)
    if BATCH in demos:
        await demo_batch(extractor, sample)
    if STREAMING in demos:
        await demo_streaming(extractor, sample)

    print_use_cases(sample)

    uri = os.getenv("NEO4J_URI")
    store = args.store if args.store is not None else bool(uri)
    if store and not uri:
        print("\n--store was requested but NEO4J_URI is not set.")
        return 2
    if store and uri:
        await store_graph(sample, entities, relations, config, uri=uri, limit=args.limit)
    else:
        print("\nSet NEO4J_URI (and pass --store) to persist this graph in Neo4j.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
