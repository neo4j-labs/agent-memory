# Smart Shopping Assistant

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Microsoft Agent Framework + Neo4j Agent Memory: a retail assistant that learns shopping preferences and recommends products via graph traversal.

A full-stack example application demonstrating Neo4j Agent Memory integration with the Microsoft Agent Framework. The retail shopping assistant showcases graph-native memory capabilities including preference learning, graph-based recommendations, and memory visualization.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Features

- **Preference Learning**: Automatically extracts and stores shopping preferences from conversation
- **Entity Deduplication**: `DeduplicationConfig` prevents duplicate product entities (e.g., "Nike Air Max" vs "Nike Air Max 90") with configurable auto-merge and flag thresholds
- **Explicit Extraction Config**: `ExtractionConfig` with spaCy + GLiNER for retail entity identification
- **Graph-Based Recommendations**: "Customers who bought X also bought Y" via graph traversals
- **Product Relationship Discovery**: Find related products through shared attributes, categories, brands
- **Inventory-Aware Suggestions**: Filter recommendations by real-time availability
- **Memory Graph Visualization**: Interactive visualization of the context graph powering recommendations
- **GDS Algorithm Integration**: PageRank for popular products, community detection for product grouping

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Microsoft Agent Retail Assistant Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Prerequisites

- Python 3.10+
- Node.js 18+
- Neo4j 5.x (local or AuraDB)
- OpenAI API key (or Azure OpenAI)

## Quick Start

### 1. Set Environment Variables

Create a `.env` file in the `backend` directory:

```bash
# Neo4j connection
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# OpenAI (or Azure OpenAI)
OPENAI_API_KEY=sk-your-key

# Or for Azure OpenAI:
# AZURE_OPENAI_API_KEY=your-key
# AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
# AZURE_OPENAI_DEPLOYMENT=gpt-4
```

### 2. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 3. Load Sample Product Data

This creates 16 products across 5 categories, with relationships, attributes, and vector embeddings:

```bash
cd backend
python -m data.load_products
```

> **Note:** Embedding generation requires `OPENAI_API_KEY`. If not set, products are still created and text search will work as a fallback.

### 4. Start the Backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Or run directly:

```bash
cd backend
python main.py
```

### 5. Install Frontend Dependencies

```bash
cd frontend
npm install
```

### 6. Start the Frontend

```bash
cd frontend
npm run dev
```

Open http://localhost:3000 in your browser.

### 7. Run Smoke Tests (Optional)

With the backend running, verify all endpoints are working:

```bash
cd backend
python test_backend.py
```

You can also point at a different host:

```bash
python test_backend.py --base-url http://localhost:9000
```

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
   - "How is this related to running shoes?"

3. **Memory Recall**:
   - "What do you know about my preferences?"
   - "What products have we discussed?"

## Project Structure

```
microsoft_agent_retail_assistant/
├── backend/
│   ├── main.py              # FastAPI server with SSE streaming
│   ├── agent.py             # Microsoft Agent Framework agent
│   ├── memory_config.py     # Neo4j memory configuration
│   ├── test_backend.py      # Smoke tests (run against live server)
│   ├── requirements.txt
│   ├── .env                 # Environment variables (not committed)
│   ├── tools/
│   │   ├── product_search.py    # Product catalog search
│   │   ├── recommendations.py   # Graph-based recommendations
│   │   ├── inventory.py         # Stock/availability checks
│   │   └── cart.py              # Shopping cart operations
│   └── data/
│       └── load_products.py     # Sample data loader (16 products)
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js 14 app router
│   │   ├── components/
│   │   │   ├── ChatInterface.tsx
│   │   │   ├── PreferencePanel.tsx
│   │   │   └── MemoryExplorer.tsx
│   │   └── lib/
│   │       └── api.ts
│   └── package.json
└── README.md
```

## Key Neo4j Features Demonstrated

### 1. Three-Layer Memory Architecture

- **Short-term**: Conversation history with semantic search
- **Long-term**: Product entities, user preferences, purchase patterns
- **Reasoning**: Past shopping assistance traces for learning

### 2. Graph Traversals

```cypher
// Find products similar to what user viewed
MATCH (p:Product {id: $productId})-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(similar)
WHERE similar <> p
RETURN similar, count(c) AS shared_categories
ORDER BY shared_categories DESC
LIMIT 5
```

### 3. GDS Algorithms (with fallback)

- **PageRank**: Identify popular/influential products
- **Community Detection**: Group related products
- **Shortest Path**: Explain product relationships

### 4. Hybrid Vector + Graph Search

```cypher
CALL db.index.vector.queryNodes('product_embedding', 10, $embedding)
YIELD node as p, score
MATCH (p)-[:IN_CATEGORY]->(c)
WHERE c.name = $preferredCategory
RETURN p, score
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Send message, get SSE streamed response |
| `/chat/sync` | POST | Send message, get complete response (non-streaming) |
| `/memory/context` | GET | Get current memory context |
| `/memory/graph` | GET | Get memory graph for visualization |
| `/memory/preferences` | GET | Get learned user preferences |
| `/products/search` | GET | Search product catalog |
| `/products/{id}` | GET | Get product details |
| `/products/{id}/related` | GET | Get related products |
| `/health` | GET | Health check (database connectivity) |

## Microsoft Agent Framework Integration

This example is built on `neo4j_agent_memory.integrations.microsoft_agent`, which provides drop-in components for the Microsoft Agent Framework.

### Neo4jContextProvider — Automatic Memory Injection

The core integration point is `Neo4jContextProvider`, a `BaseContextProvider` subclass that hooks into the agent lifecycle:

- **`before_run()`** — Called automatically before each model invocation. Extracts the latest user message, then queries all three memory layers in parallel and injects the results as extended instructions:
  - **Short-term**: Recent conversation history + semantically similar past messages (vector search with configurable similarity threshold)
  - **Long-term**: Matching user preferences and knowledge-graph entities
  - **Reasoning**: Similar past task traces (task descriptions, outcomes, success/failure)

- **`after_run()`** — Called after the model responds. Persists both the user and assistant messages to short-term memory with embeddings, and triggers background entity extraction so the knowledge graph stays current without blocking the response stream.

The provider is wired into the agent in `agent.py`:

```python
agent = chat_client.as_agent(
    name="ShoppingAssistant",
    instructions=SYSTEM_PROMPT,
    tools=all_tools,
    context_providers=[memory.context_provider],
)
```

Configuration lives in `memory_config.py`, where `Neo4jMicrosoftMemory` bundles the context provider, chat message store, and optional GDS integration into a single object:

```python
memory = Neo4jMicrosoftMemory(
    memory_client=client,
    session_id=session_id,
    include_short_term=True,   # conversation history
    include_long_term=True,    # entities & preferences
    include_reasoning=True,    # past task traces
    max_context_items=15,
    extract_entities=True,
    extract_entities_async=True,  # non-blocking extraction
    gds_config=gds_config,
)
```

### Other Integration Components

- **Neo4jChatMessageStore**: `BaseChatMessageStore` implementation that persists conversation history in the Neo4j graph with embeddings
- **`create_memory_tools()`**: Generates callable `FunctionTool` instances (search memory, save preferences, find similar entities) that the agent can invoke during a conversation
- **`record_agent_trace()`**: Records each completed interaction (user message, assistant response, tool calls, outcome) as a reasoning trace so the agent can learn from past interactions
- **GDS Integration**: Graph algorithms (PageRank, community detection, shortest path) for enhanced recommendations, with automatic fallback to Cypher when GDS is not installed

## Troubleshooting

### `load_products` clears existing data

`python -m data.load_products` runs `MATCH (n) DETACH DELETE n` before loading. This wipes the entire database. Run it only for initial setup or to reset to a clean state.

### Vector search not working

If product search returns no results, embeddings may not have been generated. Re-run the data loader with `OPENAI_API_KEY` set:

```bash
OPENAI_API_KEY=sk-... python -m data.load_products
```

The backend falls back to text search (`CONTAINS`) when vector search fails, so the app will still work without embeddings.

### Backend dependencies install from local source

`requirements.txt` installs `neo4j-agent-memory` from the local repo root (`../../../[openai,microsoft-agent]`). Make sure you're running `pip install -r requirements.txt` from the `backend/` directory so the relative path resolves correctly.

### GDS algorithms not available

GDS tools (PageRank, shortest path, node similarity) require the Neo4j GDS plugin. Without it, the app automatically falls back to Cypher-based alternatives. Set `fallback_to_basic=True` in `memory_config.py` (enabled by default).

### CORS errors from the frontend

The backend allows requests from `http://localhost:3000` and `http://127.0.0.1:3000`. If the frontend runs on a different port, update the `allow_origins` list in `main.py`.

## License

Apache 2.0

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03 (structure & import tests pass; full end-to-end run requires the `microsoft-agent` extra and a Microsoft Agent Framework runtime)._
