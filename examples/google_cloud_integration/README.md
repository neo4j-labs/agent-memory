# Google Cloud Integration Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Vertex AI embeddings, Google ADK memory, the MCP server, and a full pipeline that ties them together — one folder.

Comprehensive examples wiring `neo4j-agent-memory` into the Google Cloud surface. Use this if you've already chosen GCP; otherwise [`examples/google_adk_demo/`](../google_adk_demo/) is the smaller starting point.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Google Cloud Integration Pipeline](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Features Demonstrated

| Feature | Script | Description |
|---------|--------|-------------|
| **Vertex AI Embeddings** | `vertex_ai_embeddings.py` | Generate embeddings using Google's text-embedding-004 model |
| **Google ADK Integration** | `adk_memory_service.py` | Use Neo4jMemoryService with Google ADK agents |
| **MCP Server** | `mcp_server_demo.py` | Start and interact with the MCP server |
| **Complete Pipeline** | `full_pipeline.py` | End-to-end demo combining all features |

## Prerequisites

### 1. Neo4j Database

Start a local Neo4j instance or use Neo4j Aura:

```bash
# Docker (local)
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5-enterprise
```

### 2. Google Cloud Setup

```bash
# Authenticate with Google Cloud
gcloud auth application-default login

# Set your project
export GOOGLE_CLOUD_PROJECT=your-project-id
```

### 3. Install Dependencies

```bash
# Install with all Google Cloud features
pip install neo4j-agent-memory[google,mcp]

# Or for development
cd neo4j-agent-memory
pip install -e ".[google,mcp]"
```

### 4. Environment Variables

```bash
# Copy example and configure
cp .env.example .env

# Required variables:
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=password

# For Vertex AI embeddings:
export GOOGLE_CLOUD_PROJECT=your-project-id
export VERTEX_AI_LOCATION=us-central1
```

## Quick Start

### 1. Vertex AI Embeddings

```bash
python vertex_ai_embeddings.py
```

This demonstrates:
- Initializing `VertexAIEmbedder` with text-embedding-004
- Generating embeddings for single texts
- Batch embedding for multiple texts
- Using Vertex AI embeddings with MemoryClient

### 2. ADK Memory Service

```bash
python adk_memory_service.py
```

This demonstrates:
- Creating `Neo4jMemoryService` for ADK integration
- Storing conversation sessions
- Semantic memory search
- Entity and preference extraction

### 3. MCP Server

```bash
# Start the server
python mcp_server_demo.py

# Or use the CLI
neo4j-agent-memory mcp serve --transport stdio
```

This demonstrates:
- Starting the MCP server programmatically
- Core (6 tools) and extended (16 tools) profiles
- Tool usage: memory_store_message, memory_search, memory_get_conversation, etc.
- Both stdio and SSE transports

### 4. Full Pipeline

```bash
python full_pipeline.py
```

This runs all features together in a cohesive demo.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Google Cloud Platform                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌───────────────┐    ┌───────────────┐    ┌────────────────┐   │
│  │  Google ADK   │    │  MCP Client   │    │  Your App      │   │
│  │    Agent      │    │  (Claude)     │    │                │   │
│  └───────┬───────┘    └───────┬───────┘    └───────┬────────┘   │
│          │                    │                    │             │
│          ▼                    ▼                    ▼             │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Neo4j Agent Memory Library                    │  │
│  │  ┌─────────────────┐  ┌─────────────────┐                 │  │
│  │  │Neo4jMemoryService│  │  MCP Server    │                 │  │
│  │  │  (ADK Interface) │  │  (5 tools)     │                 │  │
│  │  └────────┬────────┘  └────────┬────────┘                 │  │
│  │           │                    │                           │  │
│  │           ▼                    ▼                           │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │                   MemoryClient                       │  │  │
│  │  │  ┌─────────────┐ ┌─────────────┐ ┌───────────────┐  │  │  │
│  │  │  │ Short-Term  │ │  Long-Term  │ │   Reasoning   │  │  │  │
│  │  │  │   Memory    │ │   Memory    │ │    Memory     │  │  │  │
│  │  │  └─────────────┘ └─────────────┘ └───────────────┘  │  │  │
│  │  └────────────────────────┬────────────────────────────┘  │  │
│  └───────────────────────────┼───────────────────────────────┘  │
│                              │                                   │
│  ┌───────────────────────────┼───────────────────────────────┐  │
│  │                           ▼                                │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │  Vertex AI      │  │         Neo4j Database          │ │  │
│  │  │  Embeddings     │  │   (Graph + Vector Storage)      │ │  │
│  │  │ text-embedding-004│ │                                 │ │  │
│  │  └─────────────────┘  └─────────────────────────────────┘ │  │
│  │                        Storage & AI Layer                  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Deploying to Cloud Run

See `deploy/cloudrun/README.md` for production deployment instructions.

Quick deploy:

```bash
# From the neo4j-agent-memory directory
cd deploy/cloudrun

# Deploy
gcloud run deploy neo4j-memory-mcp \
  --source . \
  --region us-central1 \
  --set-secrets NEO4J_URI=neo4j-uri:latest,NEO4J_PASSWORD=neo4j-password:latest
```

## MCP Tools Reference

The MCP server supports two tool profiles:

### Core Profile (6 tools)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `memory_search` | Semantic search across memory | `query`, `limit`, `memory_types` |
| `memory_get_context` | Assembled context for a session | `session_id`, `query`, `max_items` |
| `memory_store_message` | Store a conversation message | `content`, `role`, `session_id` |
| `memory_add_entity` | Create/update entity with POLE+O type | `name`, `entity_type`, `description` |
| `memory_add_preference` | Record a user preference | `category`, `preference` |
| `memory_add_fact` | Store a fact triple | `subject`, `predicate`, `object_value` |

### Extended Profile (adds 10 more tools)

| Tool | Description |
|------|-------------|
| `memory_get_conversation` | Full conversation history for a session |
| `memory_list_sessions` | List sessions with previews |
| `memory_get_entity` | Entity details with graph relationships |
| `memory_export_graph` | Export subgraph as JSON |
| `memory_create_relationship` | Create typed entity relationship |
| `memory_start_trace` | Begin reasoning trace |
| `memory_record_step` | Record reasoning step |
| `memory_complete_trace` | Complete reasoning trace |
| `memory_get_observations` | Session observations and insights |
| `graph_query` | Execute read-only Cypher queries |

## Embedding Models

Vertex AI supports these embedding models:

| Model | Dimensions | Best For |
|-------|------------|----------|
| `text-embedding-004` | 768 | General purpose (recommended) |
| `textembedding-gecko@003` | 768 | Legacy applications |
| `textembedding-gecko-multilingual@001` | 768 | Multilingual content |

## Troubleshooting

### Vertex AI Authentication

```bash
# Check authentication
gcloud auth application-default print-access-token

# Re-authenticate if needed
gcloud auth application-default login
```

### Neo4j Connection

```bash
# Test connection
cypher-shell -a bolt://localhost:7687 -u neo4j -p password "RETURN 1"
```

### MCP Server Issues

```bash
# Check server logs
neo4j-agent-memory mcp serve --transport stdio 2>&1 | tee mcp.log
```

## See Also

- [Vertex AI Embeddings Documentation](https://cloud.google.com/vertex-ai/docs/generative-ai/embeddings/get-text-embeddings)
- [Google ADK Documentation](https://cloud.google.com/vertex-ai/docs/reasoning-engine/agent-development-kit)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Neo4j Agent Memory Documentation](../../docs/)

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03. Stale `await memory_client.initialize()` snippet in `adk_memory_service.py` was corrected to `await memory_client.connect()` during this verification pass._
