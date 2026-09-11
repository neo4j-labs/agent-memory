# Google Cloud Integration Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Vertex AI embeddings, Google ADK memory, the FastMCP 4 server, reasoning-trace audit queries, and buffered writes for Cloud Run — one folder.

Four scripts wiring `neo4j-agent-memory` into the Google Cloud surface. Use this
if you have already chosen GCP; [`examples/google_adk_demo/`](../google_adk_demo/)
is the smaller starting point and owns the full ADK `Runner` loop.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** Swap providers with a single string — `MemorySettings(llm=os.environ.get("MEMORY_LLM", "openai/gpt-5-mini"), embedding="vertex_ai/gemini-embedding-001")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html).

## Scripts

| Script | What it shows |
|--------|---------------|
| `vertex_ai_embeddings.py` | `VertexAIEmbedder` with `gemini-embedding-001`, `output_dimensionality`, task types, and **multi-tenant** writes (`memory.multi_tenant=True` + `user_identifier=`) |
| `adk_memory_service.py` | The two ADK narratives unique to this folder — what `add_session_to_memory()` extracts into the graph, and how preferences are written/read through the adapter. The full `Runner` + `load_memory` loop lives in [`../google_adk_demo/`](../google_adk_demo/) |
| `mcp_server_demo.py` | MCP tool profiles (6 core / 16 extended), tool calls through `fastmcp.Client` reading `result.data`, stdio vs Streamable HTTP, `--schemas` |
| `full_pipeline.py` | All seven phases end to end: Vertex embeddings → ADK → MCP → reasoning trace + `TOUCHED` audit query → Cloud Run config + buffered writes → `MemoryIntegration` PER_DAY → consolidation dry run |
| `_common.py` | Shared `load_env()` / `build_settings()` / `describe_settings()` used by all four |

## Architecture

```
                 ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
                 │  Google ADK  │   │  MCP client  │   │   Your app  │
                 │    agent     │   │  (Claude, …) │   │             │
                 └──────┬───────┘   └──────┬───────┘   └──────┬──────┘
                        ▼                  ▼                  ▼
        ┌─────────────────────────────────────────────────────────────┐
        │                 neo4j-agent-memory                          │
        │  Neo4jMemoryService        MCP server (6 core / 16 extended)│
        │             │                         │                     │
        │             └───────────┬─────────────┘                     │
        │                   MemoryClient                              │
        │        short_term │ long_term │ reasoning │ consolidation    │
        └───────────────────────────┬─────────────────────────────────┘
                                    ▼
          ┌────────────────────┐        ┌───────────────────────────┐
          │   Vertex AI        │        │  Neo4j (graph + vector)   │
          │ gemini-embedding-  │        │  or hosted NAMS           │
          │        001         │        │                           │
          └────────────────────┘        └───────────────────────────┘
```

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) — open in [Excalidraw](https://excalidraw.com) to edit.*

## Prerequisites

### 1. Neo4j (or a hosted NAMS key)

```bash
# Docker, matching what CI uses
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/test-password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5.26-community
```

Or set `MEMORY_API_KEY` to use the hosted service and skip Neo4j entirely.

### 2. Install

```bash
# Everything these scripts touch. The [google] extra is Vertex AI only —
# [google-adk] and [mcp] are separate.
pip install "neo4j-agent-memory[google,google-adk,mcp,extraction]"

# Local, no-API-key embeddings (otherwise set OPENAI_API_KEY):
pip install "neo4j-agent-memory[sentence-transformers]"
python -m spacy download en_core_web_sm

# Developing against the source tree instead:
pip install -e ".[google,google-adk,mcp,extraction,sentence-transformers]"
```

### 3. Google Cloud (only for the Vertex phases)

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=your-project-id
export EMBEDDING_PROVIDER=vertex_ai
```

### 4. Environment

```bash
cp .env.example .env     # loaded automatically by every script
```

## Quick start

```bash
python vertex_ai_embeddings.py     # Vertex embeddings + multi-tenant writes
python adk_memory_service.py       # ADK extraction + preference narratives
python mcp_server_demo.py          # tool profiles + live tool calls
python mcp_server_demo.py --schemas --no-tools   # reference output, no database
python full_pipeline.py            # all seven phases
```

### Expected output (abridged, `full_pipeline.py`, no API keys)

```
  neo4j-agent-memory 0.5.0
Configuration:
  backend (requested): bolt
  embedding: SentenceTransformersProvider — sentence-transformers/all-MiniLM-L6-v2
  llm: None
  extractor: ExtractionPipeline (SpacyEntityExtractor, GLiNEREntityExtractor)

Phase 1: Vertex AI Embeddings        → skipped (GOOGLE_CLOUD_PROJECT not set)
Phase 2: Google ADK MemoryService
  ✓ Stored session: pipeline-20260910165524-1
  Query: 'Sarah Chen' (person entity) — 3 entr(ies)
Phase 3: MCP Server Tools            → 16 tools listed, 5 called
Phase 4: Reasoning Trace + TOUCHED Audit Query
  task='Summarise what we know about the GraphRAG project'
    action="search_entities(query='GraphRAG')" entities=['Sarah Chen']
  via MCP graph_query: 1 row(s)
Phase 5: Cloud Run config + buffered writes
  write_mode=buffered → is_buffered=True
  queued writes, pending=5 → after flush(), pending=0
Phase 6: MemoryIntegration (PER_DAY) → search: messages=2, entities=1, preferences=1
Phase 7: Consolidation (dry run)     → duplicate candidates: 0
```

## Backends

Every script resolves its backend the same way and prints it at startup:

| Environment | Backend | Notes |
|-------------|---------|-------|
| `MEMORY_API_KEY` set | `nams` | Hosted service; embeddings and extraction are server-side. `NEO4J_*` is ignored. Buffered writes and consolidation are bolt-only and are skipped. |
| `OPENAI_API_KEY` set | `bolt` | OpenAI embeddings + LLM extraction fallback. |
| neither | `bolt` | `llm=None`, local sentence-transformers embedder, local spaCy/GLiNER pipeline. |

The hosted MCP endpoint at `memory.neo4jlabs.com` is the zero-ops alternative to
deploying the MCP server yourself — see the Cloud Run section below.

## Deploying to Cloud Run

`deploy/cloudrun/` holds the maintained Dockerfile and service definition. The
transport is **Streamable HTTP** served at `/mcp/` (`--transport http`); the
legacy HTTP+SSE transport is deprecated in the MCP spec and `--transport sse`
now warns and serves Streamable HTTP.

```bash
cd deploy/cloudrun
gcloud run deploy neo4j-memory-mcp \
  --source . \
  --region us-central1 \
  --set-secrets NEO4J_URI=neo4j-uri:latest,NEO4J_PASSWORD=neo4j-password:latest
```

For a serving path that must answer before persistence completes, use buffered
writes (`MemorySettings.memory.write_mode="buffered"`,
`await client.buffered.submit(...)`, `await client.flush()`) — phase 5 of
`full_pipeline.py` runs it. See [`examples/buffered-writes/`](../buffered-writes/)
for the measured version.

## MCP tools reference

### Core profile (6 tools)

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `memory_search` | Semantic search across memory | `query`, `limit`, `memory_types` |
| `memory_get_context` | Assembled context for a session | `session_id`, `query`, `max_items` |
| `memory_store_message` | Store a conversation message | `content`, `role`, `session_id` |
| `memory_add_entity` | Create/update entity with POLE+O type | `name`, `entity_type`, `description` |
| `memory_add_preference` | Record a user preference | `category`, `preference` |
| `memory_add_fact` | Store a fact triple | `subject`, `predicate`, `object_value` |

### Extended profile (adds 10, for 16 total)

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
| `graph_query` | Execute read-only Cypher (takes `parameters`) |

Run `python mcp_server_demo.py --schemas --no-tools` for every tool's JSON input
schema.

## Embedding models

| Model | Native dimensions | Best for |
|-------|------------------|----------|
| `gemini-embedding-001` | 3072 | General purpose (default; truncated to 768 unless you pass `output_dimensionality`) |
| `text-embedding-005` | 768 | English and code, task-specific |
| `text-multilingual-embedding-002` | 768 | Multilingual content |

`text-embedding-004` (shut down 2026-01-14) and the `textembedding-gecko*` family
(shut down 2025-04-09) are retired; passing either id raises an `EmbeddingError`
naming the replacement. `VertexAIEmbedder` truncates `gemini-embedding-001`
output to 768 dimensions by default so Neo4j vector indexes created for the old
default keep working — pass `output_dimensionality=None` for the native 3072 on a
fresh database.

## Known gaps

- **`graph_query` (and every other tool) returns double-encoded JSON.** The tools
  are annotated `-> str` and return `json.dumps(...)`, so `result.data` is a JSON
  *string*; the examples do one `json.loads(result.data)` on top. Returning
  `dict` from the tools would give real output schemas but is a wire-visible
  change.
- **Message search is not session-scoped on bolt.** `search_messages(session_id=...)`
  accepts the filter (hosted NAMS requires it) but the bolt implementation
  currently searches every session.
- **`Neo4jMemoryService` does not detect preferences.** `add_session_to_memory()`
  stores messages and extracts entities; preference learning comes from
  `MemoryIntegration(auto_preferences=True)` or an explicit
  `add_memory(memory_type="preference")`.

## Troubleshooting

```bash
# Vertex AI auth
gcloud auth application-default print-access-token
gcloud auth application-default login

# Neo4j connection
cypher-shell -a bolt://localhost:7687 -u neo4j -p test-password "RETURN 1"

# MCP server over HTTP, then list its tools
neo4j-agent-memory mcp serve --transport http --host 0.0.0.0 --port 8080 &
fastmcp list http://127.0.0.1:8080/mcp/
```

Entity phases printing nothing usually means the extractor degraded to
`NoOpExtractor` — every script prints the resolved extractor at startup, so check
that line first and install `[extraction]` plus the spaCy model.

## See also

- [Vertex AI text embeddings](https://cloud.google.com/vertex-ai/docs/generative-ai/embeddings/get-text-embeddings)
- [Google ADK documentation](https://google.github.io/adk-docs/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Google Cloud how-to](https://neo4j.com/labs/agent-memory/how-to/integrations/google-cloud.html)

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev with google-adk 2.7.0, google-genai 2.22.0, fastmcp 4.0.3, google-cloud-aiplatform 2.1.0 on 2026-09-10 — all four scripts run end to end against Neo4j 5.26 with no API keys (local embedder, local extraction). The Vertex AI embedding phases require real GCP credentials and were not exercised in this pass._
