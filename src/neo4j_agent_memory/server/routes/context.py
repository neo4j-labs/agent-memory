"""Unified context and stats endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.server.dependencies import get_memory_client
from neo4j_agent_memory.server.models import (
    ContextRequest,
    ContextResponse,
    EntityResponse,
    MessageResponse,
    PreferenceResponse,
    StatsResponse,
    TraceResponse,
)

router = APIRouter(tags=["context"])


@router.post("/context", response_model=ContextResponse)
async def get_context(
    request: ContextRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> ContextResponse:
    """Get assembled context from all three memory types.

    Returns both a pre-formatted text string suitable for LLM prompt injection
    and the structured data (messages, entities, preferences, traces) that
    composed it.
    """
    result = await memory.get_context_structured(
        query=request.query,
        session_id=request.session_id,
        include_short_term=request.include_short_term,
        include_long_term=request.include_long_term,
        include_reasoning=request.include_reasoning,
        max_items=request.max_items,
    )
    return ContextResponse(
        context_text=result.context_text,
        messages=[MessageResponse.from_domain(m) for m in result.messages],
        entities=[EntityResponse.from_domain(e) for e in result.entities],
        preferences=[PreferenceResponse.from_domain(p) for p in result.preferences],
        traces=[TraceResponse.from_domain(t) for t in result.traces],
        stats=result.stats,
    )


@router.post("/context/text")
async def get_context_text(
    request: ContextRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> PlainTextResponse:
    """Get context as plain text only, suitable for direct prompt injection."""
    text = await memory.get_context(
        query=request.query,
        session_id=request.session_id,
        include_short_term=request.include_short_term,
        include_long_term=request.include_long_term,
        include_reasoning=request.include_reasoning,
        max_items=request.max_items,
    )
    return PlainTextResponse(content=text)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    memory: MemoryClient = Depends(get_memory_client),
) -> StatsResponse:
    """Get memory statistics (counts for each memory type)."""
    stats = await memory.get_stats()
    return StatsResponse(**stats)
