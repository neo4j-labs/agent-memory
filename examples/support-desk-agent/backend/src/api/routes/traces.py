"""The Reasoning panel: traces, their steps and tool calls, similar tasks, tool stats.

Reasoning memory records *how* the agent worked: one ``(:ReasoningTrace)`` per
turn, a ``(:ReasoningStep)`` per tool call with its ``(:ToolCall)``, and
``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges for the entities a call named.
Seeded traces (``metadata.seeded``) model earlier agent work and are flagged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from neo4j_agent_memory import BoltMemoryClient
from src.api import MemoryClientDep
from src.api.schemas import (
    SimilarTrace,
    StepView,
    ToolCallView,
    ToolStat,
    TouchedEntity,
    TraceDetail,
    TraceSummary,
)
from src.memory import iso, jsonable, parse_json_map
from src.ontology import entity_labels

router = APIRouter()

#: Similarity floor for the "Similar past tasks" box (MiniLM cosine).
SIMILAR_THRESHOLD = 0.5

#: What ``list_traces()`` leaves out: counts, the initiating message, metadata.
TRACE_FACTS = """
MATCH (rt:ReasoningTrace) WHERE rt.id IN $ids
OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
OPTIONAL MATCH (rt)-[:INITIATED_BY]->(m:Message)
RETURN rt.id AS id, properties(rt).metadata AS metadata,
       properties(rt).metrics_json AS metrics_json,
       count(DISTINCT s) AS step_count, count(DISTINCT tc) AS tool_call_count,
       head(collect(DISTINCT m.id)) AS message_id
"""

STEP_TOUCHED = """
MATCH (s:ReasoningStep)-[:TOUCHED]->(e:Entity)
WHERE s.id IN $ids
RETURN s.id AS step_id, e.id AS id, e.name AS name, labels(e) AS labels
ORDER BY e.name
"""


async def _facts(client: BoltMemoryClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    return {row["id"]: row for row in await client.query.cypher(TRACE_FACTS, {"ids": ids})}


def _seeded(facts: dict[str, Any] | None) -> bool:
    return bool(parse_json_map((facts or {}).get("metadata")).get("seeded"))


@router.get("/traces", response_model=list[TraceSummary])
async def list_traces(
    client: MemoryClientDep, thread_id: str | None = Query(default=None)
) -> list[TraceSummary]:
    """A thread's traces (or the latest 50 overall), newest first."""
    traces = await client.reasoning.list_traces(
        session_id=thread_id or None, limit=100 if thread_id else 50
    )
    facts = await _facts(client, [str(trace.id) for trace in traces])
    summaries: list[TraceSummary] = []
    for trace in traces:
        row = facts.get(str(trace.id), {})
        summaries.append(
            TraceSummary(
                id=str(trace.id),
                task=trace.task,
                started_at=iso(trace.started_at),
                completed_at=iso(trace.completed_at),
                success=trace.success,
                outcome_summary=trace.outcome,
                step_count=int(row.get("step_count") or 0),
                tool_call_count=int(row.get("tool_call_count") or 0),
                message_id=row.get("message_id"),
                seeded=_seeded(row),
            )
        )
    return summaries


@router.get("/traces/similar", response_model=list[SimilarTrace])
async def similar_traces(
    client: MemoryClientDep,
    task: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    exclude_id: str | None = Query(default=None, description="Leave this trace out"),
) -> list[SimilarTrace]:
    """Earlier traces whose task reads like ``task`` (vector search on the task)."""
    traces = await client.reasoning.get_similar_traces(
        task,
        limit=limit + (1 if exclude_id else 0),
        success_only=False,
        threshold=SIMILAR_THRESHOLD,
    )
    traces = [trace for trace in traces if str(trace.id) != exclude_id][:limit]
    facts = await _facts(client, [str(trace.id) for trace in traces])
    return [
        SimilarTrace(
            id=str(trace.id),
            task=trace.task,
            success=trace.success,
            outcome_summary=trace.outcome,
            seeded=_seeded(facts.get(str(trace.id))),
        )
        for trace in traces
    ]


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(client: MemoryClientDep, trace_id: str) -> TraceDetail:
    """One trace with its steps, tool calls and the entities each step touched."""
    try:
        trace = await client.reasoning.get_trace_with_steps(UUID(trace_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found") from exc
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
    facts = (await _facts(client, [trace_id])).get(trace_id, {})
    step_ids = [str(step.id) for step in trace.steps]
    touched: dict[str, list[TouchedEntity]] = {}
    for row in await client.query.cypher(STEP_TOUCHED, {"ids": step_ids}):
        touched.setdefault(row["step_id"], []).append(
            TouchedEntity(id=row["id"], name=row.get("name"), labels=entity_labels(row["labels"]))
        )
    return TraceDetail(
        id=trace_id,
        task=trace.task,
        success=trace.success,
        outcome_summary=trace.outcome,
        metrics=parse_json_map(facts.get("metrics_json")),
        seeded=_seeded(facts),
        steps=[
            StepView(
                id=str(step.id),
                thought=step.thought,
                action=step.action,
                observation=step.observation,
                tool_calls=[
                    ToolCallView(
                        id=str(call.id),
                        tool_name=call.tool_name,
                        arguments=jsonable(call.arguments) or {},
                        result=jsonable(call.result),
                        status=str(getattr(call.status, "value", call.status)),
                        duration_ms=call.duration_ms,
                        # TOUCHED edges hang off the step; each step records one call.
                        touched=touched.get(str(step.id), []),
                    )
                    for call in step.tool_calls
                ],
            )
            for step in trace.steps
        ],
    )


@router.get("/tool-stats", response_model=list[ToolStat])
async def tool_stats(client: MemoryClientDep) -> list[ToolStat]:
    """Calls, success rate and mean duration per tool (pre-aggregated on ``:Tool``)."""
    return [
        ToolStat(
            name=stat.name,
            calls=stat.total_calls,
            success_rate=stat.success_rate,
            avg_duration_ms=stat.avg_duration_ms,
        )
        for stat in await client.reasoning.get_tool_stats()
    ]
