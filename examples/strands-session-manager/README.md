# Strands SessionManager — shared-brain demo

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Two Strands agents, two sessions, **one graph**. Conversations persist
> automatically; what one agent learns is retrievable by the other.

This example demonstrates `Neo4jSessionManager`, which maps a Strands
[`SessionManager`](https://strands-agents.github.io/sdk/) onto a
`neo4j-agent-memory` conversation. Text turns are persisted to Neo4j and
restored on the next run; long-term memories (entities, preferences) extracted
from one session become available to all other sessions that share the same
database — the "shared brain" pattern.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What this demonstrates

- **Automatic persistence + restore** — messages written by one manager
  instance are restored by the next instance for the same session.
- **Opt-in long-term retrieval injection** — pass `retrieval_config=` to
  have relevant memories prepended as a `<user_context>` block inside the
  user message (in-memory only; the stored message is always the original).
- **The shared-brain pattern** — N agents, N session managers, one graph:
  entities extracted from agent A's session are searchable by agent B.
  Phase 1.5 seeds long-term memory directly (what the extraction pipeline
  would populate automatically in a production deployment).
- **Write-behind buffer** — `append_message` queues the previous message;
  it is flushed on the next append or on `close()`, so guardrail redaction
  can rewrite the latest message before it ever reaches the backend.

## Prerequisites

- Neo4j 5.x running at `bolt://localhost:7687`
  (or set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`).
- `neo4j-agent-memory[strands]` installed:
  ```bash
  uv pip install "neo4j-agent-memory[strands]"
  # or, for the local sentence-transformers embedder:
  uv pip install "neo4j-agent-memory[strands,sentence-transformers]"
  ```

## Run

From the repo root:

```bash
make neo4j-start          # start local Neo4j (skippable if NEO4J_URI is set)
NEO4J_PASSWORD=test-password uv run python examples/strands-session-manager/main.py
```

The repo's Docker Neo4j container uses `test-password`. If your instance uses
a different password, set `NEO4J_PASSWORD` accordingly.

No LLM API key required — the demo drives the `SessionManager` API
directly and uses a local `sentence-transformers` embedder.

Expected output (Phase 1.5 seeds long-term memory directly so the
`<user_context>` injection block always fires):

```
Agent A persisted 2 messages to session 'kyc-session'.
Seeded long-term memory (entity + preference).
Agent B's question, with injected shared-brain context:
<user_context>
Relevant memory:
- [entity] Acme Corp (ORGANIZATION) — Company beneficially owned by Jane Doe
- [preference] compliance: Always verify beneficial ownership before credit decisions
</user_context>
What do we know about Acme Corp?
Restored 2 messages for 'kyc-session'.
```

The demo clears its two sessions at startup so each run starts fresh; remove
the reset block to watch history accumulate across runs.

## With a real agent

```python
from strands import Agent
from neo4j_agent_memory.integrations.strands import (
    Neo4jRetrievalConfig,
    Neo4jSessionManager,
    nams_context_graph_tools,
)

manager = Neo4jSessionManager.for_nams(          # MEMORY_API_KEY from env
    "support-42",
    retrieval_config=Neo4jRetrievalConfig(),
)
agent = Agent(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    tools=nams_context_graph_tools(),
    session_manager=manager,
)
```

## Files

| File | Purpose |
|---|---|
| `main.py` | Three-phase demo: persist (Agent A), inject context (Agent B), restore (Agent C). Runs without an LLM or API key. |

## Going further

- **How-to guide:** see `docs/modules/ROOT/pages/how-to/strands-session-manager.adoc`
  for retrieval tuning, NAMS setup, and multi-tenant patterns.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.4-dev (branch `strands-session-manager`)._
