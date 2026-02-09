"""FastAPI dependencies for the Neo4j Agent Memory server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient


async def get_memory_client(request: Request) -> "MemoryClient":
    """FastAPI dependency that retrieves the MemoryClient singleton.

    The client is stored on ``app.state`` during the lifespan context.

    Raises:
        HTTPException: 503 if the memory client is not available or not connected.
    """
    client: MemoryClient | None = getattr(request.app.state, "memory_client", None)
    if client is None or not client.is_connected:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    return client
