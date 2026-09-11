"""FastAPI application for the Financial Services Advisor."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agents import reset_supervisor_agent
from .api.routes import alerts, chat, customers, graph, investigations, reports, traces
from .config import get_settings
from .services.memory_service import get_memory_service
from .services.neo4j_service import Neo4jDomainService

# Load .env from the parent directory first (handles both backend/ and root).
load_dotenv("../.env")
load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

API_VERSION = "0.2.0"

#: Upper bound on the ``/health`` Neo4j probe, so a hung database does not hang
#: the health check with it.
_HEALTH_TIMEOUT_SECONDS = 3.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect to Neo4j on startup, failing fast unless told otherwise.

    A swallowed startup error used to leave the app serving a green ``/health``
    while every domain route returned 503, with the real cause buried in a
    WARNING. Set ``ALLOW_DEGRADED_START=true`` if you genuinely want the API
    to come up without its database.
    """
    logger.info("Starting Financial Services Advisor...")
    settings = get_settings()
    logger.info("Environment: debug=%s", settings.app.debug)

    try:
        memory_service = get_memory_service()
        await memory_service.initialize()
        # One driver for both layers: the three memory tiers and the compliance
        # domain graph share the MemoryClient's connection pool.
        app.state.neo4j_service = Neo4jDomainService(memory_service.client)
        reset_supervisor_agent()
        logger.info("Connected to Neo4j at %s", settings.neo4j.uri)
    except Exception as exc:
        # Never log settings.neo4j.password.
        logger.error("Could not connect to Neo4j at %s: %s", settings.neo4j.uri, exc)
        if not settings.app.allow_degraded_start:
            raise
        logger.warning(
            "ALLOW_DEGRADED_START is set - starting without Neo4j; "
            "every domain route will return 503"
        )
        app.state.neo4j_service = None

    yield

    logger.info("Shutting down Financial Services Advisor...")
    reset_supervisor_agent()
    try:
        await get_memory_service().close()
    except Exception as exc:
        logger.warning("Error closing memory service: %s", exc)
    logger.info("Financial Services Advisor shutdown complete")


app = FastAPI(
    title="Financial Services Advisor",
    description="""
    An intelligent financial compliance assistant powered by AWS Strands Agents
    and Neo4j Agent Memory Context Graphs.

    ## Features

    - **Multi-Agent Investigation**: Coordinated KYC, AML, and compliance analysis
    - **Context Graph Intelligence**: Relationship mapping and network analysis
    - **Explainable AI**: Reasoning traces with `(:ReasoningStep)-[:TOUCHED]->(:Entity)`
      audit edges behind `/api/investigations/{id}/audit-trail`
    - **Real-time Monitoring**: Transaction and behavior pattern detection

    ## API Groups

    - **Chat**: Interact with the AI advisor (sync and SSE streaming)
    - **Customers**: Customer management and risk assessment
    - **Investigations**: Compliance investigations with audit trails
    - **Alerts**: Alert management and escalation
    - **Graph**: Relationship visualization and read-only Cypher
    - **Reports**: SAR and risk-assessment reports, persisted in Neo4j
    - **Traces**: Reasoning traces per session
    """,
    version=API_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(investigations.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(traces.router, prefix="/api")


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with API information."""
    return {
        "name": "Financial Services Advisor",
        "version": API_VERSION,
        "description": "AI-powered financial compliance assistant",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check() -> JSONResponse:
    """Liveness *and* readiness: this actually round-trips to Neo4j.

    Returns 503 when the database is unreachable, so an orchestrator (and the
    verification ``curl`` in GETTING_STARTED.md) learns the truth instead of
    reading a private ``_initialized`` flag that only says "connect() ran".
    """
    components: dict[str, Any] = {}

    memory_service = get_memory_service()
    if not memory_service.connected:
        components["neo4j"] = {"status": "not_connected"}
    else:
        try:
            await asyncio.wait_for(memory_service.ping(), timeout=_HEALTH_TIMEOUT_SECONDS)
            components["neo4j"] = {"status": "healthy"}
        except TimeoutError:
            components["neo4j"] = {
                "status": "unhealthy",
                "error": f"no response within {_HEALTH_TIMEOUT_SECONDS}s",
            }
        except Exception as exc:
            components["neo4j"] = {"status": "unhealthy", "error": str(exc)}

    components["config"] = {
        "status": "healthy",
        "bedrock_model": get_settings().bedrock.model_id,
    }

    healthy = all(component.get("status") == "healthy" for component in components.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "version": API_VERSION,
            "components": components,
        },
    )


@app.get("/api/info")
async def api_info() -> dict[str, Any]:
    """Information about the API, the agents, and what each memory tier holds."""
    return {
        "api_version": API_VERSION,
        "agents": {
            "supervisor": {
                "description": "Orchestrates investigations by delegating to specialized agents",
                "capabilities": [
                    "Task delegation",
                    "Investigation coordination",
                    "Finding synthesis",
                ],
            },
            "kyc": {
                "description": "Customer identity verification and due diligence",
                "capabilities": [
                    "Identity verification",
                    "Document checking",
                    "Risk assessment",
                    "Adverse media screening",
                ],
            },
            "aml": {
                "description": "Anti-money laundering detection and analysis",
                "capabilities": [
                    "Transaction scanning",
                    "Pattern detection",
                    "Suspicious activity flagging",
                    "Velocity analysis",
                ],
            },
            "relationship": {
                "description": "Network analysis and relationship mapping",
                "capabilities": [
                    "Connection finding",
                    "Network risk analysis",
                    "Shell company detection",
                    "Beneficial ownership mapping",
                ],
            },
            "compliance": {
                "description": "Regulatory compliance and report generation",
                "capabilities": [
                    "Sanctions screening",
                    "PEP verification",
                    "Report generation",
                    "Regulatory assessment",
                ],
            },
        },
        "memory_types": {
            "short_term": (
                "One :Conversation per session; one user and one assistant message per turn"
            ),
            "long_term": (
                "Screening facts (:Fact {predicate:'SCREENED_AGAINST'}) and analyst preferences"
            ),
            "reasoning": ("A trace per turn, one step per tool call, with :TOUCHED audit edges"),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
