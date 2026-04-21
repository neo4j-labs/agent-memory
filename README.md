# Neo4j Agent Memory

A graph-native memory system for AI agents. Store conversations, build knowledge graphs, and let your agents learn from their own reasoning -- all backed by Neo4j.

[![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)](https://neo4j.com/labs/)
[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)](https://neo4j.com/labs/)
[![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)](https://community.neo4j.com)
[![CI](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/neo4j-agent-memory.svg)](https://badge.fury.io/py/neo4j-agent-memory)
[![Python versions](https://img.shields.io/pypi/pyversions/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## What It Does

![The Neo4j Agent Memory data model](img/memory-graph-model.png)

| Short-Term Memory | Long-Term Memory | Reasoning Memory |
|---|---|---|
| Conversations & messages | Entities, preferences, facts | Reasoning traces & tool usage |
| Per-session history | Knowledge graph ([POLE+O model](https://neo4j.com/labs/agent-memory/explanation/poleo-model.html)) | Learn from past decisions |
| Vector + text search | Entity resolution & dedup | Similar task retrieval |

![The Neo4j Agent Memory entity extraction pipeline](img/extraction-pipeline.png)

**Plus:** multi-stage entity extraction (spaCy / GLiNER / LLM), relationship extraction (GLiREL), background enrichment (Wikipedia / Diffbot), geospatial queries, [MCP server](#mcp-server) with 16 tools, and integrations with [LangChain, Pydantic AI, Google ADK, Strands, CrewAI, and more](#framework-integrations).

## Quick Start

**Prerequisites:** A running Neo4j instance ([Neo4j Desktop](https://neo4j.com/download/), [Docker](https://hub.docker.com/_/neo4j), or [Neo4j Aura](https://neo4j.com/cloud/) for a free cloud database).

### Option A: MCP Server (zero code)

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

### Option B: Python API

![The memory abstractions exposed by the Neo4j Agent Memory package](img/memory-types.png)

```python
import asyncio
from neo4j_agent_memory import MemoryClient, MemorySettings

async def main():
    settings = MemorySettings(
        neo4j={"uri": "bolt://localhost:7687", "password": "your-password"}
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

### Option C: Full-Stack App with create-context-graph

Scaffold a complete full-stack AI application with built-in context graph memory:

```bash
uvx create-context-graph
```

![Create Context Graph full stack context graph application powered by Neo4j Agent Memory](img/app-three-panel.png)

This generates a ready-to-run project with a FastAPI backend, Next.js frontend, Neo4j knowledge graph, and neo4j-agent-memory pre-configured. See [create-context-graph.dev](https://create-context-graph.dev) for details.

## Installation

```bash
pip install neo4j-agent-memory                  # Core
pip install neo4j-agent-memory[openai]          # + OpenAI embeddings
pip install neo4j-agent-memory[mcp]             # + MCP server
pip install neo4j-agent-memory[langchain]       # + LangChain
pip install neo4j-agent-memory[all]             # Everything
```

See the [getting started guide](https://neo4j.com/labs/agent-memory/getting-started/) for all extras (Vertex AI, Bedrock, spaCy, GLiNER, Google ADK, Strands, etc.).

## Framework Integrations

| Framework | Extra | Import |
|---|---|---|
| [LangChain](https://neo4j.com/labs/agent-memory/how-to/integrations/langchain/) | `[langchain]` | `from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory` |
| [Pydantic AI](https://neo4j.com/labs/agent-memory/how-to/integrations/pydantic-ai/) | `[pydantic-ai]` | `from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency` |
| [Google ADK](https://neo4j.com/labs/agent-memory/how-to/integrations/google-cloud/) | `[google-adk]` | `from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService` |
| [Strands (AWS)](https://neo4j.com/labs/agent-memory/how-to/integrations/aws-strands/) | `[strands]` | `from neo4j_agent_memory.integrations.strands import context_graph_tools` |
| [CrewAI](https://neo4j.com/labs/agent-memory/how-to/integrations/crewai/) | `[crewai]` | `from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory` |
| [LlamaIndex](https://neo4j.com/labs/agent-memory/how-to/integrations/llamaindex/) | `[llamaindex]` | `from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory` |
| [OpenAI Agents](https://neo4j.com/labs/agent-memory/how-to/integrations/openai-agents/) | `[openai-agents]` | `from neo4j_agent_memory.integrations.openai_agents import ...` |
| [Microsoft Agent](https://neo4j.com/labs/agent-memory/how-to/integrations/microsoft-agent/) | `[microsoft-agent]` | `from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory` |

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

See the [MCP tools reference](https://neo4j.com/labs/agent-memory/reference/mcp-tools.html) for full details.

## Examples

| Example | Framework | Description |
|---------|-----------|-------------|
| [Lenny's Podcast Memory Explorer](examples/lennys-memory/) | PydanticAI | Flagship demo: 299 podcast episodes, knowledge graph, geospatial maps, Wikipedia enrichment |
| [Full-Stack Chat Agent](examples/full-stack-chat-agent/) | PydanticAI | News research assistant with NVL graph visualization and auto-preference detection |
| [AWS Financial Advisor](examples/financial-services-advisor/aws-financial-services-advisor/) | Strands (AWS) | Multi-agent KYC/AML compliance with Bedrock and reasoning trace audit trails |
| [Google Cloud Financial Advisor](examples/financial-services-advisor/google-cloud-financial-advisor/) | Google ADK | Multi-agent compliance with Vertex AI embeddings and real-time SSE streaming |
| [Microsoft Retail Assistant](examples/microsoft_agent_retail_assistant/) | Microsoft Agent | Shopping recommendations with GDS algorithms, entity deduplication, and context providers |
| [Domain Schema Examples](examples/domain-schemas/) | Standalone | 8 GLiNER2 extraction scripts with factory pattern, batch extraction, streaming, and GLiREL relations |
| [Google Cloud Integration](examples/google_cloud_integration/) | Google ADK | Progressive tutorial: Vertex AI, ADK, MCP server, and MemoryIntegration with session strategies |
| [Google ADK Demo](examples/google_adk_demo/) | Google ADK | Standalone demo of Neo4jMemoryService with session storage, search, and preferences |

All examples use `neo4j-agent-memory>=0.1.0` and demonstrate the latest features including `ExtractionConfig`, `DeduplicationConfig`, `MemoryIntegration`, and `SessionStrategy`.

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

This is a [Neo4j Labs](https://neo4j.com/labs/) project -- community supported, not officially backed by Neo4j. [Community Forum](https://community.neo4j.com) | [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) | [Documentation](https://neo4j.com/labs/agent-memory/)
