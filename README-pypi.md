# Neo4j Agent Memory (Python SDK)

A graph-native memory system for AI agents. Store conversations, build knowledge graphs, and record and retrieve application-supplied reasoning — backed either by the hosted **NAMS** service (zero infrastructure) or your own Neo4j.

[![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)](https://neo4j.com/labs/)
[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)](https://neo4j.com/labs/)
[![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)](https://community.neo4j.com)
[![PyPI version](https://badge.fury.io/py/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![Python versions](https://img.shields.io/pypi/pyversions/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

> This is the Python SDK. A TypeScript SDK with the same memory model ships from the same repository as [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory).

> **Neo4j Labs project**
>
> This project is part of Neo4j Labs and is actively maintained, but not officially supported. There are no SLAs or guarantees around backwards compatibility and deprecation. For questions and support, please use the [Neo4j Community Forum](https://community.neo4j.com).
>
> The Python and TypeScript packages in this repository are versioned and released independently; the status badge above reflects this package's own maturity, not the other SDK's.

## What it does

| Short-Term Memory | Long-Term Memory | Reasoning Memory |
|---|---|---|
| Conversations & messages | Entities, preferences, facts | Reasoning traces & tool usage |
| Per-session history | Knowledge graph ([POLE+O model](https://neo4j.com/labs/agent-memory/explanation/poleo-model)) | Retrieve recorded decisions |
| Vector + text search | Entity resolution & dedup | Similar task retrieval |

**Plus:** multi-stage entity extraction (spaCy / GLiNER / LLM), relationship extraction (GLiREL), background enrichment (Wikipedia / Diffbot), geospatial queries, an MCP server with 16 extended-profile tools on Bolt (20 registered on NAMS; backend limitations apply), and integrations with LangChain, Pydantic AI, Google ADK, Strands, CrewAI, and more.

## Backend capabilities

Select the backend at configuration time, then use its supported operations. Preferences, facts, client-created relationships and extraction configuration are Bolt-only. See [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends) for the full capability matrix.

- **Hosted (NAMS)** — a managed REST service. Just an API key; embedding, extraction, and dedup run server-side. Use workspace authentication for the tenancy boundary; conversation user metadata is distinct.
- **Direct Neo4j (bolt)** — connect to your AuraDB instance with client-side providers. Unlocks write-Cypher, geospatial queries, `adopt_existing_graph`, and deployments with locally configured providers.

> **Python release:** These Python instructions use the published `neo4j-agent-memory==0.6.0` package. The [Python tutorials](https://neo4j.com/labs/agent-memory/sdks/python) show the complete example programs and helpers to copy into local files, so running them does not require a repository clone or code download. TypeScript tutorials retain their [documented source setup](https://neo4j.com/labs/agent-memory/sdks/typescript); Python and npm release versions are independent.

## Quick start — Hosted (NAMS)

The fastest path: no database to run.

1. Sign up at [memory.neo4jlabs.com](https://memory.neo4jlabs.com) and copy your `nams_...` API key.
2. Install and export the key:

```bash
pip install 'neo4j-agent-memory[nams]==0.6.0'
export MEMORY_API_KEY=nams_...
```

3. The backend auto-selects NAMS when `MEMORY_API_KEY` is set:

```python
import asyncio
from neo4j_agent_memory import MemoryClient

async def main():
    # Reads MEMORY_API_KEY from the environment; backend auto-selects NAMS.
    async with MemoryClient() as memory:
        conversation = await memory.short_term.create_conversation("quickstart")
        conversation_id = str(conversation.id)
        await memory.short_term.add_message(
            session_id=conversation_id, role="user",
            content="Hi, I'm John and I love Italian food!",
        )
        saved = await memory.short_term.get_conversation(conversation_id)
        print(saved.messages[-1].content)
        print("Save this conversation ID to resume it:", conversation_id)

asyncio.run(main())
```

> `neo4j-agent-memory` is **async-only** — every operation is a coroutine. On NAMS, extraction is asynchronous; call `await memory.long_term.wait_for_extraction(...)` before asserting on freshly-extracted entities. See [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams).

<a id="quick-start-self-hosted-bolt"></a>
<a id="quick-start--self-hosted-bolt"></a>

## Quick start — Neo4j Aura (bolt)

Use a dedicated [AuraDB instance](https://neo4j.com/labs/agent-memory/tutorials/first-agent-memory.html#_step_2_set_up_neo4j) and copy its connection values. Aura uses the `bolt` backend. Install the selected adapters with `pip install 'neo4j-agent-memory[anthropic,openai]==0.6.0'` and set their keys:

```bash
export NEO4J_URI="neo4j+s://<instance-id>.databases.neo4j.io"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="replace-with-your-Aura-password"
export NEO4J_DATABASE="neo4j"
export ANTHROPIC_API_KEY="replace-with-your-Anthropic-key"
export OPENAI_API_KEY="replace-with-your-OpenAI-key"
```

The complete example reads those variables directly:

```python
import asyncio
import os
from neo4j_agent_memory import MemoryClient, MemorySettings

async def main():
    settings = MemorySettings(
        backend="bolt",
        neo4j={
            "uri": os.environ["NEO4J_URI"],
            "username": os.environ["NEO4J_USERNAME"],
            "password": os.environ["NEO4J_PASSWORD"],
            "database": os.getenv("NEO4J_DATABASE", "neo4j"),
        },
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
pip install 'neo4j-agent-memory==0.6.0'                                 # Core
pip install 'neo4j-agent-memory[nams]==0.6.0'                           # + hosted NAMS backend
pip install 'neo4j-agent-memory[openai]==0.6.0'                         # + OpenAI native adapter
pip install 'neo4j-agent-memory[anthropic]==0.6.0'                      # + Anthropic native adapter
pip install 'neo4j-agent-memory[bedrock]==0.6.0'                        # + AWS Bedrock native adapter
pip install 'neo4j-agent-memory[sentence-transformers]==0.6.0'          # + local HF embeddings
pip install 'neo4j-agent-memory[litellm]==0.6.0'                        # + LiteLLM universal fallback (100+ providers)
pip install 'neo4j-agent-memory[mcp,openai]==0.6.0'                     # + MCP server
pip install 'neo4j-agent-memory[all]==0.6.0'                            # Everything except heavy local ML
pip install 'neo4j-agent-memory[full]==0.6.0'                           # Everything including spaCy, GLiNER, sentence-transformers
```

## MCP Server

Give any MCP-compatible assistant (Claude Desktop, Claude Code, Cursor) persistent graph-backed memory. Use the Aura and OpenAI environment variables from the quickstart above; the explicit `--user` maps the exported Aura username to the CLI option:

```bash
uvx "neo4j-agent-memory[mcp,openai]" mcp serve --backend bolt --user "$NEO4J_USERNAME"
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
- A Neo4j AuraDB instance for the `bolt` quickstart, or a NAMS workspace for the hosted path

## License

Apache License 2.0

---

A [Neo4j Labs](https://neo4j.com/labs/) project — community supported, not officially backed by Neo4j. [Community Forum](https://community.neo4j.com) · [GitHub](https://github.com/neo4j-labs/agent-memory) · [Issues](https://github.com/neo4j-labs/agent-memory/issues)
