# NAMS Quickstart

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The shortest path in this repo from an API key to a populated memory graph: one file, one environment variable, no database.

`main.py` runs the three memory layers against the hosted **Neo4j Agent Memory Service (NAMS)** and then reads everything back from the server — including the entities NAMS extracted on its own.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What the script does

| Step | Calls | Why it's here |
|---|---|---|
| 1. Short-term | `create_conversation`, `bulk_add_messages` | NAMS mints conversation ids server-side; the whole transcript goes in one round-trip (100 messages max per call) |
| 2. Long-term | `add_entity`, `get_extraction_status`, `wait_for_extraction`, `search_entities` | The write is yours; the *extraction* is the server's, and it is asynchronous — see below |
| 3. Context | `get_context`, `get_observations`, `get_reflections` | The three-tier view (reflections → observations → recent messages) assembled for you |
| 4. Graph | `expand_graph` | 1-hop neighborhood of an extracted entity, the shape a graph view consumes |
| 5. Reasoning | `start_trace`, `add_step`, `record_tool_call`, `complete_trace`, `get_session_traces` | Written *and read back*, so a silent write failure is visible |
| 6. Ontology | `ontology.get_active()` | The schema server-side extraction is validated against |
| 7. Cypher | `query.cypher` | Portable read-only Cypher, identical on bolt |

### Extraction is asynchronous — this is the one thing to internalise

NAMS extracts entities from your messages in a background pipeline, so a read
immediately after a write can legitimately come back empty. Don't sleep; await
the pipeline:

```python
status = await client.short_term.get_extraction_status(conversation_id)   # pending count
settled = await client.long_term.wait_for_extraction(
    session_id=conversation_id, expected_names=["Alice"], timeout=30.0
)
extracted = await client.long_term.search_entities("Alice", limit=5)
```

`wait_for_extraction` polls the conversation's extraction status first, then
confirms the named entities are searchable. It returns `False` on timeout rather
than raising, so you can branch instead of failing.

## Prerequisites

- Python 3.10+
- A NAMS API key from <https://memory.neo4jlabs.com>
- No Neo4j, no `OPENAI_API_KEY`, no embedding provider — the service does extraction and embeddings

## Setup

```bash
uv pip install -r requirements.txt
cp .env.example .env
# edit .env: set MEMORY_API_KEY
```

The script loads this directory's `.env` itself, so exporting is optional.

Two optional variables are documented in `.env.example`:

- `MEMORY_ENDPOINT` — point at a private deployment instead of `https://memory.neo4jlabs.com/v1`.
- `MEMORY_WORKSPACE_ID` — **required by header-scoped deployments** (the development/staging service). It becomes the `X-Workspace-Id` header; without it such an endpoint answers `403` with no further hint. Leave it unset on production keys.

## Run

```bash
uv run python main.py
```

## Expected output

```
Loaded environment from /path/to/examples/nams-quickstart/.env
Connected to https://memory.neo4jlabs.com/v1 (backend=nams)

Conversation: 3f6b2c51-6d0e-4f0a-9a3a-2c1b8e0f7a11
Stored 3 messages in one request:
        user: Hi, I'm Alice.
   assistant: Nice to meet you, Alice!
        user: I love Italian food and dislike crowded restaurants.

Wrote entity: Alice (PERSON)
Extraction right after the write: 3 message(s) pending
Extraction settled: True; searchable entities: 2
   Alice (PERSON)
   Italian food (OBJECT)

Context block: 214 chars, 1 observation(s), 0 reflection(s)
Neighborhood of Alice: 2 node(s), 1 edge(s)

Server-side reasoning steps for this conversation: 1
   step 1: 1 tool call(s)

Active ontology: General (revision 1, permissive) — 5 entity type(s)
Cypher round-trip: [{'name': 'Alice'}]

Done. Your memory graph is at https://memory.neo4jlabs.com.
```

That is the line-for-line shape of a run; the numbers depend on your workspace.
Extracted-entity counts depend on what the server pulls out of the transcript,
entity-type counts on your active ontology, and observations and reflections
accumulate over a conversation — a brand-new one often reports `0` of each. The
offline smoke test (`tests/examples/test_nams_quickstart_example.py`) drives the
same path with fixed payloads if you want to see it run without a key.

## Switching to your own Neo4j

Steps 1, the entity write in 2, 5 and 7 are the backend-agnostic Protocol. Swap
the settings object and they run against your own Neo4j unchanged:

```python
import os

from pydantic import SecretStr

from neo4j_agent_memory import BoltSettings, Neo4jConfig, connect

settings = BoltSettings(
    neo4j=Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        password=SecretStr(os.environ["NEO4J_PASSWORD"]),
    ),
    embedding="openai/text-embedding-3-small",
)
client = await connect(settings)
```

Hosted-only on NAMS: `get_extraction_status`, `get_observations`,
`get_reflections`, `expand_graph`, and `client.ontology`. On bolt, extraction is
synchronous (so `wait_for_extraction` returns immediately), `add_entity` returns
`(entity, dedup_result)` instead of an entity, and you gain the bolt-only
layers (preferences and facts search, geospatial, consolidation, eval). See
[Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends).

## Going further

- **Step-by-step walkthrough:** [5-Minute NAMS Quickstart](https://neo4j.com/labs/agent-memory/tutorials/nams-quickstart) — this script, explained one call at a time.
- **Configuration:** [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams) — API keys, endpoints, workspaces, tier coverage.
- **Porting existing code:** [Migrate to NAMS](https://neo4j.com/labs/agent-memory/how-to/migrate-to-nams).
- **Next examples:** [`nams-langchain/`](../nams-langchain/) (agent with memory middleware), [`nams-fastapi/`](../nams-fastapi/) (NAMS inside a web service).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`), Python 3.12, with the NAMS transport mocked (`tests/examples/test_nams_quickstart_example.py`) — 2026-09-10. `NamsSettings`/`connect()`, `short_term.get_extraction_status` and `long_term.expand_graph` ship in the 0.6 line; until it is released, install the library from this repository (`uv pip install -e ../..`) rather than from PyPI._
