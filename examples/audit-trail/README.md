# Audit-Trail Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Wire reasoning steps to the entities they touched, then ask: *"every reasoning trace that ever touched this client."*

This example shows the reasoning audit edges in `neo4j-agent-memory`: explicit `:TOUCHED` edges from `ReasoningStep` → `Entity`, an automatic hook that infers them from tool-call results, and a structured `TraceOutcome` you can index on. The headline payoff is a one-hop audit query that is fast and explainable — and, because the trace is linked to the message that triggered it, the trail also answers *who asked for this?*

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** As of `neo4j-agent-memory` v0.3 you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-sonnet-5", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html). This demo needs neither: it runs with `llm=None` and a local sentence-transformers embedder.

> 🔌 **Bolt only.** `:TOUCHED` edges are a bolt-side schema feature. Against the hosted NAMS backend, `touched_entities` is silently dropped today and `client.graph` (used here for the idempotency reset) raises `NotSupportedError`. The audit *read* goes through `client.query.cypher`, which is portable across both backends.

## What this demonstrates

- **Approach 1 — `record_tool_call(touched_entities=[...])`** — explicit `:TOUCHED` edge writes when you already know the entities at call time.
- **Approach 2 — `@client.reasoning.on_tool_call_recorded`** — register a per-app hook that infers `EntityRef` lists from the tool name, arguments and *result*. Hook errors are logged, never raised. Both approaches converge on the same graph shape: the edge is keyed on the (step, entity) pair, so a duplicate write is a no-op.
- **`TraceOutcome`** — structured, indexable outcome on `complete_trace(outcome=...)`: success flag, summary, `error_kind` for failure-mode analytics, related entities and metrics. The run records one successful and one **failed** trace against the same client, so the error-kind query has something to find.
- **`start_trace(triggered_by_message_id=...)`** — an `INITIATED_BY` edge back to the user message, so the audit query can return the question that caused the work.
- **The headline audit query** — a single one-hop `MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)`, returning `TraceOutcome` as queryable columns (`rt.outcome`, `rt.success`, `rt.error_kind`, `rt.metrics_json`) rather than an opaque blob.
- **`client.reasoning.get_tool_stats()`** — the read side: pre-aggregated per-tool call counts and success rates.
- **Entity types are uppercase strings.** The `:TOUCHED` MERGE stores `type` verbatim, so `type="Client"` would create a second `:Entity` node that `add_entity` (which uppercases) can never match.

## Files

| File | Purpose |
|---|---|
| `main.py` | Registers the hook, records one successful and one failing trace, then prints the audit query results, the touched entities and the tool stats. |
| `tool_calls.py` | Domain-specific mapping from tool names to `EntityRef` lists. Hand-written per agent — not auto-derivable. |
| `queries.cypher` | The headline audit query plus error-kind and per-entity history queries you can run in `cypher-shell`. |

## Prerequisites

- Neo4j 5.26 LTS or 2026.x running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory` installed in your environment. No LLM and no API key: the demo runs with `llm=None` and the local `sentence-transformers/all-MiniLM-L6-v2` embedder.

The script is rerunnable — it deletes its own session's traces, steps, tool calls and messages before recording new ones.

## Run

From the repo root:

```bash
uv run python examples/audit-trail/main.py
```

Expected output:

```
Audit trail for Anthem (2 step(s)):
  - task: Add a FedRAMP specialist to the Anthem team
    triggered_by: Can you add a FedRAMP specialist to that team?
    thought: Search the bench for FedRAMP experience
    summary: No consultants matched the required skills
    success: False  error_kind: timeout
    metrics: {"tools_called": 1.0}
  - task: Recommend a team for Anthem
    triggered_by: Who should we staff on the Anthem engagement?
    thought: Look up consultants who match Anthem's needs
    summary: Recommended a 2-person team for Anthem
    success: True  error_kind: None
    metrics: {"tools_called": 2.0}

Entities touched by the successful trace:
  - CLIENT: Anthem
  - INDUSTRY: Healthcare
  - PERSON: Liam
  - PERSON: Sara

Tool usage:
  - find_consultants: 1 call(s), success rate 0%
  - lookup_industry: 1 call(s), success rate 100%
  - recommend_team: 1 call(s), success rate 100%
```

Both rows reach `Anthem` through a `:TOUCHED` edge to a `:ReasoningStep` and its parent `:ReasoningTrace` — one successful, one timed out.

Then run the supplemental queries in `cypher-shell`:

```bash
cypher-shell -a $NEO4J_URI -u $NEO4J_USERNAME -p $NEO4J_PASSWORD < examples/audit-trail/queries.cypher
```

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/audit-reasoning.adoc`](../../docs/modules/ROOT/pages/how-to/audit-reasoning.adoc) — design rationale, error-kind taxonomy, indexing tips.
- **Companion example:** [`examples/eval-harness/`](../eval-harness/) — runs a labelled audit-coverage test against `:TOUCHED` paths.
- **Keeping the trail small:** `client.consolidation.summarize_long_traces()` compacts long traces, and `client.consolidation.record_read_audit()` logs memory reads. See [`docs/modules/ROOT/pages/how-to/consolidation.adoc`](../../docs/modules/ROOT/pages/how-to/consolidation.adoc).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0 and Neo4j 5.26 (community, with APOC) on 2026-09-10._
