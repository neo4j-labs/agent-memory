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
- **Entity Deduplication**: `DeduplicationConfig` available for preventing duplicate customer entities
- **Explicit Extraction Config**: `ExtractionConfig` for configuring entity extraction pipelines

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

<!-- ![Google Cloud Console - Create Project](docs/screenshots/gcp-create-project.png) -->

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

<!-- ![gcloud auth login browser](docs/screenshots/gcp-auth-browser.png) -->

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

<!-- ![Neo4j Aura Console - Create Instance](docs/screenshots/neo4j-aura-create.png) -->

<!-- ![Neo4j Aura Console - Connection Details](docs/screenshots/neo4j-aura-connection.png) -->

#### Option B: Local Neo4j with Docker

For local development and testing:

```bash
# Start Neo4j with Docker
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5-community
```

Local connection details:
- **URI**: `bolt://localhost:7687`
- **Username**: `neo4j`
- **Password**: `password123`

Access Neo4j Browser at http://localhost:7474 to verify it's running.

<!-- ![Neo4j Browser - Local Instance](docs/screenshots/neo4j-browser-local.png) -->

---

### Step 3: Clone and Configure the Project

#### 3.1 Navigate to the Example

```bash
cd examples/google-cloud-financial-advisor
```

#### 3.2 Create Your Environment File

```bash
cp .env.example .env
```

#### 3.3 Edit `.env` with Your Credentials

Open `.env` in your editor and fill in your values:

```bash
# Google Cloud Configuration
GOOGLE_CLOUD_PROJECT=my-financial-advisor          # Your GCP project ID
VERTEX_AI_LOCATION=us-central1                     # Or your preferred region
VERTEX_AI_MODEL_ID=gemini-2.5-flash               # Gemini model for agents
VERTEX_AI_EMBEDDING_MODEL=text-embedding-004       # Embedding model

# Google AI API Key (required for Gemini via Google AI Studio)
# Get your key at https://aistudio.google.com/apikey
GOOGLE_API_KEY=your-google-api-key

# Neo4j Configuration
# For Aura:
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-aura-password

# For Local Neo4j:
# NEO4J_URI=bolt://localhost:7687
# NEO4J_USER=neo4j
# NEO4J_PASSWORD=password123

# Application Settings
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

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

<!-- ![Terminal - make install output](docs/screenshots/terminal-make-install.png) -->

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

<!-- ![Terminal - Sample data loaded](docs/screenshots/terminal-load-data.png) -->

#### Verify Data in Neo4j Browser

Open Neo4j Browser and run:

```cypher
MATCH (n) RETURN labels(n)[0] AS type, count(*) AS count
```

You should see counts for Customer, Organization, and Transaction nodes.

<!-- ![Neo4j Browser - Data verification](docs/screenshots/neo4j-verify-data.png) -->

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

- **Frontend**: Open http://localhost:5173 - You should see the Financial Advisor dashboard
- **API Docs**: Open http://localhost:8000/docs - Interactive API documentation
- **Health Check**: `curl http://localhost:8000/health` should return `{"status":"healthy"}`

<!-- ![Application - Dashboard home](docs/screenshots/app-dashboard-home.png) -->

<!-- ![FastAPI - Swagger docs](docs/screenshots/fastapi-docs.png) -->

---

### Step 7: Run Your First Investigation

Now let's use the multi-agent system to investigate a customer.

#### 7.1 Open the Chat Interface

In the application, click on **"AI Assistant"** in the sidebar to open the chat interface.

<!-- ![Application - Chat interface](docs/screenshots/app-chat-interface.png) -->

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

<!-- ![Application - Agent orchestration](docs/screenshots/app-agent-orchestration.png) -->

#### 7.3 Review the Results

The supervisor synthesizes all findings into a comprehensive report. Each assistant message includes an expandable **Agent Activity** section showing the reasoning trace timeline — click to see the full chain of agent reasoning, tool calls with arguments and results, and memory operations.

<!-- ![Application - Investigation results](docs/screenshots/app-investigation-results.png) -->

#### 7.4 Explore the Relationship Network

Click on **"Network Graph"** to visualize the customer's connections:

<!-- ![Application - Network visualization](docs/screenshots/app-network-graph.png) -->

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

<!-- ![Google Cloud Console - Cloud Run deployed](docs/screenshots/gcp-cloud-run-deployed.png) -->

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

| Agent | Responsibility |
|-------|----------------|
| **Supervisor** | Orchestrates investigation workflow |
| **KYC Agent** | Identity verification, document checking |
| **AML Agent** | Transaction monitoring, pattern detection |
| **Relationship Agent** | Network analysis using Context Graph |
| **Compliance Agent** | Sanctions/PEP screening, report generation |

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

After streaming completes, the full reasoning trace (agent steps, tool calls, results) is automatically persisted to Neo4j and retrievable via `GET /api/traces/{session_id}`.

---

## Project Structure

```
google-cloud-financial-advisor/
├── backend/
│   ├── src/
│   │   ├── agents/            # Google ADK agent definitions
│   │   │   ├── supervisor.py  # Orchestrator with sub-agents
│   │   │   ├── kyc_agent.py   # KYC specialist + _bind_tool
│   │   │   ├── aml_agent.py   # AML specialist
│   │   │   ├── relationship_agent.py
│   │   │   └── compliance_agent.py
│   │   ├── tools/             # Agent tools (KYC, AML, etc.)
│   │   ├── api/routes/        # FastAPI endpoints
│   │   │   ├── chat.py        # POST /chat (sync) + /chat/stream (SSE)
│   │   │   ├── traces.py      # GET /traces/{session_id}, /traces/detail/{id}
│   │   │   ├── customers.py   # Customer CRUD
│   │   │   ├── alerts.py      # Alert management
│   │   │   └── ...
│   │   ├── models/            # Pydantic models
│   │   └── services/          # Neo4jDomainService, FinancialMemoryService
│   ├── Dockerfile
│   └── pyproject.toml         # Dependencies managed with uv
├── frontend/
│   ├── src/
│   │   ├── hooks/
│   │   │   └── useAgentStream.ts   # SSE connection + agent state management
│   │   ├── components/
│   │   │   ├── Chat/
│   │   │   │   ├── ChatInterface.tsx           # Main chat with streaming
│   │   │   │   ├── AgentOrchestrationView.tsx  # Real-time agent visualization
│   │   │   │   ├── AgentActivityTimeline.tsx   # Post-completion trace timeline
│   │   │   │   ├── ToolCallCard.tsx            # Animated tool call display
│   │   │   │   └── MemoryAccessIndicator.tsx   # Neo4j memory flash indicator
│   │   │   ├── Dashboard/
│   │   │   │   ├── Sidebar.tsx            # Grouped nav, alert badges
│   │   │   │   ├── CustomerDashboard.tsx  # Stats, skeleton loading
│   │   │   │   └── AlertsPanel.tsx        # Empty states, semantic tokens
│   │   │   ├── Investigation/
│   │   │   │   ├── InvestigationPanel.tsx  # Timeline audit trail
│   │   │   │   └── AgentWorkflow.tsx       # Workflow visualization
│   │   │   └── Graph/
│   │   │       └── NetworkViewer.tsx       # vis-network visualization
│   │   └── lib/
│   │       └── api.ts          # API client + SSE parsing
│   └── package.json            # Includes framer-motion, chakra v3
├── infrastructure/        # Cloud Run deployment configs
├── data/                  # Sample data (JSON) and load_sample_data.py
├── Makefile              # Development commands
└── docker-compose.yml    # Local development setup
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
| `/api/graph/stats` | GET | Graph statistics |

Full API documentation available at http://localhost:8000/docs when running locally.

---

## Environment Variables Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | Yes |
| `GOOGLE_API_KEY` | Google AI API key ([get one here](https://aistudio.google.com/apikey)) | Yes |
| `VERTEX_AI_LOCATION` | Vertex AI region (e.g., `us-central1`) | Yes |
| `VERTEX_AI_MODEL_ID` | Gemini model ID (default: `gemini-2.5-flash`) | Yes |
| `VERTEX_AI_EMBEDDING_MODEL` | Embedding model ID (default: `text-embedding-004`) | Yes |
| `NEO4J_URI` | Neo4j connection URI | Yes |
| `NEO4J_USER` | Neo4j username | Yes |
| `NEO4J_PASSWORD` | Neo4j password | Yes |
| `LOG_LEVEL` | Logging level | No (default: INFO) |
| `CORS_ORIGINS` | Allowed CORS origins | No |

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

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03 (105 unit tests pass; full end-to-end run requires Google API key and `[google-adk,vertex-ai]` extras)._
