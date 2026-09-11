"""Ontology lifecycle on NAMS — import, activate, ingest, diff, migrate.

An *ontology* is the typed, versioned, validated schema the hosted **Neo4j
Agent Memory Service (NAMS)** extracts against: entity types (each mapped onto
a POLE+O ``pole_type``) with typed properties, plus typed relationships. This
script walks one whole revision cycle of a support-desk domain:

1. **Survey** — ``ontology.list()`` (system templates + workspace-owned) and
   ``ontology.get_active()`` (what extraction validates against right now).
2. **Import** — convert ``schemas/support-desk.arrows.json`` (an
   https://arrows.app export) into a native draft with ``ontology.import_()``.
   The draft is *not persisted*; ``create()`` persists it as revision 1.
3. **Activate** — bind revision 1, then read ``get_active()`` back.
4. **Ingest** — ``bulk_add_messages()`` a support transcript, then
   ``wait_for_extraction()`` and ``search_entities()``: the entities NAMS pulls
   out of those messages are typed by the ontology we just activated.
5. **Revise** — rename ``Ticket`` → ``SupportCase`` and tighten the mode to
   ``strict``; ``update()`` mints revision 2 (revisions are immutable).
6. **Diff** — ``ontology.diff(from_revision, to_revision)`` reports exactly
   what changed between the two, including the validation-mode change.
7. **Migrate** — ``ontology.migrate(type_mappings=[("Ticket", "SupportCase")])``
   re-labels the *already-extracted* entities. Run once with ``dry_run=True``
   for counts, once for real, polling ``get_migration(job.id)`` both times.
   Nothing is re-ingested: the graph is re-typed in place.
8. **Read back** — activate revision 2 and count the new label with
   ``query.cypher()``.

This is the hosted-only half of the library's schema story. Its bolt-side twin
is ``client.schema.adopt_existing_graph()`` (see ``examples/existing-graph/``);
``client.schema`` is bolt-only and ``client.ontology`` is NAMS-only — each
raises ``NotSupportedError`` on the other backend.

Run:

    cp .env.example .env     # set MEMORY_API_KEY
    uv pip install -r requirements.txt
    uv run python main.py

Re-running is safe: the script reuses the ``support-desk`` ontology if it
already exists in the workspace and mints the next revision instead of
creating a second one.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from neo4j_agent_memory import NamsMemoryClient, NamsSettings, connect
from neo4j_agent_memory.core.exceptions import NotFoundError, NotSupportedError
from neo4j_agent_memory.nams import MigrationJob, OntologyDocument, OntologyVersion

HERE = Path(__file__).parent
ARROWS_FILE = HERE / "schemas" / "support-desk.arrows.json"

#: Ontology identity lives in the document's ``domain`` block, not in the
#: ``name=`` kwarg — NAMS reads ``domain.id`` / ``domain.name``.
DOMAIN_ID = "support-desk"
DOMAIN_NAME = "Support Desk"

OLD_TYPE = "Ticket"
NEW_TYPE = "SupportCase"

CONVERSATION_NAME = "ontology-lifecycle-demo"

#: A support transcript that mentions every type in the ontology, so
#: server-side extraction has something to type.
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

#: Polling knobs. Kept as module constants so the offline smoke test can shrink
#: them without patching the logic.
EXTRACTION_TIMEOUT = 60.0
EXTRACTION_POLL_INTERVAL = 1.0
MIGRATION_TIMEOUT = 120.0
MIGRATION_POLL_INTERVAL = 1.0

#: Statuses a migration job never leaves.
TERMINAL_STATUSES = frozenset({"completed", "failed"})


def load_env() -> None:
    """Load this directory's ``.env`` so the documented setup step has an effect."""
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


def rename_entity_type(document: OntologyDocument, old: str, new: str) -> OntologyDocument:
    """Return a copy of ``document`` with one entity type relabelled.

    Relationship endpoints reference entity types by label, so they have to be
    rewritten too — otherwise revision 2 describes relationships between a type
    that no longer exists and NAMS rejects the update.
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
    """Render one half of an :class:`OntologyDiff` (entity types or relationships).

    The leaf shapes are server-defined dicts (they mirror the full ontology type
    system), so read them defensively rather than modelling them here.
    """
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
    for key in ("label", "type", "name"):
        if item.get(key):
            return str(item[key])
    return json.dumps(item, sort_keys=True)


async def run_migration(
    client: NamsMemoryClient,
    *,
    ontology_id: str,
    from_version: OntologyVersion,
    to_version: OntologyVersion,
    dry_run: bool,
) -> MigrationJob:
    """Enqueue a label-rename migration and poll it to a terminal status.

    ``migrate()`` returns as soon as the job is *queued* — the re-labelling runs
    server-side in batches, so the job id is the only handle on progress. Poll
    it; do not sleep a fixed amount and hope.
    """
    # `client.ontology` is typed `Any` (the accessor is backend-dependent), so
    # annotate the job to keep the rest of this function type-checked.
    job: MigrationJob = await client.ontology.migrate(
        ontology_id,
        from_version_id=from_version.id,
        to_version_id=to_version.id,
        type_mappings=[(OLD_TYPE, NEW_TYPE)],
        dry_run=dry_run,
        batch_size=500,
    )
    mode = "dry run" if dry_run else "for real"
    print(f"   migration {job.id} queued ({mode}), status={job.status}")

    deadline = time.monotonic() + MIGRATION_TIMEOUT
    while job.status not in TERMINAL_STATUSES:
        if time.monotonic() >= deadline:
            print(f"   still {job.status} after {MIGRATION_TIMEOUT:.0f}s — stopped polling")
            return job
        await asyncio.sleep(MIGRATION_POLL_INTERVAL)
        job = await client.ontology.get_migration(job.id)
        print(f"   ... {job.status}: {job.processed or 0}/{job.total or 0} node(s)")

    if job.status == "failed":
        print(f"   migration failed: {job.error_message}")
    else:
        print(
            f"   migration {job.status} ({mode}): {job.processed or 0} re-labelled, "
            f"{job.errored or 0} errored"
        )
    return job


async def main() -> None:
    load_env()

    if not os.environ.get("MEMORY_API_KEY"):
        raise SystemExit(
            "Set MEMORY_API_KEY to your NAMS API key. "
            "The ontology surface is hosted-only. "
            "Sign up at https://memory.neo4jlabs.com to get one."
        )

    settings = NamsSettings()
    client = await connect(settings)
    print(f"Connected to {settings.nams.endpoint} (backend={client.backend})")

    try:
        # 1. Survey the workspace ---------------------------------------------
        # `list()` returns NAMS' system templates plus anything this workspace
        # owns, with the active one flagged.
        summaries = await client.ontology.list()
        system = [s for s in summaries if s.is_system]
        owned = [s for s in summaries if not s.is_system]
        print(
            f"\nOntologies visible: {len(system)} system template(s), {len(owned)} workspace-owned"
        )
        for summary in owned:
            flag = " (active)" if summary.is_active else ""
            print(f"   {summary.name} rev {summary.current_revision}{flag}")

        try:
            active = await client.ontology.get_active()
        except (NotFoundError, NotSupportedError):
            # A fresh workspace has nothing bound: extraction falls back to
            # plain POLE+O until you activate something. The service 404s;
            # a body the SDK cannot parse surfaces as NotSupportedError.
            print("Active ontology: none bound yet (extraction uses plain POLE+O)")
        else:
            print(
                f"Active ontology: {active.document.domain.name} "
                f"(revision {active.revision}, {active.validation_mode})"
            )

        # 2. Import the Arrows document into a draft ---------------------------
        # NAMS converts Arrows / Neo4j Data Importer / RDF / GraphQL / Cypher /
        # LinkML / native documents. The result is a *draft*: nothing is stored
        # until `create()`.
        arrows_json = ARROWS_FILE.read_text(encoding="utf-8")
        draft = await client.ontology.import_(content=arrows_json, format="arrows")
        if draft.document is None:
            raise SystemExit(
                f"NAMS could not convert {ARROWS_FILE.name} into an ontology: "
                f"{[w.message for w in draft.warnings]}"
            )

        document = draft.document
        print(
            f"\nImported {ARROWS_FILE.name} (detected format: {draft.detected_format}, "
            f"suggested name: {draft.suggested_name}): "
            f"{len(document.entity_types)} entity type(s), "
            f"{len(document.relationships)} relationship(s)"
        )
        for entity_type in document.entity_types:
            properties = ", ".join(p.name for p in entity_type.properties) or "no properties"
            print(f"   {entity_type.label} -> {entity_type.pole_type} ({properties})")
        for warning in draft.warnings:
            # Conversion is lossy by nature — Arrows has no POLE+O column, so
            # the converter guesses and tells you where it did.
            print(f"   warning [{warning.code}] {warning.message}")

        # Identity comes from the document's `domain` block, so set it
        # explicitly instead of inheriting whatever the converter guessed.
        document.domain.id = DOMAIN_ID
        document.domain.name = DOMAIN_NAME
        document.domain.description = (
            "E-commerce support desk: customers, orders, products, tickets"
        )
        document.domain.emoji = "🎧"

        # 3. Persist as a revision and activate it -----------------------------
        existing = next((s for s in owned if s.name == DOMAIN_ID), None)
        if existing is None:
            v1 = await client.ontology.create(DOMAIN_NAME, document, validation_mode="permissive")
            print(f"\nCreated {DOMAIN_ID} revision {v1.revision} ({v1.validation_mode})")
        else:
            # Re-run: revisions are immutable, so this mints the next one
            # rather than overwriting revision 1.
            v1 = await client.ontology.update(existing.id, document, validation_mode="permissive")
            print(f"\nReused {DOMAIN_ID}: new revision {v1.revision} ({v1.validation_mode})")

        ontology_id = v1.ontology_id
        await client.ontology.activate(v1.id)
        active = await client.ontology.get_active()
        print(
            f"Activated: {active.document.domain.name} revision {active.revision} "
            f"({active.validation_mode}) — extraction now validates against it"
        )

        # 4. Ingest under the active ontology ----------------------------------
        conversation = await client.short_term.create_conversation(CONVERSATION_NAME)
        conversation_id = str(conversation.id)
        stored = await client.short_term.bulk_add_messages(conversation_id, TRANSCRIPT)
        print(f"\nConversation {conversation_id}: stored {len(stored)} message(s) in one request")

        # Extraction is a background pipeline; await it rather than sleeping.
        settled = await client.long_term.wait_for_extraction(
            session_id=conversation_id,
            expected_names=["Priya Raman"],
            timeout=EXTRACTION_TIMEOUT,
            interval=EXTRACTION_POLL_INTERVAL,
        )
        extracted = await client.long_term.search_entities("support ticket order", limit=10)
        print(f"Extraction settled: {settled}; {len(extracted)} entity(ies) searchable")
        for entity in extracted:
            print(f"   {entity.display_name} ({entity.full_type})")

        # 5. Revise: rename a type and tighten validation ----------------------
        revised = rename_entity_type(document, OLD_TYPE, NEW_TYPE)
        v2 = await client.ontology.update(ontology_id, revised, validation_mode="strict")
        print(
            f"\nRevision {v2.revision}: {OLD_TYPE} -> {NEW_TYPE}, "
            f"validation mode {v1.validation_mode} -> {v2.validation_mode}"
        )

        # 6. Diff the two revisions -------------------------------------------
        diff = await client.ontology.diff(
            ontology_id, from_revision=v1.revision, to_revision=v2.revision
        )
        print(f"Diff revision {diff.from_revision} -> {diff.to_revision}:")
        print(describe_section("entity types", diff.entity_types))
        print(describe_section("relationships", diff.relationships))
        if diff.mode_change:
            print(f"   validation mode: {diff.mode_change}")

        # 7. Migrate the already-extracted entities ---------------------------
        # This is the payoff: the graph gets re-typed without re-ingesting a
        # single message. Dry run first — it reports counts and touches nothing.
        print("\nMigrating existing entities onto revision 2:")
        await run_migration(
            client,
            ontology_id=ontology_id,
            from_version=v1,
            to_version=v2,
            dry_run=True,
        )
        await run_migration(
            client,
            ontology_id=ontology_id,
            from_version=v1,
            to_version=v2,
            dry_run=False,
        )

        # 8. Activate revision 2 and read the new label back -------------------
        await client.ontology.activate(v2.id)
        active = await client.ontology.get_active()
        print(
            f"\nActive: {active.document.domain.name} revision {active.revision} "
            f"({active.validation_mode})"
        )

        rows = await client.query.cypher(
            f"MATCH (e:Entity) WHERE e:{NEW_TYPE} OR e:{OLD_TYPE} "
            "RETURN labels(e) AS labels, count(*) AS count "
            "ORDER BY count DESC"
        )
        print(f"Label counts after migration: {rows}")

        print(
            f"\nDone. Inspect {DOMAIN_ID} at https://memory.neo4jlabs.com. "
            f"Clean up with: await client.ontology.delete({ontology_id!r})"
        )
    finally:
        # `connect()` hands back an already-connected client, so we own closing
        # it. `async with MemoryClient(settings)` does this for you, at the cost
        # of the NAMS-typed layers.
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
