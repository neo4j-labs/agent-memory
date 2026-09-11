# Existing Graph Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Layer `neo4j-agent-memory` on top of the production graph you already have — no duplicate nodes, no migration script, idempotent.

This is the headline example for the *adopt an existing graph* workflow. By default the library `MERGE`s entities on `(:Entity {name, type})`. If your existing graph has nodes labelled `:Person`, `:Movie`, `:Client`, etc. — none of which carry `:Entity` — those merges create duplicates. `client.schema.adopt_existing_graph(...)` attaches the `:Entity` super-label and the library's required `id`/`type`/`name` properties to your existing nodes so library writes link to them instead.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** As of `neo4j-agent-memory` v0.3 you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-opus-5", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html).

## What this demonstrates

- **`client.schema.adopt_existing_graph(label_to_type=..., name_property_per_label=..., dry_run=...)`** — one call to attach the library's super-label and properties to nodes from a pre-existing schema. Idempotent; re-runnable safely; `--dry-run` first.
- **`SchemaModel.CUSTOM`** — configure `MemorySettings` so library writes target your domain types (`MOVIE`, `GENRE`, …) instead of the default POLE+O ontology. The only runnable example in the repo using a non-POLE+O domain.
- **Deterministic mention linking** — `add_message(extraction_mode="explicit", explicit_mentions=[EntityRef(...)])` links a message to exactly the adopted nodes it talks about: no NER model, no API key, and the only path that currently links to adopted nodes at all (see "Known gaps").
- **Retrieval over the adopted graph** — backfill embeddings, then `long_term.search_entities()`, `long_term.add_relationship()` / `get_related_entities()`, and a `client.query.cypher()` traversal of the graph's own `ACTED_IN` / `DIRECTED` / `IN_GENRE` edges.
- **Adoption-aware `name_property_per_label`** — tells the library that `:Movie` nodes use `title` rather than `name` for their display name.
- **No-LLM, no-API-key path** — runs with `llm=None` and a local sentence-transformers embedder.

## Files

| File | Purpose |
|---|---|
| `memory_settings.py` | `build_settings()` plus the adoption mapping (`LABEL_TO_TYPE`, `NAME_PROPERTY_PER_LABEL`) and demo constants shared by every script. |
| `seed_domain_graph.cypher` | Three `:Person`, three `:Movie`, two `:Genre` nodes with relationships. Pre-library style — no `:Entity` labels, no library properties. Stand-in for "your existing production graph". MERGE-only. |
| `seed.py` | Runs the seed file through the library's Neo4j client (no host `cypher-shell` needed). `--reset` deletes *only* the seed labels, and only with `EXISTING_GRAPH_ALLOW_RESET=1`. |
| `adopt.py` | Calls `client.schema.adopt_existing_graph(...)`. `--dry-run` reports the projection without writing. |
| `memory_io.py` | Writes messages with explicit mentions and asserts that exactly one node exists per name across *all* labels, with `MENTIONS` edges on the adopted nodes. |
| `retrieve.py` | Embedding backfill, `search_entities`, a library relation write, and a domain-relationship traversal. |
| `run.sh` | seed → dry-run → adopt → write/verify → retrieve. Run this for the full demo. |

## Prerequisites

- **Bolt only.** `client.schema.adopt_existing_graph()` raises `NotSupportedError` on the hosted NAMS backend, where schema is server-managed. See [`explanation/backends.adoc`](../../docs/modules/ROOT/pages/explanation/backends.adoc).
- Neo4j 5.26 LTS or 2026.x. Start the repo's test container with `make neo4j-start` from the repo root (it listens on `bolt://localhost:7687` with `neo4j` / `test-password` — the defaults used here), or export `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.
- `neo4j-agent-memory` installed with the `sentence-transformers` extra so the local embedder works: `uv sync --extra sentence-transformers` (or `uv sync --all-extras`).

> **Where to run the seed.** `seed.py` is MERGE-only and never deletes anything, but it *does* write `:Person` / `:Movie` / `:Genre` nodes — run it against a scratch database. `adopt.py`, `memory_io.py` and `retrieve.py` are the parts you can point at a graph you care about (start with `adopt.py --dry-run`).

## Run

From the repo root:

```bash
bash examples/existing-graph/run.sh
```

Or step by step:

```bash
uv run python examples/existing-graph/seed.py
uv run python examples/existing-graph/adopt.py --dry-run
uv run python examples/existing-graph/adopt.py
uv run python examples/existing-graph/memory_io.py
uv run python examples/existing-graph/retrieve.py
```

You should see:

```
==> 2/4 Projecting adoption (dry run), then adopting...
Would adopt 8 nodes (0 already adopted, 0 skipped).
  Person → PERSON: +3 new, =0 already, ~0 skipped
  Movie → MOVIE: +3 new, =0 already, ~0 skipped
  Genre → GENRE: +2 new, =0 already, ~0 skipped
  (projection only — re-run without --dry-run to apply)
Adopted 8 nodes (0 already adopted, 0 skipped).
…
Nodes per demo name (1 means library writes hit the adopted node):
  Arrival        total=1   ['Entity:Movie']
  Bob Singh      total=1   ['Entity:Person']
  Carol Reyes    total=1   ['Entity:Person']
  Inception      total=1   ['Entity:Movie']

MENTIONS edges produced by add_message():
  Arrival        MOVIE    Entity:Movie
  …
2. search_entities('science fiction film', entity_types=['MOVIE'])
    Inception      MOVIE    similarity=0.720
    The Matrix     MOVIE    similarity=0.684
    Arrival        MOVIE    similarity=0.666
```

Re-run `bash examples/existing-graph/run.sh` to confirm idempotency: the second run reports `already adopted` for every node, still finds exactly one node per name, and embeds nothing new.

## How adoption works

`adopt_existing_graph(label_to_type, *, name_property_per_label=None, dry_run=False)`:

1. For each input label, attaches the `:Entity` super-label.
2. Sets `type` from the mapping, `name` from the configured property (defaulting to `name`), and keeps any existing `id` property — falling back to a deterministic `<label_lc>:<name>` id so re-runs produce the same value.
3. Skips nodes that lack the configured name property and reports them in `report.by_label[i].skipped_count`.
4. Returns an `AdoptionReport` with per-label counts. With `dry_run=True` nothing is written.

After adoption, library writes that `MERGE` on `(:Entity {name, type})` — explicit mentions, relation writes, `add_entity`, preference targeting — land on your existing domain nodes instead of creating duplicates. (Automatic NER extraction is the exception; see "Known gaps" below.)

## Known gaps (verified against v0.5.0)

These are library limitations the example works around rather than hides:

- **Automatic extraction cannot link to an adopted node.** Two silent failure modes, both reproduced on Neo4j 5.26 with v0.5.0: (a) the extractors map their labels through POLE+O, so a `MOVIE` mention is typed `OBJECT`, MERGEs a second `:Entity:Object` node and links the mention to the duplicate; (b) give the extractor a `label_mapping` that preserves `MOVIE` and the MERGE *does* hit the adopted node, but no `MENTIONS` edge appears at all, because `ShortTermMemory._extract_and_link_entities` links by the id it generated instead of the id the MERGE returned (the adopted node kept its own). This is why the example links mentions explicitly with `EntityRef` — that path links by the id MERGE returns, so no extraction extras are needed either.
- **`schema_config.strict_types` is declarative.** It governs `long_term.add_entity()` validation, never extractor output — and `MemoryClient` does not currently forward `schema_config` to `LongTermMemory`, so the flag does not reject out-of-schema types today.
- **Adopted ids must be UUID-shaped for the read helpers.** Nodes adopted with a generated `<label_lc>:<name>` id (here: the `:Genre` nodes, which have no `id` of their own) raise `ValueError: badly formed hexadecimal UUID string` if they come back from `search_entities()`, which hydrates `Entity.id` as a `UUID`. `retrieve.py` skips embedding those nodes; they stay reachable through `client.query.cypher()`.
- **Adoption writes no embedding.** Semantic search only finds adopted nodes after a backfill — see step 1 of `retrieve.py`.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/adopt-existing-graph.adoc`](../../docs/modules/ROOT/pages/how-to/adopt-existing-graph.adoc) — full design, name-property gotchas, how to bring your own ontology.
- **Reference:** [`docs/modules/ROOT/pages/reference/schema-objects.adoc`](../../docs/modules/ROOT/pages/reference/schema-objects.adoc) — declarative constraints and indexes the library expects.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0 with Neo4j 5.26.19 on 2026-09-10._
