"""FastAPI application for Google Cloud Financial Advisor.

This is the main entry point for the backend API, which provides
endpoints for interacting with the multi-agent financial compliance
system powered by Google ADK and Neo4j Agent Memory.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env into process environment so ADK/genai can find GOOGLE_API_KEY etc.
# The app root .env is the documented location; backend/.env is an optional
# developer override loaded second.
_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
load_dotenv()  # also load backend/.env if present (overrides)


def _bootstrap_google_genai_env() -> None:
    """Select the Vertex AI path for google-genai / ADK when appropriate.

    google-genai (and therefore every ADK ``LlmAgent``) chooses between Google
    AI Studio and Vertex AI from its own environment variables, not from this
    app's settings. With no ``GOOGLE_API_KEY`` but a project configured, opt
    into Vertex AI and mirror the project/location under the names google-genai
    actually reads — this is what makes the README's "Option B: Vertex AI"
    path work instead of silently having no credentials at all.
    """
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("VERTEX_AI_PROJECT_ID")
    if not project:
        return
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
    os.environ.setdefault(
        "GOOGLE_CLOUD_LOCATION",
        os.environ.get("VERTEX_AI_LOCATION", "us-central1"),
    )


_bootstrap_google_genai_env()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import alerts, chat, customers, graph, investigations, traces
from .config import get_settings
from .services.memory_service import get_memory_service
from .services.neo4j_service import Neo4jDomainService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    Handles startup and shutdown events for the application.
    """
    # Startup
    logger.info("Starting Google Cloud Financial Advisor...")

    # Initialize memory service
    memory_service = get_memory_service()
    try:
        await memory_service.initialize()
        logger.info("Memory service initialized")

        # Create domain data service on the same MemoryClient: domain reads go
        # through client.query.cypher, agent memory through the memory layers.
        neo4j_service = Neo4jDomainService(memory_service.client)
        app.state.neo4j_service = neo4j_service
        logger.info("Neo4j domain service initialized")

        # Reset and recreate agent with neo4j_service so tools can query Neo4j
        from .agents.supervisor import reset_supervisor_agent

        reset_supervisor_agent()
    except Exception as e:
        logger.warning(f"Memory service initialization failed: {e}")
        logger.warning("Some features may be unavailable without Neo4j connection")

    yield

    # Shutdown
    logger.info("Shutting down...")
    try:
        await memory_service.close()
    except Exception as e:
        logger.warning(f"Error closing memory service: {e}")


# Create FastAPI app
app = FastAPI(
    title="Google Cloud Financial Advisor",
    description="""
AI-powered financial compliance assistant using Google ADK and Neo4j Agent Memory.

## Features
- **Multi-Agent System**: Coordinated KYC, AML, Relationship, and Compliance agents
- **Context Graph Intelligence**: Neo4j-powered relationship analysis
- **Vertex AI Integration**: Gemini for reasoning, gemini-embedding-001 for search
- **Explainable AI**: Full audit trails for regulatory compliance

## Agents
- **Supervisor**: Orchestrates investigations, synthesizes findings
- **KYC Agent**: Identity verification, document validation
- **AML Agent**: Transaction monitoring, pattern detection
- **Relationship Agent**: Network analysis, beneficial ownership
- **Compliance Agent**: Sanctions screening, PEP verification
    """,
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(chat.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(investigations.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(traces.router, prefix="/api")


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Google Cloud Financial Advisor",
        "version": "0.1.0",
        "description": "AI-powered financial compliance assistant",
        "platform": "Google Cloud",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint for Cloud Run."""
    return {
        "status": "healthy",
        "platform": "Google Cloud",
        "service": "financial-advisor",
    }


@app.get("/api/info")
async def api_info():
    """Get API information and available agents."""
    return {
        "agents": [
            {
                "name": "supervisor",
                "description": "Orchestrates multi-agent investigations",
            },
            {
                "name": "kyc_agent",
                "description": "Identity verification and customer due diligence",
            },
            {
                "name": "aml_agent",
                "description": "Transaction monitoring and suspicious activity detection",
            },
            {
                "name": "relationship_agent",
                "description": "Network analysis and beneficial ownership tracing",
            },
            {
                "name": "compliance_agent",
                "description": "Sanctions screening and regulatory reporting",
            },
        ],
        "memory_types": [
            "short_term (conversation history)",
            "long_term (entities, preferences, facts)",
            "reasoning (audit trails, tool usage)",
        ],
        "endpoints": {
            "chat": "/api/chat",
            "customers": "/api/customers",
            "investigations": "/api/investigations",
            "alerts": "/api/alerts",
            "graph": "/api/graph",
        },
    }
