#!/usr/bin/env python3
"""Run neo4j-agent-memory with no LLM provider.

This example shows how to use neo4j-agent-memory when you cannot (or do not
want to) call an LLM — air-gapped deployments, cost-sensitive workloads, or
deterministic test environments:

- ``llm=None`` on ``MemorySettings`` — no LLM client is ever constructed.
- A local embedder (sentence-transformers) — no embeddings API is called.
- A local extractor pipeline (spaCy + GLiNER) with the LLM fallback disabled.
- ``backend="bolt"`` — pinned, so a ``MEMORY_API_KEY`` in the environment does
  not silently redirect this demo to the hosted service.

All three memory layers work with no LLM: short-term messages with local NER,
long-term preferences/facts/entities, and reasoning traces. The run prints the
entities the local pipeline extracted so a degraded install is obvious.

Requirements:

    pip install "neo4j-agent-memory[extraction,sentence-transformers]"
    python -m spacy download en_core_web_sm

Model weights (sentence-transformers ~90 MB, GLiNER ~500 MB) download once on
first run; see the README's "Truly offline" section for warming the caches and
running with ``HF_HUB_OFFLINE=1``.

Copy ``examples/.env.example`` to ``examples/.env`` to change the Neo4j
connection, or export ``NEO4J_URI`` / ``NEO4J_USERNAME`` / ``NEO4J_PASSWORD``.
Defaults target the throwaway container that ``make neo4j-start`` launches.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
)
from neo4j_agent_memory.extraction import create_extractor, is_gliner_available
from neo4j_agent_memory.schema import TraceOutcome

SESSION_ID = "no-llm-demo"
SPACY_MODEL = "en_core_web_sm"
DEMO_MESSAGE = "John Smith works at Acme Corp in New York."
# Provider string — resolves to a local SentenceTransformersProvider. Override
# with LOCAL_EMBEDDING_MODEL (any sentence-transformers model id).
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Read back the entity nodes the local pipeline wrote. ``client.query.cypher``
# is the supported read-only Cypher accessor (no private driver access).
EXTRACTED_ENTITY_NODES = """
MATCH (e:Entity) WHERE e.name IN $names
RETURN e.name AS name, labels(e) AS labels
ORDER BY name
"""


def load_env() -> None:
    """Load ``examples/.env`` if present, so the documented setup step works."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
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


def check_local_stack() -> None:
    """Fail fast when the local extraction stack is incomplete.

    Without this guard a missing spaCy model or GLiNER install degrades
    silently: ``ExtractionPipeline`` swallows per-stage failures, so the run
    would print an empty-looking context instead of an error.
    """
    missing: list[str] = []

    try:
        import spacy
    except ImportError:
        missing.append('spaCy — pip install "neo4j-agent-memory[extraction]"')
    else:
        if not spacy.util.is_package(SPACY_MODEL):
            missing.append(f"spaCy model — python -m spacy download {SPACY_MODEL}")

    if not is_gliner_available():
        missing.append('GLiNER — pip install "neo4j-agent-memory[extraction]"')

    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        missing.append(
            'sentence-transformers — pip install "neo4j-agent-memory[sentence-transformers]"'
        )

    if missing:
        raise SystemExit(
            "Local extraction stack is incomplete — this example would report zero "
            "entities.\n  Missing:\n    - " + "\n    - ".join(missing)
        )


def build_settings() -> MemorySettings:
    return MemorySettings(
        # Pin the backend: without this, a MEMORY_API_KEY in the environment
        # would resolve to the hosted service, where extraction and embeddings
        # run server-side — the opposite of what this example demonstrates.
        backend="bolt",
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        ),
        # Explicit opt-out: never construct an LLM client.
        llm=None,
        # v0.3+ provider-string shorthand. Resolves to a local
        # SentenceTransformersProvider — embeddings stay on this machine.
        embedding=os.getenv("LOCAL_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        # Local extraction only. enable_llm_fallback=False is required when
        # llm=None — otherwise MemorySettings raises a ValidationError.
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=True,
            enable_gliner=True,
            enable_llm_fallback=False,
        ),
    )


async def main() -> None:
    load_env()
    check_local_stack()

    settings = build_settings()
    print(f"llm configured: {settings.llm}")
    print(f"embedding provider: {type(settings.embedding).__name__} ({settings.embedding.model})")

    # Build the local pipeline up front so we can both (a) show what it
    # extracts and (b) hand the same loaded models to the client, instead of
    # loading spaCy and GLiNER twice.
    extractor = create_extractor(settings.extraction)
    print(f"extractor: {type(extractor).__name__}")

    extraction = await extractor.extract(DEMO_MESSAGE)
    extraction = extraction.filter_invalid_entities()
    if not extraction.entities:
        raise SystemExit(
            "The local pipeline extracted nothing from the demo sentence — something "
            f"is wrong with the install. Check that the {SPACY_MODEL} model and the "
            "GLiNER weights load, then re-run."
        )
    for entity in extraction.entities:
        print(f"  extracted locally: {entity.name} ({entity.full_type})")

    async with MemoryClient(settings, extractor=extractor) as memory:
        print(f"\nbackend: {memory.backend}")

        # Idempotent re-runs: drop this session's conversation first, so the
        # printed history does not grow. Entities, preferences and facts are
        # long-term and MERGE on identity, so they are safe to re-write.
        # (Reasoning traces are not cleared — each run adds one.)
        await memory.short_term.clear_session(SESSION_ID)

        # --- Short-term memory: messages + local NER ------------------------
        # add_message runs the same extractor and MERGEs an :Entity node per
        # extracted name, with POLE+O type/subtype as Neo4j labels. No LLM.
        message = await memory.short_term.add_message(SESSION_ID, "user", DEMO_MESSAGE)
        await memory.short_term.add_message(
            SESSION_ID, "assistant", "Got it — I'll remember John and Acme Corp."
        )

        names = [entity.name for entity in extraction.entities]
        for row in await memory.query.cypher(EXTRACTED_ENTITY_NODES, {"names": names}):
            print(f"  in the graph: {row['name']} {row['labels']}")

        # --- Long-term memory: preferences, facts, curated entities ---------
        # All three embed with the local model and dedupe on similarity —
        # no LLM anywhere in the write path.
        await memory.long_term.add_preference("food", "Prefers Italian")
        await memory.long_term.add_fact("John Smith", "WORKS_AT", "Acme Corp")
        # Extraction writes entities without embeddings (NER gives a name and a
        # type, not a vector). add_entity MERGEs onto the node the extractor
        # created and fills in a locally-computed embedding + description, which
        # is what makes the entity reachable by semantic search below.
        john, _dedup = await memory.long_term.add_entity(
            "John Smith",
            "PERSON",
            description="Works at Acme Corp in New York",
        )

        print()
        for pref in await memory.long_term.search_preferences("restaurant choice", category="food"):
            print(f"  preference: [{pref.category}] {pref.preference}")
        for fact in await memory.long_term.get_facts_about(john.name):
            print(f"  fact: {fact.subject} -{fact.predicate}-> {fact.object}")
        # Vector search over the local embeddings — no embeddings API call.
        hits = await memory.long_term.search_entities("John Smith")
        print(f"  semantic entity search: {[f'{e.name} ({e.full_type})' for e in hits]}")

        # --- Reasoning memory: a trace, linked to the message that began it -
        trace = await memory.reasoning.start_trace(
            SESSION_ID,
            "Answer a question about John",
            triggered_by_message_id=message.id,
        )
        await memory.reasoning.add_step(
            trace.id,
            thought="Look up what local memory already knows about John",
            action="search_entities",
        )
        await memory.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary="Answered from local memory — no LLM call",
                metrics={"entities_extracted": float(len(extraction.entities))},
            ),
        )

        # --- Combined context, assembled from all three layers --------------
        context = await memory.get_context("What do we know about John?", session_id=SESSION_ID)
        print(f"\n{context}")

        # Consolidation is pure Cypher + stored embeddings: it runs without an
        # LLM too. dry_run=True reports candidates without mutating anything.
        report = await memory.consolidation.dedupe_entities(dry_run=True)
        print(f"\ndedupe candidates (dry run): {report.candidate_count}")


if __name__ == "__main__":
    asyncio.run(main())
