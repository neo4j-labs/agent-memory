#!/usr/bin/env python3
"""Ontology-driven extraction and resolution, end to end, with no API keys.

One ``ontology.yaml`` drives the whole run:

* **GLiNER2.5 (JointIE)** decodes entities *and* typed relations in a single
  pass, with the ontology's head/tail constraints enforced during beam search.
  Each type's ``description`` is an annotation guideline the model reads.
* **Ontology-aware resolution** (on by default since v0.7) merges the three
  surface forms of one organization onto one node — and leaves the near-miss
  ("Acme Bank") alone, because the gazetteer never claimed it.
* **Typed relations carry provenance**: ``r.type``, ``r.support`` (how many
  times the triple was observed), ``r.confidence``, ``r.derived``.
* **``client.ontology``** stores the same document as a versioned
  ``:OntologyVersion``, so you can activate it, revise it and diff revisions.

No LLM, no API key: ``llm=None`` plus a local sentence-transformers embedder.
Weights (GLiNER2.5 ~407 MB, MiniLM ~90 MB) download once and are cached.

Run from the repository root::

    make example-ontology-extraction
    # or
    uv run python examples/ontology-extraction/main.py

Connection details come from ``examples/.env`` (copy ``examples/.env.example``)
and default to the throwaway container ``make neo4j-start`` launches.
"""

from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from neo4j_agent_memory import BoltMemoryClient, Neo4jConfig, connect
from neo4j_agent_memory.config.settings import (
    BoltSettings,
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
)
from neo4j_agent_memory.ontology import OntologyDocument, RelationshipDef, load_ontology

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import (  # type: ignore[import-not-found]  # ty: ignore[unused-ignore-comment]  # noqa: E402
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USERNAME,
)

ONTOLOGY_PATH = Path(__file__).with_name("ontology.yaml")
LOCAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

INTAKE, FOLLOWUP = "ontology-demo-intake", "ontology-demo-followup"

#: Two sessions, six messages. "Acme Corp" / "Acme" / "ACME Corporation" are
#: the same customer and must collapse onto one node; "Acme Bank" is a
#: different company and must not.
MESSAGES: tuple[tuple[str, str, str], ...] = (
    (
        INTAKE,
        "user",
        "Dana Whitfield from Acme Corp reported INC-4471: the Nightshade"
        " importer crashes on every nightly sync.",
    ),
    (
        INTAKE,
        "assistant",
        "Thanks Dana — incident INC-4471 is assigned to Priya Raman, one of our support engineers.",
    ),
    (
        INTAKE,
        "user",
        "Dana confirms that Acme also runs Nightshade Enterprise in"
        " Frankfurt, where the importer is part of Nightshade Enterprise.",
    ),
    (
        FOLLOWUP,
        "user",
        "Following up for Dana Whitfield at ACME Corporation: is the"
        " Nightshade importer fix shipping this week?",
    ),
    (
        FOLLOWUP,
        "assistant",
        "Support engineer Priya Raman confirmed the importer patch"
        " ships Thursday; incident INC-4471 stays open until then.",
    ),
    (
        FOLLOWUP,
        "user",
        "Separately, Acme Bank asked about Nightshade Lite — a different"
        " company, so do not merge their tickets.",
    ),
)
SESSIONS = [INTAKE, FOLLOWUP]

#: Entity nodes this demo's messages mention, with the aliases resolution
#: folded into them. Scoped through MENTIONS so a shared database stays legible.
DEMO_ENTITIES = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id IN $sessions
RETURN e.name AS name, e.type AS type, e.subtype AS subtype,
       coalesce(e.aliases, []) AS aliases, count(DISTINCT m) AS mentions
ORDER BY mentions DESC, name
"""

#: Typed relations with their provenance. The relationship type stays
#: ``RELATED_TO``; ``r.type`` carries the ontology's relation name, and the
#: MERGE key includes it, so two relation types between one pair coexist.
DEMO_RELATIONS = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(source:Entity)
MATCH (source)-[r:RELATED_TO]->(target:Entity)
WHERE c.session_id IN $sessions
RETURN DISTINCT source.name AS source, target.name AS target,
       type(r) AS neo4j_type, r.type AS relation_type, r.support AS support,
       r.confidence AS confidence, r.derived AS derived
ORDER BY support DESC, relation_type, source
"""

#: Idempotency: drop the entities this demo's sessions mention before re-running,
#: so ``r.support`` counts one run rather than every run. Only nodes reachable
#: from these two sessions are touched.
DELETE_DEMO_ENTITIES = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id IN $sessions
WITH DISTINCT e
DETACH DELETE e
"""


def build_settings() -> BoltSettings:
    """Local, keyless settings whose schema is the ontology file.

    ``BoltSettings`` pins the backend: a stray ``MEMORY_API_KEY`` must not
    redirect this demo to the hosted service, where extraction and resolution
    run server-side.
    """
    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=SecretStr(NEO4J_PASSWORD),
        ),
        llm=None,
        embedding=LOCAL_EMBEDDING_MODEL,
        # The whole point: one ontology file, resolved once at connect time and
        # handed to the extractor, the resolver and both write paths.
        schema_config=SchemaConfig(ontology_path=str(ONTOLOGY_PATH)),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            # GLiNER2.5 only: spaCy has no ontology labels to offer here and
            # the LLM fallback needs a key.
            enable_spacy=False,
            enable_gliner=True,
            enable_llm_fallback=False,
        ),
        # Resolution defaults: resolve_on_ingest=True, auto-merge at 0.90,
        # review band at 0.85. The ontology's per-type overrides win.
    )


def describe(doc: OntologyDocument) -> None:
    """Print what the ontology declares — labels, patterns, constraints."""
    print(f"\nOntology: {doc.domain.name} — {doc.domain.description}")
    for et in doc.entity_types:
        full = f"{et.pole_type}:{et.subtype}" if et.subtype else et.pole_type
        extra = f", threshold={et.threshold}" if et.threshold else ""
        print(f"  label {et.label:<13} -> {full}{extra}")
    print(
        f"  {len(doc.relationship_types())} relationship types, "
        f"{len(doc.patterns())} endpoint-typed patterns:"
    )
    for rel in doc.relationships:
        flags = ", ".join(
            name
            for name, on in (
                ("unique_source", rel.unique_source),
                ("unique_target", rel.unique_target),
                ("acyclic", rel.acyclic),
            )
            if on
        )
        print(f"    {rel.source} -[{rel.type}]-> {rel.target}{f'  [{flags}]' if flags else ''}")


def revised(doc: OntologyDocument) -> OntologyDocument:
    """Revision 2: the support desk learns to assign incidents to engineers."""
    return doc.model_copy(
        update={
            "relationships": [
                *doc.relationships,
                RelationshipDef(
                    type="ASSIGNED_TO",
                    source="Incident",
                    target="Engineer",
                    description="The incident is being worked by this engineer.",
                    unique_source=True,
                ),
            ]
        }
    )


async def ingest(client: BoltMemoryClient) -> int:
    """Store every demo message, extracting under the ontology. Returns count."""
    current = ""
    for session_id, role, content in MESSAGES:
        if session_id != current:
            print(f"\n  session {session_id}")
            current = session_id
        await client.short_term.add_message(session_id, role, content)
        print(f"    {role:<9} {content[:72]}…")
    return len(MESSAGES)


async def main() -> None:
    # feasible=False and over-long input are quiet failure modes, so the
    # extractor raises RuntimeWarning for both. Show every occurrence rather
    # than Python's default once-per-location — they are worth reading.
    warnings.simplefilter("always", RuntimeWarning)

    doc = load_ontology(ONTOLOGY_PATH)
    problems = doc.validate_structure()
    if problems:
        raise SystemExit("ontology.yaml is not sound:\n  " + "\n  ".join(problems))
    describe(doc)

    # ``connect()`` on BoltSettings returns a concretely-typed BoltMemoryClient,
    # which is what gives ``long_term.find_potential_duplicates()`` and
    # ``client.graph`` a checked type (the base client is Protocol-typed).
    client = await connect(build_settings())
    try:
        resolved = client.ontology_document
        print(
            f"\nclient resolved ontology: "
            f"{resolved.domain.name if resolved else None} "
            f"({len(resolved.labels()) if resolved else 0} labels, "
            f"validation_mode={client.validation_mode})"
        )
        await client.graph.execute_write(DELETE_DEMO_ENTITIES, {"sessions": SESSIONS})
        for session_id in SESSIONS:
            await client.short_term.clear_session(session_id)

        # --- The ontology store ---------------------------------------------
        # ``schema_config.ontology_path`` above is what actually drives this
        # run's extraction (it outranks the stored version). Registering the
        # same document here versions it in the database: activate it and any
        # later client with no file configured picks it up by itself.
        version = await client.ontology.create("support-desk", doc)
        await client.ontology.activate(version.id)
        active = await client.ontology.get_active()
        print(
            f"\nstored ontology: {version.ontology_id} revision {version.revision} "
            f"({version.validation_mode}), active = "
            f"{active.document.domain.name} r{active.revision}"
        )

        # --- Ingest ----------------------------------------------------------
        message_count = await ingest(client)

        # --- What landed in the graph ---------------------------------------
        entities: list[dict[str, Any]] = list(
            await client.query.cypher(DEMO_ENTITIES, {"sessions": SESSIONS})
        )
        print(f"\nentity nodes ({len(entities)}):")
        mention_edges = 0
        for row in entities:
            mention_edges += int(row["mentions"])
            full = f"{row['type']}:{row['subtype']}" if row["subtype"] else row["type"]
            aliases = f"  aliases={row['aliases']}" if row["aliases"] else ""
            print(f"  {row['name']:<24} {full:<19} mentions={row['mentions']}{aliases}")

        relations: list[dict[str, Any]] = list(
            await client.query.cypher(DEMO_RELATIONS, {"sessions": SESSIONS})
        )
        print(f"\ntyped edges ({len(relations)}):")
        for row in relations:
            print(
                f"  ({row['source']})-[:{row['neo4j_type']} "
                f"{{type: {row['relation_type']!r}}}]->({row['target']})"
                f"  support={row['support']} confidence={row['confidence']:.2f} "
                f"derived={row['derived']}"
            )

        # --- The review band -------------------------------------------------
        # A score in [review_threshold, auto_merge_threshold) — 0.85 to 0.90 by
        # default — creates a pending SAME_AS edge instead of merging, for a
        # human to judge with long_term.review_duplicate(confirm=True/False).
        # "Acme Bank" is the deliberate near-miss: it shares a whole-token
        # prefix with "Acme Corp", scores in the band, and is left alone.
        pending = await client.long_term.find_potential_duplicates(limit=10)
        print(f"\npending SAME_AS review pairs ({len(pending)}):")
        for left, right, confidence in pending:
            print(
                f"  {left.name!r} ~ {right.name!r}  score={confidence:.2f} (neither node was merged)"
            )

        # --- Revising the ontology -------------------------------------------
        next_version = await client.ontology.update(version.ontology_id, revised(doc))
        diff = await client.ontology.diff(version.ontology_id, 1, next_version.revision)
        added = [rel.get("type") for rel in diff.relationships.get("added", [])]
        print(
            f"\ndiff r{diff.from_revision} -> r{diff.to_revision}: "
            f"relationships added {added}, removed "
            f"{len(diff.relationships.get('removed', []))}, entity types "
            f"{len(diff.entity_types.get('added', []))} added"
        )

        # --- Summary ---------------------------------------------------------
        supports = [int(row["support"]) for row in relations]
        print(
            "\nSummary\n"
            f"  {len(entities)} entity nodes from {mention_edges} MENTIONS edges "
            f"across {message_count} messages\n"
            f"  {len(relations)} typed edges over "
            f"{len({row['relation_type'] for row in relations})} relationship types "
            f"(max support {max(supports) if supports else 0})\n"
            f"  {len(pending)} pending SAME_AS pair(s) awaiting review"
        )

        # Leave the entities; drop the ontology. An active stored version would
        # otherwise outlive this demo and drive the next client that connects
        # with no ontology configured.
        await client.ontology.delete(version.ontology_id)
        print(f"\ndeleted ontology {version.ontology_id} (entities left in place)")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
