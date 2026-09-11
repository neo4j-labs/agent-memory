"""Graph API routes for Context Graph queries and visualization.

Every read here goes through ``MemoryClient.query.cypher``, which validates
read-only-ness for us and works against both the bolt and the hosted (NAMS)
backends. The Cypher itself lives in :class:`Neo4jDomainService` so it is
unit-testable without FastAPI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...services.memory_service import (
    FinancialMemoryService,
    get_initialized_memory_service,
)
from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


class CypherQueryRequest(BaseModel):
    """Request model for Cypher queries."""

    query: str = Field(..., description="Cypher query (read-only)")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Query parameters")


class CypherQueryResponse(BaseModel):
    """Response model for Cypher queries."""

    query: str
    results: list[dict[str, Any]]
    count: int


class EntityNeighborsRequest(BaseModel):
    """Request for entity neighbors."""

    entity_id: str
    depth: int = Field(default=1, ge=1, le=3)
    relationship_types: list[str] | None = None


def _require_neo4j_service(request: Request) -> Neo4jDomainService:
    """Fetch the domain service from app state or fail with 503."""
    service = getattr(request.app.state, "neo4j_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    return service


@router.post("/query", response_model=CypherQueryResponse)
async def execute_cypher_query(
    request: CypherQueryRequest,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> CypherQueryResponse:
    """Execute a read-only Cypher query against the Context Graph.

    ``client.query.cypher`` rejects writes itself (CREATE/MERGE/DELETE/SET/…),
    so this route does not hand-roll a keyword blocklist. For production, back
    it with a read-only Neo4j role as well.
    """
    try:
        records = await memory_service.client.query.cypher(request.query, request.parameters)
    except ValueError as exc:
        # Raised by the read-only validator.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Cypher query error: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CypherQueryResponse(
        query=request.query,
        results=records,
        count=len(records),
    )


@router.get("/neighbors/{entity_id}")
async def get_entity_neighbors(
    entity_id: str,
    request: Request,
    depth: int = Query(1, ge=1, le=3, description="Traversal depth"),
    limit: int = Query(50, ge=1, le=200, description="Maximum neighbors"),
) -> dict[str, Any]:
    """Get neighbors of a node from the Context Graph (nodes + edges)."""
    neo4j_service = _require_neo4j_service(request)
    try:
        return await neo4j_service.get_neighbors(entity_id, depth=depth, limit=limit)
    except Exception as exc:
        logger.error("Error getting neighbors: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/stats")
async def get_graph_stats(request: Request) -> dict[str, Any]:
    """Get statistics about the Context Graph."""
    neo4j_service = _require_neo4j_service(request)
    try:
        return await neo4j_service.get_graph_stats()
    except Exception as exc:
        logger.error("Error getting stats: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/memory")
async def get_memory_graph(
    request: Request,
    session_id: str | None = Query(None, description="Scope to one conversation"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """Get the combined domain + memory graph for visualization."""
    neo4j_service = _require_neo4j_service(request)
    try:
        return await neo4j_service.get_memory_graph(session_id=session_id, limit=limit)
    except Exception as exc:
        logger.error("Error building memory graph: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/audit-trail/{entity_name}")
async def get_entity_audit_trail(
    entity_name: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Which reasoning steps touched this entity, and via which tool?

    Backed by the ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges the chat
    route writes when it records tool calls with ``touched_entities=``.
    """
    neo4j_service = _require_neo4j_service(request)
    try:
        rows = await neo4j_service.get_entity_audit_trail(entity_name, limit=limit)
    except Exception as exc:
        logger.error("Error building audit trail: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"entity_name": entity_name, "steps": rows, "total": len(rows)}
