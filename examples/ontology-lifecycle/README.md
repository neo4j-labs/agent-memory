# Ontology Lifecycle (NAMS)

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Rename a type across a memory graph that is already full of extracted entities — without re-ingesting a single message.

`main.py` walks one complete revision cycle of a support-desk domain model on
the hosted **Neo4j Agent Memory Service (NAMS)**: import an
[Arrows](https://arrows.app) diagram into an ontology draft, activate it, ingest
a support transcript under it, rename `Ticket` → `SupportCase`, diff the two
revisions, then run a migration job that re-labels the entities already in the
graph.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Why this is a graph story

A key–value or vector memory store cannot rename a type across its own history:
the type only exists inside the text of each record. Here the type is a label on
a node, so renaming it is a migration — one job, counted, dry-runnable,
restartable — and `diff` tells you exactly what changed between two immutable
revisions before you run it.

## What the script does

| Step | Calls | Why it's here |
|---|---|---|
| 1. Survey | `ontology.list()`, `ontology.get_active()` | System templates vs workspace-owned, and what extraction validates against *right now* |
| 2. Import | `ontology.import_(content=…, format="arrows")` | Converts `schemas/support-desk.arrows.json` into a native draft — plus the conversion warnings, because the conversion guesses |
| 3. Activate | `ontology.create()` / `ontology.update()`, `ontology.activate()` | Persist the draft as an immutable revision, then bind it |
| 4. Ingest | `short_term.bulk_add_messages()`, `long_term.wait_for_extraction()`, `search_entities()` | The entities NAMS extracts from the transcript are typed by the ontology you just activated |
| 5. Revise | `ontology.update(validation_mode="strict")` | `Ticket` → `SupportCase`, `permissive` → `strict`; revisions are immutable, so this mints revision 2 |
| 6. Diff | `ontology.diff(from_revision, to_revision)` | Structural `added` / `removed` / `renamed` / `modified` plus the mode change |
| 7. Migrate | `ontology.migrate(type_mappings=…, dry_run=…)`, `ontology.get_migration(job_id)` | Re-labels the already-extracted entities. Dry run first, then for real — both polled to a terminal status |
| 8. Read back | `ontology.activate()`, `query.cypher()` | Count the new label in the graph to prove the rename landed |

### The draft is not persisted

`import_()` returns a **draft** — a converted document plus warnings, stored
nowhere. `create()` is what persists it, and the ontology's identity comes from
the document's `domain` block (NAMS reads `domain.id` / `domain.name`), not from
the `name=` argument:

```python
draft = await client.ontology.import_(content=arrows_json, format="arrows")
document = draft.document
document.domain.id = "support-desk"          # identity lives here
version = await client.ontology.create("Support Desk", document, validation_mode="permissive")
await client.ontology.activate(version.id)   # activate binds a *version*
```

### Migrations are jobs, not calls

`migrate()` returns as soon as the job is queued; the re-labelling runs
server-side in batches. The job id is the only handle on progress, so poll it —
do not sleep a fixed amount and hope:

```python
job = await client.ontology.migrate(
    ontology_id,
    from_version_id=v1.id,
    to_version_id=v2.id,
    type_mappings=[("Ticket", "SupportCase")],
    dry_run=True,           # counts only, touches nothing
    batch_size=500,
)
while job.status not in {"completed", "failed"}:
    await asyncio.sleep(1.0)
    job = await client.ontology.get_migration(job.id)
```

Run it once with `dry_run=True` to see the node count it would touch, then again
with `dry_run=False`. The example does both.

## Prerequisites

- Python 3.10+
- A NAMS API key from <https://memory.neo4jlabs.com>, on an endpoint with a
  `/vN` segment — the ontology routes need the REST transport
- **No Neo4j, no `OPENAI_API_KEY`, no embedding provider.** `client.ontology` is
  hosted-only; its bolt-side counterpart is
  [`client.schema.adopt_existing_graph()`](../existing-graph/)

## Setup

```bash
uv pip install -r requirements.txt
cp .env.example .env
# edit .env: set MEMORY_API_KEY
```

The script loads this directory's `.env` itself, so exporting is optional.
`MEMORY_WORKSPACE_ID` is **required by header-scoped deployments** (the
development/staging service); leave it unset on production keys.

## Run

```bash
MEMORY_API_KEY=nams_xxxxxxxxxxxxxxxx uv run python main.py
```

Re-running is safe: the script reuses the `support-desk` ontology if the
workspace already owns one and mints the next revision instead of creating a
duplicate. Clean up with `await client.ontology.delete(ontology_id)`.

## Expected output

```
Loaded environment from /path/to/examples/ontology-lifecycle/.env
Connected to https://memory.neo4jlabs.com/v1 (backend=nams)

Ontologies visible: 28 system template(s), 0 workspace-owned
Active ontology: none bound yet (extraction uses plain POLE+O)

Imported support-desk.arrows.json (detected format: arrows, suggested name: support-desk-import): 4 entity type(s), 5 relationship(s)
   Customer -> PERSON (email, tier, signed_up_at)
   Order -> EVENT (order_number, total, placed_at)
   Product -> OBJECT (sku, name, category)
   Ticket -> EVENT (reference, subject, severity, opened_at)
   warning [pole_type_inferred] Inferred pole_type EVENT for label 'Ticket'.

Created support-desk revision 1 (permissive)
Activated: Support Desk revision 1 (permissive) — extraction now validates against it

Conversation 3f6b2c51-6d0e-4f0a-9a3a-2c1b8e0f7a11: stored 6 message(s) in one request
Extraction settled: True; 5 entity(ies) searchable
   Priya Raman (CUSTOMER)
   TK-2210 (TICKET)
   SO-4417 (ORDER)
   Aurora Desk Lamp (PRODUCT)
   TK-2211 (TICKET)

Revision 2: Ticket -> SupportCase, validation mode permissive -> strict
Diff revision 1 -> 2:
   entity types: renamed=1 [Ticket -> SupportCase]
   relationships: modified=3 [OPENED, ABOUT, CONCERNS]
   validation mode: {'from': 'permissive', 'to': 'strict'}

Migrating existing entities onto revision 2:
   migration mig_7f31 queued (dry run), status=pending
   ... running: 0/2 node(s)
   ... completed: 2/2 node(s)
   migration completed (dry run): 2 re-labelled, 0 errored
   migration mig_8a02 queued (for real), status=pending
   ... running: 1/2 node(s)
   ... completed: 2/2 node(s)
   migration completed (for real): 2 re-labelled, 0 errored

Active: Support Desk revision 2 (strict)
Label counts after migration: [{'labels': ['Entity', 'SupportCase'], 'count': 2}]

Done. Inspect support-desk at https://memory.neo4jlabs.com. Clean up with: await client.ontology.delete('ont_01J…')
```

That is the line-for-line shape of a run; the numbers depend on your workspace.
Extracted-entity counts depend on what the server pulls out of the transcript,
the system-template count on the deployment, and job ids are per-run. The
offline smoke test (`tests/examples/test_ontology_lifecycle_example.py`) drives
the same path with fixed payloads if you want to see it run without a key:

```bash
uv run pytest tests/examples/test_ontology_lifecycle_example.py
```

## Editing the domain model

`schemas/support-desk.arrows.json` is a plain [Arrows](https://arrows.app)
export — open it in Arrows, change it, export it again. Node labels become
entity types (mapped onto POLE+O), node properties become typed properties, and
relationships become typed relationships. `import_()` also accepts Neo4j Data
Importer, RDF, GraphQL, Cypher, LinkML and native documents — pass `format=None`
to let the service detect, or `url=` to have it fetch the document (https only,
SSRF-guarded, rate-limited).

## Bolt has a different story

`client.ontology` raises `NotSupportedError` on the bolt backend, and
`client.schema` raises it on NAMS. If you run your own Neo4j, the equivalent is
[`examples/existing-graph/`](../existing-graph/): `adopt_existing_graph()` maps
*your* labels onto library entity types in place, with `SchemaModel.CUSTOM` so
writes target your domain types instead of POLE+O. There is no migration job on
bolt — you own the Cypher.

## Going further

- **Step-by-step walkthrough:** [Ontology Quickstart](https://neo4j.com/labs/agent-memory/tutorials/ontology-quickstart) (`docs/modules/ROOT/pages/tutorials/ontology-quickstart.adoc`) — cloning a system template, strict-mode rejections, and cleanup.
- **API surface:** [Ontology API reference](https://neo4j.com/labs/agent-memory/reference/ontology-api).
- **TypeScript parity:** [`typescript/examples/ontology-lifecycle/`](../../typescript/examples/ontology-lifecycle/) runs this same lifecycle through `OntologyClient`.
- **First steps on NAMS:** [`nams-quickstart/`](../nams-quickstart/).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`), Python 3.12, with the NAMS transport mocked (`tests/examples/test_ontology_lifecycle_example.py`) — 2026-09-10. `ontology.import_`, `ontology.diff`, `ontology.migrate` and `ontology.get_migration` ship in the 0.6 line; until it is released, install the library from this repository (`uv pip install -e ../..`) rather than from PyPI. The migration path has not been exercised against the production deployment — run the dry run first._
