[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/neo4j-labs-agent-memory-badge.png)](https://mseep.ai/app/neo4j-labs-agent-memory)

# Neo4j Agent Memory

A graph-native memory system for AI agents. Store conversations, build knowledge graphs, and let your agents learn from their own reasoning -- all backed by Neo4j.

[![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)](https://neo4j.com/labs/)
[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)](https://neo4j.com/labs/)
[![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)](https://community.neo4j.com)
[![Python CI](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci-python.yml/badge.svg)](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci-python.yml)
[![TypeScript CI](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci-typescript.yml/badge.svg)](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci-typescript.yml)
[![PyPI version](https://badge.fury.io/py/neo4j-agent-memory.svg)](https://badge.fury.io/py/neo4j-agent-memory)
[![npm version](https://img.shields.io/npm/v/@neo4j-labs/agent-memory.svg)](https://www.npmjs.com/package/@neo4j-labs/agent-memory)
[![Python versions](https://img.shields.io/pypi/pyversions/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## What It Does

![The Neo4j Agent Memory data model](img/memory-graph-model.png)

| Short-Term Memory | Long-Term Memory | Reasoning Memory |
|---|---|---|
| Conversations & messages | Entities, preferences, facts | Reasoning traces & tool usage |
| Per-session history | Knowledge graph ([POLE+O model](https://neo4j.com/labs/agent-memory/explanation/poleo-model)) | Learn from past decisions |
| Vector + text search | Entity resolution & dedup | Similar task retrieval |

![The Neo4j Agent Memory entity extraction pipeline](img/extraction-pipeline.png)

**Plus:** multi-stage entity extraction (spaCy / GLiNER / LLM), relationship extraction (GLiREL), background enrichment (Wikipedia / Diffbot), geospatial queries, [MCP server](#mcp-server) with 16 tools, and integrations with [LangChain, Pydantic AI, Google ADK, Strands, CrewAI, and more](#framework-integrations).

**Production features:** adopt an existing Neo4j graph as long-term memory (`client.schema.adopt_existing_graph(...)`), multi-tenant scoping (`user_identifier=`), fire-and-forget [buffered writes](examples/buffered-writes/) (`client.buffered.submit(...)`), [consolidation primitives](examples/audit-trail/) (`client.consolidation.dedupe_entities(...)`), an [eval harness](examples/eval-harness/) (`client.eval.run(suite)`), and explicit `:TOUCHED` audit edges from reasoning steps to entities.

**Bring your own model:** `MemorySettings.embedding` and `MemorySettings.llm` accept a provider-string shorthand (`"anthropic/claude-3-5-sonnet-latest"`, `"BAAI/bge-small-en-v1.5"`) or a Provider instance. Native adapters for OpenAI, Anthropic, Bedrock, Vertex AI, and sentence-transformers; LiteLLM universal fallback covers 100+ providers (Cohere, Voyage, Groq, Together, Mistral, Ollama, ...). See the [provider migration guide](https://neo4j.com/labs/agent-memory/how-to/migrate-to-providers.html). _(These configure the **self-hosted** backend; on NAMS, embedding and extraction run server-side.)_

## SDKs

`neo4j-labs/agent-memory` ships two SDKs with the same memory model, both
backed by the [NAMS](https://neo4j.com/labs/agent-memory/reference/rest-api)
hosted service. Pick the one that matches your stack — mixed Python +
TypeScript agents read and write the same memory.

| Language | Package | Install | Docs |
|---|---|---|---|
| Python | [`neo4j-agent-memory`](https://pypi.org/project/neo4j-agent-memory/) | `pip install neo4j-agent-memory` | [Python SDK docs](https://neo4j.com/labs/agent-memory/sdks/python) |
| TypeScript | [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory) | `npm install @neo4j-labs/agent-memory` | [TypeScript SDK docs](https://neo4j.com/labs/agent-memory/sdks/typescript) |

The Python SDK lives at the repo root (`src/neo4j_agent_memory/`,
`examples/`); the TypeScript SDK lives at `typescript/`. The two SDKs are
versioned and released independently — `python-v*` tags publish to PyPI,
`typescript-v*` tags publish to npm. Cross-language behavioral conformance
is enforced by the
[`agent-memory-tck`](https://github.com/neo4j-labs/agent-memory-tck)
spec suite, which consumes both SDKs as external dependencies.

## Quick Start

The fastest path is the hosted **NAMS** service — sign up, set one API key, and there's no database to run. Already operate Neo4j, or need write-Cypher / geospatial / air-gapped? Use the [self-hosted (bolt) path](#option-c-self-hosted-neo4j-bolt). The `MemoryClient` API is identical either way; see [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends) for the trade-offs.

### Option A: Hosted (NAMS) — zero infrastructure

1. Sign up at [memory.neo4jlabs.com](https://memory.neo4jlabs.com) and copy your `nams_...` API key.
2. Install the SDK and export the key:

```bash
pip install "neo4j-agent-memory[nams]"
export MEMORY_API_KEY=nams_...
```

3. The backend auto-selects NAMS when `MEMORY_API_KEY` is set — same API, no Neo4j to manage:

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

> On NAMS, entity extraction runs server-side and is asynchronous — call `await memory.long_term.wait_for_extraction(...)` before asserting on freshly-extracted entities. See [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams).

### Option B: MCP Server (zero code)

Give any MCP-compatible AI assistant (Claude Desktop, Claude Code, Cursor, VS Code Copilot) persistent memory backed by a knowledge graph:

```bash
# Run directly with uvx (no install needed)
uvx "neo4j-agent-memory[mcp]" mcp serve --password <neo4j-password>
```

![Neo4j Agent Memory MCP server](img/memory-architecture.png)

**Claude Code:**

```bash
claude mcp add neo4j-agent-memory -- \
  uvx "neo4j-agent-memory[mcp]" mcp serve --password <neo4j-password>
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "neo4j-agent-memory": {
      "command": "uvx",
      "args": ["neo4j-agent-memory[mcp]", "mcp", "serve", "--password", "your-password"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

### Option C: Self-hosted Neo4j (bolt)

Prefer to run your own database? Point the client at any Neo4j instance ([Desktop](https://neo4j.com/download/), [Docker](https://hub.docker.com/_/neo4j), or [Aura](https://neo4j.com/cloud/)). This path unlocks bolt-only features: write-Cypher, geospatial queries, `adopt_existing_graph`, and air-gapped operation.

![The memory abstractions exposed by the Neo4j Agent Memory package](img/memory-types.png)

> **`neo4j-agent-memory` is async-only.** Every memory operation is a
> coroutine. From a script, wrap your entry point in `asyncio.run(...)`
> as shown below. From a notebook, prefix calls with `await`. From a
> framework that runs its own loop (FastAPI, PydanticAI, Google ADK),
> just use `await` inside your handler. There is no synchronous
> wrapper — by design.

```python
import asyncio
from neo4j_agent_memory import MemoryClient, MemorySettings

async def main():
    # Pass the model as a provider-prefixed string. Swap in
    # "openai/...", "bedrock/...", "vertex_ai/...", or any of the 100+
    # LiteLLM-supported providers. Defaults to a working OpenAI setup
    # when llm/embedding are omitted.
    settings = MemorySettings(
        neo4j={"uri": "bolt://localhost:7687", "password": "your-password"},
        llm="anthropic/claude-3-5-sonnet-latest",
        embedding="openai/text-embedding-3-small",
    )

    async with MemoryClient(settings) as memory:
        # Store a conversation message
        await memory.short_term.add_message(
            session_id="user-123", role="user",
            content="Hi, I'm John and I love Italian food!"
        )

        # Build the knowledge graph
        await memory.long_term.add_entity("John", "PERSON")
        await memory.long_term.add_preference(
            category="food", preference="Loves Italian cuisine"
        )

        # Get combined context for an LLM prompt
        context = await memory.get_context(
            "What restaurant should I recommend?",
            session_id="user-123"
        )
        print(context)

asyncio.run(main())
```

> Already using `EmbeddingConfig`/`LLMConfig`? It still works — you'll just see a one-time `DeprecationWarning` at construction. See the [provider migration guide](https://neo4j.com/labs/agent-memory/how-to/migrate-to-providers.html).

### Option D: Full-Stack App with create-context-graph

Scaffold a complete full-stack AI application with built-in context graph memory:

```bash
uvx create-context-graph
```

![Create Context Graph full stack context graph application powered by Neo4j Agent Memory](img/app-three-panel.png)

This generates a ready-to-run project with a FastAPI backend, Next.js frontend, Neo4j knowledge graph, and neo4j-agent-memory pre-configured. See [create-context-graph.dev](https://create-context-graph.dev) for details.

## Installation

```bash
pip install neo4j-agent-memory                       # Core
pip install neo4j-agent-memory[openai]               # + OpenAI native adapter
pip install neo4j-agent-memory[anthropic]            # + Anthropic native adapter
pip install neo4j-agent-memory[bedrock]              # + AWS Bedrock native adapter
pip install neo4j-agent-memory[sentence-transformers]# + local HF embeddings
pip install neo4j-agent-memory[litellm]              # + LiteLLM universal fallback (100+ providers)
pip install neo4j-agent-memory[mcp]                  # + MCP server
pip install neo4j-agent-memory[langchain]            # + LangChain
pip install neo4j-agent-memory[all]                  # Everything except heavy local ML
pip install neo4j-agent-memory[full]                 # Everything including spaCy, GLiNER, sentence-transformers, instructor
```

Provider extras follow native-first resolution: with both `[openai]` and `[litellm]` installed, an `"openai/..."` model uses the native adapter; an unsupported provider like `"groq/..."` falls through to LiteLLM. See [Bring your own model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html) for details.

## Framework Integrations

| Framework | Extra | Import |
|---|---|---|
| [LangChain](https://neo4j.com/labs/agent-memory/how-to/integrations/langchain) | `[langchain]` | `from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory` |
| [Pydantic AI](https://neo4j.com/labs/agent-memory/how-to/integrations/pydantic-ai) | `[pydantic-ai]` | `from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency` |
| [Google ADK](https://neo4j.com/labs/agent-memory/how-to/integrations/google-cloud) | `[google-adk]` | `from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService` |
| [Strands (AWS)](https://neo4j.com/labs/agent-memory/how-to/integrations/aws-strands) | `[strands]` | `from neo4j_agent_memory.integrations.strands import context_graph_tools` |
| [CrewAI](https://neo4j.com/labs/agent-memory/how-to/integrations/crewai) | `[crewai]` | `from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory` |
| [LlamaIndex](https://neo4j.com/labs/agent-memory/how-to/integrations/llamaindex) | `[llamaindex]` | `from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory` |
| [OpenAI Agents](https://neo4j.com/labs/agent-memory/how-to/integrations/openai-agents) | `[openai-agents]` | `from neo4j_agent_memory.integrations.openai_agents import ...` |
| [Microsoft Agent](https://neo4j.com/labs/agent-memory/how-to/integrations/microsoft-agent) | `[microsoft-agent]` | `from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory` |

## MCP Server

The MCP server exposes memory capabilities as tools for AI assistants.

```bash
# stdio transport (Claude Desktop, Claude Code)
neo4j-agent-memory mcp serve --password <pw>

# SSE transport (network deployment)
neo4j-agent-memory mcp serve --transport sse --port 8080 --password <pw>

# Core profile (fewer tools, less context overhead)
neo4j-agent-memory mcp serve --profile core --password <pw>

# Session continuity across conversations
neo4j-agent-memory mcp serve --session-strategy per_day --user-id alice --password <pw>
```

**Tool Profiles:**

| Profile | Tools | Description |
|---------|-------|-------------|
| **core** | 6 | Essential read/write: `memory_search`, `memory_get_context`, `memory_store_message`, `memory_add_entity`, `memory_add_preference`, `memory_add_fact` |
| **extended** (default) | 16 | Full surface adding: conversation history, entity details, graph export, relationship creation, reasoning traces, observations, read-only Cypher |

See the [MCP tools reference](https://neo4j.com/labs/agent-memory/reference/mcp-tools) for full details.

## Examples

See [`examples/README.md`](examples/README.md) for the full index. Highlights:

**Full-stack reference apps**

| Example | Framework | Description |
|---------|-----------|-------------|
| [Lenny's Podcast Memory Explorer](examples/lennys-memory/) | PydanticAI | Flagship demo: 299 podcast episodes, knowledge graph, geospatial maps, Wikipedia enrichment |
| [Full-Stack Chat Agent](examples/full-stack-chat-agent/) | PydanticAI | News research assistant with NVL graph visualization and auto-preference detection |
| [AWS Financial Advisor](examples/financial-services-advisor/aws-financial-services-advisor/) | Strands (AWS) | Multi-agent KYC/AML compliance with Bedrock and reasoning trace audit trails |
| [Google Cloud Financial Advisor](examples/financial-services-advisor/google-cloud-financial-advisor/) | Google ADK | Multi-agent compliance with Vertex AI embeddings and real-time SSE streaming |
| [Microsoft Retail Assistant](examples/microsoft_agent_retail_assistant/) | Microsoft Agent | Shopping recommendations with GDS algorithms, entity deduplication, and context providers |

**v0.2 feature demos** _(small, single-purpose, no LLM required)_

| Example | Demonstrates |
|---|---|
| [`existing-graph/`](examples/existing-graph/) | `client.schema.adopt_existing_graph(...)` — layer the library over a graph you already have in production |
| [`buffered-writes/`](examples/buffered-writes/) | `write_mode="buffered"`, `client.buffered.submit(...)`, `client.flush()` — agent responses unblocked from Neo4j round-trips |
| [`audit-trail/`](examples/audit-trail/) | Explicit `:TOUCHED` edges from reasoning steps to entities, plus `TraceOutcome` for indexable audit queries |
| [`eval-harness/`](examples/eval-harness/) | `client.eval.run(EvalSuite(...))` — labelled regression tests for memory quality |

**Tooling & extraction**

| Example | Framework | Description |
|---------|-----------|-------------|
| [`no_llm/`](examples/no_llm/) | Standalone | Run with `llm=None` plus local sentence-transformers + spaCy/GLiNER (air-gapped, deterministic) |
| [Domain Schema Examples](examples/domain-schemas/) | Standalone | 8 GLiNER2 extraction scripts with factory pattern, batch extraction, streaming, and GLiREL relations |
| [Google Cloud Integration](examples/google_cloud_integration/) | Google ADK | Progressive tutorial: Vertex AI, ADK, MCP server, and MemoryIntegration with session strategies |
| [Google ADK Demo](examples/google_adk_demo/) | Google ADK | Standalone demo of Neo4jMemoryService with session storage, search, and preferences |

Most examples pin `neo4j-agent-memory>=0.4.0` (the NAMS-capable release). TypeScript examples pin `@neo4j-labs/agent-memory@^0.3.0`. See each example's `pyproject.toml` / `package.json` for the exact pin.

## Documentation

Full documentation at **[neo4j.com/labs/agent-memory](https://neo4j.com/labs/agent-memory/)**

- [Tutorials](https://neo4j.com/labs/agent-memory/tutorials/) -- Build your first memory-enabled agent
- [How-To Guides](https://neo4j.com/labs/agent-memory/how-to/) -- Entity extraction, deduplication, enrichment, integrations
- [API Reference](https://neo4j.com/labs/agent-memory/reference/) -- Configuration, CLI, MCP tools
- [Concepts](https://neo4j.com/labs/agent-memory/explanation/) -- POLE+O model, memory types, extraction pipeline

## Development

```bash
git clone https://github.com/neo4j-labs/agent-memory.git
cd agent-memory/neo4j-agent-memory
uv sync --group dev
make test-unit    # Run unit tests
make check        # Lint + format + typecheck
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide, CI pipeline, and documentation guidelines.

## Requirements

- Python 3.10+
- Neo4j 5.20+ (for vector indexes)

## License

Apache License 2.0

---

This is a [Neo4j Labs](https://neo4j.com/labs/) project -- community supported, not officially backed by Neo4j. [Community Forum](https://community.neo4j.com) | [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) | [Documentation](https://neo4j.com/labs/agent-memory/) | [TypeScript SDK](typescript/README.md)
