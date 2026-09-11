"""Graph API routes for visualization and exploration."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


def _get_neo4j_service(request: Request) -> Neo4jDomainService:
    svc = getattr(request.app.state, "neo4j_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, svc)


class CypherQueryRequest(BaseModel):
    query: str = Field(..., description="Cypher query (read-only)")
    parameters: dict[str, Any] = Field(default_factory=dict)


class CypherQueryResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]
    count: int


@router.post("/query", response_model=CypherQueryResponse)
async def execute_cypher_query(request: Request, body: CypherQueryRequest) -> CypherQueryResponse:
    """Execute a read-only Cypher query against the graph.

    Read-only enforcement is the library's (``client.query.cypher`` validates
    before any round-trip and raises ``ValueError`` on a write). The previous
    keyword blocklist was wrong in both directions: ``CALL apoc.cypher.doIt()``
    and ``CALL { ... }`` subqueries slipped through, while ``MATCH (a:Asset)``
    and any query containing ``OFFSET`` were rejected.

    This endpoint is a demo affordance. Before deploying it, put it behind the
    Cognito authorizer *and* a read-only Neo4j role — client-side validation is
    defence in depth, not a security boundary.
    """
    neo4j_service = _get_neo4j_service(request)
    try:
        results = await neo4j_service.read_only_cypher(body.query, body.parameters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Cypher query error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CypherQueryResponse(query=body.query, results=results, count=len(results))


@router.get("/neighbors/{entity_id}")
async def get_entity_neighbors(
    request: Request,
    entity_id: str,
    depth: int = Query(1, ge=1, le=3),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Get neighbors of a compliance node."""
    neo4j_service = _get_neo4j_service(request)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    root = await neo4j_service.get_node_summary(entity_id)
    if root:
        root_id = root["id"] or entity_id
        nodes[root_id] = {
            "id": root_id,
            "label": root["name"] or root_id,
            "type": root["labels"][0] if root["labels"] else "Unknown",
            "isRoot": True,
        }

    conn_data = await neo4j_service.find_connections(entity_id, depth=depth)
    for conn in conn_data.get("connections", [])[:limit]:
        entity = conn.get("entity", {})
        eid = entity.get("id") or entity.get("name")
        if not eid:
            continue
        if eid not in nodes:
            nodes[eid] = {
                "id": eid,
                "label": entity.get("name") or eid,
                "type": entity.get("customer_type") or entity.get("type") or "Unknown",
                "isRoot": False,
            }
        rel_types = conn.get("rel_types", [])
        edges.append(
            {
                "from": entity_id,
                "to": eid,
                "relationship": rel_types[0] if rel_types else "RELATED",
            }
        )

    return {
        "entity_id": entity_id,
        "depth": depth,
        "nodes": list(nodes.values()),
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


@router.get("/entity/{entity_name}")
async def get_entity_graph(
    request: Request,
    entity_name: str,
    depth: int = Query(2, ge=1, le=3),
) -> dict[str, Any]:
    """Get the subgraph around a named entity."""
    return await get_entity_neighbors(request, entity_name, depth=depth)


@router.post("/search")
async def search_entities(
    request: Request,
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Search compliance nodes by name."""
    neo4j_service = _get_neo4j_service(request)
    results = await neo4j_service.search_nodes(query, limit=limit)
    return [
        {
            "id": r.get("id") or r.get("name"),
            "name": r.get("name"),
            "type": r.get("type"),
            "jurisdiction": r.get("jurisdiction"),
        }
        for r in results
    ]


@router.get("/stats")
async def get_graph_statistics(request: Request) -> dict[str, Any]:
    """Node and relationship counts for the whole graph (domain plus memory)."""
    neo4j_service = _get_neo4j_service(request)
    return await neo4j_service.get_graph_stats()


@router.get("/memory")
async def get_memory_graph(
    request: Request,
    session_id: str | None = Query(None, description="Filter by session"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """Compliance subgraph shaped for the Neo4j Visualization Library."""
    neo4j_service = _get_neo4j_service(request)
    return await neo4j_service.get_memory_graph(session_id=session_id, limit=limit)
