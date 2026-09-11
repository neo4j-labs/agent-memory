# Smart Shopping Assistant

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Microsoft Agent Framework + Neo4j Agent Memory: a retail assistant that learns shopping preferences and recommends products via graph traversal.

A full-stack example application demonstrating Neo4j Agent Memory integration with the Microsoft Agent Framework (1.x GA). The retail shopping assistant showcases graph-native memory: preference learning, graph-based recommendations, reasoning-trace audit edges, an entity-deduplication review queue, and memory graph visualization.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Need a different LLM or embedding model?** For the *memory* layers you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-sonnet-4-5", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html). The *agent* is driven by the Agent Framework's own chat client, so its model comes from `OPENAI_MODEL` (or the Azure deployment name).

## Features

Every bullet below maps to code in this example — see the file in parentheses.

- **Automatic memory injection and persistence**: `Neo4jContextProvider` loads conversation, preferences, entities and similar past traces before each model call, then persists the turn afterwards (`agent.py`, `memory_config.py`)
- **Preference learning**: the agent records preferences through the library's `remember_preference` tool, and `POST /memory/preferences` gives the UI a deterministic write path (`main.py`)
- **Entity deduplication with a review queue**: a custom `DeduplicationConfig` (`flag_threshold=0.80` for noisy retail names) flags near-duplicate product entities; `GET /memory/duplicates` lists the pending pairs and `POST /memory/duplicates/review` merges or rejects them (`memory_config.py`, `main.py`)
- **Reasoning audit trail**: every turn becomes a `ReasoningTrace` linked to the message that triggered it (`INITIATED_BY`), with `(:ReasoningStep)-[:TOUCHED]->(:Entity)` edges for the products each tool call touched and a structured `TraceOutcome` — **failures included** (`agent.py`)
- **Explicit extraction config**: `ExtractionConfig` with spaCy + GLiNER, so entity extraction costs no LLM calls (`memory_config.py`)
- **Graph-based recommendations**: "same category / same brand / similar / bought together / shared attribute" traversals through a closed allow-list of relationship types (`tools/recommendations.py`)
- **Hybrid vector + graph product search**: vector search over `product_embedding` with graph filters, falling back to text matching — and the response says which branch ran (`tools/product_search.py`)
- **Inventory-aware suggestions**: stock checks and in-stock alternatives (`tools/inventory.py`)
- **Graph-native cart**: `(:Cart {session_id})-[:CONTAINS]->(:Product)` (`tools/cart.py`)
- **Memory graph visualization**: `GET /memory/graph` via `client.get_graph()`, plus a `center_entity` traversal view (`main.py`)
- **GDS algorithm integration**: PageRank / node similarity / shortest path exposed as agent tools, with a Cypher fallback — the backend logs at startup which mode is actually active (`memory_config.py`, `main.py`)
- **Per-day sessions**: `MemoryIntegration(session_strategy=SessionStrategy.PER_DAY)` resolves session ids, and each shopper gets a first-class `:User` node (`main.py`)

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Microsoft Agent Retail Assistant Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Prerequisites

- Python 3.10+
- Node.js 18+
- **Neo4j 5.23+** (5.26 LTS recommended, or AuraDB) — the recommendation queries use the variable-scope `CALL (var) { ... }` clause introduced in 5.23
- OpenAI API key (or Azure OpenAI). Without one the app still starts: product search falls back to text matching and `/chat` returns a clear error.

## Quick Start

### 1. Configure the environment

```bash
cd backend
cp .env.example .env
# edit .env: NEO4J_PASSWORD is required, OPENAI_API_KEY is needed for chat
```

`NEO4J_PASSWORD` has no default — the backend fails at startup rather than silently trying `password`. See [`backend/.env.example`](backend/.env.example) for every variable.

### 2. Install backend dependencies

```bash
cd backend
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # required by get_extraction_config()
```

The install pulls `neo4j-agent-memory[openai,microsoft-agent,extraction,fuzzy]`. The `extraction` extra supplies spaCy + GLiNER (local, no per-message LLM cost) and `fuzzy` supplies rapidfuzz for the fuzzy deduplication path. If you would rather not install the local models, set `enable_spacy=False, enable_gliner=False, enable_llm_fallback=True` in `get_extraction_config()` and pay per message instead.

### 3. Load sample product data

This creates 16 products across 5 categories with relationships, attributes, and (with an OpenAI key) vector embeddings:

```bash
cd backend
python -m data.load_products
```

Every write is a `MERGE`, so the default run is **non-destructive** and safe to repeat — including on a database that already holds agent memory. To start from a clean database:

```bash
python -m data.load_products --reset          # prompts before deleting
python -m data.load_products --reset --yes    # no prompt (CI)
```

> **Note:** Embedding generation requires `OPENAI_API_KEY`. Without it, products are still created and text search works as a fallback.

### 4. Start the backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Or run directly:

```bash
cd backend
python main.py
```

Watch the startup log — it reports the Neo4j connection, whether vector search is enabled, and whether the GDS plugin was detected.

### 5. Install frontend dependencies

```bash
cd frontend
npm install
```

### 6. Start the frontend

```bash
cd frontend
npm run dev
```

Open http://localhost:3000 in your browser.

### 7. Run the smoke checks (optional)

With the backend running, verify every endpoint:

```bash
cd backend
python smoke_check.py                  # all checks (needs OPENAI_API_KEY for the two chat checks)
python smoke_check.py --no-chat        # everything except the LLM calls
python smoke_check.py --base-url http://localhost:9000
```

`smoke_check.py` is standard library only and deliberately not named `test_*`, so pytest never collects it.

### Expected output

A healthy run of `python smoke_check.py --no-chat` against a loaded database prints:

```
Testing backend at http://localhost:8000

[CHECK] Health check
  {"status": "healthy", "database": "connected", "vector_search": "enabled"}
  PASS

[CHECK] Memory context
  ...
  PASS

[CHECK] Product search
  total=6 mode=vector
  PASS

...

Results: 10 passed, 0 failed out of 10
```

`mode=text` instead of `mode=vector` means embeddings are missing — re-run the loader with `OPENAI_API_KEY` set.

## Example Conversations

Try these conversations to see the memory features in action:

1. **Preference Learning**:
   - "I'm looking for running shoes"
   - "I prefer Nike brand"
   - "My budget is under $150"
   - Later: "What shoes would you recommend?" (uses learned preferences)

2. **Graph-Based Recommendations**:
   - "Show me the Nike Air Max 90"
   - "What products are similar to this?"
   - "How is this related to the Nike Pegasus 40?" (uses `explain_product_connection`)

3. **Memory Recall**:
   - "What do you know about my preferences?"
   - "What products have we discussed?"

Then look at what the conversation left behind:

```cypher
// What did this turn's tool calls touch? One hop, thanks to the TOUCHED edges.
MATCH (t:ReasoningTrace {session_id: $sessionId})-[:HAS_STEP]->(s:ReasoningStep)-[:TOUCHED]->(e:Entity)
RETURN t.task, t.success, collect(DISTINCT e.name) AS touched
```

## Project Structure

```
microsoft_agent_retail_assistant/
├── backend/
│   ├── main.py              # FastAPI server with SSE streaming
│   ├── agent.py             # Microsoft Agent Framework agent + trace recording
│   ├── memory_config.py     # Settings, MemorySettings, memory factories
│   ├── smoke_check.py       # Endpoint checks (run against a live server)
│   ├── requirements.txt
│   ├── .env.example         # Copy to .env (which is not committed)
│   ├── tools/               # The single implementation of every catalog operation
│   │   ├── product_search.py    # Product catalog search
│   │   ├── recommendations.py   # Graph-based recommendations
│   │   ├── inventory.py         # Stock/availability checks
│   │   └── cart.py              # Shopping cart operations
│   └── data/
│       └── load_products.py     # Sample data loader (16 products)
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js app router
│   │   ├── components/
│   │   │   ├── ChatInterface.tsx
│   │   │   ├── PreferencePanel.tsx
│   │   │   └── MemoryExplorer.tsx
│   │   └── lib/
│   │       └── api.ts
│   └── package.json
└── README.md
```

**One implementation per operation.** `backend/tools/*.py` holds plain async functions that take a connected `MemoryClient` first; `agent.py` wraps each as an Agent Framework `FunctionTool` and `main.py` serves several of them over REST. No catalog Cypher lives anywhere else.

## Key Neo4j Features Demonstrated

### 1. Three-Layer Memory Architecture

- **Short-term**: Conversation history with semantic search
- **Long-term**: Product entities, user preferences, purchase patterns
- **Reasoning**: Traces of past shopping assistance, with audit edges to the entities each step touched

### 2. Graph Traversals

```cypher
// Find products similar to what the user viewed
MATCH (p:Product {id: $productId})-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(similar)
WHERE similar <> p
RETURN similar, count(c) AS shared_categories
ORDER BY shared_categories DESC
LIMIT 5
```

### 3. GDS Algorithms (with fallback)

- **PageRank**: Identify popular/influential products
- **Node similarity**: Find comparable products
- **Shortest path**: Explain product relationships

The startup log says which mode is live (`GDS plugin detected` vs `NOT detected — graph algorithms run as Cypher fallbacks`).

### 4. Hybrid Vector + Graph Search

```cypher
CALL db.index.vector.queryNodes('product_embedding', $candidates, $embedding)
YIELD node AS p, score
MATCH (p)-[:IN_CATEGORY]->(c)
WHERE c.name = $preferredCategory
RETURN p, score
```

### 5. Read-only Cypher through a portable accessor

Every read in this example goes through `client.query.cypher(...)`, which works on both the self-hosted (bolt) and hosted (NAMS) backends and rejects write queries client-side. Writes use `client.graph.execute_write(...)`, which is bolt-only.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Send message, get SSE streamed response |
| `/chat/sync` | POST | Send message, get complete response (non-streaming) |
| `/memory/context` | GET | Current memory context (all three layers) |
| `/memory/graph` | GET | Memory graph for visualization (`session_id`, or `center_entity` + `max_hops`) |
| `/memory/preferences` | GET | Learned user preferences |
| `/memory/preferences` | POST | Record a preference explicitly |
| `/memory/duplicates` | GET | Entity pairs flagged as potential duplicates, plus dedup stats |
| `/memory/duplicates/review` | POST | Confirm (merge) or reject a flagged pair |
| `/products/search` | GET | Search product catalog (reports `search_mode`) |
| `/products/categories` | GET | Categories with product counts |
| `/products/brands` | GET | Brands, optionally filtered by category |
| `/products/{id}` | GET | Product details |
| `/products/{id}/related` | GET | Related products (`relationship_type` ∈ similar, bought_together, category, brand, attribute) |
| `/health` | GET | Health check (database connectivity, vector-search mode) |

`relationship_type` is a closed enum: anything else returns 422. Relationship types cannot be parameterised in Cypher, so an allow-list is the only safe way to accept one from a query string — see the comment at the `/products/{id}/related` handler.

## Microsoft Agent Framework Integration

This example is built on `neo4j_agent_memory.integrations.microsoft_agent`, which provides drop-in components for the Microsoft Agent Framework 1.x GA line.

### Installing the framework

The Agent Framework is split across distributions:

```
agent-framework-core>=1.17,<2    # the `agent_framework` package itself
agent-framework-openai>=1.14,<2  # OpenAIChatClient (and Azure OpenAI routing)
```

`agent_framework.openai` is a lazy re-export: importing `OpenAIChatClient` with only `-core` installed raises `ModuleNotFoundError: The package agent-framework-openai is required`. The `agent-framework` meta-package works too, but at GA it is `agent-framework-core[all]` — roughly 30 provider distributions.

### Neo4jContextProvider — Automatic Memory Injection

The core integration point is `Neo4jContextProvider`, a `ContextProvider` subclass (renamed from `BaseContextProvider` at GA) that hooks into the agent lifecycle:

- **`before_run()`** — Called automatically before each model invocation. Extracts the latest user message, then queries all three memory layers in parallel and injects the results as extended instructions:
  - **Short-term**: Recent conversation history + semantically similar past messages (vector search with configurable similarity threshold)
  - **Long-term**: Matching user preferences and knowledge-graph entities
  - **Reasoning**: Similar past task traces (task descriptions, outcomes, success/failure)

- **`after_run()`** — Called after the model responds. Persists **both** the user and assistant messages to short-term memory with embeddings, and triggers background entity extraction so the knowledge graph stays current without blocking the response stream.

Because the provider owns persistence, `run_agent_stream()` does **not** call `memory.save_message()`. Doing both is how this example used to write every message twice.

The provider is wired into the agent in `agent.py`:

```python
agent = chat_client.as_agent(
    name="ShoppingAssistant",
    instructions=SYSTEM_PROMPT,
    tools=[*memory_tools, *product_tools],
    context_providers=[memory.context_provider],
)
```

Configuration lives in `memory_config.py`, where `Neo4jMicrosoftMemory` bundles the context provider, chat message store, and optional GDS integration into a single per-session object around one shared `MemoryClient`:

```python
def create_memory(client: MemoryClient, session_id: str, user_id: str | None = None):
    return Neo4jMicrosoftMemory(
        memory_client=client,      # the connected client from the FastAPI lifespan
        session_id=session_id,
        user_id=user_id,
        include_short_term=True,   # conversation history
        include_long_term=True,    # entities & preferences
        include_reasoning=True,    # past task traces
        max_context_items=15,
        extract_entities=True,
        extract_entities_async=True,  # non-blocking extraction
        gds_config=get_gds_config(),
    )
```

`Neo4jMicrosoftMemory` is a thin wrapper, so one connected client backs every session. Creating a client per request (as this example used to) leaks a Neo4j driver per chat turn.

### Other Integration Components

- **`Neo4jChatMessageStore`**: a `HistoryProvider` (renamed from `BaseHistoryProvider` at GA) that persists conversation history in the graph with embeddings
- **`create_memory_tools()`**: generates callable `FunctionTool` instances — `search_memory`, `remember_preference`, `recall_preferences`, `search_knowledge`, `remember_fact`, `find_similar_tasks`, plus GDS-backed tools when a `GDSConfig` is enabled
- **`record_agent_trace()`**: a one-call trace recorder. This example instead drops to `client.reasoning` directly, because the one-call helper exposes neither `touched_entities` nor `TraceOutcome` (see Known gaps)
- **GDS integration**: graph algorithms with automatic fallback to Cypher when the plugin is not installed

## Known gaps

Honest notes about what is *not* demonstrated here, and why:

- **Multi-tenancy is not enabled.** `MemorySettings.memory.multi_tenant=True` makes every short-term write require `user_identifier=`, and `Neo4jContextProvider.after_run()` does not thread one through — so turning it on would silently stop messages being saved. The example creates a `:User` node per shopper (`client.users.upsert_user(...)`) and scopes sessions per user, but tenant-scoped reads await a library change.
- **`DeduplicationConfig` cannot come from settings.** `MemorySettings` has no `deduplication` field and `MemoryClient` never forwards one to `LongTermMemory`, so `client.long_term` always runs library defaults. `memory_config.create_long_term_memory()` constructs the layer directly to apply this example's thresholds; `/memory/duplicates` uses that instance.
- **`find_potential_duplicates()` reports both directions of a flagged pair** and a `confidence` of `0.0` even when the `SAME_AS` relationship carries a score — the confidence shown in `GET /memory/duplicates` is therefore not yet meaningful.
- **Azure OpenAI** goes through the same `OpenAIChatClient` with `azure_endpoint=`; the old `AzureOpenAIResponsesClient` is deprecated and ships in a separate pre-release distribution, so it is not used.

## Troubleshooting

### The backend exits immediately with a validation error

`NEO4J_PASSWORD` is required and has no default. `cp .env.example .env` and fill it in.

### `ModuleNotFoundError: The package agent-framework-openai is required`

Install the chat-client distribution as well as the core package: `pip install -r requirements.txt` installs both.

### `ImportError: cannot import name 'BaseContextProvider'`

You are on a `neo4j-agent-memory` release that predates the Agent Framework GA rename. Upgrade to `neo4j-agent-memory>=0.5.0` (this example pins `>=0.5.0,<0.7`).

### Product search returns no results

Two causes. If `search_mode` is `text`, embeddings are missing — re-run the loader with `OPENAI_API_KEY` set:

```bash
OPENAI_API_KEY=sk-... python -m data.load_products
```

If `search_mode` is `text` *and* the log shows a vector-search warning, the `product_embedding` index is missing or has the wrong dimensions (it must match `OPENAI_EMBEDDING_MODEL`; the default is 1536). The backend deliberately logs at WARNING when it falls back instead of degrading silently.

If the catalog is empty altogether, run the loader. Note that the text branch matches substrings: `query=shoe` matches, `query=shoes` does not.

### `Neo.ClientError.Statement.SyntaxError` mentioning `CALL (`

Your Neo4j is older than 5.23. The recommendation and alternatives queries use the variable-scope `CALL (var) { ... }` clause. Upgrade to 5.26 LTS.

### `Only read-only Cypher queries are allowed`

`client.query.cypher()` validates queries by scanning their **raw text** for write keywords — comments included. A comment containing the word "set" is enough to trip it. Use `client.graph.execute_write()` for real writes.

### GDS algorithms not available

GDS tools (PageRank, shortest path, node similarity) require the Neo4j GDS plugin. Without it, the app falls back to Cypher alternatives; the startup log says which mode is active. `fallback_to_basic=True` (default) keeps the tools working either way.

### CORS errors from the frontend

The backend allows requests from `http://localhost:3000` and `http://127.0.0.1:3000`. If the frontend runs on a different port, update the `allow_origins` list in `main.py`.

## License

Apache 2.0

## Support

- 📖 [Microsoft Agent Framework how-to guide](https://neo4j.com/labs/agent-memory/how-to/integrations/microsoft-agent.html) — the conceptual companion to this app
- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (the Agent Framework GA rename is unreleased; pin `>=0.5.0,<0.7`), `agent-framework-core` 1.18.0, `agent-framework-openai` 1.14.3, `fastapi` 0.141.1, `sse-starlette` 3.2.0 and Neo4j 5.26.19 on 2026-09-10. Every REST endpoint was exercised against a live Neo4j via `httpx.ASGITransport`, and the chat turn (message persistence, reasoning trace, `TOUCHED` audit edges, failure recording) against a fake chat client — a full LLM run additionally needs `OPENAI_API_KEY`._
