# Financial Services Advisor

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Multi-agent AML/KYC compliance investigations powered by Neo4j Agent Memory Context Graphs — implemented twice, once on AWS Strands + Bedrock, once on Google ADK + Gemini.

A supervisor agent orchestrates four specialist agents (KYC, AML, Relationship, Compliance) to investigate customers, detect money-laundering patterns, trace shell-company networks, and screen sanctions lists — all backed by real Neo4j graph queries.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

![agent chat](img/beneficial-ownership.png)

This example is implemented twice using different cloud AI platforms, demonstrating how the same graph-powered agent architecture works across providers:

| | [AWS Implementation](aws-financial-services-advisor/) | [Google Cloud Implementation](google-cloud-financial-advisor/) |
|-|--------------------------------------------------------|---------------------------------------------------------------|
| **Agent Framework** | [AWS Strands Agents](https://strandsagents.com/) | [Google ADK](https://google.github.io/adk-docs/) |
| **LLM** | Amazon Bedrock (Claude Sonnet, via a cross-region inference profile) | Google Gemini 2.5 Flash |
| **Embeddings** | Amazon Titan Embed V2 (1024-d) | Vertex AI (768-d) |
| **Deployment** | AWS Lambda + API Gateway + CloudFront (CDK) | Google Cloud Run |

Both implementations share the same sample data, Neo4j schema, tool implementations, and frontend design. Choose whichever matches your cloud platform.

---

> **Need a different LLM or embedding model?** As of `neo4j-agent-memory` v0.3 you can swap providers via a single string — `MemorySettings(llm="anthropic/claude-sonnet-4-6", embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html).

## What It Does

Ask a question like *"Investigate customer CUST-003 for potential money laundering"* and the system:

1. **Supervisor** analyzes the request and delegates to specialist agents
2. **KYC Agent** verifies identity, checks documents, assesses customer risk factors
3. **AML Agent** scans transactions, detects structuring patterns (4x $9,500 cash deposits just under the $10K reporting threshold), identifies rapid fund movement and offshore layering
4. **Relationship Agent** maps the entity network via Neo4j graph traversal, detects shell companies (Cayman, Seychelles, BVI), traces beneficial ownership chains
5. **Compliance Agent** screens against sanctions lists, checks PEP status, generates SAR report drafts
6. **Supervisor** synthesizes all findings into a comprehensive risk assessment with recommendations

![agent chat](img/aml-wire-transfer.png)

All 16 tool results come from real Cypher queries against Neo4j — there is no simulated data in either app.

---

## Architecture

| AWS (Strands + Bedrock) | Google Cloud (ADK + Gemini) |
|:-:|:-:|
| ![AWS Architecture](aws-financial-services-advisor/img/architecture.png) | ![GCP Architecture](google-cloud-financial-advisor/img/architecture.png) |

### Multi-Agent System

The supervisor orchestrates 4 specialist agents, each with Neo4j-backed tools:

| Agent | Tools | What It Queries |
|-------|-------|-----------------|
| **KYC** | `verify_identity`, `check_documents`, `assess_customer_risk`, `check_adverse_media` | Customer nodes, Document nodes, risk factors |
| **AML** | `scan_transactions`, `detect_patterns`, `flag_suspicious_transaction`, `analyze_velocity` | Transaction nodes -- detects structuring, rapid movement, layering |
| **Relationship** | `find_connections`, `analyze_network_risk`, `detect_shell_companies`, `map_beneficial_ownership` | Graph traversal across Organizations, ownership chains |
| **Compliance** | `check_sanctions`, `verify_pep_status`, `generate_sar_report`, `assess_regulatory_requirements` | SanctionedEntity, PEP nodes, regulatory frameworks |

### Three Memory Types

| Memory | What gets written | API |
|--------|------------------|-----|
| **Short-Term** | One `:Conversation` per session, two `:Message` nodes per turn, entity extraction on the user turn | `MemoryClient.short_term` |
| **Long-Term** | Sanctions/PEP screening outcomes as `(:Fact {predicate: 'SCREENED_AGAINST'})`, analyst preferences, and — after the optional adoption pass — the compliance nodes themselves as `:Entity` | `MemoryClient.long_term`, `MemoryClient.schema.adopt_existing_graph` |
| **Reasoning** | A trace per turn linked to its triggering message, a step and `:ToolCall` per tool use, `(:ReasoningStep)-[:TOUCHED]->(:Entity)` audit edges, a structured `TraceOutcome` | `MemoryClient.reasoning` |

### Neo4j Graph Schema

The sample data creates this graph structure:

```
(:Customer:IndividualCustomer)-[:HAS_DOCUMENT]->(:Document)
(:Customer:CorporateCustomer)-[:HAS_DOCUMENT]->(:Document)
(:Customer)-[:HAS_TRANSACTION]->(:Transaction)
(:Customer)-[:HAS_ALERT]->(:Alert)
(:Customer)-[:HAS_INVESTIGATION]->(:Investigation)
(:Customer)-[:OWNS|CONTROLS|DIRECTED_BY|EMPLOYED_BY]->(:Organization)
(:Organization)-[:CONNECTED_TO|LINKED_TO|TRADES_WITH]->(:Organization)
(:Alert)-[:RELATED_TO_TRANSACTION]->(:Transaction)
(:SanctionedEntity)<-[:ALIAS_OF]-(:SanctionAlias)
(:PEPRelative)-[:RELATIVE_OF]->(:PEP)
(:Report)-[:ABOUT]->(:Customer)
(:Investigation)-[:HAS_TRACE]->(:ReasoningTrace)
```

Two conventions in there are deliberate, and they are the interesting part:

- **Every node the loader writes also carries `:Compliance`.** The library derives Neo4j labels from POLE+O types, so an `ORGANIZATION` extracted from a chat turn becomes `:Entity:Organization` — the same label the compliance graph uses. The marker keeps the two namespaces apart, and the domain queries are scoped to it.
- **Customers are split by secondary label, not just a property.** `:IndividualCustomer` and `:CorporateCustomer` give `adopt_existing_graph` one entity type per label (`PERSON` / `ORGANIZATION`), and neither collides with a library-derived label.

`Transaction.date`, `Document.expiry_date` and `Document.submission_date` are real Neo4j `DATE` values, generated relative to the load date — which is what makes the agents' 90-day AML windows return anything.

---

## Alerts

![automated alerts](img/alerts.png)

## Investigations

Creating a new investigation manually:

![agent chat](img/create-investigation.png)

Example:

![agent chat](img/investigations.png)

## Sample Data

Both implementations share the same data in the [`data/`](data/) directory:

- **3 customers**: John Smith (low-risk individual), Maria Garcia (medium-risk import/export), Global Holdings Ltd (high-risk BVI corporate with shell company connections)
- **16 transactions**: Normal salary deposits, rapid wire movement patterns, 4x $9,500 cash deposits (structuring just under the $10K CTR threshold), offshore wire transfers
- **6 organizations**: Including Shell Corp - Cayman, Anonymous Trust - Seychelles, and Nominee Director Services Ltd with shell company indicators
- **3 sanctions entries**: OFAC SDN and EU Consolidated list entries with aliases
- **3 PEP entries**: Minister of Finance (Tier 1), Deputy PM (Tier 1), State Senator (Tier 2) with relatives
- **3 pre-built alerts**: Structuring (CRITICAL), shell company network (HIGH), rapid movement (MEDIUM)

Transaction and document dates are stored as `days_ago` / `expiry_days` offsets rather than absolute dates, so a fixture shipped today still falls inside the agents' time windows a year from now.

The loader is safe to re-run: every write is a `MERGE`, so a second run changes nothing, and it never deletes anything it did not create. `--reset` exists for a clean slate and scopes its delete to the demo labels — your `:Conversation`, `:Message`, `:Entity` and `:ReasoningTrace` nodes survive it.

```bash
uv run python data/load_sample_data.py              # idempotent load
uv run python data/load_sample_data.py --reset      # clean slate, demo labels only
uv run python data/load_sample_data.py --adopt-only # adopt the graph as memory entities
```

### Turning the domain graph into long-term memory

`--adopt` runs [`client.schema.adopt_existing_graph()`](https://neo4j.com/labs/agent-memory/how-to/adopt-existing-graph.html), which attaches the library's `:Entity` super-label and `id` / `type` / `name` properties to the compliance nodes. After it, extraction on a chat turn links mentions to the *existing* customers and organizations instead of MERGEing duplicates beside them.

`:Transaction` and `:Document` are deliberately excluded. Adoption sets `n.type` to the library entity type, and in this graph `type` already means `'cash_deposit'` / `'passport'` — which the AML and KYC tools match on. That is the general rule for adopting a graph you did not design for the library: rename any domain property called `id`, `type` or `name` first.

---

## How the Two Implementations Differ

Both apps produce the same investigation results. The differences are in the agent framework and streaming model.

### Agent Delegation

**AWS Strands** uses explicit `@tool` delegation functions -- the supervisor has tools like `delegate_to_kyc_agent()` that create and invoke sub-agents:

```python
@tool
def delegate_to_kyc_agent(customer_id: str, task: str) -> dict:
    kyc_agent = _create_sub_agent("kyc", KYC_PROMPT, kyc_tools)
    result = kyc_agent(prompt)
    return {"agent": "kyc", "findings": str(result)}

supervisor = Agent(model=BedrockModel(...), tools=[delegate_to_kyc_agent, ...])
```

**Google ADK** uses native sub-agent delegation -- the framework handles routing automatically:

```python
kyc_agent = LlmAgent(name="kyc_agent", model=model, tools=[...])
supervisor = LlmAgent(name="supervisor", sub_agents=[kyc_agent, aml_agent, ...])

async for event in Runner.run_async(user_id, session_id, message):
    # Real-time events as each sub-agent executes
```

### SSE Streaming

This is the primary UX difference:

| | AWS | Google Cloud |
|-|-----|-------------|
| **During investigation** | Live events: `thinking` text deltas, plus `tool_call` / `tool_result` per tool use | Live animated cards showing each agent activating, calling tools, accessing memory |
| **Event types** | 9 (agent_start, thinking, tool_call, tool_result, agent_complete, response, trace_saved, done, error) | 11 (+ delegate, memory_access) |
| **How** | `Agent.stream_async()`, mapping `current_tool_use` and `toolResult` events | `Runner.run_async()` is an async generator |

Both frontends use the same Framer Motion components (`AgentOrchestrationView`, `ToolCallCard`, `MemoryAccessIndicator`). The GCP version still shows per-sub-agent delegation, which the Strands topology surfaces as tool calls on the supervisor instead.

### Reasoning Traces

Both record reasoning traces to Neo4j. The AWS app records them *while* the run happens — a trace opened with `triggered_by_message_id`, a step and `:ToolCall` per tool use, `:TOUCHED` edges to the entities acted on, and a structured `TraceOutcome` at the end — which is what makes its `/api/investigations/{id}/audit-trail` a single traversal.

### Largely, But Not Exactly, the Same

Both apps share the same shape: a `Neo4jDomainService` of focused Cypher methods, 16 tool functions, a `bind_tool()` pattern that injects collaborators while hiding them from the LLM, a FastAPI backend, and a React + Chakra UI v3 frontend.

They have drifted in the details, though, and it is worth knowing before you treat one as a copy of the other: the two `neo4j_service.py` files differ by a few hundred lines, and the four `tools/*.py` pairs differ by 3-85 lines each. Nothing enforces that the shared halves stay shared. The AWS app additionally exposes `/api/reports/*` and persists SAR and risk-assessment reports in the graph.

---

## Getting Started

### Hosted vs self-hosted

The honest answer is "half and half". The three **memory** layers work unchanged against the hosted [Neo4j Agent Memory Service](https://memory.neo4jlabs.com) — point `MemorySettings` at it with a `MEMORY_API_KEY` and the conversations, entities, facts and reasoning traces are stored there. The **compliance domain graph** is not memory: it is your data, in your schema, and the loader writes it over bolt to a database you own. `client.schema.adopt_existing_graph()` is bolt-only for the same reason — the schema is server-managed on the hosted service.

So: run both apps against a Neo4j instance you control (Aura Free is enough), which is what the steps below do. See [Backends: self-hosted vs hosted](https://neo4j.com/labs/agent-memory/explanation/backends.html) for the full picture.

One constraint if you run both apps: they use different embedders (Titan is 1024-d, Vertex is 768-d) and the memory vector indexes are sized from whichever connects first. Give each app its own database, or `MemoryClient.connect()` will raise `EmbeddingDimensionMismatchError` — deliberately, rather than corrupting the indexes.

### AWS (Bedrock + Strands)

```bash
cd aws-financial-services-advisor
cp .env.example backend/.env    # Configure Neo4j + AWS credentials
make install                    # Install Python + Node dependencies
make load-data                  # Load sample data into Neo4j (idempotent)
make adopt-graph                # Optional: adopt the graph as memory entities
make run                        # Start backend (8000) + frontend (5173)
```

Full tutorial: [aws-financial-services-advisor/GETTING_STARTED.md](aws-financial-services-advisor/GETTING_STARTED.md)

### Google Cloud (Gemini + ADK)

```bash
cd google-cloud-financial-advisor
cp .env.example backend/.env    # Configure Neo4j + Google API key
make install                    # Install Python + Node dependencies
make load-data                  # Load sample data into Neo4j
make dev                        # Start backend (8000) + frontend (5173)
```

Full tutorial: [google-cloud-financial-advisor/GETTING_STARTED.md](google-cloud-financial-advisor/GETTING_STARTED.md)

---

## Project Structure

```
financial-services-advisor/
├── data/                                  # Shared sample data
│   ├── customers.json                     # 3 customers (low/medium/high risk)
│   ├── organizations.json                 # 6 organizations (incl. shell companies)
│   ├── transactions.json                  # 16 transactions (incl. AML patterns)
│   ├── sanctions.json                     # 3 sanctioned entities with aliases
│   ├── pep.json                           # 3 PEPs + 1 relative
│   ├── alerts.json                        # 3 compliance alerts
│   └── load_sample_data.py                # Idempotent async loader (+ --adopt phase)
│
├── img/                                   # Screenshots used by this README
│
├── aws-financial-services-advisor/        # AWS implementation
│   ├── backend/
│   │   ├── src/
│   │   │   ├── agents/                    # Strands agents with @tool delegation
│   │   │   ├── tools/                     # 16 Neo4j-backed tool functions
│   │   │   ├── services/                  # memory_service, neo4j_service, risk_service
│   │   │   └── api/routes/                # FastAPI endpoints
│   │   └── tests/                         # 151 tests (run with `make test`)
│   ├── frontend/                          # React + Chakra + Framer Motion
│   ├── infrastructure/                    # Six AWS CDK stacks (TypeScript)
│   ├── docs/diagrams/                     # Editable Excalidraw sources
│   ├── img/                               # Architecture diagram
│   ├── GETTING_STARTED.md
│   └── Makefile
│
├── google-cloud-financial-advisor/        # Google Cloud implementation
│   ├── backend/
│   │   ├── src/
│   │   │   ├── agents/                    # ADK agents with native sub_agents
│   │   │   ├── tools/                     # 16 Neo4j-backed tool functions
│   │   │   ├── services/                  # memory_service, neo4j_service
│   │   │   └── api/routes/                # FastAPI endpoints (incl. SSE streaming)
│   ├── frontend/                          # React + Chakra + Framer Motion
│   ├── infrastructure/                    # Cloud Build config + deploy scripts
│   ├── img/                               # Architecture diagram
│   ├── GETTING_STARTED.md
│   └── Makefile
│
└── README.md                              # This file
```

---

## API Endpoints

Both implementations expose this core REST API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Chat with supervisor (non-streaming) |
| `/api/chat/stream` | POST | Chat with SSE streaming |
| `/api/customers` | GET | List customers from Neo4j |
| `/api/customers/{id}/risk` | GET | Risk assessment with contributing factors |
| `/api/customers/{id}/network` | GET | Relationship network graph |
| `/api/alerts` | GET/POST | Alert management |
| `/api/alerts/summary` | GET | Alert statistics by severity/status |
| `/api/investigations` | GET/POST | Investigation management |
| `/api/investigations/{id}/audit-trail` | GET | Reasoning trace plus the entities it touched |
| `/api/traces/{session_id}` | GET | Reasoning traces for a chat session |
| `/api/graph/stats` | GET | Neo4j node and relationship counts |
| `/api/graph/neighbors/{id}` | GET | Entity neighborhood subgraph |
| `/api/graph/query` | POST | Read-only Cypher query execution |
| `/health` | GET | Health check |

**AWS additionally exposes `/api/reports/*`** — SAR and risk-assessment reports, persisted in Neo4j as `(:Report)-[:ABOUT]->(:Customer)`. Each app's own `/docs` is the authoritative list.

---

## References

- [Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory) -- The memory library powering both examples
- [AWS Strands Agents](https://strandsagents.com/) -- Agent framework for the AWS implementation
- [Google ADK](https://google.github.io/adk-docs/) -- Agent Development Kit for the Google Cloud implementation
- [Neo4j Aura](https://neo4j.io/aura) -- Free hosted Neo4j for getting started

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0 on 2026-09-10 — the shared loader runs idempotently against Neo4j 5.26 and the AWS implementation's 151 tests pass (124 unit, 27 integration). A full end-to-end chat run needs AWS or GCP credentials._
