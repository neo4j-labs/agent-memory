"""HTTP API: routes under ``/api`` and their request/response schemas."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from neo4j_agent_memory import BoltMemoryClient
from src.memory import MemoryService, MemoryUnavailableError


def memory_service(request: Request) -> MemoryService:
    """The app's :class:`MemoryService` (created in the lifespan)."""
    service: MemoryService = request.app.state.memory
    return service


async def memory_client(request: Request) -> BoltMemoryClient:
    """The current client, read per request (it changes on reconnect). 503 when down.

    If Neo4j was unreachable at startup, every request tries to connect again.
    """
    try:
        return await memory_service(request).get_client()
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j is unavailable: {exc}") from exc


#: A route parameter typed ``client: MemoryClientDep`` receives the current client.
MemoryClientDep = Annotated[BoltMemoryClient, Depends(memory_client)]
