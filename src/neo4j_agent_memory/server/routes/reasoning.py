"""Reasoning memory endpoints: traces, steps, and tool calls."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.server.dependencies import get_memory_client
from neo4j_agent_memory.server.models import (
    AddStepRequest,
    CompleteTraceRequest,
    RecordToolCallRequest,
    SearchTracesRequest,
    StartTraceRequest,
    StepResponse,
    ToolCallResponse,
    ToolStatsResponse,
    TraceResponse,
)

router = APIRouter(tags=["reasoning"])


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


@router.get("/traces", response_model=list[TraceResponse])
async def list_traces(
    session_id: str | None = None,
    success_only: bool | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order_by: str = Query(default="started_at"),
    order_dir: str = Query(default="desc"),
    memory: MemoryClient = Depends(get_memory_client),
) -> list[TraceResponse]:
    """List reasoning traces with optional filtering."""
    traces = await memory.reasoning.list_traces(
        session_id=session_id,
        success_only=success_only,
        limit=limit,
        offset=offset,
        order_by=order_by,
        order_dir=order_dir,
    )
    return [TraceResponse.from_domain(t) for t in traces]


@router.post("/traces", response_model=TraceResponse, status_code=201)
async def start_trace(
    request: StartTraceRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> TraceResponse:
    """Start a new reasoning trace."""
    trace = await memory.reasoning.start_trace(
        session_id=request.session_id,
        task=request.task,
        metadata=request.metadata,
    )
    return TraceResponse.from_domain(trace)


@router.get("/traces/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: str,
    memory: MemoryClient = Depends(get_memory_client),
) -> TraceResponse:
    """Get a reasoning trace with all its steps and tool calls."""
    trace = await memory.reasoning.get_trace(UUID(trace_id))
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return TraceResponse.from_domain(trace)


@router.post("/traces/{trace_id}/complete", response_model=TraceResponse)
async def complete_trace(
    trace_id: str,
    request: CompleteTraceRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> TraceResponse:
    """Complete a reasoning trace with an outcome."""
    trace = await memory.reasoning.complete_trace(
        trace_id=UUID(trace_id),
        outcome=request.outcome,
        success=request.success,
    )
    return TraceResponse.from_domain(trace)


@router.post("/traces/{trace_id}/steps", response_model=StepResponse, status_code=201)
async def add_step(
    trace_id: str,
    request: AddStepRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> StepResponse:
    """Add a reasoning step to a trace."""
    step = await memory.reasoning.add_step(
        trace_id=UUID(trace_id),
        thought=request.thought,
        action=request.action,
        observation=request.observation,
    )
    return StepResponse.from_domain(step)


@router.post("/traces/search", response_model=list[TraceResponse])
async def search_traces(
    request: SearchTracesRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[TraceResponse]:
    """Find similar past reasoning traces."""
    traces = await memory.reasoning.get_similar_traces(
        task=request.query,
        limit=request.limit,
        success_only=request.success_only,
        threshold=request.threshold,
    )
    return [TraceResponse.from_domain(t) for t in traces]


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


@router.post("/tool-calls", response_model=ToolCallResponse, status_code=201)
async def record_tool_call(
    request: RecordToolCallRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> ToolCallResponse:
    """Record a tool call for a reasoning step."""
    # Map status string to enum
    try:
        status = ToolCallStatus(request.status)
    except ValueError:
        status = ToolCallStatus.SUCCESS

    tc = await memory.reasoning.record_tool_call(
        step_id=UUID(request.step_id),
        tool_name=request.tool_name,
        arguments=request.arguments,
        result=request.result,
        status=status,
        duration_ms=request.duration_ms,
        error=request.error,
    )
    return ToolCallResponse.from_domain(tc)


@router.get("/tool-stats", response_model=list[ToolStatsResponse])
async def get_tool_stats(
    tool_name: str | None = None,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[ToolStatsResponse]:
    """Get tool usage statistics."""
    stats = await memory.reasoning.get_tool_stats(tool_name=tool_name)
    return [ToolStatsResponse.from_domain(s) for s in stats]
