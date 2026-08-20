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

## What this demonstrates

- **`search()`** — fans out over entities, preferences, and facts (entities
  only on hosted NAMS), returning `MemoryEntry` objects with a formatted
  `content` string and a `metadata["kind"]` tag.
- **`add()`** — default sink writes a message with extraction on;
  `metadata["kind"]` (`"preference"` / `"fact"` / `"entity"`) routes to a
  typed write instead.
- **`get_tools()`** — graph-native tools (`get_entity_graph`, and, bolt-only
  with a configured `user_id`, `get_user_preferences`) that a `MemoryManager`
  cannot provide on its own.

`Neo4jSessionManager` (`examples/strands-session-manager/`) remains for
transcript persistence — the session manager restores sessions, the memory
store feeds the agent loop. See the guide's "Pairing with the session
manager" section for combining both on one agent.

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

```bash
make neo4j-start
NEO4J_PASSWORD=test-password uv run python examples/strands-memory-store/main.py
```

No LLM API key required — `llm=None` plus a local `sentence-transformers`
embedder.

Expected output:

```
search('what does the user prefer?'):
      entity: [entity] Acme Corp (ORGANIZATION)
  preference: [preference] ui: Prefers dark mode
add(...): {'kind': 'message', 'id': '...'}
tools: ['get_entity_graph', 'get_user_preferences']
```

## With a real agent

```python
from strands import Agent
from strands.memory import MemoryManager

from neo4j_agent_memory.integrations.strands import (
    Neo4jMemoryStore,
    Neo4jMemoryStoreConfig,
)

store = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=client))

agent = Agent(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    memory_manager=MemoryManager(stores=[store]),
)
```

## Files

| File | Purpose |
|---|---|
| `main.py` | Seeds a preference and an entity, then exercises `search()`, `add()`, and `get_tools()`. |

## Going further

- **How-to guide:** `docs/modules/ROOT/pages/how-to/integrations/aws-strands.adoc`
  — configuration, backend differences, and the session-manager pairing rule.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)
