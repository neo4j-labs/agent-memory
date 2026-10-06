# Support-desk agent

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> A customer-support agent that works real STATE-Bench requests against an ontology-typed memory graph, remembers how earlier requests were resolved, and writes every return, refund, exchange and cancellation back to the graph.

A FastAPI + PydanticAI 2.x agent and a Next.js + Chakra UI v3 app, seeded with 24 customer-support conversations from Microsoft's [STATE-Bench](https://github.com/microsoft/STATE-Bench) benchmark. The store's records (customers, orders, order lines, products, warranties, policies) live in Neo4j as ontology-typed entities. The conversations are stored as short-term memory. Every tool call the benchmark's agent made is stored as reasoning memory. The agent has the benchmark's eleven tools under their own names, and they run the benchmark's own environment against the records in the graph. The app shows what the memory layer does underneath: the entities each message produced, the graph, the ontology's revisions and the agent's reasoning traces.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> [!WARNING]
> **Use a database dedicated to this demo.** The seed activates the `customer-support` ontology, and activation is per database: every client that connects to it with `schema_config.use_active_ontology=True` (the default) extracts against `customer-support`. The Ontology panel's rename switches the database to a strict revision. `make reseed` removes everything the seed created and every chat started in the app.

## What it shows

**Records and conversations in one graph.** The seed stores each task's starting records as entities typed by the ontology: `:Entity:Person:Customer`, `:Entity:Event:Order`, `:Entity:Object:OrderLine`, `:Entity:Object:Product`, `:Entity:Event:Warranty`, linked by `PLACED`, `CONTAINS`, `OF_PRODUCT`, `COVERS` and `REPLACED_BY`. Each entity also carries the environment's record as a JSON `record` property. Then each conversation is stored with `short_term.add_message`. GLiNER2.5 extracts against the active ontology, keyless and in-process, and ingest-time resolution merges each mention onto its record: `ORD-6014` onto the order, `PROD-2092` or "UltraShield Phone Case" onto the product, `cust_005` onto Priya Patel. Near-misses wait in the review band for you to confirm or reject: `Emma` ~ `Emma Chen`, `TechPhone` ~ `TechPhone Pro 16`.

**Reasoning memory, both ways.** Every assistant turn in the trajectories that called tools becomes a seeded `ReasoningTrace`, initiated by the user turn before it. It keeps the benchmark's tool calls, arguments and results, with `(:ReasoningStep)-[:TOUCHED]->(:Entity)` edges to the records each call named. That is 38 traces and 140 tool calls. Before a non-trivial request the live agent calls `recall_similar_tasks` (`reasoning.get_similar_traces()`) and follows how a similar case was handled: which policy it checked, what it previewed, what it confirmed. Each live turn is recorded the same way.

**Actions that change the graph.** `process_return`, `process_refund`, `process_exchange`, `cancel_order` and `process_warranty_claim` run STATE-Bench's environment, copied unchanged, on records loaded from the graph. Whatever they change is written back. A confirmed return updates the order line, an exchange adds the replacement line with its `REPLACED_BY` edge, and a partial return changes the order's status. The environment's own rules apply. A write needs `get_policies` for its topic first and a preview (`confirm=false`) before it confirms; that state is kept per chat session. Each order is evaluated at its own task's date, because return windows and fees depend on it.

**A live ontology revision.** The Ontology panel renames `Warranty` → `WarrantyCoverage` in a strict revision 2. The backend runs `update`, `diff`, a `migrate` dry run, the migration itself and `activate`, then reconnects its memory client, because a Bolt client resolves its ontology when it connects. The warranties already in the graph are relabelled in place.

## The app

| Area | What it does | API |
|---|---|---|
| Threads sidebar | The 24 seeded conversations, titled with the customer and the task, and your chats, each titled after its first message | `GET/POST /api/threads`, `GET /api/threads/{id}` |
| Chat | Streams the answer; tool calls appear as cards with arguments, result and the entities they touched, labelled by the ontology; each turn links to its trace. Reopening a chat shows the same cards, rebuilt from the recorded trace | `POST /api/chat` (SSE) |
| Memory | Entities this conversation mentions, grouped by ontology label; pending review pairs with Confirm / Reject | `GET /api/memory/context`, `POST /api/memory/duplicates/review` |
| Graph | NVL view of the conversation, its messages (`HAS_MESSAGE`, `MENTIONS`), the entities they mention and their record context (one typed edge away: an order's lines, a line's product), typed `RELATED_TO` edges and pending `SAME_AS` review pairs; or of every record and seeded mention. A d3-force layout tuned per edge kind keeps facts together and lets busy messages fan out; only typed and `SAME_AS` edges are labelled until you select a node. Fitted to the panel (**Fit to view** re-fits); double-click to expand | `GET /api/graph`, `GET /api/graph/neighbors/{id}` |
| Ontology | Active revision and mode, the revision the app's client resolved, revision history, diff, label counts, rename and migrate, activate an older revision | `GET /api/ontology`, `GET /api/ontology/diff`, `POST /api/ontology/rename`, `POST /api/ontology/activate` |
| Reasoning | The conversation's traces as a timeline; a trace's steps, tool calls and touched entities; similar past tasks (`exclude_id` leaves the selected trace out); tool statistics | `GET /api/traces`, `GET /api/traces/{id}`, `GET /api/traces/similar`, `GET /api/tool-stats` |

Both side columns are resizable: drag the edge between a column and the chat, or focus that edge and use the arrow keys (Shift for bigger steps, Home and End for the limits); double-click to reset. The chat never gets narrower than 420 px, each width is remembered in your browser, and the graph re-fits itself to the new size.

### The agent's tools

The eleven STATE-Bench tools keep the benchmark's names, descriptions and JSON schemas (`Tool.from_schema`), so live traces and seeded traces read the same way:

| Tool | What it does |
|---|---|
| `get_customer`, `get_order`, `get_product_details`, `search_products`, `get_warranty_status` | Read records (from the graph) |
| `get_policies` | The return, refund, exchange, cancellation, shipping or warranty policy; required before any write |
| `process_return`, `process_refund`, `process_exchange`, `cancel_order`, `process_warranty_claim` | Preview with `confirm=false`, then apply with `confirm=true`; changes are written to the graph |

Four more tools read memory itself:

| Tool | What it reads |
|---|---|
| `recall_similar_tasks` | Earlier reasoning traces for a similar request, with the tool calls that resolved it |
| `find_customer` | A customer by name, email or id, with the `customer_id` that `get_customer` takes, their orders (`PLACED` edges) and the conversations that mention them |
| `search_support_history` | Messages from other conversations, each with the orders and products its conversation names, and matching entities |
| `get_ontology` | The active revision, its mode, labels and relationships |

## Data

`data/state-bench/` holds 24 of the 100 customer-support training trajectories in [microsoft/STATE-Bench](https://github.com/microsoft/STATE-Bench), at commit `5644b1838d96`, copied unchanged under its MIT licence (`data/state-bench/LICENSE`). There are one to three tasks per family: returns, exchanges, shipping claims, warranty claims, cancellations, price matches, compound requests and policy edge cases. Between them they cover all five customers and all eleven tools.

| Folder | What it holds |
|---|---|
| `trajectories/<id>.json` | The conversation, with each assistant turn's tool calls (name, arguments, the environment's result) |
| `tasks/<id>.json` | The customer, the task's date (`now`), its type and a summary |
| `task_envs/<id>.json` | The customers, orders, order items, products and warranties the task starts from |

`backend/src/statebench/vendor/` is the benchmark's customer-support environment, copied unchanged except for its import lines (same commit, same licence). The tests replay all 140 recorded tool calls against it and get the recorded results.

Orders, items and warranties belong to one task, and the five customers are identical in every task, so the 24 starting environments merge into one world: 5 customers, 25 orders, 32 order lines, 3 warranties and 38 product records. The products appear under 14 names, so the seed stores one Product entity per name with every product id as an alias. Six Policy entities hold what `get_policies` returns. The seed does not replay the trajectories, so the records keep their starting state and every task can be worked again in the app.

`data/customer-support.ontology.yaml` declares `Customer`, `Order`, `OrderLine`, `Product` and `Warranty`, with descriptions GLiNER2.5 reads as annotation guidelines. Two choices in it were measured on the seed messages:

- **`OrderLine`, not STATE-Bench's "item".** With the label `Item`, GLiNER2.5 tagged 107 product mentions ("phone case", "SoundMax Basic Headphones") as order lines. With `OrderLine` they extract as products.
- **Policies are records, not an entity type.** Declared as a type, `Policy` caught every "return" and "refund" in the text. The six policies are still entities (`:Entity:Object:Policy`) that `get_policies` calls touch, but nothing is extracted as one.

GLiNER2.5 still types roughly one order-id mention in six as a product or an order line, and resolution never crosses types. So after storing each message the backend repairs it: a mention whose name is a record's id is moved onto that record (`world.repair_id_mentions`).

After `make seed` on an empty database (about 45 seconds once the models are cached) you should see roughly:

| Label | Count | What |
|---|---|---|
| Customer | 9 | the 5 customers, plus first-name and initial mentions held in review (`Emma`, `E. Chen`) |
| Order | 26 | the 25 orders |
| OrderLine | 32 | the 32 order lines |
| Product | about 34 | the 14 catalogue products, plus product mentions such as "Elite headphones" |
| Warranty | 4 | the 3 warranties |
| Policy | 6 | one per `get_policies` topic |

There are also about six pending review pairs, 38 seeded traces and 140 seeded tool calls.

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
make seed                                 # ontology revision 1, the records, 24 conversations and their traces
```

`make seed` refuses to run when the database already holds the `customer-support` ontology, its records or any `seed-*` conversation, including a half-finished earlier seed, so it never seeds twice. `make reseed` removes all of them, and every chat started in the app, then seeds again.

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

1. Open *Priya Patel: Return full order*. The Memory panel shows Priya Patel, `ORD-6014` and the products she named. The Graph shows the order's three lines and their products through `PLACED`, `CONTAINS` and `OF_PRODUCT`. The Reasoning panel shows the benchmark agent's trace: `get_order`, `get_policies(topic="return")`, then a preview and a confirmation per item, each touching the records it read.
2. Start a chat: *"Hi, I'm Priya Patel. I want to return everything from order ORD-6014."* The agent recalls the seeded trace, finds the customer, checks the return policy, previews the three returns and asks you to confirm. Reply *"Yes, go ahead"* and it processes them. The order lines turn `returned` in the graph, and the order becomes `fully_returned`.
3. Try the other suggestions: Marcus Johnson cancelling `ORD-6015` before it ships, or David Kim's coffee maker that broke just after its warranty ended (`ORD-7232`).
4. In the Memory panel, confirm or reject a review pair such as `Emma` ~ `Emma Chen`. A confirmed pair merges, and `Emma` becomes an alias.
5. In the Ontology panel, run **Rename Warranty → WarrantyCoverage and migrate**. The diff and the migration result appear, the label counts move from `Warranty` to `WarrantyCoverage`, and the app's client reports `strict`.

Activating revision 1 again does not migrate back: the warranties keep `:WarrantyCoverage`. To start over, run `make reseed`.

## Known limitations

- **Each order lives at its task's date.** The 24 tasks are set on different days, so a policy window is evaluated at the date of the order's own task, not today.
- **The policy gate and previews are kept in the backend's memory,** per chat session. After a backend restart the agent has to check the policy and preview again before it confirms.
- **`AGENT_MODEL=test` sends no message history,** because `TestModel` only calls tools on a turn with no earlier model response.
- **Re-activating revision 1 does not migrate back** (see above); `make reseed` starts over.

## Tests

From the repository root:

```bash
make -C examples/support-desk-agent test
# or, with a Neo4j available through testcontainers:
RUN_INTEGRATION_TESTS=1 uv run pytest tests/examples/test_support_desk_agent_example.py -q
```

`tests/examples/test_support_desk_agent_example.py` checks the structure and the pins, then the data:
- the licence and the source commit of the copied data and code;
- the 24 tasks covering every family, customer and tool;
- every recorded tool call replaying to its recorded result in the copied environment;
- the ontology.

It builds the agent offline on `TestModel`. An end-to-end test against Neo4j then:
- seeds the database;
- checks a seeded thread's memory and graph;
- sends a chat turn through the FastAPI app;
- runs a return through the graph-backed environment (policy gate, preview, confirmation, graph write);
- renames and migrates, then removes the seed and restores the previous ontology binding.

CI type-checks, lints and builds the frontend in the `frontend-build` job.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

> _Verified against `neo4j-agent-memory` 0.7.0 (in-tree, branch `gliner-2.5`), PydanticAI 2.52, FastAPI, Next.js 16, Chakra UI v3, `gliner2` 2.0.0 with `fastino/gliner2.5-base-v1`, MiniLM and Neo4j 5.26 Community (Docker) on 2026-10-04: `make reseed` on the demo database (24 conversations, 38 seeded traces, 140 tool calls) and the example's test module against a testcontainers Neo4j. With `AGENT_MODEL=openai:gpt-5-mini`, Priya Patel's full return of `ORD-6014` was run through the app in headless Chrome: recall, customer, order and policy lookups, three previews, then three confirmed returns ($51 + $16 + $27 = $94, as in the STATE-Bench trajectory) written back to the graph. The frontend type-checks, lints and builds._
