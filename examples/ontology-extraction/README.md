# Ontology-Driven Extraction Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> One YAML file types the extraction, constrains the relations and steers the resolution — six support-desk messages, ten entity nodes, seven typed edges, no API key.

This example is the v0.7 ontology surface end to end. `ontology.yaml` is an `OntologyDocument`: six entity labels mapped onto POLE+O, five relationship types with explicit source/target and cardinality constraints, and a description on every type that GLiNER2.5 reads as an annotation guideline. `main.py` points `MemorySettings` at that file and stores six messages — everything else follows from the ontology.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **`schema_config=SchemaConfig(ontology_path=...)`** — the ontology is resolved once at connect time and handed to the extractor, the resolver and both write paths. `client.ontology_document` and `client.validation_mode` show what the client settled on.
- **Descriptions as annotation guidelines** — each `description` ends in the negative case ("The thing that broke, never the fix for it."). GLiNER2.5 serialises them into the encoder input, so they are the highest-leverage thing in the file.
- **Joint entity + relation decoding** — one GLiNER2.5 (JointIE) pass per message produces typed entities *and* typed relations, with the ontology's head/tail types enforced during beam search. No LLM, no separate relation model.
- **Per-relation constraints** — `unique_source: true` on `EMPLOYED_BY` (one employer per person), `acyclic: true` on `PART_OF`, and a deliberately low `threshold: 0.35` because employment is stated obliquely in support text.
- **Alias resolution on the ingestion path** (on by default since v0.7) — "Acme Corp", "Acme" and "ACME Corporation" arrive across two sessions and collapse onto **one** node with the other two as `aliases`, via the gazetteer declared in the ontology.
- **The review band** — "Acme Bank" shares a whole-token prefix with "Acme Corp", scores 0.88 (between `review_threshold` 0.85 and `auto_merge_threshold` 0.90) and is therefore *not* merged: it gets a pending `SAME_AS` edge for a human to confirm or reject.
- **Typed relations with provenance** — the relationship type stays `RELATED_TO`, the MERGE key includes `r.type`, and `r.support` counts how many times the triple was observed. `EMPLOYED_BY` reaches `support=2` across two sessions *because* the two organization surface forms resolved to the same node.
- **`client.ontology` on bolt** — `create()`, `activate()`, `get_active()`, `update()` (revision 2 adds `ASSIGNED_TO`), `diff(1, 2)` and `delete()`, all against `:Ontology`/`:OntologyVersion` nodes in your own database.

## Files

| File | Purpose |
|---|---|
| `ontology.yaml` | The ontology: `Customer`/`Engineer` (PERSON), `Organization`, `Product`/`Component` (OBJECT), `Incident` (EVENT); `EMPLOYED_BY`, `REPORTED`, `USES`, `AFFECTS`, `PART_OF`. Edit this file first. |
| `main.py` | Loads it, configures a keyless client, ingests six messages, then prints the nodes, the typed edges, the review band and an ontology diff. |
| `requirements.txt` | The two extras this needs, pinned. |

## Prerequisites

- Neo4j 5.26 LTS or 2025.x+ at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`). From the repo root, `make neo4j-start` launches one with password `test-password` — the defaults point at it.
- The extraction and local-embedding extras:

  ```bash
  pip install -r examples/ontology-extraction/requirements.txt
  # or, in this repo
  uv sync --extra extraction --extra sentence-transformers
  ```

  First run downloads GLiNER2.5 `fastino/gliner2.5-base-v1` (~407 MB) and `all-MiniLM-L6-v2` (~90 MB) once, then reads them from `~/.cache/huggingface`.
- **No API key.** `llm=None` plus a local sentence-transformers embedder; nothing leaves the machine.
- **Bolt only.** On the hosted NAMS backend extraction and resolution run server-side and the ontology lives in the workspace — see [`ontology-lifecycle/`](../ontology-lifecycle/) for the hosted twin.

Connection details are read from `examples/.env` if present (copy [`examples/.env.example`](../.env.example)); there is no per-example `.env`.

## Run

From the repo root:

```bash
make example-ontology-extraction
# or
uv run python examples/ontology-extraction/main.py
```

Validate or compile the ontology on its own — no database, no weights for `validate`:

```bash
uv run neo4j-agent-memory ontology validate examples/ontology-extraction/ontology.yaml
uv run neo4j-agent-memory ontology compile  examples/ontology-extraction/ontology.yaml
```

## Expected output

Trimmed; the run is idempotent, so a second run prints the same counts.

```
Ontology: support-desk — Customers, engineers, products and the incidents that connect them.
  label Customer      -> PERSON:CUSTOMER, threshold=0.55
  label Organization  -> ORGANIZATION
  …
  5 relationship types, 8 endpoint-typed patterns:
    Customer -[EMPLOYED_BY]-> Organization  [unique_source]
    Component -[PART_OF]-> Product  [acyclic]

client resolved ontology: support-desk (6 labels, validation_mode=permissive)
stored ontology: 9e98325350db… revision 1 (permissive), active = support-desk r1

entity nodes (10):
  importer                 OBJECT:COMPONENT    mentions=4
  Acme Corp                ORGANIZATION        mentions=3  aliases=['Acme', 'ACME Corporation']
  INC-4471                 EVENT:INCIDENT      mentions=3
  Dana Whitfield           PERSON:CUSTOMER     mentions=2
  Acme Bank                ORGANIZATION        mentions=1

typed edges (7):
  (Dana Whitfield)-[:RELATED_TO {type: 'EMPLOYED_BY'}]->(Acme Corp)  support=2 confidence=0.98 derived=False
  (importer)-[:RELATED_TO {type: 'PART_OF'}]->(Nightshade Enterprise)  support=2 confidence=0.97 derived=False
  (Acme Corp)-[:RELATED_TO {type: 'USES'}]->(Nightshade Enterprise)  support=2 confidence=0.97 derived=False
  (INC-4471)-[:RELATED_TO {type: 'AFFECTS'}]->(Nightshade)  support=1 confidence=0.85 derived=False
  (Dana Whitfield)-[:RELATED_TO {type: 'REPORTED'}]->(INC-4471)  support=1 confidence=0.93 derived=False

pending SAME_AS review pairs (1):
  'Acme Bank' ~ 'Acme Corp'  score=0.88 (neither node was merged)

diff r1 -> r2: relationships added ['ASSIGNED_TO'], removed 0, entity types 0 added

Summary
  10 entity nodes from 21 MENTIONS edges across 6 messages
  7 typed edges over 5 relationship types (max support 2)
  1 pending SAME_AS pair(s) awaiting review
```

Reading it:

- **Ten nodes from twenty-one mentions** is the resolution working. The headline row is `Acme Corp` with `aliases=['Acme', 'ACME Corporation']`: three surface forms, two sessions, one node.
- **`support=2`** on `EMPLOYED_BY` is the pay-off of that merge — the same triple observed in both sessions, not two near-duplicate edges.
- **`Dana` and `Dana Whitfield` stay separate.** A bare first name scores below the review band against the full name, so the resolver creates a second node rather than guessing. Lower `resolution.review_threshold`, or add `aliases: {Dana Whitfield: [Dana]}` under `Customer`, to change that — and see the "how to adapt" list below.
- `derived=False` everywhere means every edge was asserted by the decoder, not inferred from an `inverse:` mirror.

## How to adapt the ontology

Work in `ontology.yaml`; nothing in `main.py` knows the domain.

1. **Rewrite the descriptions first.** They are prompt surface, not documentation. Name the thing, then name the confusable thing it is not. This moves accuracy more than any threshold.
2. **Add your labels**, each with `pole_type` (one of PERSON, ORGANIZATION, LOCATION, EVENT, OBJECT) and an optional `subtype`. The pair becomes the node's `type`/`subtype` and its PascalCase Neo4j labels.
3. **Declare relationships with explicit `source`/`target` labels.** Several entries may share one `type` (`EMPLOYED_BY` appears twice here); they compile into a single JointIE relation with head/tail lists.
4. **Tune per-relation `threshold`** when a relation is stated obliquely (start near 0.35) or over-fires (raise toward 0.6).
5. **Constrain the shape**: `unique_source` / `unique_target` for cardinality, `acyclic` for containment, `inverse` for a mirrored pair — never a `symmetric` flag.
6. **Feed resolution** with `aliases` (canonical name → surface forms) and, per type, `resolution_threshold` / `review_threshold` overrides.
7. **Check it before you run it**: `ontology validate` reports structural problems (undeclared endpoints, bad inverse pairs, non-POLE+O `pole_type`) and `ontology compile` prints the exact JointIE schema the extractor will use.

`validation_mode: strict` (via `SchemaConfig(validation_mode="strict")`) turns the ontology into enforcement: undeclared labels and forbidden endpoint pairs raise instead of being written. This example stays `permissive`.

## Going further

- **How-to guide:** [Ontology-driven extraction](https://neo4j.com/labs/agent-memory/how-to/ontology-driven-extraction) — authoring, activation, strict vs permissive.
- **How-to guide:** [Tune entity resolution](https://neo4j.com/labs/agent-memory/how-to/tune-entity-resolution) — thresholds, aliases, the review band.
- **Reference:** [Extractors](https://neo4j.com/labs/agent-memory/reference/extractors) — `GLiNER2Extractor`, model sizes, windowing and the `feasible=False` warning.
- **Hosted twin:** [`ontology-lifecycle/`](../ontology-lifecycle/) — the same lifecycle against NAMS, including `import_` from an arrows.app diagram and a type migration.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `neo4j-agent-memory` v0.7.0 with gliner2 2.0.0 (`fastino/gliner2.5-base-v1`), sentence-transformers 5.7.0, and Neo4j 5.26 (Docker) on 2026-09-17._
