# Existing Graph Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Layer `neo4j-agent-memory` on top of the production graph you already have — no duplicate nodes, no migration script, idempotent.

This is the headline example for the v0.2 *adopt an existing graph* workflow. By default the library `MERGE`s entities on `(:Entity {name, type})`. If your existing graph has nodes labelled `:Person`, `:Movie`, `:Client`, etc. — none of which carry `:Entity` — those merges create duplicates. `client.schema.adopt_existing_graph(...)` attaches the `:Entity` super-label and the library's required `id`/`type`/`name` properties to your existing nodes so library writes link to them instead.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **`client.schema.adopt_existing_graph(label_to_type=..., name_property_per_label=...)`** — one call to attach the library's super-label and properties to nodes from a pre-existing schema. Idempotent; re-runnable safely.
- **`SchemaModel.CUSTOM`** — configure `MemorySettings` so library writes target your domain types (`MOVIE`, `GENRE`, …) instead of the default POLE+O ontology. This is the first runnable example in the repo using a non-POLE+O domain.
- **No-LLM, no-API-key path** — runs with `llm=None` and a local sentence-transformers embedder so you can try it offline.
- **Adoption-aware `name_property_per_label`** — tells the library that `:Movie` nodes use `title` rather than `name` for their display name.

## Files

| File | Purpose |
|---|---|
| `seed_domain_graph.cypher` | Loads three `:Person`, three `:Movie`, two `:Genre` nodes with relationships. Pre-library style — no `:Entity` labels, no library properties. Stand-in for "your existing production graph." |
| `memory_settings.py` | `build_settings()` — `SchemaModel.CUSTOM` with the same domain types as the seed graph; no LLM. |
| `adopt.py` | Calls `client.schema.adopt_existing_graph(...)` with the per-label name-property mapping. |
| `memory_io.py` | Writes a couple of messages and verifies that `MENTIONS` edges link to the existing domain nodes (no duplicates created). |
| `run.sh` | Loads → adopts → writes → verifies. Run this for the full demo. |

## Prerequisites

- Neo4j 5.x at `bolt://localhost:7687`. Either start the test container with `make neo4j-start` from the repo root, or point `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` at your own instance.
- `neo4j-agent-memory` installed with the `sentence-transformers` extra so the local embedder works.

## Run

From the repo root:

```bash
bash examples/existing-graph/run.sh
```

You should see:

```
Adopted 8 nodes (0 already adopted, 0 skipped).
  Person → PERSON: +3 new, =0 already, ~0 skipped
  Movie  → MOVIE:  +3 new, =0 already, ~0 skipped
  Genre  → GENRE:  +2 new, =0 already, ~0 skipped
…
Per-name node count after writes (1 means adoption worked):
  Bob Singh    -> 1
  Carol Reyes  -> 1
  Inception    -> 1
  Arrival      -> 1
```

Re-run `bash examples/existing-graph/run.sh` to confirm idempotency: the second run reports `already adopted` for every node and creates no duplicates.

## How adoption works

`adopt_existing_graph(label_to_type, *, name_property_per_label=None, dry_run=False)`:

1. For each input label, attaches the `:Entity` super-label.
2. Sets `type` from the mapping, `name` from the configured property (defaulting to `name`), and `id` from any existing `id` property — falling back to a deterministic `label:name` hash so re-runs produce the same id.
3. Skips nodes that lack the configured name property and reports them in `report.by_label[i].skipped_count`.
4. Returns an `AdoptionReport` with per-label counts.

After adoption, library APIs that `MERGE` on `(:Entity {name, type})` — message-mention extraction, relation writes, preference targeting — link to your existing domain nodes instead of creating duplicates.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/adopt-existing-graph.adoc`](../../docs/modules/ROOT/pages/how-to/adopt-existing-graph.adoc) — full design, name-property gotchas, how to bring your own ontology.
- **Reference:** [`docs/modules/ROOT/pages/reference/schema-objects.adoc`](../../docs/modules/ROOT/pages/reference/schema-objects.adoc) — declarative constraints and indexes the library expects.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.2-dev (branch `adopt-existing-graph`) on 2026-05-03._
