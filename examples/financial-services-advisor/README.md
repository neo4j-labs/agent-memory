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
| **LLM** | Amazon Bedrock (Claude Sonnet 4) | Google Gemini 2.5 Flash |
| **Embeddings** | Amazon Titan Embed V2 | Vertex AI text-embedding-004 |
| **Deployment** | AWS Lambda + CDK | Google Cloud Run |

Both implementations share the same sample data, Neo4j schema, tool implementations, and frontend design. Choose whichever matches your cloud platform.

---

## What It Does

Ask a question like *"Investigate customer CUST-003 for potential money laundering"* and the system:

1. **Supervisor** analyzes the request and delegates to specialist agents
2. **KYC Agent** verifies identity, checks documents, assesses customer risk factors
3. **AML Agent** scans transactions, detects structuring patterns (4x $9,500 cash deposits just under the $10K reporting threshold), identifies rapid fund movement and offshore layering
4. **Relationship Agent** maps the entity network via Neo4j graph traversal, detects shell companies (Cayman, Seychelles, BVI), traces beneficial ownership chains
5. **Compliance Agent** screens against sanctions lists, checks PEP status, generates SAR report drafts
6. **Supervisor** synthesizes all findings into a comprehensive risk assessment with recommendations

![agent chat](img/aml-wire-transfer.png)

All tool results come from real Cypher queries against Neo4j -- not simulated data.

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

| Memory | Purpose | Stored In |
|--------|---------|-----------|
| **Short-Term** | Conversation history per session | Neo4j via `MemoryClient.short_term` |
| **Long-Term** | Customer entities and relationships | Neo4j via `MemoryClient.long_term` |
| **Reasoning** | Investigation audit trails with agent steps and tool calls | Neo4j via `MemoryClient.reasoning` |

### Neo4j Graph Schema

The sample data creates this graph structure:

```
(:Customer)-[:HAS_DOCUMENT]->(:Document)
(:Customer)-[:HAS_TRANSACTION]->(:Transaction)
(:Customer)-[:HAS_ALERT]->(:Alert)
(:Customer)-[:OWNS]->(:Organization)
(:Customer)-[:CONTROLS]->(:Organization)
(:Customer)-[:EMPLOYED_BY]->(:Organization)
(:Organization)-[:CONNECTED_TO]->(:Organization)
(:Organization)-[:LINKED_TO]->(:Organization)
(:Alert)-[:RELATED_TO_TRANSACTION]->(:Transaction)
(:SanctionedEntity)<-[:ALIAS_OF]-(:SanctionAlias)
(:PEPRelative)-[:RELATIVE_OF]->(:PEP)
```

---

## Alerts

![automated alerts](img/alerts.png)

## Investigations

Creating a new investigation manually:

![agent chat](img/create-investigation.png)

Example:

![agent chat](img/investigations.png)

## Alerts

## Sample Data

Both implementations share the same data in the [`data/`](data/) directory:

- **3 customers**: John Smith (low-risk individual), Maria Garcia (medium-risk import/export), Global Holdings Ltd (high-risk BVI corporate with shell company connections)
- **16 transactions**: Normal salary deposits, rapid wire movement patterns, 4x $9,500 cash deposits (structuring just under the $10K CTR threshold), offshore wire transfers
- **6 organizations**: Including Shell Corp - Cayman, Anonymous Trust - Seychelles, and Nominee Director Services Ltd with shell company indicators
- **3 sanctions entries**: OFAC SDN and EU Consolidated list entries with aliases
- **3 PEP entries**: Minister of Finance (Tier 1), Deputy PM (Tier 1), State Senator (Tier 2) with relatives
- **3 pre-built alerts**: Structuring (CRITICAL), shell company network (HIGH), rapid movement (MEDIUM)

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
| **During investigation** | Loading spinner (supervisor blocks until done) | Live animated cards showing each agent activating, calling tools, accessing memory |
| **Event types** | 5 (start, complete, response, trace, done) | 11 (+ delegate, tool_call, tool_result, memory_access, thinking) |
| **Why** | Strands `agent(prompt)` is synchronous | ADK `Runner.run_async()` is an async generator |

Both frontends use the same Framer Motion components (`AgentOrchestrationView`, `ToolCallCard`, `MemoryAccessIndicator`), but the GCP version shows richer real-time activity.

### Reasoning Traces

Both record reasoning traces to Neo4j after each chat. The GCP version captures more detail (1 step per sub-agent with nested tool calls) because ADK exposes per-agent events during execution.

### Everything Else Is the Same

- Same `Neo4jDomainService` with ~30 Cypher query methods
- Same 16 tool functions (KYC, AML, Relationship, Compliance)
- Same `bind_tool()` pattern to inject `neo4j_service` while hiding it from the LLM
- Same FastAPI backend structure and API endpoints
- Same React + Chakra UI v3 frontend
- Same `MemoryClient` integration for all three memory types

---

## Getting Started

### AWS (Bedrock + Strands)

```bash
cd aws-financial-services-advisor
cp .env.example backend/.env    # Configure Neo4j + AWS credentials
make install                    # Install Python + Node dependencies
make load-data                  # Load sample data into Neo4j
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
│   └── load_sample_data.py                # Async Neo4j data loader
│
├── aws-financial-services-advisor/        # AWS implementation
│   ├── backend/
│   │   ├── src/
│   │   │   ├── agents/                    # Strands agents with @tool delegation
│   │   │   ├── tools/                     # 16 Neo4j-backed tool functions
│   │   │   ├── services/                  # memory_service, neo4j_service, risk_service
│   │   │   └── api/routes/                # FastAPI endpoints
│   │   └── tests/                         # 113 unit tests
│   ├── frontend/                          # React + Chakra + Framer Motion
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
│   ├── GETTING_STARTED.md
│   └── Makefile
│
└── README.md                              # This file
```

---

## API Endpoints

Both implementations expose the same REST API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Chat with supervisor (synchronous) |
| `/api/chat/stream` | POST | Chat with SSE streaming |
| `/api/customers` | GET | List customers from Neo4j |
| `/api/customers/{id}/risk` | GET | Risk assessment with contributing factors |
| `/api/customers/{id}/network` | GET | Relationship network graph |
| `/api/alerts` | GET/POST | Alert management |
| `/api/alerts/summary` | GET | Alert statistics by severity/status |
| `/api/traces/{session_id}` | GET | Reasoning traces for audit trail |
| `/api/graph/stats` | GET | Neo4j node and relationship counts |
| `/api/graph/neighbors/{id}` | GET | Entity neighborhood subgraph |
| `/api/graph/query` | POST | Read-only Cypher query execution |
| `/health` | GET | Health check |

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

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03 (structure, syntax, import tests, and 105 unit tests pass for the GCP implementation; full end-to-end run requires AWS or GCP credentials)._
