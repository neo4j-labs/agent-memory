# Eval Harness Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Treat memory quality like any other regression: write labelled test cases, score them, watch the score over time.

This example shows the evaluation harness in `neo4j-agent-memory`. You define an `EvalSuite` of labelled cases — expected entity ids for retrieval, expected `:TOUCHED` paths for audit, expected active preferences per user — and `client.eval.run(suite)` produces a structured report you can diff between commits.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **`AuditCase`** — assert that a given entity is reachable through `:TOUCHED` from one or more expected `ReasoningStep` ids. Catches silent breakage in reasoning-trace wiring.
- **`PreferenceCase`** — assert that a user's *active* preferences match an expected set. Catches preference dedupe and supersede regressions.
- **`EvalSuite` / `client.eval.run(suite)`** — runs all cases, returns an `EvalReport` with per-dimension scores and an overall.
- **Multi-tenant scoping** — cases use `user_identifier=` and verify the multi-tenant memory isolation introduced in v0.2.

The harness intentionally pairs with `tests/integration/test_eval_harness.py` — the same primitives drive both your hand-written eval suites and the library's own regression coverage.

## Files

| File | Purpose |
|---|---|
| `main.py` | Seeds a small deterministic dataset (one user, one preference, one trace touching one entity), defines a tiny suite, runs it, prints the report. |

## Prerequisites

- Neo4j 5.x running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory` installed (demo runs without an LLM).

## Run

From the repo root:

```bash
uv run python examples/eval-harness/main.py
```

You should see:

```
=== Eval report ===
Overall: 1.00
Audit:    cases=1 score=1.00
Pref:     cases=1 score=1.00
```

A failing case prints the expected vs. actual ids — copy that into a fixture and you have a regression test.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/evaluation.adoc`](../../docs/modules/ROOT/pages/how-to/evaluation.adoc) — design rationale, how to grow a suite without it becoming flaky, recall@k for retrieval cases.
- **Companion example:** [`examples/audit-trail/`](../audit-trail/) — produces the `:TOUCHED` edges that `AuditCase` is checking against.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.2-dev (branch `adopt-existing-graph`) on 2026-05-03._
