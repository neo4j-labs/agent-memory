# Ontology Lifecycle (Bolt)

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Rename a type across a graph that is already full of extracted entities — on your own Neo4j, with no API key.

The bolt twin of [`ontology-lifecycle/`](../ontology-lifecycle/), which runs the
same story on the hosted service. Same Arrows diagram, same support transcript:
import the diagram into an ontology draft, activate it, ingest the transcript
under it, rename `Ticket` → `SupportCase`, diff the two revisions, then migrate
the entities already in the graph. Here the ontology is stored in your database
as `:Ontology` / `:OntologyVersion` nodes and drives local GLiNER2.5 extraction.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> [!WARNING]
> **The run leaves `support-desk` revision 2 (strict) active in the database.**
> Activation is per database: every client that connects to it with
> `schema_config.use_active_ontology=True` (the default) extracts against
> `support-desk` from then on, other examples included. Use a dedicated
> database. The script keeps everything it writes and prints the one call that
> restores the previous binding. That is why `make examples` does not run it.

## NAMS and bolt, side by side

| | [`ontology-lifecycle/`](../ontology-lifecycle/) (NAMS) | This example (bolt) |
|---|---|---|
| Where the ontology lives | Server-side, per workspace | `:Ontology` / `:OntologyVersion` nodes in your database |
| Arrows import | Converted by the service, which infers POLE+O types | Converted locally; every label falls back to `OBJECT`, so the script repairs the draft |
| Extraction | Server-side and asynchronous; the script polls `wait_for_extraction()` | In-process during `add_message`, by GLiNER2.5 |
| When activation takes effect | Immediately, for the workspace | At the next connect: a client resolves its ontology once |
| `diff` of a rename | Reports it as `renamed` | Reports the type as `removed` plus `added` |
| `migrate` | Queues a job you poll with `get_migration()` | Runs inline and returns a finished job |
| Node labels | Set by the service | The ontology's label for the entity's exact type and subtype: `:Entity:Event:Ticket` |

## What the script does

| Step | Calls | Why it's here |
|---|---|---|
| 1. Survey | `ontology.list()`, `ontology.get_active()` | The eight built-in templates, what is stored, and what is active right now |
| 2. Import | `ontology.import_(content=…, format="arrows")` | Converts `schemas/support-desk.arrows.json` into a draft, with a warning for every type the conversion guessed |
| 3. Repair | `repair_draft()` | Sets `Customer` → `PERSON`, `Order` / `Ticket` → `EVENT`, and a description on every type. GLiNER2.5 reads descriptions as annotation guidelines |
| 4. Activate | `ontology.create()` / `ontology.update()`, `ontology.activate()` | Persists the draft as an immutable revision and binds it |
| 5. Reconnect | `client.ontology_document`, `connect()` | Shows the connected client still on the old ontology after `activate()`, then on revision 1 after reconnecting |
| 6. Ingest | `short_term.add_message()` | GLiNER2.5 extracts against revision 1; tickets are written as `:Entity:Event:Ticket`. The script stops if none was extracted |
| 7. Revise | `ontology.update(validation_mode="strict")` | `Ticket` → `SupportCase`, `permissive` → `strict`: revision 2 |
| 8. Diff | `ontology.diff(from_revision, to_revision)` | What changed between the revisions, including the validation mode |
| 9. Migrate | `ontology.migrate(type_mappings=…, dry_run=…)`, `ontology.get_migration()` | A dry run counts the `:Ticket` nodes; the real run relabels them `:SupportCase` |
| 10. Read back | `ontology.activate()`, `connect()`, `query.cypher()` | Activates revision 2, reconnects, and counts both labels |

## Files

| File | Purpose |
|---|---|
| `main.py` | The whole lifecycle. Reads `NEO4J_URI`, `NEO4J_USERNAME` and `NEO4J_PASSWORD` with no fallback values |
| `schemas/support-desk.arrows.json` | The Arrows diagram, identical to the NAMS twin's copy |
| `requirements.txt` | The `gliner2` and `sentence-transformers` extras, pinned |
| `.env.example` | Connection values for the local Docker Neo4j |

## Prerequisites

- Python 3.10+
- Neo4j 5.26 LTS or 2025.x+, ideally a dedicated database (see the warning above). From the repository root, `make neo4j-start` launches a throwaway one with password `test-password`.
- The extras: `pip install -r examples/ontology-lifecycle-bolt/requirements.txt`. Model weights (GLiNER2.5 ~407 MB, MiniLM ~90 MB) download once on the first run and are cached.
- **No API key.** The client runs with `llm=None` and a local embedder.

## Run

From the repository root, against the local Docker Neo4j:

```bash
make example-ontology-lifecycle-bolt
```

Or against any database:

```bash
cp examples/ontology-lifecycle-bolt/.env.example examples/ontology-lifecycle-bolt/.env
# edit .env, or export NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD instead
uv run python examples/ontology-lifecycle-bolt/main.py
```

## Expected output

From a run against an empty database (the session id and ontology id differ per run):

```
Connected to bolt://localhost:7687 (backend=bolt)

Ontologies: 8 built-in template(s), 0 stored in this database
Active ontology: none bound (the client falls back to POLE+O)
   client ontology at connect: poleo (permissive)

Imported support-desk.arrows.json (detected format: arrows): 4 entity type(s), 5 relationship(s)
   warning [unmapped_label] Label 'Customer' has no POLE+O mapping; it fell back to OBJECT:CUSTOMER. Set pole_type explicitly if that is wrong.
   warning [unmapped_label] Label 'Order' has no POLE+O mapping; it fell back to OBJECT:ORDER. Set pole_type explicitly if that is wrong.
   warning [unmapped_label] Label 'Ticket' has no POLE+O mapping; it fell back to OBJECT:TICKET. Set pole_type explicitly if that is wrong.
Repaired draft:
   Customer -> PERSON:CUSTOMER
   Order -> EVENT:ORDER
   Product -> OBJECT:PRODUCT
   Ticket -> EVENT:TICKET

Created support-desk revision 1 (permissive)
Activated revision 1
   client ontology after activate(): poleo (permissive)
   client ontology after reconnecting: support-desk (permissive)

Stored 6 message(s) in session ontology-lifecycle-bolt-93a9475d. Entities:
   SO-4390                EVENT:ORDER      :Entity:Event:Order
   SO-4417                EVENT:ORDER      :Entity:Event:Order
   TK-2210                EVENT:TICKET     :Entity:Event:Ticket
   TK-2211                EVENT:TICKET     :Entity:Event:Ticket
   Aurora Desk Lamp       OBJECT:PRODUCT   :Entity:Object:Product
   Northwind Cable Tray   OBJECT:PRODUCT   :Entity:Object:Product
   Priya                  PERSON:CUSTOMER  :Entity:Person:Customer
   Priya Raman            PERSON:CUSTOMER  :Entity:Person:Customer

Revision 2: Ticket -> SupportCase, validation mode permissive -> strict
Diff revision 1 -> 2:
   entity types: added=1 [SupportCase], removed=1 [Ticket]
   relationships: added=3 [Customer-OPENED->SupportCase, SupportCase-ABOUT->Order, SupportCase-CONCERNS->Product], removed=3 [Customer-OPENED->Ticket, Ticket-ABOUT->Order, Ticket-CONCERNS->Product]
   validation mode: {'from': 'permissive', 'to': 'strict'}

Migrating :Ticket onto revision 2:
   dry run: 2 node(s) would be relabelled
   migration 62a72b0761b54289bf5e50569173d12f: completed, 2 relabelled, 0 errored
   client ontology after activating revision 2: support-desk (strict)

Session ontology-lifecycle-bolt-93a9475d after the migration:
   SO-4390                EVENT:ORDER      :Entity:Event:Order
   SO-4417                EVENT:ORDER      :Entity:Event:Order
   TK-2210                EVENT:TICKET     :Entity:Event:SupportCase
   TK-2211                EVENT:TICKET     :Entity:Event:SupportCase
   Aurora Desk Lamp       OBJECT:PRODUCT   :Entity:Object:Product
   Northwind Cable Tray   OBJECT:PRODUCT   :Entity:Object:Product
   Priya                  PERSON:CUSTOMER  :Entity:Person:Customer
   Priya Raman            PERSON:CUSTOMER  :Entity:Person:Customer
   :Ticket 0, :SupportCase 2

support-desk revision 2 (strict) is now the active ontology in this database. Every client that connects with use_active_ontology=True (the default) extracts against it — including other examples. Everything this run wrote is kept. To restore the previous binding:
   await client.ontology.delete("bc739c44c99149eeb3595d1b28560b36")
```

`Priya` and `Priya Raman` stay two nodes: a first name is a whole-token prefix
of the full name, which ingest-time resolution never merges on its own. The
pair waits in the review band (`long_term.find_potential_duplicates()`).

In a database that already holds entities, mentions resolve onto them: a
`Priya Raman` another example wrote as an engineer keeps that type.

## Reruns and restoring the binding

A rerun reuses `support-desk`: it mints the next two revisions, reactivates the
`Ticket` revision, and ingests the transcript into a new session. The tickets
resolve onto the nodes the previous run migrated, pick up `:Ticket` again
under the reactivated revision, and the migration renames them once more.

The final line names the call that restores the binding the run found:

- `await client.ontology.activate("<version id>")` when another version was
  active before the run;
- `await client.ontology.delete("<ontology id>")` when nothing was. Deleting
  removes `support-desk` and its revisions and migration records; the entities
  and their labels stay.

The script stops with an error, rather than running a migration that changes
nothing, if GLiNER2.5 extracts no `Ticket`. If that happens, sharpen the
`Ticket` description in `REPAIRS` in `main.py`.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

> _Verified against `neo4j-agent-memory` 0.7.0 (in-tree, branch `gliner-2.5`), `gliner2` 2.0.0 with `fastino/gliner2.5-base-v1`, `sentence-transformers/all-MiniLM-L6-v2`, Python 3.12 and Neo4j 5.26 Community (Docker) on 2026-10-01: two consecutive runs on one empty database (the output above is the first; the second minted revisions 3 and 4 and migrated the same two tickets), plus `tests/examples/test_ontology_lifecycle_bolt_example.py` against a testcontainers Neo4j._
