# Financial Services Advisor — Google Cloud Implementation

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Multi-agent KYC/AML compliance assistant on Google ADK + Gemini + Vertex AI + Neo4j Agent Memory.

An intelligent compliance assistant powered by **Google ADK** (Agent Development Kit) and **Neo4j Agent Memory Context Graphs**, demonstrating multi-agent AI for KYC/AML compliance, fraud detection, and relationship intelligence. The AWS variant lives at [`../aws-financial-services-advisor/`](../aws-financial-services-advisor/); both share the same sample data and Neo4j schema.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Google Cloud Financial Advisor Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Overview

This example application showcases the Google Cloud-Neo4j integration through a production-ready architecture for financial services compliance. It demonstrates how AI agents can leverage graph-based memory for explainable, auditable decision-making.

### Key Features

- **Multi-Agent Investigation**: Coordinated KYC, AML, relationship, and compliance analysis using Google ADK
- **Real-Time Agent Visualization**: SSE streaming shows agent delegation, tool calls, and memory access as they happen — animated with Framer Motion
- **Reasoning Trace Persistence**: All agent reasoning (thoughts, tool calls, results) stored to Neo4j via the reasoning memory layer
- **Context Graph Intelligence**: Relationship mapping and network analysis with Neo4j
- **Explainable AI**: Full audit trails for regulatory compliance (EU AI Act ready)
- **Real-time Monitoring**: Transaction and behavior pattern detection
- **Graph-based RAG**: Reduces hallucinations through grounded, relationship-aware retrieval
- **Entity Deduplication**: customer entities are auto-merged above 0.95 similarity and flagged with `SAME_AS` above 0.85 (`MEMORY_DEDUP_*`)
- **Real Entity Extraction**: conversations are mined for `:Entity` nodes by the same Gemini model the agents use — the startup log names the resolved extractor so a silent no-op is impossible
- **Portable Query Layer**: domain reads go through `client.query.cypher`, which is read-only validated and works on both the bolt and hosted backends

---

## Sample Prompts

The chat interface includes suggested prompt cards. Here are the built-in examples and what they demonstrate:

| Prompt | Agents | What It Demonstrates |
|--------|--------|---------------------|
| **Full Compliance Investigation** — Run a full compliance investigation on CUST-003 Global Holdings Ltd — check KYC documents, scan for structuring patterns, trace the shell company network, and screen against sanctions lists | KYC, AML, Relationship, Compliance | Full multi-agent orchestration with all 4 specialist agents |
| **Detect Structuring Pattern** — I see four cash deposits of $9,500 each from CUST-003 in late January. Analyze whether this is a structuring pattern and identify where the funds went | AML | Pattern detection for transactions just under the $10K reporting threshold |
| **Compare Customer Risk Profiles** — Compare the risk profiles of all three customers and flag which ones need enhanced due diligence | KYC, Compliance | Cross-customer comparison across low/medium/high risk profiles |
| **Trace Beneficial Ownership** — Trace the beneficial ownership chain from Global Holdings Ltd through Shell Corp Cayman and Anonymous Trust Seychelles — who ultimately controls these entities? | Relationship | Network tracing through BVI, Cayman, and Seychelles corporate layers |
| **Investigate Wire Transfers** — Maria Garcia (CUST-002) has rapid wire transfers totaling over $280K. Investigate whether her import/export business justifies this transaction volume | AML, KYC | Transaction velocity analysis on medium-risk customer |
| **Generate SAR Report** — Generate a Suspicious Activity Report for the $250,000 wire from an unknown offshore entity to CUST-003 that was moved to Shell Corp Cayman the next day | Compliance, AML | Triggers the Compliance agent's `generate_sar_report` tool |

## Getting Started Tutorial

This tutorial walks you through setting up the Financial Advisor from scratch, including Google Cloud configuration, Neo4j setup, and running your first compliance investigation.

### Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.11+** - [Download Python](https://www.python.org/downloads/)
- **uv** - Fast Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node.js 18+** - [Download Node.js](https://nodejs.org/)
- **Google Cloud CLI** - [Install gcloud](https://cloud.google.com/sdk/docs/install)
- **Docker Desktop** (optional, only if running Neo4j locally via Docker) - [Download Docker](https://www.docker.com/products/docker-desktop/)

---

### Step 1: Set Up Google Cloud Project

First, create and configure a Google Cloud project with the required APIs.

#### 1.1 Create a New Project (or use an existing one)

```bash
# Create a new project
gcloud projects create my-financial-advisor --name="Financial Advisor"

# Set it as the current project
gcloud config set project my-financial-advisor
```


#### 1.2 Enable Required APIs

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

#### 1.3 Set Up Authentication

For local development, authenticate with Application Default Credentials:

```bash
gcloud auth application-default login
```

This opens a browser for you to authenticate. Once complete, your credentials are stored locally and will be used by the application.


#### 1.4 Verify Vertex AI Access

Test that you can access Vertex AI:

```bash
gcloud ai models list --region=us-central1 --limit=5
```

You should see a list of available models. If you get a permission error, ensure the Vertex AI API is enabled and you have the required roles.

---

### Step 2: Set Up Neo4j

You have two options: **Neo4j Aura** (cloud, recommended) or **Local Neo4j** (Docker).

#### Option A: Neo4j Aura (Recommended for Production)

1. Go to [Neo4j Aura Console](https://console.neo4j.io/)
2. Click **Create Instance** → Select **Free** tier
3. Choose a cloud provider and region (ideally close to your Google Cloud region)
4. Wait for the instance to be created (~2 minutes)
5. **Save the password** shown - you won't see it again!
6. Copy the **Connection URI** (looks like `neo4j+s://xxxxxxxx.databases.neo4j.io`)



#### Option B: Local Neo4j with Docker

For local development and testing:

```bash
# Start Neo4j with Docker (same image and password as docker-compose.yml)
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5.26-community
```

Local connection details:
- **URI**: `bolt://localhost:7687`
- **Username**: `neo4j`
- **Password**: `password`

Or bring up Neo4j, the backend and the frontend together with
`docker compose up -d` (see [`docker-compose.yml`](docker-compose.yml)).

Access Neo4j Browser at http://localhost:7474 to verify it's running.


---

### Step 3: Clone and Configure the Project

#### 3.1 Navigate to the Example

```bash
cd examples/financial-services-advisor/google-cloud-financial-advisor
```

#### 3.2 Create Your Environment File

```bash
cp .env.example .env
```

`.env` belongs in **this directory** (the app root). `backend/.env` is an
optional developer override loaded on top of it.

#### 3.3 Edit `.env` with Your Credentials

Open `.env` in your editor and fill in your values:

```bash
# Gemini credentials — pick ONE path.
# Option A: Google AI Studio (https://aistudio.google.com/apikey)
GOOGLE_API_KEY=your-google-api-key
# Option B: Vertex AI — leave GOOGLE_API_KEY unset, run
# `gcloud auth application-default login`, and set the project:
GOOGLE_CLOUD_PROJECT=my-financial-advisor
# GOOGLE_GENAI_USE_VERTEXAI=true

# Models
VERTEX_AI_LOCATION=us-central1
VERTEX_AI_MODEL_ID=gemini-2.5-flash                # supervisor + 4 specialists + extraction
VERTEX_AI_EMBEDDING_MODEL=gemini-embedding-001     # text-embedding-004 was retired 2026-01-14
VERTEX_AI_EMBEDDING_DIMENSIONS=768                 # truncates the native 3072 dims

# Neo4j — Aura:
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-aura-password

# Neo4j — local (docker-compose / docker run above):
# NEO4J_URI=bolt://localhost:7687
# NEO4J_USER=neo4j
# NEO4J_PASSWORD=password

# Application Settings
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

> **Which credential does what?** The ADK agents and the entity-extraction LLM
> both use the Gemini model above. Embeddings always go through Vertex AI, so
> semantic search needs a GCP project with the Vertex AI API enabled even on
> Option A. Without any Gemini credential the app still starts; the startup log
> then names the extractor as disabled and no `:Entity` nodes are created.

---

### Step 4: Install Dependencies

The project uses **uv** for Python (backend) and **npm** for Node.js (frontend).

#### 4.1 Install Everything with Make

```bash
make install
```

This runs:
- `cd backend && uv sync` - Installs Python dependencies
- `cd frontend && npm install` - Installs Node.js dependencies

#### 4.2 Manual Installation (Alternative)

If you prefer to run commands manually:

```bash
# Backend
cd backend
uv sync
cd ..

# Frontend
cd frontend
npm install
cd ..
```


---

### Step 5: Load Sample Data

Load example customers, organizations, and transactions into Neo4j. The script reads Neo4j credentials from your `.env` file:

```bash
make load-data
```

Or manually:

```bash
cd backend && uv run python ../../data/load_sample_data.py
```

You should see output like:

```
INFO:__main__:Connecting to Neo4j at neo4j+s://...
INFO:__main__:Clearing existing data...
INFO:__main__:Creating constraints...
INFO:__main__:Loading customers...
INFO:__main__:  Created customer: Alice Johnson
INFO:__main__:  Created customer: Maria Garcia
INFO:__main__:  Created customer: Global Holdings Ltd
...
INFO:__main__:Done!
```


#### Verify Data in Neo4j Browser

Open Neo4j Browser and run:

```cypher
MATCH (n) RETURN labels(n)[0] AS type, count(*) AS count
```

You should see counts for Customer, Organization, and Transaction nodes.


---

### Step 6: Start the Application

#### 6.1 Start All Services

```bash
make dev
```

This starts:
- **Backend** (FastAPI) - http://localhost:8000
- **Frontend** (Vite) - http://localhost:5173

> **Note:** Ensure your Neo4j instance is already running (either Aura or local Docker) before starting the application.

#### 6.2 Or Start Services Separately

In separate terminal windows:

```bash
# Terminal 1: Backend
cd backend
uv run uvicorn src.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend
npm run dev
```

#### 6.3 Verify Services Are Running

- **Frontend**: Open http://localhost:5173 — the Financial Advisor dashboard
- **API Docs**: Open http://localhost:8000/docs — interactive API documentation
- **Health Check**: `curl http://localhost:8000/health` → `{"status":"healthy","platform":"Google Cloud","service":"financial-advisor"}`

Expected backend startup log:

```
INFO  src.main - Starting Google Cloud Financial Advisor...
INFO  src.services.memory_service - Financial Memory Service initialized (extractor=LLMEntityExtractor, embedder=VertexAIEmbeddingProvider, dedup=on)
INFO  src.main - Memory service initialized
INFO  src.main - Neo4j domain service initialized
INFO  uvicorn - Application startup complete.
```

`extractor=` is the line to read: if it says `NoOpExtractor`, or a warning says
extraction is disabled, no Gemini credential was found and no `:Entity` nodes
will be written.

Two quick smoke checks that need no LLM call:

```bash
curl -s localhost:8000/api/graph/stats | head -c 200
# {"total_nodes":93,"total_relationships":142,"nodes_by_label":{"Transaction":44,...

curl -s localhost:8000/api/investigations
# []   ← persisted in Neo4j, so this survives a restart
```

#### 6.4 Run the Tests

```bash
cd backend && uv run pytest        # 42 offline tests; no Neo4j, no credentials
```



---

### Step 7: Run Your First Investigation

Now let's use the multi-agent system to investigate a customer.

#### 7.1 Open the Chat Interface

In the application, click on **"AI Assistant"** in the sidebar to open the chat interface.


#### 7.2 Start an Investigation

Type a query like:

```
Investigate customer CUST-003 for potential money laundering risks
```

Press Enter and watch the **real-time agent orchestration panel** as it streams events:

1. **Supervisor Agent** analyzes your request (pulsing active indicator)
2. **KYC Agent** activates — tool calls slide in with arguments and results
3. **AML Agent** scans transaction patterns — memory access indicators flash
4. **Relationship Agent** maps network connections
5. **Compliance Agent** checks sanctions lists
6. **Reasoning trace** is automatically saved to Neo4j

Each agent card animates in from the left as it becomes active, tool calls appear with staggered fade-in animations, and results show success/error transitions.


#### 7.3 Review the Results

The supervisor synthesizes all findings into a comprehensive report. Each assistant message includes an expandable **Agent Activity** section showing the reasoning trace timeline — click to see the full chain of agent reasoning, tool calls with arguments and results, and memory operations.


#### 7.4 Explore the Relationship Network

Click on **"Network Graph"** to visualize the customer's connections:


---

### Step 8: Deploy to Google Cloud Run (Optional)

Ready to deploy to production? Follow these steps.

#### 8.1 Set Up Secrets

Store your Neo4j credentials securely:

```bash
# Create secrets
echo -n "neo4j+s://xxx.databases.neo4j.io" | \
  gcloud secrets create neo4j-uri --data-file=-

echo -n "your-neo4j-password" | \
  gcloud secrets create neo4j-password --data-file=-
```

#### 8.2 Deploy the Backend

```bash
cd backend

gcloud run deploy financial-advisor-backend \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets NEO4J_URI=neo4j-uri:latest,NEO4J_PASSWORD=neo4j-password:latest \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,VERTEX_AI_LOCATION=us-central1
```

#### 8.3 Deploy the Frontend

```bash
cd frontend
npm run build

# Deploy to Cloud Storage + CDN, or Cloud Run
gcloud run deploy financial-advisor-frontend \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```


---

## Troubleshooting

### "Permission denied" when accessing Vertex AI

Ensure you've authenticated and have the right roles:

```bash
gcloud auth application-default login
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:your-email@example.com" \
  --role="roles/aiplatform.user"
```

### Neo4j connection refused

For local Neo4j, ensure Docker is running:

```bash
docker ps | grep neo4j
# If not running:
docker start neo4j
```

For Aura, verify your URI includes `neo4j+s://` (not `bolt://`).

### Frontend can't connect to backend

Check that CORS is configured correctly in `.env`:

```bash
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

### "Module not found" errors

Reinstall dependencies:

```bash
make clean
make install
```

### No `:Entity` nodes appear in the graph

Check the backend startup log:

```
Financial Memory Service initialized (extractor=LLMEntityExtractor, embedder=VertexAIEmbeddingProvider, dedup=on)
```

If it says `extractor=NoOpExtractor` (or warns that extraction is disabled),
no Gemini credential was found — set `GOOGLE_API_KEY`, or
`GOOGLE_GENAI_USE_VERTEXAI=true` together with `GOOGLE_CLOUD_PROJECT`.

### `docker compose up --build` or `make build` fails on `neo4j-agent-memory`

The image installs from `backend/requirements-docker.txt`, not from
`pyproject.toml`, because the manifest points the library at an editable path
outside the build context. After changing dependencies, re-export it:

```bash
make docker-requirements
```

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Google Cloud                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────────┐│
│  │  Cloud CDN   │───▶│    Cloud Run     │───▶│        Vertex AI           ││
│  │  (Frontend)  │    │ (FastAPI + ADK)  │    │  (Gemini + Embeddings)     ││
│  └──────────────┘    └──────────────────┘    └────────────────────────────┘│
│                               │                                              │
│         ┌─────────────────────┼──────────────────────┐                      │
│         ▼                     ▼                      ▼                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │Secret Manager│    │  Neo4j Aura  │    │Cloud Storage │                  │
│  │ (Credentials)│    │(Context Graph)│    │  (Documents) │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Multi-Agent System

```
                     ┌───────────────────┐
                     │ SupervisorAgent   │
                     │ (Coordinator)     │
                     └─────────┬─────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
    ┌──────┴──────┐    ┌──────┴──────┐    ┌──────┴──────┐
    │             │    │             │    │             │
┌───┴───┐    ┌───┴───┐    ┌─────────┴─┐    ┌─────────┐
│  KYC  │    │  AML  │    │Relationship│    │Compliance│
│ Agent │    │ Agent │    │   Agent    │    │  Agent   │
└───────┘    └───────┘    └────────────┘    └──────────┘
```

| Agent | Responsibility | Domain tools |
|-------|----------------|--------------|
| **Supervisor** | Orchestrates the workflow, synthesises findings | — (delegates via `sub_agents`) |
| **KYC Agent** | Identity verification, document checking | `verify_identity`, `check_documents`, `assess_customer_risk`, `check_adverse_media` |
| **AML Agent** | Transaction monitoring, pattern detection | `scan_transactions`, `detect_patterns`, `flag_suspicious_transaction`, `analyze_velocity` |
| **Relationship Agent** | Network analysis using the context graph | `find_connections`, `analyze_network_risk`, `detect_shell_companies`, `map_beneficial_ownership` |
| **Compliance Agent** | Sanctions/PEP screening, report generation | `check_sanctions`, `verify_pep_status`, `generate_sar_report`, `assess_regulatory_requirements` |

All five agents are built by one factory, `agents/_base.py::create_specialist_agent`,
and all five use the model named by `VERTEX_AI_MODEL_ID`.

**Memory wiring.** The `Runner` is constructed with
`memory_service=memory_service.adk_memory_service`, so every agent gets ADK's
built-in `load_memory` tool backed by Neo4j — there is no hand-rolled search
tool per agent. Writes stay domain-specific: each agent has one
`store_*_finding` tool that records a first-class `:Fact` through
`client.long_term.add_fact`.

```python
runner = Runner(
    agent=supervisor,
    app_name="financial_advisor",
    session_service=session_service,
    memory_service=memory_service.adk_memory_service,  # ← Neo4jMemoryService
)
```

### SSE Streaming & Reasoning Traces

The chat backend provides two modes of interaction:

1. **Synchronous** (`POST /api/chat`) — Returns a complete JSON response after all agents finish
2. **Streaming** (`POST /api/chat/stream`) — Streams real-time Server-Sent Events as agents work

The SSE stream emits structured events for each stage of the multi-agent workflow:

```
Client                     Server (SSE stream)
  |                            |
  |-- POST /api/chat/stream -->|
  |<-- agent_start (supervisor)|
  |<-- agent_delegate ---------|  (supervisor → kyc_agent)
  |<-- agent_start (kyc_agent) |
  |<-- tool_call --------------|  (verify_identity)
  |<-- tool_result ------------|  (verified, 320ms)
  |<-- memory_access ----------|  (search context)
  |<-- agent_complete ---------|  (kyc_agent done)
  |<-- agent_delegate ---------|  (supervisor → aml_agent)
  |<-- ...                     |
  |<-- response ---------------|  (final text)
  |<-- trace_saved ------------|  (reasoning trace persisted)
  |<-- done -------------------|  (summary)
```

The reasoning trace is written **as the run proceeds**, not after it, so a
crashed run still leaves a partial trace. What gets persisted:

| Written | Why it matters |
|---|---|
| `ReasoningTrace` with `triggered_by_message_id` | `(:ReasoningTrace)-[:INITIATED_BY]->(:Message)` — ties the reasoning to the question that caused it |
| One `ReasoningStep` per agent activation | The delegation sequence, replayable |
| `ToolCall` with `touched_entities=[EntityRef(...)]` | `(:ReasoningStep)-[:TOUCHED]->(:Entity)` — the audit edge |
| `TraceOutcome(success=, summary=, error_kind=, related_entities=, metrics=)` | Indexable failure categories and per-run metrics |

Which makes the regulator's question a one-hop query:

```cypher
MATCH (e:Entity {name: 'CUST-003'})<-[:TOUCHED]-(s:ReasoningStep)
      <-[:HAS_STEP]-(t:ReasoningTrace)
OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
RETURN t.task, s.step_number, s.thought, collect(tc.tool_name) AS tools
ORDER BY s.step_number
```

That query is exposed as `GET /api/graph/audit-trail/{entity_name}`; full traces
are available at `GET /api/traces/{session_id}` and
`GET /api/investigations/{id}/audit-trail`.

---

## Project Structure

```
financial-services-advisor/
├── data/                  # Sample JSON + load_sample_data.py — SHARED with the
│                          # AWS sibling example, one level above this app
└── google-cloud-financial-advisor/
    ├── backend/
    │   ├── src/
    │   │   ├── agents/
    │   │   │   ├── _base.py         # create_specialist_agent + bind_tool
    │   │   │   ├── supervisor.py    # Orchestrator: sub_agents + load_memory
    │   │   │   ├── kyc_agent.py     # KYC specialist
    │   │   │   ├── aml_agent.py     # AML specialist
    │   │   │   ├── relationship_agent.py
    │   │   │   ├── compliance_agent.py
    │   │   │   └── prompts.py       # Agent instructions
    │   │   ├── tools/               # Domain tools (KYC, AML, relationship, compliance)
    │   │   ├── api/routes/
    │   │   │   ├── chat.py          # POST /chat (JSON) + /chat/stream (SSE)
    │   │   │   ├── traces.py        # GET /traces/{session_id}, /traces/detail/{id}
    │   │   │   ├── graph.py         # Cypher, neighbours, stats, memory, audit-trail
    │   │   │   ├── investigations.py# Neo4j-backed investigations + audit trail
    │   │   │   ├── customers.py
    │   │   │   └── alerts.py
    │   │   ├── models/              # Pydantic request/response models
    │   │   └── services/
    │   │       ├── memory_service.py  # MemoryClient: embeddings, extractor, dedup
    │   │       ├── neo4j_service.py   # All domain Cypher (reads via query.cypher)
    │   │       ├── adk_events.py      # The only place that knows the ADK Event shape
    │   │       └── trace_writer.py    # Audit-grade reasoning traces
    │   ├── tests/                   # Offline FastAPI TestClient + unit tests
    │   ├── Dockerfile
    │   ├── requirements-docker.txt  # Locked deps exported without [tool.uv.sources]
    │   └── pyproject.toml
    ├── frontend/
    │   ├── src/
    │   │   ├── hooks/useAgentStream.ts          # SSE connection + agent state
    │   │   ├── components/
    │   │   │   ├── Chat/                        # ChatInterface, AgentOrchestrationView,
    │   │   │   │                                # AgentActivityTimeline, ToolCallCard,
    │   │   │   │                                # MemoryAccessIndicator
    │   │   │   ├── Dashboard/                   # Sidebar, CustomerDashboard, AlertsPanel
    │   │   │   ├── Investigation/               # InvestigationPanel, AgentWorkflow
    │   │   │   └── Graph/MemoryGraphView.tsx    # Routed graph view
    │   │   └── lib/api.ts                      # API client + SSE parsing
    │   └── package.json
    ├── infrastructure/    # cloudbuild.yaml + Cloud Run deploy scripts
    ├── Makefile
    └── docker-compose.yml
```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send message to AI advisor (synchronous response) |
| `/api/chat/stream` | POST | Send message with SSE streaming (real-time agent events) |
| `/api/chat/history/{session_id}` | GET | Get conversation history |
| `/api/chat/search` | POST | Search the context graph |
| `/api/traces/{session_id}` | GET | Get reasoning traces for a session |
| `/api/traces/detail/{trace_id}` | GET | Get a single reasoning trace with full details |
| `/api/customers` | GET | List customers |
| `/api/customers/{id}` | GET | Get customer details |
| `/api/customers/{id}/risk` | GET | Risk assessment |
| `/api/customers/{id}/network` | GET | Relationship network |
| `/api/investigations` | POST | Create investigation |
| `/api/investigations/{id}/start` | POST | Start multi-agent investigation |
| `/api/investigations/{id}/audit-trail` | GET | Get investigation audit trail |
| `/api/alerts` | GET | List compliance alerts |
| `/api/alerts/{id}` | GET/PATCH | Get or update an alert |
| `/api/alerts/summary` | GET | Alert statistics summary |
| `/api/investigations` | GET | List persisted investigations |
| `/api/investigations/{id}` | GET | Get one investigation |
| `/api/graph/stats` | GET | Node and relationship counts by label/type |
| `/api/graph/query` | POST | Read-only Cypher (validated by `client.query.cypher`) |
| `/api/graph/neighbors/{id}` | GET | Neighbourhood of a node, shaped for visualisation |
| `/api/graph/memory` | GET | Domain + memory subgraph, optionally scoped to `session_id` |
| `/api/graph/audit-trail/{entity_name}` | GET | Reasoning steps that `TOUCHED` an entity |

Full API documentation available at http://localhost:8000/docs when running locally.

---

## Environment Variables Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `NEO4J_URI` | Neo4j connection URI | Yes |
| `NEO4J_USER` | Neo4j username | Yes (default: `neo4j`) |
| `NEO4J_PASSWORD` | Neo4j password | **Yes** — the only hard requirement |
| `NEO4J_DATABASE` | Neo4j database name | No (default: `neo4j`) |
| `GOOGLE_API_KEY` | Gemini key for the Google AI Studio path ([get one](https://aistudio.google.com/apikey)) | One of this or the Vertex path |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID. Required for Vertex AI embeddings either way | For embeddings |
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `true` to force the Vertex AI path. Exported automatically when `GOOGLE_API_KEY` is absent and a project is set | No |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a service-account key / ADC file for Vertex AI | For Vertex AI |
| `VERTEX_AI_LOCATION` | Vertex AI region | No (default: `us-central1`) |
| `VERTEX_AI_MODEL_ID` | Gemini model for all five agents **and** entity extraction | No (default: `gemini-2.5-flash`) |
| `VERTEX_AI_EMBEDDING_MODEL` | Vertex AI embedding model | No (default: `gemini-embedding-001`) |
| `VERTEX_AI_EMBEDDING_DIMENSIONS` | Output dimensionality (fixes the Neo4j vector index size) | No (default: `768`) |
| `MEMORY_ENABLE_EXTRACTION` | Extract entities from conversations | No (default: `true`) |
| `MEMORY_ENABLE_DEDUPLICATION` | Deduplicate entities on ingest | No (default: `true`) |
| `MEMORY_DEDUP_AUTO_MERGE_THRESHOLD` | Similarity at or above which entities auto-merge | No (default: `0.95`) |
| `MEMORY_DEDUP_FLAG_THRESHOLD` | Similarity at or above which a `SAME_AS` review edge is written | No (default: `0.85`) |
| `LOG_LEVEL` | Logging level | No (default: `INFO`) |
| `CORS_ORIGINS` | Comma-separated allowed CORS origins | No |

---

## References

- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory)
- [Vertex AI Documentation](https://cloud.google.com/vertex-ai/docs)
- [Neo4j Aura](https://neo4j.com/cloud/aura/)

## License

This example is part of the neo4j-agent-memory project and is licensed under the Apache 2.0 License.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (PyPI floor `>=0.5.0,<0.7`), google-adk 2.9.0, google-genai 2.23.0, google-cloud-aiplatform 2.1.0, FastAPI 0.141.1, neo4j 6.3.0 on Python 3.12 — 2026-09-10._
_Checked: `uv sync`, `uv run ruff check src/ tests/`, `uv run pytest` (42 offline tests), `docker build ./backend`. A full end-to-end investigation additionally needs Gemini credentials and a GCP project with the Vertex AI API enabled._
