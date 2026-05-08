# Google ADK Memory Demo

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Drop-in `Neo4jMemoryService` for Google's Agent Development Kit — use Neo4j Agent Memory as your ADK agent's persistent memory backend.

This example shows the minimal wiring to back a Google ADK agent with `neo4j-agent-memory`: storing conversation sessions, semantic search across all three memory types, automatic entity extraction, and preference learning from natural language.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Looking for more?** [`examples/google_cloud_integration/`](../google_cloud_integration/) covers the full Google Cloud surface — Vertex AI embeddings, MCP server, Cloud Run deployment.

## What this demonstrates

- **`Neo4jMemoryService`** — the ADK-compatible `MemoryService` interface backed by `MemoryClient`.
- **`add_session_to_memory(session)`** — store a conversation session in one call. Sessions get message-level storage plus optional auto-extraction of entities and preferences.
- **`search_memory(query)`** — semantic search across short-term, long-term, and reasoning memory in a single call.
- **`get_memories_for_session(session_id)`** — recall a specific session's history.
- **`MemoryIntegration` + `SessionStrategy`** — alternative pattern shown in the demo for higher-level session scoping (per-conversation, per-day, persistent).

## Architecture

```
┌─────────────────┐    ┌──────────────────────┐    ┌─────────────┐
│   Google ADK    │───▶│  Neo4jMemoryService  │───▶│   Neo4j     │
│     Agent       │    │                      │    │  Database   │
└─────────────────┘    └──────────┬───────────┘    └─────────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │   MemoryClient   │
                         │  - short_term    │
                         │  - long_term     │
                         │  - reasoning     │
                         └──────────────────┘
```

## Prerequisites

- Python 3.10+
- Neo4j 5.x (local Docker, Aura, or self-hosted)
- A Google Cloud project (only required if you want Vertex AI embeddings; the demo works with OpenAI or local embeddings too)

## Setup

```bash
# Install dependencies (with Google ADK and optional Vertex AI extras)
pip install "neo4j-agent-memory[google-adk,vertex-ai]"

# Configure environment
cp .env.example .env
# Edit .env — set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
# and optionally GOOGLE_CLOUD_PROJECT for Vertex AI embeddings.
```

If you're developing against the package source rather than a release:

```bash
cd /path/to/neo4j-agent-memory
uv pip install -e ".[google-adk,vertex-ai]"
```

## Run

```bash
python demo.py
```

The demo stores a couple of project-discussion sessions, performs semantic search across them, and shows how preferences mentioned mid-conversation become first-class records in long-term memory.

## Minimal usage pattern

```python
from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

settings = MemorySettings(
    neo4j=Neo4jConfig(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
    )
)

async with MemoryClient(settings) as client:
    memory_service = Neo4jMemoryService(
        memory_client=client,
        user_id="user-123",
    )

    # Store a session
    await memory_service.add_session_to_memory({
        "id": "session-1",
        "messages": [
            {"role": "user", "content": "I'm working on Project Alpha"},
            {"role": "assistant", "content": "Tell me more about Project Alpha"},
        ],
    })

    # Search
    for entry in await memory_service.search_memory("project deadline"):
        print(f"[{entry.memory_type}] {entry.content}")

    # Recall a session
    history = await memory_service.get_memories_for_session("session-1")
```

> **ADK gotcha:** `Runner.run_async()` returns an `AsyncGenerator` — use `async for event in runner.run_async(...)`, not `await`. Same for `InMemorySessionService.get_session()` / `create_session()` — they're async, so they need `await`.

## Going further

- **Companion example:** [`examples/google_cloud_integration/`](../google_cloud_integration/) — Vertex AI embeddings, MCP server, full pipeline, Cloud Run deployment.
- **Production deployment:** [`deploy/cloudrun/README.md`](../../deploy/cloudrun/README.md).
- **ADK docs:** [google.github.io/adk-docs](https://google.github.io/adk-docs/).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03 (structure & import tests; end-to-end run requires ADK environment)._
