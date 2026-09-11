"""Reasoning trace retrieval endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ...services.memory_service import get_memory_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("/{session_id}")
async def get_session_traces(
    session_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get all reasoning traces for a session.

    Returns traces ordered by most recent first, with steps and tool calls.
    """
    try:
        memory_service = get_memory_service()
        reasoning = memory_service.client.reasoning

        # List traces for this session
        traces = await reasoning.list_traces(
            session_id=session_id,
            limit=limit,
        )

        result = []
        for trace in traces:
            # Get full trace with steps
            full_trace = await reasoning.get_trace(trace.id)
            if full_trace:
                trace_dict = {
                    "id": str(full_trace.id),
                    "session_id": session_id,
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
                            "id": str(s.id),
                            "step_number": s.step_number,
                            "thought": s.thought,
                            "action": s.action,
                            "observation": s.observation,
                            "tool_calls": [
                                {
                                    "tool_name": tc.tool_name,
                                    "arguments": tc.arguments,
                                    "result": tc.result,
                                    "status": tc.status.value
                                    if hasattr(tc.status, "value")
                                    else str(tc.status),
                                    "duration_ms": tc.duration_ms,
                                }
                                for tc in (s.tool_calls or [])
                            ],
                        }
                        for s in (full_trace.steps or [])
                    ],
                }
                result.append(trace_dict)

        return result

    except Exception as e:
        logger.error(f"Error fetching traces for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/detail/{trace_id}")
async def get_trace_detail(trace_id: str) -> dict[str, Any]:
    """Get a single reasoning trace by ID with full details."""
    try:
        memory_service = get_memory_service()
        reasoning = memory_service.client.reasoning

        trace = await reasoning.get_trace(trace_id)
        if not trace:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

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
                    "id": str(s.id),
                    "step_number": s.step_number,
                    "thought": s.thought,
                    "action": s.action,
                    "observation": s.observation,
                    "tool_calls": [
                        {
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "result": tc.result,
                            "status": tc.status.value
                            if hasattr(tc.status, "value")
                            else str(tc.status),
                            "duration_ms": tc.duration_ms,
                        }
                        for tc in (s.tool_calls or [])
                    ],
                }
                for s in (trace.steps or [])
            ],
            "metadata": trace.metadata,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching trace {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
