# Audit-Trail Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Wire reasoning steps to the entities they touched, then ask: *"every reasoning trace that ever touched this client."*

This example shows the reasoning-region polish in `neo4j-agent-memory`: explicit `:TOUCHED` edges from `ReasoningStep` → `Entity`, an automatic hook that infers them from tool-call results, and a structured `TraceOutcome` you can index on. The headline payoff is a one-hop audit query that is fast and explainable.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **`record_tool_call(touched_entities=[...])`** — explicit `:TOUCHED` edge writes from a reasoning step to one or more entities.
- **`@client.reasoning.on_tool_call_recorded`** — register a per-app hook that infers `EntityRef` lists from the tool name and arguments. Hook errors are logged, never raised.
- **`TraceOutcome`** — structured, indexable outcome on `complete_trace(outcome=...)`: success flag, summary, `error_kind` for failure-mode analytics, related entities, and metrics.
- **The headline audit query** — a single one-hop `MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)`. Fast and explainable.

## Files

| File | Purpose |
|---|---|
| `main.py` | Registers the observer hook, runs a trace, completes it with a structured outcome, prints the audit query results. |
| `tool_calls.py` | Domain-specific mapping from tool names to `EntityRef` lists. Hand-written per agent — not auto-derivable. |
| `queries.cypher` | The headline audit query plus error-kind and per-entity history queries you can run in `cypher-shell`. |

## Prerequisites

- Neo4j 5.x running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory` installed in your environment (the demo runs without an LLM — `llm=None`, sentence-transformers embedder).

## Run

From the repo root:

```bash
uv run python -m examples.audit-trail.main
```

You should see the audit query produce a row that links `Anthem` back through a `:TOUCHED` edge to a `:ReasoningStep` and its parent `:ReasoningTrace`:

```
Audit trail for Anthem:
  - task: Recommend a team for Anthem
    thought: Look up consultants who match Anthem's needs
    outcome: {"success": true, "summary": "Recommended a 2-person team for Anthem", ...}
```

Then run the supplemental queries in `cypher-shell`:

```bash
cypher-shell -a $NEO4J_URI -u $NEO4J_USERNAME -p $NEO4J_PASSWORD < examples/audit-trail/queries.cypher
```

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/audit-reasoning.adoc`](../../docs/modules/ROOT/pages/how-to/audit-reasoning.adoc) — design rationale, error-kind taxonomy, indexing tips.
- **Companion example:** [`examples/eval-harness/`](../eval-harness/) — runs a labelled audit-coverage test against `:TOUCHED` paths.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.2-dev (branch `adopt-existing-graph`) on 2026-05-03._
