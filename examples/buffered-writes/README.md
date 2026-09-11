# Buffered Writes Example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The agent's response to the user is *not* blocked on Neo4j round-trips — and this example measures by how much.

This example shows the fire-and-forget write API in `neo4j-agent-memory`. With `write_mode="buffered"`, calls to `client.buffered.submit(...)` queue Cypher writes and return immediately; a background drain task talks to Neo4j out-of-band. You call `client.flush()` at shutdown (or between bursts) and inspect `client.write_errors` for any background failures.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** As of `neo4j-agent-memory` v0.3 you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-opus-5", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html).

## What this demonstrates

- **`MemorySettings.memory.write_mode = "buffered"`** — opt-in fire-and-forget mode.
- **`MemorySettings.memory.max_pending`** — bound the queue so sustained overload applies back-pressure instead of silently growing memory. The library default is 200; section 3 of the run drops it to 4 so you can see `submit()` block.
- **`client.buffered.submit(query, params)`** — enqueue a write and return immediately.
- **`client.buffered.pending`** — live queue depth.
- **`client.flush()`** — drain the queue (blocks until queued writes have committed). `client.wait_for_pending()` is an alias, and `close()` / leaving the `async with` block drains too.
- **`client.write_errors`** — background failures since startup, each a `BufferedWriteError` with the originating Cypher, the exception and a timestamp.
- **Where the boundary is** — `client.short_term.add_message(...)` returns a `Message`, so it commits inline; the derived writes nothing in the turn reads back (an audit node, a session counter) are what goes on the fire-and-forget channel.

When to reach for it: agent turns where the user-visible latency budget cannot absorb a write round-trip — typing-feel chat UIs, streaming token responses where a side-effectful write would otherwise stall the stream, ingestion pipelines where you want the producer decoupled from Neo4j throughput.

## Files

| File | Purpose |
|---|---|
| `main.py` | Runs the same sequential 50-turn agent loop in `sync` then `buffered` mode and prints both totals; then shows queue depth, back-pressure with a small `max_pending`, and a populated `client.write_errors`. |

## Prerequisites

- Neo4j 5.26 LTS or 2025.x+ running at `bolt://localhost:7687` (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory` installed with the `sentence-transformers` extra (`uv sync --extra sentence-transformers`) — the demo uses a local embedder and no LLM, so no API key is needed.
- **Bolt only.** `client.buffered` raises `NotSupportedError` on the hosted NAMS backend, which commits writes server-side; `client.write_errors` is always empty there. See [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends).

## Run

From the repo root:

```bash
uv run python examples/buffered-writes/main.py
```

You should see something like:

```
1. Sync vs buffered — same loop, same writes, measured
   sync:        271.6 ms for 50 turns (5.43 ms/turn)
   buffered:    157.4 ms for 50 turns (3.15 ms/turn)
   → 1.7x faster on the user-visible path, 2.28 ms saved per turn
   (each turn also does one inline add_message — identical in both modes)

2. Queue depth
   pending when the last turn returned: 2  # varies: 0 = drainer kept up
   pending after flush():               0  # the invariant that matters

3. Back-pressure (50 submits, roomy queue vs. max_pending=4)
   max_pending=200:    0.04 ms, queue peaked at 50
   max_pending=4:     61.40 ms, queue peaked at 4
   submit() blocks once the queue is full — bounded memory, no dropped writes

4. Error channel (one deliberately invalid write)
   query:  'MERGE (t:AgentTurn {turn: $turn}) SET t.'
   error:  CypherSyntaxError
   when:   2026-09-10T22:15:36.616932+00:00
   submit() and flush() both returned normally — nothing raised

Verification
   AgentTurn rows for the buffered session: 50
   AgentTurn rows for the sync session:     50
```

Reading the numbers:

- The ratio in section 1 is **machine- and network-dependent**. Against a local Docker Neo4j each round-trip is only a couple of milliseconds, so the gap is small; against a remote database where a round-trip costs 20–50 ms, removing two derived writes per turn from the critical path matters far more. Each turn also performs one inline `add_message` that is identical in both modes, which is why the buffered total is not near-zero.
- `pending` in section 2 varies run to run — it is a live `qsize()`. Only the post-`flush()` value is guaranteed, and it is always 0.
- Section 4 also emits a `WARNING` from the library's drainer on stderr (`Buffered write failed: … Error retained at client.write_errors[0]`). Its interleaving with stdout varies; the failure is retained either way.

## Going further

- **How-to guide:** [`docs/modules/ROOT/pages/how-to/buffered-writes.adoc`](../../docs/modules/ROOT/pages/how-to/buffered-writes.adoc) — backpressure semantics, error handling, when *not* to buffer (writes you'll read back immediately).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0 and Neo4j 5.26 (Docker) on 2026-09-10. The buffered-write API shipped in v0.2.0._
