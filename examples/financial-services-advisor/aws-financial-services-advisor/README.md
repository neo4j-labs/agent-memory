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

- **Multi-Agent Investigation**: Coordinated KYC, AML, relationship, and compliance analysis
- **Context Graph Intelligence**: Relationship mapping and network analysis with Neo4j
- **Explainable AI**: Full audit trails for regulatory compliance (EU AI Act ready)
- **Real-time Monitoring**: Transaction and behavior pattern detection
- **Graph-based RAG**: Reduces hallucinations through grounded, relationship-aware retrieval
- **Entity Deduplication**: `DeduplicationConfig` available for preventing duplicate customer/account entities
- **Provenance Tracking**: `link_entity_to_message()` and `link_entity_to_extractor()` for compliance audit trails
- **Explicit Extraction Config**: `ExtractionConfig` for configuring entity extraction pipelines

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

## Current Status

This example is in **alpha/demo** state. The architecture and agent orchestration pattern are functional, but several components use simulated data rather than real Neo4j queries. See [REVIEW.md](REVIEW.md) for a detailed analysis and [GETTING_STARTED.md](GETTING_STARTED.md) for a working setup guide.

**Working**: Chat with supervisor agent, conversation memory, customer/alert CRUD, risk scoring, frontend UI
**Simulated**: KYC/AML/Relationship/Compliance tool results (use `random.choice()`), graph visualization data

## Quick Start

> For detailed setup instructions and troubleshooting, see **[GETTING_STARTED.md](GETTING_STARTED.md)**.

### Prerequisites

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- AWS CLI configured with Bedrock access (Claude Sonnet 4 + Titan Embed V2)
- Neo4j Aura account (or local Neo4j via Docker)

### Local Development

1. **Navigate to the example:**

```bash
cd examples/aws-financial-services-advisor
```

2. **Set up environment:**

```bash
cp .env.example backend/.env
# Edit backend/.env with your Neo4j and AWS credentials
```

3. **Install dependencies:**

```bash
make install
# or: cd backend && uv sync && cd ../frontend && npm install
```

4. **Run the application:**

```bash
make run
```

5. **Access the application:**
   - Frontend: http://localhost:5173
   - API Docs: http://localhost:8000/docs

### AWS Deployment

Deploy the complete stack using AWS CDK:

```bash
cd infrastructure
npm install
npx cdk bootstrap  # First time only
npx cdk deploy --all
```

## Project Structure

```
aws-financial-services-advisor/
├── backend/
│   ├── src/
│   │   ├── agents/        # Strands Agent definitions
│   │   ├── api/routes/    # FastAPI endpoints
│   │   ├── models/        # Pydantic models
│   │   └── services/      # Business logic
│   ├── handler.py         # Lambda handler
│   └── pyproject.toml     # Python dependencies (uv)
├── frontend/
│   ├── src/
│   │   ├── components/    # React components
│   │   └── lib/           # API client and types
│   └── package.json
├── .env.example           # Environment template
├── Makefile               # Development commands
├── GETTING_STARTED.md     # Setup guide
└── REVIEW.md              # Code review
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Interact with the AI advisor |
| `/api/customers` | GET/POST | Customer management |
| `/api/customers/{id}/risk` | GET | Risk assessment |
| `/api/customers/{id}/network` | GET | Relationship network |
| `/api/investigations` | GET/POST | Investigation management |
| `/api/investigations/{id}/start` | POST | Start multi-agent investigation |
| `/api/investigations/{id}/audit-trail` | GET | Get reasoning trace |
| `/api/alerts` | GET | Compliance alerts |
| `/api/reports/sar` | POST | Generate SAR report |

## Memory Types

The application uses three types of Context Graph memory:

| Memory Type | Purpose | Example Use |
|-------------|---------|-------------|
| **Short-Term** | Conversation history | Chat context, session state |
| **Long-Term** | Entities & relationships | Customer profiles, org networks |
| **Reasoning** | Decision audit trails | Investigation traces, agent reasoning |

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

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03 (structure, syntax, and import tests pass; full end-to-end run requires AWS Bedrock credentials and the Strands Agents SDK)._
