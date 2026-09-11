# Ontology lifecycle (NAMS)

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Rename a type across a memory graph that is already full of extracted entities — without re-ingesting a single message.

`src/index.ts` walks one complete revision cycle of a support-desk domain model
on the hosted [Neo4j Agent Memory Service](https://memory.neo4jlabs.com) through
`OntologyClient`: import an [Arrows](https://arrows.app) diagram into an ontology
draft, activate it, ingest a support transcript under it, rename `Ticket` →
`SupportCase`, diff the two revisions, then run a migration job that re-labels
the entities already in the graph. It is the parity twin of the Python
[`examples/ontology-lifecycle/`](../../../examples/ontology-lifecycle/).

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## Why this is a graph story

A key–value or vector memory store cannot rename a type across its own history:
the type only exists inside the text of each record. Here the type is a label on
a node, so renaming it is a migration — one job, counted, dry-runnable — and
`diff` tells you exactly what changed between two immutable revisions before you
run it.

## What it shows

| Step | Calls | Why it's here |
|---|---|---|
| 1. Survey | `ontology.list()`, `ontology.getActive()` | System templates vs workspace-owned, and what extraction validates against *right now* |
| 2. Import | `ontology.import({ content, format: "arrows" })` | Converts `schemas/support-desk.arrows.json` into a draft — plus the conversion warnings, because the conversion guesses |
| 3. Activate | `ontology.create()` / `ontology.update()`, `ontology.activate()` | Persist the draft as an immutable revision, then bind it |
| 4. Ingest | `shortTerm.bulkAddMessages()`, `longTerm.waitForExtraction()`, `searchEntities()` | The entities NAMS extracts from the transcript are typed by the ontology you just activated |
| 5. Revise | `ontology.update({ validationMode: "strict" })` | `Ticket` → `SupportCase`, `permissive` → `strict`; revisions are immutable, so this mints revision 2 |
| 6. Diff | `ontology.diff(id, from, to)` | Structural `added` / `removed` / `renamed` / `modified` plus the mode change |
| 7. Migrate | `ontology.migrate({ typeMappings, dryRun })`, `ontology.getMigration(id)` | Re-labels the already-extracted entities. Dry run first, then for real — both polled to a terminal status |
| 8. Read back | `ontology.activate()`, `query.cypher()` | Count the new label in the graph to prove the rename landed |

### The draft is not persisted

`import()` returns a **draft** — a converted document plus warnings, stored
nowhere. `create()` is what persists it, and the ontology's identity comes from
the document's `domain` block (NAMS reads `domain.id` / `domain.name`), not from
the `name` option:

```ts
const draft = await client.ontology.import({ content: arrowsJson, format: "arrows" });
const document = draft.document!;
document.domain.id = "support-desk";          // identity lives here
const v1 = await client.ontology.create({
  name: "Support Desk",
  schema: document,
  validationMode: "permissive",
});
await client.ontology.activate(v1.id);        // activate binds a *version*
```

### Migrations are jobs, not calls

`migrate()` returns as soon as the job is queued; the re-labelling runs
server-side in batches. The job id is the only handle on progress, so poll it —
do not sleep a fixed amount and hope:

```ts
let job = await client.ontology.migrate(ontologyId, {
  fromVersionId: v1.id,
  toVersionId: v2.id,
  typeMappings: [{ from: "Ticket", to: "SupportCase" }],
  dryRun: true,        // counts only, touches nothing
  batchSize: 500,
});
while (job.status === undefined || !["completed", "failed"].includes(job.status)) {
  await new Promise((r) => setTimeout(r, 1000));
  job = await client.ontology.getMigration(job.id);
}
```

## Prerequisites

- Node.js 22+ (Node 20 is EOL)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com),
  on an endpoint with a `/vN` segment — the ontology routes need the REST
  transport
- **No `OPENAI_API_KEY` and no Neo4j.** `client.ontology` is hosted-only;
  extraction and embeddings run server-side

## Run it

```bash
cp .env.example .env       # set MEMORY_API_KEY
npm install
MEMORY_API_KEY=nams_xxxxxxxxxxxxxxxx npm start
```

Running without `MEMORY_API_KEY` fails immediately with a named error rather
than a transport 401. Re-running is safe: the script reuses the `support-desk`
ontology if the workspace already owns one and mints the next revision instead
of creating a duplicate. Clean up with
`await client.ontology.delete(ontologyId)`.

## Expected output

```
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
Extraction settled: true; 5 entity(ies) searchable
   Priya Raman (customer)
   TK-2210 (ticket)
   SO-4417 (order)
   Aurora Desk Lamp (product)
   TK-2211 (ticket)

Revision 2: Ticket -> SupportCase, validation mode permissive -> strict
Diff revision 1 -> 2:
   entity types: renamed=1 [Ticket -> SupportCase]
   relationships: modified=3 [OPENED, ABOUT, CONCERNS]
   validation mode: {"from":"permissive","to":"strict"}

Migrating existing entities onto revision 2:
   migration mig_7f31 queued (dry run), status=pending
   ... running: 1/2 node(s)
   ... completed: 2/2 node(s)
   migration completed (dry run): 2 re-labelled, 0 errored
   migration mig_8a02 queued (for real), status=pending
   ... running: 1/2 node(s)
   ... completed: 2/2 node(s)
   migration completed (for real): 2 re-labelled, 0 errored

Active: Support Desk revision 2 (strict)
Label counts after migration: [[["Entity","SupportCase"],2]]

Done. Inspect support-desk at https://memory.neo4jlabs.com. Clean up with: await client.ontology.delete("ont_01J…")
```

That is the line-for-line shape of a run; the numbers depend on your workspace —
extracted-entity counts on what the server pulls out of the transcript, the
system-template count on the deployment, and job ids are per-run.

## Tests

```bash
npm test
```

Runs the whole script offline against `test/fake-nams.ts` — no API key, no
network, no Neo4j. The fake stubs `fetch` rather than injecting a `Transport`, so
the SDK's real `RestTransport` route table is exercised (`import_ontology` →
`POST /ontologies/import`, the snake_case ontology bodies, the
`?from=&to=` diff query). It is stateful where the lesson is: the active ontology
is unbound until something is activated, entity search stays empty until the
second poll, and migration jobs go `pending` → `running` → `completed` — so the
test fails if `waitForExtraction()` or the migration polling loop is replaced by
a fixed sleep.

The SDK's [`@neo4j-labs/agent-memory/testing`](../../src/testing.ts) export ships
`BridgeTransport` for cross-language TCK conformance. It is deliberately **not**
used here: the ontology endpoints are REST-only (the Python SDK refuses them over
the bridge outright), so testing them through the bridge would assert a shape
that cannot exist in production.

## Editing the domain model

`schemas/support-desk.arrows.json` is a plain [Arrows](https://arrows.app)
export — open it in Arrows, change it, export it again. Node labels become entity
types (mapped onto POLE+O), node properties become typed properties, and
relationships become typed relationships. `import()` also accepts Neo4j Data
Importer, RDF, GraphQL, Cypher, LinkML and native documents — omit `format` to
let the service detect, or pass `url` to have it fetch the document (https only,
SSRF-guarded, rate-limited).

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [Tutorial: Ontology quickstart](https://neo4j.com/labs/agent-memory/tutorials/ontology-quickstart) (`docs/modules/ROOT/pages/tutorials/ontology-quickstart.adoc`) — cloning a system template, strict-mode rejections, cleanup.
- [Ontology API reference](https://neo4j.com/labs/agent-memory/reference/ontology-api).
- [Python parity example](../../../examples/ontology-lifecycle/) — the same eight steps through `client.ontology`.

---

_Verified against the in-tree `@neo4j-labs/agent-memory` (`file:../..`, `package.json` version 0.4.1, carrying the unreleased 0.6.0-dev ontology surface), TypeScript 5.9.3, vitest 5.0, tsx 4, Node 22+ — 2026-09-10, with the NAMS transport mocked (`npm test`). `ontology.import`, `diff`, `migrate` and `getMigration` are not in any published npm release, so this example must use the `file:` dependency rather than a version range. The migration path has not been exercised against the production deployment — run the dry run first._
