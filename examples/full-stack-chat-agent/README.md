# Full-Stack Chat Agent

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> A small PydanticAI + Next.js chat agent wired to Neo4j Agent Memory — all three memory types, SSE streaming, and an interactive memory graph view.

A complete example demonstrating `neo4j-agent-memory` integration with a PydanticAI chat agent and a Next.js frontend. The agent is a news research assistant that reads **two** graphs:

- a **memory graph** written by `neo4j-agent-memory` — conversations, entities, preferences, reasoning traces;
- a **news graph** it queries read-only with its own Neo4j driver and a set of Cypher tools.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).
>
> **Looking for the heavier end-to-end demo?** See [`examples/lennys-memory/`](../lennys-memory/) — same architecture but with 299 podcast episodes, 19 agent tools, and Wikipedia-enriched entity cards.

## Features

- **PydanticAI 2.x agent**: a news research assistant whose `@agent.instructions` hook injects memory context relevant to *this* turn's question
- **Three memory types**
  - Short-term: conversation history (`Conversation` / `Message`)
  - Long-term: preferences plus entities extracted from every turn (`Entity`, POLE+O typed)
  - Reasoning: one `ReasoningTrace` per turn, one `ReasoningStep` + `ToolCall` per tool call, and `(:ReasoningStep)-[:TOUCHED]->(:Entity)` audit edges
- **`MemoryIntegration`**: the high-level wrapper, constructed around the *already connected* client, so storing a message also runs entity extraction and pattern-based preference detection
- **Automatic preference detection**: `PreferenceDetector` runs as a background task on each stored user message — regex patterns, no LLM call, no added latency
- **Read-only news-graph tools**: search, topic/location/date filters, schema introspection, and agent-authored Cypher executed inside a managed **read** transaction, so the server (not a string scan) rejects writes
- **SSE streaming**: tool-call events are emitted *as they happen*, before the answer finishes streaming, so the UI's pending → success transition is real
- **Next.js frontend**: React UI with Chakra UI v3 components
- **Memory graph visualization**: interactive graph view using the Neo4j Visualization Library (NVL), conversation-scoped, with double-click neighbour expansion
- **Memory context panel**: preferences and the entities *this thread* mentioned

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Full-Stack Chat Agent Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Prerequisites

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- Docker (for Neo4j)
- An OpenAI API key (or another provider — see [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html))

## Quick Start

### 1. Start Neo4j

```bash
cd examples/full-stack-chat-agent
docker compose up -d
```

This starts Neo4j 5.26 LTS with APOC on `bolt://localhost:7687`, user `neo4j`, password `password`. Wait for http://localhost:7474 to answer.

> The compose password, `backend/.env.example` and the table below all say `password`. If you change it, change it in all three places — otherwise the backend boots with memory **disabled** and `/health` reports `memory_connected: false`.

### 2. Set up the backend

```bash
cd backend

cp .env.example .env
# Edit .env and set OPENAI_API_KEY

uv sync

# Seed ~16 sample news articles (plus embeddings, if OPENAI_API_KEY is set)
uv run python scripts/load_news_sample.py

uv run uvicorn src.main:app --reload --port 8000
```

Check that both graphs are connected:

```bash
curl -s localhost:8000/health
# {"status":"healthy","memory_connected":true,"memory_error":null,
#  "news_connected":true,"news_error":null,"extraction_mode":"local"}
```

> **First run with `EXTRACTION_MODE=local`** downloads the spaCy and GLiNER models (a few hundred MB, once). Set `EXTRACTION_MODE=llm` to extract with the LLM instead (no download, small per-message cost), or `none` to turn extraction off.

### 3. Set up the frontend

```bash
cd frontend

cp .env.example .env
npm install
npm run dev
```

### 4. Open the app

Visit http://localhost:3000 and ask something like *"What's new in offshore wind?"* or *"I prefer short summaries — what's happening in AI policy?"*.

## Expected output

After a couple of turns:

| Where | What you should see |
|---|---|
| Chat pane | tokens streaming, with tool cards appearing **before** the answer completes |
| Memory context panel | the entities this thread mentioned (Statkraft, Brussels, …) and any detected preferences |
| Graph view | `Conversation → Message → Entity`, plus `ReasoningTrace → ReasoningStep → ToolCall` |
| `GET /api/preferences` | at least one auto-detected preference if you stated one ("I prefer…", "I like…") |
| `GET /api/entities` | the extracted entities, POLE+O typed |
| `GET /api/memory/traces` | one trace per turn with `step_count > 0` |
| `GET /api/memory/tool-stats` | per-tool call counts, success rates and average durations |

A one-hop audit query answers "what did this reasoning step affect?":

```cypher
MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)
RETURN rt.task, s.action, collect(e.name) AS touched
```

## Configuration

### Backend environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEO4J_URI` | Memory graph Neo4j URI | `bolt://localhost:7687` |
| `NEO4J_USERNAME` | Memory graph username | `neo4j` |
| `NEO4J_PASSWORD` | Memory graph password (must match `docker-compose.yml`) | `password` |
| `NEWS_GRAPH_URI` | News graph Neo4j URI | `bolt://localhost:7687` |
| `NEWS_GRAPH_USERNAME` | News graph username — use a **read-only** user for a real graph | `neo4j` |
| `NEWS_GRAPH_PASSWORD` | News graph password | `password` |
| `NEWS_GRAPH_DATABASE` | News graph database name | `neo4j` |
| `NEWS_EMBEDDING_MODEL` | Embedding model for news vector search; must match the `article_embeddings` index | `text-embedding-3-small` |
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `AGENT_MODEL` | PydanticAI model string for the chat agent | `openai:gpt-5-mini` |
| `EXTRACTION_MODE` | `none` \| `local` (spaCy + GLiNER) \| `llm` (spaCy + LLM) | `local` |
| `LLM_MODEL` / `EMBEDDING_MODEL` | Override the *memory library's* providers (see below) | (unset) |
| `CYPHER_TIMEOUT_SECONDS` / `CYPHER_MAX_ROWS` | Bounds on agent-authored Cypher | `15` / `100` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | `http://localhost:3000` |

`AGENT_MODEL` is passed straight to PydanticAI. On 2.x the `openai:` prefix selects the **Responses** API and `openai-chat:` selects Chat Completions. Other providers need the matching extra — the backend installs `pydantic-ai-slim[openai]`, so add e.g. `pydantic-ai-slim[openai,anthropic]` before setting `AGENT_MODEL=anthropic:claude-sonnet-4-5`.

### Frontend environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Backend API URL | `http://localhost:8000/api` |

### Swapping the memory library's LLM / embedding provider

`LLM_MODEL` and `EMBEDDING_MODEL` go through the library's provider-string shorthand, so the memory layer can run on a different provider than the agent:

```bash
# Anthropic for extraction + local embeddings (no OpenAI dependency)
LLM_MODEL=anthropic/claude-sonnet-4-5
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ANTHROPIC_API_KEY=sk-ant-...
```

Then install the matching extras: `uv sync --extra anthropic --extra sentence-transformers`. See the [provider migration guide](https://neo4j.com/labs/agent-memory/how-to/migrate-to-providers.html) for the full matrix.

Changing `EMBEDDING_MODEL` changes the vector dimensionality. Against an existing database the library raises `EmbeddingDimensionMismatchError` at startup rather than writing vectors the indexes cannot serve — drop the `*_embedding_idx` indexes (or use a fresh database) when you switch.

> **Backend is bolt-only.** It calls `client.get_graph()` for the graph view, which the hosted [NAMS](https://memory.neo4jlabs.com) backend does not implement. See the `examples/nams-*` examples for the hosted variant.

## News graph

The seed script creates this schema:

```
Node labels:
- Article: {title, abstract, published, url, embedding}
- Topic: {name}
- Person: {name}
- Organization: {name}
- Geo: {name}

Relationships:
- (Article)-[:HAS_TOPIC]->(Topic)
- (Article)-[:ABOUT_PERSON]->(Person)
- (Article)-[:ABOUT_ORGANIZATION]->(Organization)
- (Article)-[:ABOUT_GEO]->(Geo)
```

```bash
cd backend
uv run python scripts/load_news_sample.py            # load (idempotent)
uv run python scripts/load_news_sample.py --reset    # wipe the sample first
```

Embeddings and the `article_embeddings` vector index are only created when `OPENAI_API_KEY` is set; without it every tool except `vector_search_news` works.

To point the agent at your own news graph, set `NEWS_GRAPH_*` and keep the labels above (or adjust the Cypher in `backend/src/agent/tools.py`). **Use a read-only database user**: the agent writes an LLM-authored Cypher tool, and while the example runs every query inside a managed read transaction, a read-only role is the right second line of defence.

## Available tools

| Tool | Description |
|------|-------------|
| `search_news` | Text search on title and abstract |
| `vector_search_news` | Semantic vector search (embedding computed in the app) |
| `get_recent_news` | Articles from the last N days |
| `get_news_by_topic` | Filter by topic |
| `get_topics` | List topics with article counts |
| `search_news_by_location` | Filter by geography |
| `search_news_by_date_range` | Date-range filter |
| `search_news_multi_topic` | Match any of several topics, people, organizations or places |
| `get_database_schema` | Labels, relationship types and a prose schema description |
| `execute_cypher` | Agent-authored read-only Cypher, row- and time-bounded |

## Memory integration

### `MemoryIntegration` around the connected client

```python
# src/memory/client.py
_memory_client = MemoryClient(memory_settings)
await _memory_client.connect()

_memory_integration = MemoryIntegration(
    client=_memory_client,            # reuse the connected client: one driver
    session_strategy=SessionStrategy.PER_CONVERSATION,
    auto_extract=True,                # entity extraction on store
    auto_preferences=True,            # background PreferenceDetector
)
await _memory_integration.connect()   # no-op when a client was supplied
```

Constructing it from `neo4j_uri=` / `neo4j_password=` instead would open a *second* driver with its own configuration — and `auto_extract` only does anything if the shared client has an extractor, which is what `EXTRACTION_MODE` controls.

### Short-term memory

```python
# src/api/routes/chat.py
stored = await integration.store_message("user", request.message, session_id=thread_id)
user_message_id = stored["id"]
```

### Long-term memory

Preferences are detected automatically; entities come from the extraction pipeline. Both can also be written explicitly:

```python
await memory.long_term.add_preference(
    category="news",
    preference="Interested in AI startups",
    context="User research",
)
entities = await memory.long_term.search_entities("companies")
```

### Reasoning memory

One trace per turn, one step per tool call, plus a structured outcome:

```python
trace = await memory.reasoning.start_trace(
    session_id=thread_id,
    task=request.message,
    triggered_by_message_id=user_message_id,   # (ReasoningTrace)-[:INITIATED_BY]->(Message)
)

# ...as each FunctionToolResultEvent arrives:
step = await memory.reasoning.add_step(trace.id, thought=..., action=tool_name)
await memory.reasoning.record_tool_call(
    step.id,
    tool_name=tool_name,
    arguments=args,
    result=result,
    duration_ms=duration_ms,
    message_id=user_message_id,                # (ToolCall)-[:TRIGGERED_BY]->(Message)
    touched_entities=[EntityRef(name="Statkraft", type="ORGANIZATION")],
)

await memory.reasoning.complete_trace(
    trace.id,
    outcome=TraceOutcome(success=True, summary=answer[:500], metrics={"tool_calls": 3.0}),
)
```

A failing turn completes the trace with `success=False` and `error_kind=type(e).__name__`, so the graph never accumulates traces without `completed_at`.

## API endpoints

### Chat
- `POST /api/chat` — message in, SSE stream out: `token`, `tool_call`, `tool_result`, `done`, `error`

### Threads
- `GET /api/threads` — list threads (title, message count, timestamps)
- `POST /api/threads` — create a thread
- `GET /api/threads/{id}` — thread with messages
- `PATCH /api/threads/{id}?title=…` — rename (persisted on the `Conversation` node)
- `DELETE /api/threads/{id}` — delete the conversation, its messages and its traces
- `GET /api/threads/{id}/summary` — conversation summary

### Memory
- `GET /api/memory/context?thread_id=…` — preferences, thread-scoped entities, recent messages
- `GET /api/memory/graph?session_id=…` — conversation-scoped memory graph
- `GET /api/memory/graph/neighbors/{node_id}?depth=1&limit=50` — expand a node
- `GET /api/memory/traces?session_id=…` — reasoning traces with step and tool-call counts
- `GET /api/memory/tool-stats` — per-tool usage statistics
- `DELETE /api/memory/messages/{id}` — delete a message
- `GET /api/preferences`, `POST /api/preferences`, `DELETE /api/preferences/{id}`
- `GET /api/entities?type=…&query=…` — list (or semantically search) entities

## Development

### Backend

```bash
cd backend

uv run uvicorn src.main:app --reload   # auto-reload
uv run mypy src                        # strict type check
uv run ruff format src && uv run ruff check src
```

The route smoke tests live in the repository root:

```bash
# from the repo root, against a throwaway Neo4j
NEO4J_URI=bolt://localhost:7688 NEO4J_PASSWORD=test-password \
  uv run pytest tests/examples/test_full_stack_apps.py
```

They build the real FastAPI app, drive `POST /threads → POST /chat → GET /threads/{id}` over `httpx.ASGITransport` with PydanticAI's `test` model, and assert the graph state afterwards — no API key needed.

### Deployment

`pyproject.toml` + `uv.lock` are the only dependency manifests. `nixpacks.toml`, `Procfile` and `railway.toml` all run `uv sync --frozen` and then uvicorn, so a deploy installs exactly the locked tree. For production, drop the `[tool.uv.sources]` editable entry so the published release resolves from PyPI.

### Frontend

```bash
cd frontend
npm run dev
npm run build
npm run lint
```

## License

Apache 2.0 — see the main `neo4j-agent-memory` repository for details.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `neo4j-agent-memory` 0.5.0 (editable `0.6.0-dev` surface in-repo), PydanticAI 2.42, FastAPI 0.128, neo4j driver 6.1, Neo4j 5.26 on 2026-09-10. Backend lint, strict mypy and the route smoke tests pass; the frontend is covered by the frontend agent's pass._
