"""Route aggregation for the Neo4j Agent Memory server."""

from __future__ import annotations

from fastapi import APIRouter

from neo4j_agent_memory.server.routes.context import router as context_router
from neo4j_agent_memory.server.routes.long_term import router as long_term_router
from neo4j_agent_memory.server.routes.reasoning import router as reasoning_router
from neo4j_agent_memory.server.routes.short_term import router as short_term_router


def create_api_router() -> APIRouter:
    """Create the aggregate API router with all sub-routers."""
    api_router = APIRouter()
    api_router.include_router(context_router)
    api_router.include_router(short_term_router)
    api_router.include_router(long_term_router)
    api_router.include_router(reasoning_router)
    return api_router
