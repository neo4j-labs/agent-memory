# Buffered Writes Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The agent's response to the user is *not* blocked on Neo4j round-trips.

This example shows the fire-and-forget write API in `neo4j-agent-memory`. With `write_mode="buffered"`, calls to `client.buffered.submit(...)` queue Cypher writes and return immediately; a background drain task talks to Neo4j out-of-band. You call `client.flush()` at shutdown (or between bursts) and inspect `client.write_errors` for any background failures.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **`MemorySettings.memory.write_mode = "buffered"`** — opt-in fire-and-forget mode.
- **`MemorySettings.memory.max_pending`** — bound the queue to fail fast under sustained backpressure rather than silently growing memory.
- **`client.buffered.submit(query, params)`** — enqueue a write and return immediately.
- **`client.buffered.pending`** — current queue depth.
- **`client.flush()`** — drain the queue (blocks until queued writes have committed).
- **`client.write_errors`** — list of background failures since startup, each with the originating Cypher.

When to reach for it: agent turns where the user-visible latency budget cannot absorb a write round-trip — typing-feel chat UIs, streaming token responses where a side-effectful write would otherwise stall the stream, ingestion pipelines where you want the producer decoupled from Neo4j throughput.

## Files

| File | Purpose |
|---|---|
| `main.py` | Builds settings with `write_mode="buffered"`, runs 50 simulated agent turns concurrently, times the user-visible path vs. the flush, prints any background errors. |

## Prerequisites

- Neo4j 5.x running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory` installed (demo runs without an LLM).

## Run

From the repo root:

```bash
uv run python examples/buffered-writes/main.py
```

You should see something like:

```
50 turns produced 50 responses in 12.3 ms
Pending writes after responses returned: 14
flush() drained the queue in 95.2 ms
AgentTurn rows in Neo4j: 50
No buffered-write errors.
```

The 12 ms result-return time is the point — without buffering, each turn would block on a Neo4j round-trip.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/buffered-writes.adoc`](../../docs/modules/ROOT/pages/how-to/buffered-writes.adoc) — backpressure semantics, error handling, when *not* to buffer (writes you'll read back immediately).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.2-dev (branch `adopt-existing-graph`) on 2026-05-03._
