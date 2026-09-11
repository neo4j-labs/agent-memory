"""Investigation API routes.

Investigations are **persisted in Neo4j**, not in a process-local dict: a
regulator-facing audit trail that disappears on restart (or differs per Cloud
Run instance) is the wrong lesson for this app. Reads and writes go through
:class:`Neo4jDomainService`.

The audit trail is a projection of the reasoning trace recorded while the
multi-agent run executed — the same ``ReasoningTrace``/``ReasoningStep``/
``ToolCall`` chain that ``/api/traces`` serves.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ...agents.supervisor import get_supervisor_agent
from ...models.investigation import (
    AuditTrailEntry,
    Investigation,
    InvestigationCreate,
    InvestigationStatus,
    InvestigationType,
)
from ...services.adk_events import collect_run
from ...services.memory_service import (
    FinancialMemoryService,
    get_initialized_memory_service,
)
from ...services.neo4j_service import Neo4jDomainService
from ...services.trace_writer import TraceWriter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/investigations", tags=["investigations"])

APP_NAME = "financial_advisor"

# ADK session service (agent state only — investigation state lives in Neo4j).
session_service = InMemorySessionService()


def _require_neo4j_service(request: Request) -> Neo4jDomainService:
    """Get the domain service from app state or fail with 503."""
    service = getattr(request.app.state, "neo4j_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Neo4j service not available — investigations are persisted in Neo4j.",
        )
    return service


def _as_datetime(value: Any) -> datetime | None:
    """Convert a Neo4j temporal (or ISO string) to a Python datetime."""
    if value is None:
        return None
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        native = to_native()
        return native if isinstance(native, datetime) else None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _enum_or_default(enum_cls: Any, value: Any, default: Any) -> Any:
    """Coerce a stored string into an enum member, falling back to a default."""
    if value is None:
        return default
    try:
        return enum_cls(str(value).lower())
    except ValueError:
        return default


def _to_investigation(record: dict[str, Any]) -> Investigation:
    """Map a persisted Investigation node onto the API model."""
    return Investigation(
        id=record["id"],
        customer_id=record.get("customer_id", ""),
        type=_enum_or_default(
            InvestigationType, record.get("type"), InvestigationType.COMPREHENSIVE
        ),
        reason=record.get("reason") or record.get("title") or record.get("trigger") or "",
        status=_enum_or_default(
            InvestigationStatus, record.get("status"), InvestigationStatus.PENDING
        ),
        priority=record.get("priority") or "normal",
        overall_risk_level=record.get("overall_risk_level"),
        summary=record.get("summary"),
        agents_consulted=list(record.get("agents_consulted") or []),
        assigned_to=record.get("assigned_to"),
        reviewed_by=record.get("reviewed_by"),
        created_at=_as_datetime(record.get("created_at")) or datetime.now(),
        started_at=_as_datetime(record.get("started_at")),
        completed_at=_as_datetime(record.get("completed_at")),
        session_id=record.get("session_id"),
    )


@router.get("", response_model=list[Investigation])
async def list_investigations(
    raw_request: Request,
    status: InvestigationStatus | None = Query(None),
    customer_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[Investigation]:
    """List persisted investigations with optional filtering."""
    neo4j_service = _require_neo4j_service(raw_request)
    records = await neo4j_service.list_investigations(
        status=status.value if status else None,
        customer_id=customer_id,
        limit=limit + offset,
    )
    return [_to_investigation(record) for record in records[offset : offset + limit]]


@router.post("", response_model=Investigation)
async def create_investigation(
    request: InvestigationCreate,
    raw_request: Request,
) -> Investigation:
    """Create a new investigation."""
    neo4j_service = _require_neo4j_service(raw_request)

    customer = await neo4j_service.get_customer(request.customer_id)
    if not customer:
        raise HTTPException(
            status_code=404,
            detail=f"Customer {request.customer_id} not found",
        )

    record = await neo4j_service.create_investigation(
        {
            "customer_id": request.customer_id,
            "title": request.reason,
            "reason": request.reason,
            "description": f"Type: {request.type.value}, Priority: {request.priority}",
            "trigger": request.reason,
            "type": request.type.value,
            "status": InvestigationStatus.PENDING.value,
            "priority": request.priority,
            "assigned_to": request.assigned_to,
        }
    )

    investigation_id = record["id"]
    session_id = f"inv-session-{investigation_id}"
    record = (
        await neo4j_service.update_investigation(investigation_id, {"session_id": session_id})
        or record
    )

    logger.info("Created investigation %s", investigation_id)
    return _to_investigation(record)


@router.get("/{investigation_id}", response_model=Investigation)
async def get_investigation(investigation_id: str, raw_request: Request) -> Investigation:
    """Get a specific investigation."""
    neo4j_service = _require_neo4j_service(raw_request)
    record = await neo4j_service.get_investigation(investigation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )
    return _to_investigation(record)


def _parse_risk_level(response_text: str) -> str:
    """Extract a risk level from the supervisor's summary.

    Word-boundary matching, so "FOLLOW" does not read as "LOW".
    """
    response_upper = response_text.upper()
    if re.search(r"\bCRITICAL\b", response_upper):
        return "CRITICAL"
    if re.search(r"\bHIGH\b", response_upper):
        return "HIGH"
    if re.search(r"\bLOW\b", response_upper):
        return "LOW"
    return "MEDIUM"


@router.post("/{investigation_id}/start")
async def start_investigation(
    investigation_id: str,
    raw_request: Request,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> dict[str, Any]:
    """Start a multi-agent investigation.

    Triggers the supervisor agent to orchestrate the investigation by
    delegating to the KYC, AML, Relationship and Compliance agents, and records
    the whole run as a reasoning trace attached to the investigation.
    """
    neo4j_service = _require_neo4j_service(raw_request)
    record = await neo4j_service.get_investigation(investigation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )

    investigation = _to_investigation(record)
    if investigation.status not in (
        InvestigationStatus.PENDING,
        InvestigationStatus.IN_PROGRESS,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Investigation is {investigation.status.value}, cannot start",
        )

    start_time = time.time()
    session_id = investigation.session_id or f"inv-session-{investigation_id}"
    await neo4j_service.update_investigation(
        investigation_id,
        {"status": InvestigationStatus.IN_PROGRESS.value, "session_id": session_id},
    )

    prompt = f"""Conduct a comprehensive {investigation.type.value} investigation for customer {investigation.customer_id}.

Reason for investigation: {investigation.reason}
Priority: {investigation.priority}

Please:
1. Delegate appropriate tasks to specialized agents (KYC, AML, Relationship, Compliance)
2. Gather and analyze all findings
3. Assess overall risk level
4. Provide specific recommendations
5. Document all steps for the audit trail

Begin the investigation now."""

    trace = TraceWriter(memory_service, session_id)

    try:
        supervisor = get_supervisor_agent(memory_service, neo4j_service=neo4j_service)
        await session_service.create_session(
            app_name=APP_NAME,
            user_id="investigator",
            session_id=session_id,
        )
        runner = Runner(
            agent=supervisor,
            app_name=APP_NAME,
            session_service=session_service,
            memory_service=memory_service.adk_memory_service,
        )

        stored = await memory_service.store_message(session_id, "user", prompt)
        await trace.start(
            f"{investigation.type.value} investigation of {investigation.customer_id}",
            triggered_by_message_id=stored.id,
        )

        try:
            summary = await collect_run(
                runner,
                user_id="investigator",
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
            )
        finally:
            await runner.close()

        for run_event in summary.events:
            await trace.handle(run_event)

        response_text = summary.response_text or "Investigation complete."
        await memory_service.store_message(session_id, "assistant", response_text)

        duration = time.time() - start_time
        risk_level = _parse_risk_level(response_text)
        await trace.complete(
            summary=response_text,
            success=True,
            duration_ms=int(duration * 1000),
        )

        await neo4j_service.update_investigation(
            investigation_id,
            {
                "status": InvestigationStatus.COMPLETED.value,
                "overall_risk_level": risk_level,
                "summary": response_text[:2000],
                "agents_consulted": summary.agents_consulted,
                "trace_id": str(trace.trace_id) if trace.trace_id else None,
            },
        )

        return {
            "investigation_id": investigation_id,
            "status": InvestigationStatus.COMPLETED.value,
            "overall_risk_level": risk_level,
            "summary": response_text[:2000],
            "agents_consulted": summary.agents_consulted,
            "tool_calls_count": len(summary.tool_calls),
            "trace_id": str(trace.trace_id) if trace.trace_id else None,
            "duration_seconds": round(duration, 2),
        }

    except Exception as exc:
        logger.error("Investigation error: %s", exc, exc_info=True)
        await trace.complete(
            summary=str(exc),
            success=False,
            duration_ms=int((time.time() - start_time) * 1000),
            error_kind=type(exc).__name__,
        )
        await neo4j_service.update_investigation(
            investigation_id,
            {"status": InvestigationStatus.ESCALATED.value, "summary": f"Error: {exc}"[:2000]},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{investigation_id}/audit-trail", response_model=list[AuditTrailEntry])
async def get_audit_trail(
    investigation_id: str,
    raw_request: Request,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> list[AuditTrailEntry]:
    """Get the audit trail for an investigation.

    Projected from the persisted reasoning trace: one entry per step, plus one
    per tool call, so the trail shows which agent used which tool and what it
    observed.
    """
    neo4j_service = _require_neo4j_service(raw_request)
    record = await neo4j_service.get_investigation(investigation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )

    entries: list[AuditTrailEntry] = [
        AuditTrailEntry(
            timestamp=_as_datetime(record.get("created_at")) or datetime.now(),
            action="CREATED",
            details=record.get("reason") or record.get("title"),
        )
    ]

    trace_id = record.get("trace_id")
    if not trace_id:
        return entries

    trace = await memory_service.client.reasoning.get_trace_with_steps(UUID(str(trace_id)))
    if trace is None:
        return entries

    entries.append(
        AuditTrailEntry(
            timestamp=trace.started_at or datetime.now(),
            action="STARTED",
            details=trace.task,
        )
    )
    for step in trace.steps:
        entries.append(
            AuditTrailEntry(
                timestamp=step.created_at,
                action="REASONING_STEP",
                details=step.thought or step.action,
                result_summary=step.observation,
            )
        )
        for tool_call in step.tool_calls:
            entries.append(
                AuditTrailEntry(
                    timestamp=tool_call.created_at,
                    action="TOOL_CALL",
                    agent=step.action,
                    tool_used=tool_call.tool_name,
                    details=f"Called {tool_call.tool_name}",
                    result_summary=str(tool_call.result)[:300] if tool_call.result else None,
                )
            )

    if trace.completed_at:
        entries.append(
            AuditTrailEntry(
                timestamp=trace.completed_at,
                action="COMPLETED" if trace.success else "ERROR",
                details=trace.outcome,
            )
        )

    return entries
