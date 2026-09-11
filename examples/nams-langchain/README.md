# NAMS + LangChain 1.x

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> A LangChain 1.x `create_agent` agent with memory, running on the hosted **Neo4j Agent Memory Service (NAMS)** — no Neo4j to operate, no embedding or extraction key.

This is [`examples/langchain_agent.py`](../langchain_agent.py) with one thing changed: the settings object. The agent, `Neo4jMemoryMiddleware` and `Neo4jMemoryRetriever` are identical; `NamsSettings()` replaces `BoltSettings(neo4j=...)`.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What NAMS does server-side

| Concern | On NAMS |
|---|---|
| Entity extraction | Runs server-side, asynchronously. No extractor to configure, no LLM key for memory. |
| Embeddings | Generated server-side. No embedding provider, no `text-embedding-*` key. |
| The graph | Operated for you. No Neo4j, no vector indexes to create. |
| Your chat model | Still yours. The agent's `ChatOpenAI` (or any `BaseChatModel`) is unchanged. |

## What is not available yet, and how the adapters behave

The adapters tolerate the narrower hosted surface instead of raising, so the
same code runs on both backends:

| Call | On NAMS | Adapter behaviour |
|---|---|---|
| `short_term.*` | supported | full history and conversation context |
| `long_term.search_entities` | supported | entity documents and the context block |
| `long_term.get_context` | returns `""` | falls back to `search_entities` |
| `long_term.search_preferences` | `NotSupportedError` | preferences come back empty |
| `reasoning.get_similar_traces` | `NotSupportedError` | this example sets `include_reasoning=False` |
| `short_term.search_messages` | needs a conversation id | always pass `session_id=` to the retriever |

See [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends) for the full comparison.

## Prerequisites

- Python 3.10+
- A NAMS API key from <https://memory.neo4jlabs.com>
- `OPENAI_API_KEY` is **optional**: without it the script runs a scripted offline chat model and still exercises the whole memory path against NAMS

## Setup

```bash
uv pip install -r requirements.txt
cp .env.example .env
# edit .env: set MEMORY_API_KEY (and OPENAI_API_KEY for a real model turn)
```

The script loads this directory's `.env` itself, so exporting is optional.

## Run

```bash
uv run python main.py
```

## Expected output

With no `OPENAI_API_KEY` (the offline path):

```
Loaded environment from /path/to/examples/nams-langchain/.env
Connected to https://memory.neo4jlabs.com/v1
Conversation: 3f6b2c51-6d0e-4f0a-9a3a-2c1b8e0f7a11
Model: FakeListChatModel (no OPENAI_API_KEY) — memory still runs against NAMS

User:      I prefer dark mode in all my apps. Remember that.
Assistant: Noted — dark mode everywhere, and I'll keep using it in future answers.

2 messages persisted on NAMS:
        user: I prefer dark mode in all my apps. Remember that.
   assistant: Noted — dark mode everywhere, and I'll keep using it in futu

Entities NAMS extracted server-side: 1
   Dark Mode (CONCEPT)

Retriever returned 2 documents:
   [message] I prefer dark mode in all my apps. Remember that.
   [entity] Dark Mode

Done. Same code on your own Neo4j: examples/langchain_agent.py
```

Entity counts depend on what the server extracts from your text; extraction is
asynchronous, so the script polls with `long_term.wait_for_extraction(...)`
before reading entities back.

## Switching to your own Neo4j

Swap the settings object; nothing else changes:

```python
import os

from pydantic import SecretStr

from neo4j_agent_memory import BoltSettings, Neo4jConfig

settings = BoltSettings(
    neo4j=Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        password=SecretStr(os.environ["NEO4J_PASSWORD"]),
    ),
    embedding="openai/text-embedding-3-small",
)
```

On bolt you also get preference search and similar-trace search, so
`include_reasoning=True` and `search_reasoning=True` start returning results.
[`examples/langchain_agent.py`](../langchain_agent.py) is that version, with
memory-backed agent tools and a reasoning-trace middleware.

## Going further

- **How-to guide:** [Use with LangChain](https://neo4j.com/labs/agent-memory/how-to/integrations/langchain) — all three adapters, middleware options, tools, legacy (`langchain-classic`) migration table.
- **Hosted backend:** [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams) and [`examples/nams-quickstart/`](../nams-quickstart/).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`), `langchain` 1.4.0, `langchain-core` 1.6.2, with the NAMS transport mocked (`tests/examples/test_nams_langchain_example.py`) — 2026-09-10._
