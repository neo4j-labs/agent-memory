# Financial Services Advisor — AWS Implementation

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Multi-agent KYC/AML compliance assistant on AWS Strands Agents + Bedrock + Neo4j Agent Memory.

An intelligent compliance assistant powered by **AWS Strands Agents** and **Neo4j Agent Memory Context Graphs**, demonstrating multi-agent AI for KYC/AML compliance, fraud detection, and relationship intelligence. The Google Cloud variant lives at [`../google-cloud-financial-advisor/`](../google-cloud-financial-advisor/); both share the same sample data and Neo4j schema.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Overview

This example application showcases the AWS-Neo4j partnership through a production-ready architecture for financial services compliance. It demonstrates how AI agents can leverage graph-based memory for explainable, auditable decision-making.

### Key Features

- **Multi-Agent Investigation**: a supervisor delegates to KYC, AML, Relationship and Compliance specialists, each with tools that run real Cypher against a Neo4j compliance graph
- **Live streaming**: `Agent.stream_async()` drives the SSE route, so `tool_call` and `tool_result` events reach the browser *during* the investigation
- **Explainable AI**: a reasoning trace per turn, one step per tool call, and `(:ReasoningStep)-[:TOUCHED]->(:Entity)` audit edges — so "which entities did this investigation act on?" is one hop:

  ```cypher
  MATCH (rt:ReasoningTrace {id: $trace_id})-[:HAS_STEP]->(s:ReasoningStep)-[:TOUCHED]->(e:Entity)
  OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
  RETURN e.name, e.type, s.action, collect(DISTINCT tc.tool_name) AS tools
  ```

  That query is what `GET /api/investigations/{id}/audit-trail` returns.
- **Long-term memory with real content**: sanctions and PEP screenings are written as `(:Fact {predicate: 'SCREENED_AGAINST'})` triples and read back on the next investigation of the same subject
- **Existing-graph adoption**: `client.schema.adopt_existing_graph()` turns the compliance nodes into long-term memory entities, so extraction links to them instead of duplicating them
- **Portable graph access**: every read goes through `client.query.cypher()`, which validates read-only before the round-trip and works identically on self-hosted Neo4j and the hosted service

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![AWS Financial Services Advisor Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

### AWS Services Highlighted

| Service | Role |
|---------|------|
| **Amazon Bedrock** | LLM (Claude) + Embeddings (Titan) |
| **AWS Lambda** | Serverless compute for API |
| **Amazon API Gateway** | REST API management |
| **Amazon CloudFront** | CDN for frontend |
| **Amazon Cognito** | Authentication |
| **Amazon CloudWatch** | Monitoring & logging |
| **Amazon S3** | Document storage |

### Multi-Agent System

| Agent | Responsibility |
|-------|----------------|
| **Supervisor** | Orchestrates investigation workflow |
| **KYC Agent** | Identity verification, document checking |
| **AML Agent** | Transaction monitoring, pattern detection |
| **Relationship Agent** | Network analysis using Context Graph |
| **Compliance Agent** | Sanctions/PEP screening, report generation |

## Quick Start

> For detailed setup instructions and troubleshooting, see **[GETTING_STARTED.md](GETTING_STARTED.md)**.

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- AWS CLI configured with Bedrock access (a current Claude Sonnet inference profile + Titan Embed V2)
- Neo4j Aura account (or local Neo4j via Docker)

### Local Development

1. **Navigate to the example:**

```bash
cd examples/financial-services-advisor/aws-financial-services-advisor
```

2. **Set up environment:**

```bash
cp .env.example backend/.env
# Edit backend/.env with your Neo4j and AWS credentials
```

3. **Install dependencies:**

```bash
make install
# or: cd backend && uv sync --extra dev && cd ../frontend && npm ci
```

4. **Load the shared sample data:**

```bash
make load-data          # idempotent; re-running changes nothing
make adopt-graph        # optional: make the domain nodes long-term memory entities
```

5. **Run the application:**

```bash
make run
```

6. **Access the application:**
   - Frontend: http://localhost:5173
   - API Docs: http://localhost:8000/docs

### AWS Deployment

```bash
make synth              # synthesize all six stacks — no AWS credentials needed
make build              # build the frontend; the api stack uploads it to S3
make deploy             # six stacks: network, auth, data, compute, api, monitoring
```

API Gateway fronts the Lambda with one authorized `{proxy+}` resource, so every FastAPI route is reachable; `/health` stays unauthenticated. Neo4j credentials come from a Secrets Manager secret whose ARN the compute stack injects as `NEO4J_SECRET_ARN` — they never appear in the function's environment. See [GETTING_STARTED.md](GETTING_STARTED.md#aws-deployment-advanced) for the bootstrap and secret-population steps.

## Project Structure

```
aws-financial-services-advisor/
├── backend/
│   ├── src/
│   │   ├── agents/        # Supervisor + the four specialists' prompts
│   │   ├── api/routes/    # FastAPI endpoints
│   │   ├── models/        # Pydantic models
│   │   ├── tools/         # 16 Neo4j-backed tools + bind_tool()
│   │   └── services/      # memory_service, neo4j_service, risk_service
│   ├── handler.py         # Lambda handler (Mangum)
│   ├── pyproject.toml     # Python dependencies (uv)
│   └── tests/             # 151 tests
├── frontend/              # React + Chakra UI v3 + Vite
├── infrastructure/        # Six AWS CDK stacks (TypeScript)
├── docs/diagrams/         # Editable Excalidraw sources
├── img/                   # Architecture diagram
├── .env.example           # Environment template
├── Makefile               # Development commands
└── GETTING_STARTED.md     # Setup guide
```

The sample data and its loader live one level up, in [`../data/`](../data/), shared with the Google Cloud twin.

## API Endpoints

The full list is at http://localhost:8000/docs. The ones worth knowing:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Interact with the AI advisor (awaits `invoke_async`) |
| `/api/chat/stream` | POST | Same, as SSE — tool events arrive during the run |
| `/api/customers` | GET/POST | Customer management |
| `/api/customers/{id}/risk` | GET | Risk assessment with contributing factors |
| `/api/customers/{id}/network` | GET | Relationship network |
| `/api/investigations` | GET/POST | Investigation management |
| `/api/investigations/{id}/start` | POST | Start multi-agent investigation |
| `/api/investigations/{id}/audit-trail` | GET | Reasoning traces plus the `:TOUCHED` entity projection |
| `/api/alerts` | GET/POST | Compliance alerts |
| `/api/graph/query` | POST | Read-only Cypher (validated by `client.query.cypher`) |
| `/api/reports/sar` | GET/POST | SAR reports, persisted in Neo4j |
| `/health` | GET | Readiness probe — round-trips to Neo4j, 503 when it fails |

## Memory Types

The application uses three types of Context Graph memory:

| Memory Type | What this app writes | Read back by |
|-------------|---------------------|--------------|
| **Short-Term** | One `:Conversation` per session; exactly one user and one assistant `:Message` per turn, with entity extraction on the user turn | `GET /api/chat/history/{session_id}`, `POST /api/chat/search` |
| **Long-Term** | `(:Fact {predicate: 'SCREENED_AGAINST'})` per sanctions/PEP screening, plus analyst preferences. With `make adopt-graph`, the compliance nodes themselves become `:Entity` | `check_sanctions` / `verify_pep_status` surface `prior_screenings` on the next run |
| **Reasoning** | A `:ReasoningTrace` per turn linked to the triggering message, a `:ReasoningStep` and `:ToolCall` per tool use, `:TOUCHED` edges to the entities acted on, and a structured `TraceOutcome` | `GET /api/traces/{session_id}`, `GET /api/investigations/{id}/audit-trail` |

## Sample Investigation Flow

1. **User Request**: "Investigate customer CUST-003 for potential money laundering"

2. **Supervisor Agent**:
   - Analyzes the request
   - Delegates to specialized agents in parallel

3. **KYC Agent**: Verifies identity, checks documents
4. **AML Agent**: Scans transactions, detects patterns
5. **Relationship Agent**: Maps network connections via Context Graph
6. **Compliance Agent**: Screens against sanctions lists

5. **Supervisor Synthesis**:
   - Combines all findings
   - Generates risk assessment
   - Provides recommendations

6. **Audit Trail**: Complete reasoning trace stored in Neo4j

## Environment Variables

```bash
# Neo4j
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-20250514-v1:0
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0

# App
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173
```

## Security Considerations

> This is a demo. `POST /api/graph/query` accepts caller-supplied Cypher; the library validates it as read-only before any round-trip, but before a real deployment put it behind the Cognito authorizer *and* a read-only Neo4j role. Client-side validation is defence in depth, not a security boundary.

- **Authentication**: Cognito with MFA for compliance users
- **Authorization**: Role-based access (Analyst, Supervisor, Admin)
- **Audit Trail**: All actions logged to CloudWatch and Context Graph
- **Data Encryption**: At-rest (S3, Neo4j) and in-transit (TLS)
- **PII Handling**: Tokenization for sensitive customer data

## Business Value

### For Financial Institutions
- **Faster Investigations**: Multi-agent parallel processing
- **Better Detection**: Graph-based relationship analysis
- **Regulatory Compliance**: Full audit trails for AI decisions
- **Reduced False Positives**: Context-aware risk assessment

### For AWS-Neo4j Partnership
- Demonstrates Bedrock + Strands for regulated industries
- Shows Context Graphs as essential AI memory layer
- Highlights serverless architecture for AI workloads
- Joint solution for EU AI Act requirements

## References

- [AWS Agentic AI in Financial Services](https://aws.amazon.com/blogs/industries/agentic-ai-in-financial-services/)
- [Neo4j + AWS Strategic Partnership](https://neo4j.com/press-releases/neo4j-aws-bedrock-integration/)
- [Strands Agents SDK](https://strandsagents.com/latest/)
- [Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory)

## License

This example is part of the neo4j-agent-memory project and is licensed under the Apache 2.0 License.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.5.0, strands-agents 1.55.1, fastapi 0.141.1, aws-cdk-lib 2.269.0 on 2026-09-10 — 151 backend tests pass (124 unit, 27 integration against Neo4j 5.26), `cdk synth` succeeds with no AWS credentials, and the domain API was exercised end to end. A full chat run additionally needs AWS Bedrock credentials._
