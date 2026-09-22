# Neo4j Agent Memory (Python SDK)

A graph-native memory system for AI agents. Store conversations, build knowledge graphs, and let your agents learn from their own reasoning — backed either by the hosted **NAMS** service (zero infrastructure) or your own Neo4j.

[![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)](https://neo4j.com/labs/)
[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)](https://neo4j.com/labs/)
[![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)](https://community.neo4j.com)
[![PyPI version](https://badge.fury.io/py/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![Python versions](https://img.shields.io/pypi/pyversions/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

> This is the Python SDK. A TypeScript SDK with the same memory model ships from the same repository as [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory).

## What It Does

| Short-Term Memory | Long-Term Memory | Reasoning Memory |
|---|---|---|
| Conversations & messages | Entities, preferences, facts | Reasoning traces & tool usage |
| Per-session history | Knowledge graph ([POLE+O model](https://neo4j.com/labs/agent-memory/explanation/poleo-model)) | Learn from past decisions |
| Vector + text search | Entity resolution & dedup | Similar task retrieval |

**Plus:** multi-stage entity extraction (spaCy / GLiNER2.5 / LLM) with typed relations decoded in the same pass, background enrichment (Wikipedia / Diffbot), geospatial queries, an MCP server with 16 tools, and integrations with LangChain, Pydantic AI, Google ADK, Strands, CrewAI, and more.

## Two backends, one API

The same `MemoryClient` runs against either backend — pick at config time. See [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends) for the full capability matrix.

- **Hosted (NAMS)** — a managed REST service. Just an API key; embedding, extraction, and dedup run server-side. Best for prototypes, demos, and multi-tenant SaaS.
- **Self-hosted (bolt)** — your own Neo4j (Aura, Desktop, Docker). Unlocks write-Cypher, geospatial queries, `adopt_existing_graph`, and air-gapped operation.

## Quick Start — Hosted (NAMS)

The fastest path: no database to run.

1. Sign up at [memory.neo4jlabs.com](https://memory.neo4jlabs.com) and copy your `nams_...` API key.
2. Install and export the key:

```bash
pip install "neo4j-agent-memory[nams]"
export MEMORY_API_KEY=nams_...
```

3. The backend auto-selects NAMS when `MEMORY_API_KEY` is set:

```python
import asyncio
from neo4j_agent_memory import MemoryClient

async def main():
    # Reads MEMORY_API_KEY from the environment; backend auto-selects NAMS.
    async with MemoryClient() as memory:
        await memory.short_term.add_message(
            session_id="user-123", role="user",
            content="Hi, I'm John and I love Italian food!",
        )
        await memory.long_term.add_entity("John", "PERSON")
        context = await memory.get_context(
            "What restaurant should I recommend?", session_id="user-123",
        )
        print(context)

asyncio.run(main())
```

> `neo4j-agent-memory` is **async-only** — every operation is a coroutine. On NAMS, extraction is asynchronous; call `await memory.long_term.wait_for_extraction(...)` before asserting on freshly-extracted entities. See [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams).

## Quick Start — Self-hosted (bolt)

Point the client at any Neo4j instance and pass your model as a provider-prefixed string:

```python
import asyncio
from neo4j_agent_memory import MemoryClient, MemorySettings

async def main():
    settings = MemorySettings(
        neo4j={"uri": "bolt://localhost:7687", "password": "your-password"},
        llm="anthropic/claude-3-5-sonnet-latest",
        embedding="openai/text-embedding-3-small",
    )
    async with MemoryClient(settings) as memory:
        await memory.short_term.add_message(
            session_id="user-123", role="user",
            content="Hi, I'm John and I love Italian food!",
        )
        await memory.long_term.add_entity("John", "PERSON")
        print(await memory.get_context("Recommend a restaurant?", session_id="user-123"))

asyncio.run(main())
```

## Installation

```bash
pip install neo4j-agent-memory                        # Core
pip install "neo4j-agent-memory[nams]"                # + hosted NAMS backend
pip install "neo4j-agent-memory[openai]"              # + OpenAI native adapter
pip install "neo4j-agent-memory[anthropic]"           # + Anthropic native adapter
pip install "neo4j-agent-memory[bedrock]"             # + AWS Bedrock native adapter
pip install "neo4j-agent-memory[sentence-transformers]" # + local HF embeddings
pip install "neo4j-agent-memory[litellm]"             # + LiteLLM universal fallback (100+ providers)
pip install "neo4j-agent-memory[mcp]"                 # + MCP server
pip install "neo4j-agent-memory[all]"                 # Everything except heavy local ML
pip install "neo4j-agent-memory[gliner2]"             # + GLiNER2.5 local entity + relation extraction
pip install "neo4j-agent-memory[full]"                # Everything including spaCy, GLiNER2.5, sentence-transformers
```

## MCP Server

Give any MCP-compatible assistant (Claude Desktop, Claude Code, Cursor) persistent graph-backed memory:

```bash
uvx "neo4j-agent-memory[mcp]" mcp serve --password <neo4j-password>
```

See the [MCP tools reference](https://neo4j.com/labs/agent-memory/reference/mcp-tools).

## Documentation

Full documentation: **[neo4j.com/labs/agent-memory](https://neo4j.com/labs/agent-memory/)**

- [Tutorials](https://neo4j.com/labs/agent-memory/tutorials/) — build your first memory-enabled agent
- [How-To Guides](https://neo4j.com/labs/agent-memory/how-to/) — NAMS, extraction, dedup, integrations
- [Reference](https://neo4j.com/labs/agent-memory/reference/) — configuration, CLI, REST API, MCP tools
- [Concepts](https://neo4j.com/labs/agent-memory/explanation/) — POLE+O model, memory types, Bolt vs NAMS

## Requirements

- Python 3.10+
- Neo4j 5.20+ (self-hosted/bolt path only)

## License

Apache License 2.0

---

A [Neo4j Labs](https://neo4j.com/labs/) project — community supported, not officially backed by Neo4j. [Community Forum](https://community.neo4j.com) · [GitHub](https://github.com/neo4j-labs/agent-memory) · [Issues](https://github.com/neo4j-labs/agent-memory/issues)
