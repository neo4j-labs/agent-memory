# Eval Harness Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Treat memory quality like any other regression: write labelled test cases, score them, gate CI on the score, watch it over time.

This example shows the evaluation harness in `neo4j-agent-memory`. You define an `EvalSuite` of labelled cases — expected entity ids for retrieval, expected `:TOUCHED` paths for audit, expected active preferences per user — and `client.eval.run(suite)` produces a structured report you can diff between commits or fail a build on.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** As of `neo4j-agent-memory` v0.3 you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-sonnet-5", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html). This demo needs no LLM: it runs with `llm=None` and a local sentence-transformers embedder.

## What this demonstrates

- **`RetrievalCase`** — recall@k of `long_term.search_entities()` against labelled entity ids. Catches extraction, embedding and dedupe-threshold regressions.
- **`AuditCase`** — assert that an entity is reachable through `:TOUCHED` from the expected `ReasoningStep` ids. Catches silent breakage in reasoning-trace wiring.
- **`PreferenceCase`** — assert that a user's *active* preferences match an expected set, scored as F1 so both a missed supersede and a leaked cross-tenant preference count as failures.
- **`EvalSuite` / `client.eval.run(suite, dimensions=[...])`** — runs all cases (or a subset), returns an `EvalReport` with per-dimension scores, per-case `details`, and an overall.
- **Multi-tenant scoping** — `settings.memory.multi_tenant = True`, two tenants, and `user_identifier=` on every scoped write. Drop a kwarg and the seed raises instead of writing unscoped data.
- **A CI gate** — `main.py --min-score` exits non-zero on a regression; `ci_gate.py` adds a JSON report for `actions/upload-artifact`.

### Every case can fail

An eval suite that cannot go red is decoration. This one is built so each dimension has a reachable failure:

| Break this | What happens |
|---|---|
| Comment out the `supersede_preference` call in `seed()` | Both preferences stay active, tenant A's F1 drops to 0.67, the dimension to 0.83 |
| Remove a `user_identifier=` from a seed write | `ValueError` — `multi_tenant=True` refuses unscoped writes |
| Remove `touched_entities=` from `record_tool_call` | The audit dimension drops to 0.00 |
| Change `RETRIEVAL_QUERY` to something unrelated | Recall@k drops to 0.00 (the 0.7 similarity floor in `search_entities` applies) |

The harness intentionally pairs with `tests/integration/test_eval_harness.py` — the same primitives drive both your hand-written eval suites and the library's own regression coverage.

## Files

| File | Purpose |
|---|---|
| `main.py` | Seeds a deterministic fixture graph (two tenants, three entities, a trace touching one of them, a superseded preference), defines the suite, runs it, prints the report. Exits non-zero below `--min-score`. |
| `ci_gate.py` | The ten-line CI wrapper: reuses `main.py`, writes a JSON report with full per-case detail, exits 0 / 1 / 2. |

## Prerequisites

- Neo4j 5.26 LTS or 2026.x running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`). `make neo4j-start` from the repo root starts a suitable container.
- `neo4j-agent-memory` installed with the `sentence-transformers` extra (`uv sync --extra sentence-transformers`) — the demo uses a local embedder and no LLM, and the retrieval dimension needs real embeddings.

## Run

From the repo root:

```bash
uv run python examples/eval-harness/main.py
```

You should see:

```
=== Eval report ===
Overall:    1.00  (gate: >= 1.00)
Retrieval:  cases=1 score=1.00
Audit:      cases=1 score=1.00
Preference: cases=2 score=1.00
```

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--min-score` | `1.0` | Exit 1 when the overall score falls below this. |
| `--dimensions` | all | Comma-separated subset, e.g. `--dimensions retrieval,preference`. Skipped dimensions print as `skipped` and are `null` in the report. |
| `--out` | — | Append one JSONL row per run (`timestamp`, `overall`, per-dimension scores). Upload it as a CI artifact and plot the trend. |

### What a failure looks like

Revert the supersede in `seed()` and re-run — the gate prints the expected-vs-actual breakdown from `DimensionReport.details` and exits 1:

```
=== Eval report ===
Overall:    0.94  (gate: >= 1.00)
Retrieval:  cases=1 score=1.00
Audit:      cases=1 score=1.00
Preference: cases=2 score=0.83

--- preference misses ---
  case user_identifier='eval-user-a@demo'
    expected = ['c1b183f4-afc0-446d-9773-2bec88f5c6fe']
    actual   = ['c1b183f4-afc0-446d-9773-2bec88f5c6fe', 'ebf5cd83-ad05-44c9-862e-a07e27768ab2']
    scores   = {'recall': 1.0, 'precision': 0.5, 'f1': 0.6666666666666666}
FAIL: overall 0.94 < --min-score 1.00
```

Copy that into a fixture and you have a regression test.

## Use it in CI

`ci_gate.py` writes `eval-report.json` (overall, per-dimension scores, every case's expected-vs-actual) and exits 1 below the threshold, 2 if the run itself failed:

```yaml
- name: Memory-quality gate
  env:
    NEO4J_URI: bolt://localhost:7687
    NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}
  run: uv run python examples/eval-harness/ci_gate.py --min-score 0.9 --report eval-report.json

- name: Upload eval report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: eval-report
    path: eval-report.json
```

Start with `--min-score` at the score you see today, raise it as you clean up, and treat a drop as you would a failing unit test. Keep the per-run JSONL from `main.py --out` if you want the trend rather than the snapshot.

## Backend boundary

`client.eval.run()` is backend-portable, but this example's **seed** is bolt-only:

- `client.users.upsert_user()` and `client.graph.execute_write()` both raise `NotSupportedError` on the hosted NAMS backend.
- The `:TOUCHED` audit edges the audit dimension traverses are a bolt-side schema feature, so on NAMS the audit dimension has nothing to read.

On NAMS, seed through the memory APIs and run `--dimensions retrieval,preference`.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/evaluation.adoc`](../../docs/modules/ROOT/pages/how-to/evaluation.adoc) — design rationale, how to grow a suite without it becoming flaky, recall@k for retrieval cases.
- **Companion example:** [`examples/audit-trail/`](../audit-trail/) — produces the `:TOUCHED` edges that `AuditCase` is checking against.
- **Multi-tenancy:** [`docs/modules/ROOT/pages/how-to/multi-tenancy.adoc`](../../docs/modules/ROOT/pages/how-to/multi-tenancy.adoc) — what `multi_tenant=True` enforces and what it does not.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0, sentence-transformers 6.0.1, the `neo4j` 6.3.0 driver and Neo4j 5.26 (Docker) on 2026-09-10. The evaluation harness shipped in v0.2.0._
