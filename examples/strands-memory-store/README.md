# Strands MemoryStore — long-term recall

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> `Neo4jMemoryStore` implements Strands' `MemoryStore` protocol: pass it to
> `MemoryManager(stores=[...])` and the agent loop recalls entities,
> preferences, and facts from a Neo4j graph across sessions.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> ℹ️ **Unreleased API.** `Neo4jMemoryStore` is not in PyPI 0.5.0 — it ships in
> the next release. Until then, install the library from this repository (see
> *In your own project* below).

## What this demonstrates

- **`search()`** — fans out over entities, preferences, and facts (entities
  only on hosted NAMS), returning `MemoryEntry` objects with a formatted
  `content` string and a `metadata["kind"]` tag.
- **`add()`** — default sink writes a message with extraction on;
  `metadata["kind"]` (`"preference"` / `"fact"` / `"entity"`) routes to a
  typed write instead.
- **`get_tools()`** — graph-native tools that a `MemoryManager` cannot provide
  on its own: `{name}_get_entity_graph`, and — bolt-only, with a configured
  `user_id` — `{name}_get_user_preferences`. The store's `name` prefixes them
  so they coexist with `context_graph_tools`' identically-named tools.

`Neo4jSessionManager` (`examples/strands-session-manager/`) remains for
transcript persistence — the session manager restores sessions, the memory
store feeds the agent loop. See the guide's "Pairing with the session
manager" section for combining both on one agent.

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
once the release carrying `Neo4jMemoryStore` is on PyPI.

## Run

```bash
make neo4j-start
NEO4J_PASSWORD=test-password uv run python examples/strands-memory-store/main.py
```

No LLM API key required — `llm=None` plus a local `sentence-transformers`
embedder.

The container's data volume persists across `make neo4j-stop` / `neo4j-start`
(only `neo4j-clean` wipes it). If this container previously ran against a
different-dimension embedder (e.g. OpenAI's 1536-dim default), connecting
here fails with `EmbeddingDimensionMismatchError`, not a silent problem —
run `make neo4j-clean && make neo4j-start` to reset.

Expected output **on a fresh database**. Recall is database-wide, not
session-scoped, so an already-populated Neo4j adds its own `[entity]` /
`[fact]` lines:

```
search('what does the user prefer?'):
  preference: [preference] ui: Prefers dark mode
search('Acme Corp'):
      entity: [entity] Acme Corp (ORGANIZATION:COMPANY)
  preference: [preference] ui: Prefers dark mode
        fact: [fact] Acme Corp HEADQUARTERED_IN Berlin
add(...): {'kind': 'message', 'id': '...'}
tools: ['graph_get_entity_graph', 'graph_get_user_preferences']
```

Two things to read off that output:

- **Entity and fact recall are similarity-gated; user-scoped preference recall
  is not.** With a `user_id` configured the store *lists* that user's active
  preferences (`get_preferences_for`) rather than searching them, because
  `search_preferences` applies no tenant filter — tenancy correctness over
  query relevance. So the preference answers both queries.
- **`min_score` is a Neo4j vector-index score, not a raw cosine similarity.**
  The index reports `(1 + cosine) / 2`, so `0.5` means "orthogonal" and the
  `0.2` default filters nothing at all. This example passes `0.75`
  (= cosine `0.5`).

## With a real agent

```python
from strands import Agent
from strands.memory import MemoryManager

from neo4j_agent_memory.integrations.strands import (
    Neo4jMemoryStore,
    Neo4jMemoryStoreConfig,
    bedrock_llm_model,
)

store = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=client))

agent = Agent(
    # Resolves a cross-region Bedrock inference-profile id; override with
    # BEDROCK_MODEL_ID, or swap the region prefix with
    # BEDROCK_INFERENCE_PROFILE_PREFIX=eu.
    model=bedrock_llm_model(),
    memory_manager=MemoryManager(stores=[store]),
)
```

## Files

| File | Purpose |
|---|---|
| `main.py` | Seeds a preference, an entity and a fact, then exercises `search()`, `add()`, and `get_tools()`. |
| `.env.example` | Connection settings; copy to `.env` or export the variables. |

## Limitations

- **Entity-only recall on hosted NAMS** — preference and fact search, and the
  `{name}_get_user_preferences` tool, are bolt-only.
- **Recall is database-wide**, not session-scoped. `user_id=` scopes
  preferences; entity and fact search have no scoping knob.
- **Do not let both sides extract the same turns.** Paired with
  `Neo4jSessionManager`, leave this store's `extraction=False` (the default)
  or pass `extract_entities=False` to the session manager — the manager raises
  `ValueError` if both would extract.

## Going further

- **How-to guide:** [AWS Strands Agents → Memory Store](https://neo4j.com/labs/agent-memory/how-to/integrations/aws-strands#_memory_store)
  — configuration, backend differences, and the session-manager pairing rule.
- **Push-based sibling:** [`strands-session-manager/`](../strands-session-manager/)
  — `Neo4jSessionManager` for transcript persistence and restore.
- **TypeScript sibling:** [Strands Agents (TypeScript)](https://neo4j.com/labs/agent-memory/how-to/typescript/strands)
  — `Neo4jMemoryStore` for the TS SDK.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`),
`strands-agents` 1.55.1, `sentence-transformers` 6.0.1 and Neo4j 5.26
(Docker, with APOC) on 2026-09-10. `Neo4jMemoryStore` is unreleased — it is
not in PyPI 0.5.0._
