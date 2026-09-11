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

> ℹ️ **Unreleased API.** `Neo4jSessionManager` is not in PyPI 0.5.0 — it ships
> in the next release. Until then, install the library from this repository
> (see *In your own project* below).

## What this demonstrates

- **Automatic persistence + restore** — messages written by one manager
  instance are restored by the next instance for the same session.
- **The real hook path** — the demo builds a `HookRegistry`, calls
  `registry.add_hook(manager)`, and dispatches `AgentInitializedEvent` /
  `MessageAddedEvent` / `AfterInvocationEvent`: exactly what
  `Agent(session_manager=manager)` does for you. No private methods are
  called, so the demo cannot drift from the real lifecycle.
- **Opt-in long-term retrieval injection** — pass `retrieval_config=` to
  have relevant memories prepended as a `<user_context>` block inside the
  user message (in-memory only; the stored message is always the original).
- **The shared-brain pattern** — N agents, N session managers, one graph:
  entities one session contributes are searchable by every other session.
  Entities are global; preferences are scoped to the manager's `user_id=`,
  so analyst-1's preference never reaches analyst-2's context block.
- **Write-behind buffer** — `append_message` queues the previous message;
  it is flushed on the next append or on `close()`, so guardrail redaction
  can rewrite the latest message before it ever reaches the backend.
- **Reasoning memory** — `record_tool_calls=True` mirrors `toolUse` blocks
  into a reasoning trace, the third memory layer.
- **`client.users`** — `user_id=` on the manager links writes to a `:User`
  node, read back here with `users.get_user()`.

## Prerequisites

- Neo4j 5.26 (or later) reachable at `bolt://localhost:7687`, with the
  credentials in `.env.example` — copy it and adjust, or export
  `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD`.
- `strands-agents` 1.52–1.55 (the range this integration is pinned to).
- No LLM or API key of any kind.

### From this repo

```bash
uv sync --all-extras
```

### In your own project

```bash
uv pip install "neo4j-agent-memory[strands,sentence-transformers] @ git+https://github.com/neo4j-labs/agent-memory@main"
```

Switch to `uv pip install "neo4j-agent-memory[strands,sentence-transformers]>=0.6.0"`
once the release carrying `Neo4jSessionManager` is on PyPI.

## Run

From the repo root:

```bash
make neo4j-start          # start local Neo4j (skippable if NEO4J_URI is set)
NEO4J_PASSWORD=test-password uv run python examples/strands-session-manager/main.py
```

The repo's Docker Neo4j container uses `test-password`. If your instance uses
a different password, set `NEO4J_PASSWORD` accordingly.

No LLM API key required — the demo drives the `SessionManager` hooks directly
and uses a local `sentence-transformers` embedder.

Expected output **on a fresh database**. Long-term search is database-wide, not
session-scoped, so an already-populated Neo4j adds its own `[entity]` /
`[fact]` lines and may show a different description for `Acme Corp`:

```
Seeded long-term memory (2 entities, 1 relationship, 2 preferences).
Agent A persisted 2 messages to session 'kyc-session'.
Agent B's question, with injected shared-brain context:
<user_context>
Relevant memory:
- [entity] Acme Corp (ORGANIZATION:COMPANY) — Company beneficially owned by Jane Doe
- [preference] compliance: Always verify beneficial ownership before credit decisions
</user_context>
Should we approve credit for Acme Corp?
Graph: Acme Corp —[RELATED_TO]— Jane Doe (PERSON)
Restored 2 messages for 'kyc-session'.
Owner of 'kyc-session': analyst-1 (KYC analyst)
Reasoning: Strands agent session -> tool call check_sanctions({'entity': 'Acme Corp'})
```

The demo clears its two sessions at startup so each run starts fresh; remove
the `clear_session` loop in `_prepare()` to watch history accumulate across
runs.

### A note on `min_score`

`Neo4jRetrievalConfig(min_score=...)` is a **Neo4j vector-index score**, not a
raw cosine similarity: the index reports `(1 + cosine) / 2`. So `0.5` means
"orthogonal", anything below it filters nothing at all, and `0.75` is the
equivalent of cosine `0.5`. The demo uses `0.75`, which is what makes the
injected block above contain exactly two lines instead of every entity in the
database.

## With a real agent

```python
import os

from strands import Agent
from neo4j_agent_memory.integrations.strands import (
    Neo4jRetrievalConfig,
    Neo4jSessionManager,
    bedrock_llm_model,
    nams_context_graph_tools,
)

manager = Neo4jSessionManager.for_nams(          # MEMORY_API_KEY from env
    "support-42",
    retrieval_config=Neo4jRetrievalConfig(),
)
agent = Agent(
    # Resolves a cross-region Bedrock inference-profile id; override with
    # BEDROCK_MODEL_ID, or swap the region prefix with
    # BEDROCK_INFERENCE_PROFILE_PREFIX=eu.
    model=bedrock_llm_model(),
    tools=nams_context_graph_tools(),
    session_manager=manager,
)
```

On NAMS the injected block contains **entities only** — preference and fact
search are bolt-only, so `Neo4jRetrievalConfig.include_preferences` /
`include_facts` have no effect there.

## Limitations

- **One manager per `Agent`.** `Neo4jSessionManager` does not implement
  Strands Graph/Swarm or bidirectional-agent persistence — attaching it to
  those raises `NotImplementedError` from the base class. The shared-brain
  pattern here is N independent agents on one graph, not a Strands
  multi-agent orchestrator.
- **Memory-grade persistence.** Text turns are stored and restored;
  `agent.state` is not, and `toolUse` blocks go to reasoning memory rather
  than the transcript.
- **Retrieval is database-wide**, not session- or conversation-scoped. Scope
  preferences with `user_id=`; entity search has no scoping knob.
- **`relationship.type` reports the Neo4j edge type** (`RELATED_TO`) rather
  than the semantic relation, which is stored as a property on that edge —
  hence `—[RELATED_TO]—` in the output above rather than
  `—[BENEFICIAL_OWNER_OF]—`.
- **Reasoning traces are left open.** `record_tool_calls=True` starts a trace
  and records steps, but `close()` does not yet call `complete_trace()`, so
  the trace has no outcome and no `success` flag.

## Files

| File | Purpose |
|---|---|
| `main.py` | Four-phase demo: persist (agent A), inject context (agent B), restore (agent C), mirror a tool call into reasoning memory. Runs without an LLM or API key. |
| `.env.example` | Connection settings; copy to `.env` or export the variables. |

## Going further

- **How-to guide:** [AWS Strands Agents → Session Manager](https://neo4j.com/labs/agent-memory/how-to/integrations/aws-strands#_session_manager_push_based)
  — retrieval tuning, NAMS setup, multi-tenant patterns, and the full
  limitations list.
- **Pull-based sibling:** [`strands-memory-store/`](../strands-memory-store/)
  — `Neo4jMemoryStore` on `MemoryManager(stores=[...])` for cross-session
  recall. Complementary, not an alternative.
- **TypeScript sibling:** [Strands Agents (TypeScript)](https://neo4j.com/labs/agent-memory/how-to/typescript/strands)
  — `Neo4jSessionStorage` + `Neo4jConversationManager` for the TS SDK.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`),
`strands-agents` 1.55.1, `sentence-transformers` 6.0.1 and Neo4j 5.26
(Docker, with APOC) on 2026-09-10. `Neo4jSessionManager` is unreleased —
it is not in PyPI 0.5.0._
