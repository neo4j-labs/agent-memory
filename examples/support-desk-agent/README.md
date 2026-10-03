# Support-desk agent

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> A chat agent over an ontology-typed memory graph that remembers how it solved earlier requests — and a one-click type rename that re-labels the graph under it.

The full-stack version of [`ontology-lifecycle-bolt/`](../ontology-lifecycle-bolt/). It uses the same support-desk ontology, imported from the same Arrows diagram, stored and activated in your own Neo4j, and the same support transcript, plus seven more conversations. A FastAPI + PydanticAI 2.x agent answers support questions over that graph. A Next.js + Chakra UI v3 app shows what the memory layer does underneath: the entities each message produced and their ontology labels, the graph, the ontology's revisions, and the agent's reasoning memory.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> [!WARNING]
> **Use a database dedicated to this demo.** The seed activates the `support-desk` ontology, and activation is per database: every client that connects to it with `schema_config.use_active_ontology=True` (the default) extracts against `support-desk`. The Ontology panel's rename switches the database to a strict revision. `make reseed` removes everything the seed created and every chat started in the app.

## What it shows

**Ontology-typed memory.** Every message is stored with `short_term.add_message`, and GLiNER2.5 extracts against the active ontology, keyless and in-process. Entities land as `:Entity:Person:Customer`, `:Entity:Event:Order`, `:Entity:Object:Product` and `:Entity:Event:Ticket`. Ingest-time resolution merges repeat mentions across conversations and parks the near-misses (`Priya` ~ `Priya Raman`, `Elena` ~ `Elena Fischer`) in the review band for you to confirm or reject.

**Reasoning memory, both ways.** Each chat turn:

1. stores the user message, then starts a `ReasoningTrace` linked to it (`(:ReasoningTrace)-[:INITIATED_BY]->(:Message)`);
2. records every tool call as a `ReasoningStep` with its `ToolCall`, linked to the message (`TRIGGERED_BY`), with `(:ReasoningStep)-[:TOUCHED]->(:Entity)` audit edges to the customers, orders, products and tickets the tool returned;
3. completes the trace with a `TraceOutcome` (success, summary, tool-call count).

The agent also *reads* its reasoning memory. Before a non-trivial request it calls `recall_similar_tasks`, which runs `reasoning.get_similar_traces()` over earlier traces, so it can follow how a similar request was handled. The seed includes three traces of earlier agent work, marked `seeded`, so recall has something to find on the first chat.

**A live ontology revision.** The Ontology panel renames `Ticket` → `SupportCase` in a strict revision 2. The backend runs `update`, `diff`, a `migrate` dry run, the migration itself and `activate`, then reconnects its memory client, because a Bolt client resolves its ontology when it connects. The tickets already in the graph are relabelled in place, and the agent's ticket tools follow the new label.

## The app

| Area | What it does | API |
|---|---|---|
| Threads sidebar | The eight seeded conversations, marked as seeded, and your chats, each titled after its first message | `GET/POST /api/threads`, `GET /api/threads/{id}` |
| Chat | Streams the answer; tool calls appear as cards with arguments, result and the entities they touched, labelled by the ontology; each turn links to its trace. Reopening a chat shows the same cards, rebuilt from the recorded trace | `POST /api/chat` (SSE) |
| Memory | Entities this conversation mentions, grouped by ontology label; pending review pairs with Confirm / Reject | `GET /api/memory/context`, `POST /api/memory/duplicates/review` |
| Graph | NVL view of the conversation, its messages (`HAS_MESSAGE`, `MENTIONS`), entities, typed `RELATED_TO` edges and pending `SAME_AS` review pairs, or of all seeded data. A d3-force layout tuned per edge kind keeps facts together and lets busy messages fan out; only typed and `SAME_AS` edges are labelled until you select a node. Fitted to the panel (**Fit to view** re-fits); double-click to expand | `GET /api/graph`, `GET /api/graph/neighbors/{id}` |
| Ontology | Active revision and mode, the revision the app's client resolved, revision history, diff, label counts, rename and migrate, activate an older revision | `GET /api/ontology`, `GET /api/ontology/diff`, `POST /api/ontology/rename`, `POST /api/ontology/activate` |
| Reasoning | The conversation's traces as a timeline; a trace's steps, tool calls and touched entities; similar past tasks (`exclude_id` leaves the selected trace out); tool statistics | `GET /api/traces`, `GET /api/traces/{id}`, `GET /api/traces/similar`, `GET /api/tool-stats` |

Both side columns are resizable: drag the edge between a column and the chat, or focus that edge and use the arrow keys (Shift for bigger steps, Home and End for the limits); double-click to reset. The chat never gets narrower than 420 px, each width is remembered in your browser, and the graph re-fits itself to the new size.

### The agent's tools

| Tool | What it reads |
|---|---|
| `recall_similar_tasks` | Earlier reasoning traces similar to the request (reasoning memory) |
| `find_customer` | A customer, their orders and tickets (typed `RELATED_TO` edges) and the conversations that mention them |
| `get_ticket` | A ticket under the active ticket label, with its order, product, customer and the messages that mention it |
| `list_tickets` | Tickets, optionally for one customer |
| `get_order` | An order with its products and tickets |
| `search_support_history` | Messages from other conversations, each with the tickets and orders its conversation names, and matching entities |
| `get_ontology` | The active revision, its mode, labels and relationships |

## Data

`data/support-desk.arrows.json` is the Arrows diagram both lifecycle examples import, byte for byte. `data/conversations.json` holds eight conversations, 34 messages. The first is exactly the lifecycle examples' transcript. The others involve the same people, orders and products, so resolution has repeat mentions to merge, first-name-only mentions to hold back, and tickets referenced from later conversations.

After `make seed` on an empty database you should see:

| Label | Count | Examples |
|---|---|---|
| Customer | 7 | Priya Raman, Marcus Bell, Elena Fischer, Daniel Okafor, Grace Liu, plus the first-name-only `Priya` and `Elena` |
| Order | 8 | SO-4390 … SO-4510 |
| Product | 7 | Aurora Desk Lamp, Halcyon Ergonomic Chair, Breeze Standing Desk, … |
| Ticket | 7 | TK-2210 … TK-2226 |

There are also 2 pending review pairs and 3 seeded traces.

## Prerequisites

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- Docker, for the bundled Neo4j 5.26, or any Neo4j 5.26+ database dedicated to the demo
- `OPENAI_API_KEY` for the chat (or another `AGENT_MODEL`, see below). Extraction and embeddings run locally with no key. The first run downloads GLiNER2.5 (~407 MB) and MiniLM (~90 MB).

## Setup and run

```bash
cd examples/support-desk-agent
cp backend/.env.example backend/.env      # set OPENAI_API_KEY
cp frontend/.env.example frontend/.env.local
make install                              # uv sync + npm ci
make neo4j                                # Neo4j on 7474 / 7687, password test-password
make seed                                 # ontology revision 1 + the eight conversations
```

`make seed` refuses to run when the database already holds the `support-desk` ontology or any `seed-*` conversation, including a half-finished earlier seed, so it never seeds twice. `make reseed` removes both, and every chat started in the app, then seeds again.

Then, in two terminals:

```bash
make run-backend      # http://localhost:8000
make run-frontend     # http://localhost:3000
```

Open http://localhost:3000. Neo4j Browser is at http://localhost:7474 (`neo4j` / `test-password`).

If the repository's own test Neo4j (`make neo4j-start` at the root) already holds ports 7474 / 7687, skip `make neo4j` and point `backend/.env` at a free database instead.

### Configuration

| Variable | Where | Default | Notes |
|---|---|---|---|
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | `backend/.env` | none | Required |
| `NEO4J_DATABASE` | `backend/.env` | `neo4j` | |
| `AGENT_MODEL` | `backend/.env` | `openai:gpt-5-mini` | Any PydanticAI model string. Other providers need their `pydantic-ai-slim` extra |
| `OPENAI_API_KEY` | `backend/.env` | none | For `openai:` models. Without it, each chat turn streams an `error` event and records a failed trace |
| `LOCAL_EMBEDDING_MODEL` | `backend/.env` | `sentence-transformers/all-MiniLM-L6-v2` | |
| `CORS_ORIGINS` | `backend/.env` | `http://localhost:3000` | |
| `NEXT_PUBLIC_API_URL` | `frontend/.env.local` | `http://localhost:8000` | Inlined at build time |

`AGENT_MODEL=test` selects PydanticAI's `TestModel`. It needs no key, calls every tool once with placeholder arguments and answers with a dump of the results. It exercises the whole pipeline (traces, tool calls, `TOUCHED` edges) but it is **not a real agent**, and the UI says so in a banner.

## Things to try

1. Open *Priya Raman: cracked Aurora Desk Lamp*. The Memory panel shows `TK-2210` as a Ticket and the `Priya` ~ `Priya Raman` review pair; confirm it and `Priya` becomes an alias.
2. Start a chat: *"Which open tickets does Grace Liu have?"* `recall_similar_tasks` returns the seeded "Find the open tickets for a customer" trace, then the customer and ticket tools run. Open the turn's trace in the Reasoning panel to see the touched entities. *"Where is order SO-4471?"* recalls "Check the status of an order" instead. Your own turns become recallable too: ask a similar question later and the earlier trace comes back.
3. In the Ontology panel, run **Rename Ticket → SupportCase and migrate**. The diff and the migration result appear. The label counts move from `Ticket` to `SupportCase`, and the app's client reports `strict`. Ask about a ticket again: the tools now read `:SupportCase`.

Activating revision 1 again does not migrate back. The tickets keep `:SupportCase`, and only tickets extracted afterwards are labelled `:Ticket`. To start over, run `make reseed`.

## Known limitations

- **`AGENT_MODEL=test` sends no message history,** because `TestModel` only calls tools on a turn with no earlier model response.
- **Re-activating revision 1 does not migrate back** (see above); `make reseed` starts over.

## Tests

From the repository root:

```bash
make -C examples/support-desk-agent test
# or, with a Neo4j available through testcontainers:
RUN_INTEGRATION_TESTS=1 uv run pytest tests/examples/test_support_desk_agent_example.py -q
```

`tests/examples/test_support_desk_agent_example.py` checks the structure, the pins and the parity with `ontology-lifecycle-bolt`: the Arrows file, the transcript and the type repairs. It builds the agent offline on `TestModel`, and runs an end-to-end test against Neo4j. That test seeds the database, sends a chat turn through the FastAPI app, checks the trace, its tool calls and `TOUCHED` edges, renames and migrates, then removes the seed and restores the previous ontology binding. CI type-checks, lints and builds the frontend in the `frontend-build` job.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

> _Verified against `neo4j-agent-memory` 0.7.0 (in-tree, branch `gliner-2.5`), PydanticAI 2.31, FastAPI, Next.js 16, Chakra UI v3, `gliner2` 2.0.0 with `fastino/gliner2.5-base-v1`, MiniLM and Neo4j 5.26 Community (Docker) on 2026-10-01: `make seed` on an empty database, chat turns through the running backend and frontend with `AGENT_MODEL=test` (message stored with typed entities, trace with seven tool calls and `TOUCHED` edges, recall of the seeded traces), the rename and migration of seven tickets, and the example's test module against a testcontainers Neo4j. The frontend type-checks, lints and builds. On 2026-10-02 the running app was also driven in headless Chrome with `AGENT_MODEL=openai:gpt-5-mini`: real-model chat turns (tool cards with ontology labels, thread titles, recall), a reopened chat's tool cards, every panel, the graph fitted to the panel, dark mode and a 390 px phone layout, with no console errors._
