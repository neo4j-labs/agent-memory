"""Reasoning trace API routes for retrieving stored agent reasoning traces."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ...services.memory_service import (
    FinancialMemoryService,
    get_initialized_memory_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("/{session_id}")
async def get_session_traces(
    session_id: str,
    limit: int = Query(20, ge=1, le=100),
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> list[dict[str, Any]]:
    """Get reasoning traces for a session.

    Returns a list of traces with their steps and tool calls,
    ordered by most recent first.
    """
    try:
        reasoning = memory_service.client.reasoning
        traces = await reasoning.list_traces(
            session_id=session_id,
            limit=limit,
            order_dir="desc",
        )

        results = []
        for trace in traces:
            # Fetch full trace with steps and tool calls
            full_trace = await reasoning.get_trace(trace.id)
            if not full_trace:
                continue

            results.append(
                {
                    "id": str(full_trace.id),
                    "session_id": full_trace.session_id,
                    "task": full_trace.task,
                    "outcome": full_trace.outcome,
                    "success": full_trace.success,
                    "started_at": full_trace.started_at.isoformat()
                    if full_trace.started_at
                    else None,
                    "completed_at": full_trace.completed_at.isoformat()
                    if full_trace.completed_at
                    else None,
                    "steps": [
                        {
                            "id": str(step.id),
                            "step_number": step.step_number,
                            "thought": step.thought,
                            "action": step.action,
                            "observation": step.observation,
                            "tool_calls": [
                                {
                                    "id": str(tc.id),
                                    "tool_name": tc.tool_name,
                                    "arguments": tc.arguments,
                                    "result": tc.result,
                                    "status": tc.status.value if tc.status else None,
                                    "duration_ms": tc.duration_ms,
                                    "error": tc.error,
                                }
                                for tc in step.tool_calls
                            ],
                        }
                        for step in full_trace.steps
                    ],
                }
            )

        return results

    except Exception as e:
        logger.error("Error retrieving traces for session %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/detail/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> dict[str, Any]:
    """Get a single reasoning trace with full details."""
    try:
        reasoning = memory_service.client.reasoning
        trace = await reasoning.get_trace(UUID(trace_id))

        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")

        return {
            "id": str(trace.id),
            "session_id": trace.session_id,
            "task": trace.task,
            "outcome": trace.outcome,
            "success": trace.success,
            "started_at": trace.started_at.isoformat() if trace.started_at else None,
            "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
            "steps": [
                {
                    "id": str(step.id),
                    "step_number": step.step_number,
                    "thought": step.thought,
                    "action": step.action,
                    "observation": step.observation,
                    "tool_calls": [
                        {
                            "id": str(tc.id),
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "result": tc.result,
                            "status": tc.status.value if tc.status else None,
                            "duration_ms": tc.duration_ms,
                            "error": tc.error,
                        }
                        for tc in step.tool_calls
                    ],
                }
                for step in trace.steps
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrieving trace %s: %s", trace_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
