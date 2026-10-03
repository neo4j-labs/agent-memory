"""Ontology lifecycle on bolt — import, activate, ingest, diff, migrate.

The bolt twin of ``examples/ontology-lifecycle`` (which runs the same story on
the hosted service). An *ontology* is the typed, versioned schema the client
extracts against: entity types mapped onto POLE+O, plus typed relationships.
On bolt it is stored in your own database as ``:Ontology`` /
``:OntologyVersion`` nodes, and it drives the local pipeline — GLiNER2.5's
labels and descriptions, relation validation and entity resolution. This
script walks one revision cycle of a support-desk domain:

1. **Survey** — ``ontology.list()`` (the eight built-in templates plus stored
   ontologies) and ``ontology.get_active()``.
2. **Import** — convert ``schemas/support-desk.arrows.json`` (an
   https://arrows.app export) into a draft with ``ontology.import_()``. The
   conversion runs locally and guesses: every label falls back to ``OBJECT``.
3. **Repair** — set the POLE+O types the guesses missed and give every type a
   description. GLiNER2.5 reads descriptions as annotation guidelines.
4. **Activate** — ``create()`` revision 1 and ``activate()`` it.
5. **Reconnect** — a client resolves its ontology once, when it connects, so
   the client that activated revision 1 still extracts against the old one.
   The script shows that, then reconnects.
6. **Ingest** — store a support transcript. GLiNER2.5 types the mentions with
   the ontology, and each ``Ticket`` node is written as
   ``:Entity:Event:Ticket``.
7. **Revise** — rename ``Ticket`` → ``SupportCase`` and switch to ``strict``;
   ``update()`` mints revision 2.
8. **Diff** — ``ontology.diff()`` between the two revisions.
9. **Migrate** — ``ontology.migrate(type_mappings=[("Ticket", "SupportCase")])``
   relabels the entities already in the graph. On bolt it runs inline and
   returns a finished job; a dry run first counts what it would touch.
10. **Read back** — activate revision 2, reconnect, and count the labels.

Install and run::

    pip install "neo4j-agent-memory[gliner2,sentence-transformers]==0.7.0"
    python ontology-lifecycle-bolt/main.py

Model weights (GLiNER2.5 ~407 MB, MiniLM ~90 MB) download once and are cached.

Export ``NEO4J_URI``, ``NEO4J_USERNAME`` and ``NEO4J_PASSWORD`` for a dedicated
database; ``NEO4J_DATABASE`` defaults to ``neo4j``. These credentials have no
fallback values. An optional ``.env`` beside this script is loaded first, and
exported values win.

**The run leaves revision 2 active.** Activation is per database: every client
that connects to it with ``schema_config.use_active_ontology=True`` (the
default) extracts against ``support-desk`` in strict mode from then on. The
script keeps everything it writes and prints how to restore the previous
binding. A rerun mints the next two revisions of the same ontology.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from neo4j_agent_memory import BoltMemoryClient, Neo4jConfig, connect
from neo4j_agent_memory.config.settings import (
    BoltSettings,
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
)
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.extraction import is_gliner2_available
from neo4j_agent_memory.ontology import OntologyDocument

HERE = Path(__file__).resolve().parent
ARROWS_FILE = HERE / "schemas" / "support-desk.arrows.json"

#: The ontology's identity comes from the document's ``domain`` block.
DOMAIN_ID = "support-desk"
DOMAIN_NAME = "Support Desk"

OLD_TYPE = "Ticket"
NEW_TYPE = "SupportCase"

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: The same transcript the hosted twin sends, so the two runs can be compared.
TRANSCRIPT = [
    {
        "role": "user",
        "content": (
            "Hi, this is Priya Raman. Order SO-4417 arrived yesterday and the "
            "Aurora Desk Lamp was cracked."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Sorry about that, Priya. I've opened ticket TK-2210 for order "
            "SO-4417 covering the damaged Aurora Desk Lamp."
        ),
    },
    {
        "role": "user",
        "content": "Can you send a replacement lamp rather than a refund?",
    },
    {
        "role": "assistant",
        "content": (
            "Done — ticket TK-2210 is now a replacement request for the Aurora "
            "Desk Lamp, shipping to the address on order SO-4417."
        ),
    },
    {
        "role": "user",
        "content": (
            "Also, the Northwind Cable Tray from order SO-4390 was the wrong "
            "colour. Same ticket or a new one?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "I've opened ticket TK-2211 for the Northwind Cable Tray on order "
            "SO-4390 so the two shipments stay separate."
        ),
    },
]

#: What the Arrows import cannot know. The hosted service infers these POLE+O
#: types itself; the local converter falls back to OBJECT and says so. Each
#: description ends in the negative case, because GLiNER2.5 reads descriptions
#: as annotation guidelines.
REPAIRS: dict[str, tuple[str, str, str]] = {
    "Customer": (
        "PERSON",
        "CUSTOMER",
        "A shopper who contacts support, named in full (Priya Raman). "
        "Never a product, an order or a ticket.",
    ),
    "Order": (
        "EVENT",
        "ORDER",
        "A purchase, identified by an order number that starts with SO- (SO-4417). "
        "Never the product it contains.",
    ),
    "Product": (
        "OBJECT",
        "PRODUCT",
        "An item the store sells, named by its product name (Aurora Desk Lamp). "
        "Never an order number or a ticket number.",
    ),
    "Ticket": (
        "EVENT",
        "TICKET",
        "A support ticket, identified by a reference that starts with TK- (TK-2210). "
        "Never the order or the product it is about.",
    ),
}

#: Entities this run's messages mention, with their labels.
SESSION_ENTITIES = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(e:Entity)
WITH DISTINCT e
RETURN e.name AS name, e.type AS type, e.subtype AS subtype,
       [label IN labels(e) WHERE label <> 'Entity'] AS labels
ORDER BY type, name
"""

#: How many of this run's entities carry ``label``.
SESSION_LABEL_COUNT = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(e:Entity)
WITH DISTINCT e
RETURN count(CASE WHEN $label IN labels(e) THEN 1 END) AS count
"""


def load_env() -> None:
    """Load the optional ``.env`` beside this script; exported values win."""
    env_file = HERE / ".env"
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
    """Fail fast when local extraction cannot run.

    Without GLiNER2.5 the pipeline extracts nothing, and the migration would
    have nothing to relabel.
    """
    missing: list[str] = []
    if not is_gliner2_available():
        missing.append('GLiNER2.5 — pip install "neo4j-agent-memory[gliner2]"')
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        missing.append(
            'sentence-transformers — pip install "neo4j-agent-memory[sentence-transformers]"'
        )
    if missing:
        raise SystemExit("Local extraction is not installed:\n  - " + "\n  - ".join(missing))


def build_settings() -> BoltSettings:
    """Keyless bolt settings whose ontology is whatever the database has active."""
    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=os.environ["NEO4J_URI"],
            username=os.environ["NEO4J_USERNAME"],
            password=SecretStr(os.environ["NEO4J_PASSWORD"]),
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
        ),
        llm=None,
        embedding=os.getenv("LOCAL_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        # No ontology file or template: with use_active_ontology=True (the
        # default, spelled out here) the client adopts the version this
        # database has active, at connect time. That is the mechanism the
        # whole script turns on.
        schema_config=SchemaConfig(use_active_ontology=True),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=False,
            enable_gliner=True,
            enable_llm_fallback=False,
        ),
    )


def repair_draft(document: OntologyDocument) -> OntologyDocument:
    """Return the imported draft with POLE+O types, subtypes and descriptions set."""
    repaired = document.model_copy(deep=True)
    repaired.domain.id = DOMAIN_ID
    repaired.domain.name = DOMAIN_NAME
    repaired.domain.description = "E-commerce support desk: customers, orders, products, tickets"
    for entity_type in repaired.entity_types:
        if entity_type.label in REPAIRS:
            pole_type, subtype, description = REPAIRS[entity_type.label]
            entity_type.pole_type = pole_type
            entity_type.subtype = subtype
            entity_type.description = description
    return repaired


def rename_entity_type(document: OntologyDocument, old: str, new: str) -> OntologyDocument:
    """Return a copy of ``document`` with one entity type relabelled.

    Relationship endpoints reference entity types by label, so they are
    rewritten too; otherwise revision 2 declares relationships between a type
    that no longer exists and fails validation.
    """
    revised = document.model_copy(deep=True)
    for entity_type in revised.entity_types:
        if entity_type.label == old:
            entity_type.label = new
    for relationship in revised.relationships:
        if relationship.source == old:
            relationship.source = new
        if relationship.target == old:
            relationship.target = new
    return revised


def describe_section(label: str, section: dict[str, Any]) -> str:
    """Render one half of an :class:`OntologyDiff` (entity types or relationships)."""
    parts: list[str] = []
    for kind in ("added", "removed", "renamed", "modified"):
        items = section.get(kind) or []
        if items:
            parts.append(f"{kind}={len(items)} [{', '.join(_describe_item(i) for i in items)}]")
    return f"   {label}: " + (", ".join(parts) if parts else "no changes")


def _describe_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    if item.get("from") and item.get("to"):
        return f"{item['from']} -> {item['to']}"
    if item.get("source") and item.get("target") and item.get("type"):
        return f"{item['source']}-{item['type']}->{item['target']}"
    for key in ("label", "type", "name"):
        if item.get(key):
            return str(item[key])
    return json.dumps(item, sort_keys=True)


def describe_binding(client: BoltMemoryClient, moment: str) -> None:
    """Print the ontology this client resolved when it connected."""
    document = client.ontology_document
    domain = document.domain.id if document is not None else None
    print(f"   client ontology {moment}: {domain} ({client.validation_mode})")


async def reconnect(client: BoltMemoryClient) -> BoltMemoryClient:
    """Close ``client`` and connect a new one.

    ``activate()`` changes what the database has bound, not what a connected
    client extracts against: the ontology is resolved once, at connect time.
    """
    await client.close()
    return await connect(build_settings())


async def label_count(client: BoltMemoryClient, session_id: str, label: str) -> int:
    rows = await client.query.cypher(
        SESSION_LABEL_COUNT, {"session_id": session_id, "label": label}
    )
    return int(rows[0]["count"]) if rows else 0


async def print_session_entities(client: BoltMemoryClient, session_id: str) -> None:
    rows = await client.query.cypher(SESSION_ENTITIES, {"session_id": session_id})
    for row in rows:
        stored = f"{row['type']}:{row['subtype']}" if row["subtype"] else row["type"]
        print(f"   {row['name']:<22} {stored:<16} :Entity:{':'.join(row['labels'])}")


async def main() -> dict[str, Any]:
    """Run the lifecycle. Returns what the run did, for tests and notebooks."""
    load_env()
    check_local_stack()

    client = await connect(build_settings())
    print(f"Connected to {os.environ['NEO4J_URI']} (backend={client.backend})")

    try:
        # 1. Survey ------------------------------------------------------------
        summaries = await client.ontology.list()
        templates = [s for s in summaries if s.is_system]
        stored = [s for s in summaries if not s.is_system]
        print(
            f"\nOntologies: {len(templates)} built-in template(s), "
            f"{len(stored)} stored in this database"
        )
        previous_version_id: str | None = None
        try:
            active = await client.ontology.get_active()
        except NotSupportedError:
            print("Active ontology: none bound (the client falls back to POLE+O)")
        else:
            previous_version_id = active.version_id
            print(
                f"Active ontology: {active.document.domain.id} revision {active.revision} "
                f"({active.validation_mode})"
            )
        describe_binding(client, "at connect")

        # 2. Import the Arrows document into a draft ---------------------------
        draft = await client.ontology.import_(
            content=ARROWS_FILE.read_text(encoding="utf-8"), format="arrows"
        )
        if draft.document is None:
            raise SystemExit(f"Could not convert {ARROWS_FILE.name}: {draft.warnings}")
        print(
            f"\nImported {ARROWS_FILE.name} (detected format: {draft.detected_format}): "
            f"{len(draft.document.entity_types)} entity type(s), "
            f"{len(draft.document.relationships)} relationship(s)"
        )
        for warning in draft.warnings:
            print(f"   warning [{warning.code}] {warning.message}")

        # 3. Repair what the conversion guessed --------------------------------
        document = repair_draft(draft.document)
        print("Repaired draft:")
        for entity_type in document.entity_types:
            print(f"   {entity_type.label} -> {entity_type.pole_type}:{entity_type.subtype}")

        # 4. Persist revision 1 and activate it --------------------------------
        existing = next((s for s in stored if s.name == DOMAIN_ID), None)
        if existing is None:
            v1 = await client.ontology.create(DOMAIN_NAME, document, validation_mode="permissive")
            print(f"\nCreated {DOMAIN_ID} revision {v1.revision} ({v1.validation_mode})")
        else:
            # A rerun: revisions are immutable, so this mints the next one.
            v1 = await client.ontology.update(existing.id, document, validation_mode="permissive")
            print(f"\nReused {DOMAIN_ID}: new revision {v1.revision} ({v1.validation_mode})")
        ontology_id = v1.ontology_id
        assert ontology_id is not None
        await client.ontology.activate(v1.id)
        print(f"Activated revision {v1.revision}")

        # 5. The connected client still extracts against the old ontology ------
        describe_binding(client, "after activate()")
        client = await reconnect(client)
        describe_binding(client, "after reconnecting")

        # 6. Ingest under revision 1 -------------------------------------------
        session_id = f"ontology-lifecycle-bolt-{uuid4().hex[:8]}"
        for message in TRANSCRIPT:
            await client.short_term.add_message(session_id, message["role"], message["content"])
        print(f"\nStored {len(TRANSCRIPT)} message(s) in session {session_id}. Entities:")
        await print_session_entities(client, session_id)

        tickets = await label_count(client, session_id, OLD_TYPE)
        if tickets == 0:
            raise SystemExit(
                f"No :{OLD_TYPE} node was extracted, so there is nothing to migrate. "
                f"Check the {OLD_TYPE} description in REPAIRS: GLiNER2.5 reads it as "
                "its annotation guideline."
            )

        # 7. Revise: rename a type and tighten validation ----------------------
        revised = rename_entity_type(document, OLD_TYPE, NEW_TYPE)
        v2 = await client.ontology.update(ontology_id, revised, validation_mode="strict")
        print(
            f"\nRevision {v2.revision}: {OLD_TYPE} -> {NEW_TYPE}, "
            f"validation mode {v1.validation_mode} -> {v2.validation_mode}"
        )

        # 8. Diff the two revisions --------------------------------------------
        diff = await client.ontology.diff(
            ontology_id, from_revision=v1.revision, to_revision=v2.revision
        )
        print(f"Diff revision {v1.revision} -> {v2.revision}:")
        print(describe_section("entity types", diff.entity_types))
        print(describe_section("relationships", diff.relationships))
        if diff.mode_change:
            print(f"   validation mode: {diff.mode_change}")

        # 9. Migrate the entities already in the graph -------------------------
        # On bolt this runs inline, one transaction per label pair, and returns
        # a finished job. The dry run counts and changes nothing.
        print(f"\nMigrating :{OLD_TYPE} onto revision {v2.revision}:")
        mapping = [(OLD_TYPE, NEW_TYPE)]
        preview = await client.ontology.migrate(
            ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=mapping,
            dry_run=True,
        )
        print(f"   dry run: {preview.total} node(s) would be relabelled")
        job = await client.ontology.migrate(
            ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=mapping,
        )
        if job.status != "completed" or job.errored:
            raise SystemExit(f"Migration {job.id} did not complete: {job.error_message}")
        recorded = await client.ontology.get_migration(job.id)
        print(
            f"   migration {recorded.id}: {recorded.status}, "
            f"{recorded.processed} relabelled, {recorded.errored} errored"
        )

        # 10. Activate revision 2 and read the graph back -----------------------
        await client.ontology.activate(v2.id)
        client = await reconnect(client)
        describe_binding(client, f"after activating revision {v2.revision}")
        print(f"\nSession {session_id} after the migration:")
        await print_session_entities(client, session_id)
        print(
            f"   :{OLD_TYPE} {await label_count(client, session_id, OLD_TYPE)}, "
            f":{NEW_TYPE} {await label_count(client, session_id, NEW_TYPE)}"
        )

        restore = (
            f'await client.ontology.activate("{previous_version_id}")'
            if previous_version_id
            else f'await client.ontology.delete("{ontology_id}")'
        )
        print(
            f"\n{DOMAIN_ID} revision {v2.revision} (strict) is now the active ontology in "
            "this database. Every client that connects with use_active_ontology=True "
            "(the default) extracts against it — including other examples. Everything "
            f"this run wrote is kept. To restore the previous binding:\n   {restore}"
        )
        return {
            "session_id": session_id,
            "ontology_id": ontology_id,
            "previous_version_id": previous_version_id,
            "v1": v1.id,
            "v2": v2.id,
            "dry_run_total": preview.total,
            "relabelled": recorded.processed,
        }
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyError as missing:
        sys.exit(f"Set {missing} (and NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD) first.")
