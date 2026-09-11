"""Investigation API routes.

Investigations *and* the link to the reasoning trace that produced them are
persisted to Neo4j: ``(:Investigation)-[:HAS_TRACE]->(:ReasoningTrace)``. A
process-local mapping dict would vanish on reload and would be per-instance
under the documented Lambda deployment, so a SAR written by one request would
be invisible to the next one.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...agents import get_supervisor_agent
from ...services.memory_service import get_memory_service
from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/investigations", tags=["investigations"])


def _get_neo4j_service(request: Request) -> Neo4jDomainService:
    svc = getattr(request.app.state, "neo4j_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, svc)


class InvestigationCreateRequest(BaseModel):
    customer_id: str
    title: str
    description: str = ""
    trigger: str = ""
    priority: str = "MEDIUM"


class StartInvestigationRequest(BaseModel):
    run_kyc: bool = Field(default=True)
    run_aml: bool = Field(default=True)
    run_relationship: bool = Field(default=True)
    run_compliance: bool = Field(default=True)
    time_period_days: int = Field(default=90)


class CompleteInvestigationRequest(BaseModel):
    conclusion: str
    recommended_actions: list[str] = Field(default_factory=list)
    file_sar: bool = False


def _session_id(investigation_id: str) -> str:
    return f"investigation-{investigation_id}"


@router.get("")
async def list_investigations(
    request: Request,
    status: str | None = Query(None),
    customer_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """List all investigations from Neo4j."""
    neo4j_service = _get_neo4j_service(request)
    return await neo4j_service.list_investigations(
        status=status, customer_id=customer_id, limit=limit
    )


@router.post("", status_code=201)
async def create_investigation(
    body: InvestigationCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Create a new investigation and open its reasoning trace."""
    neo4j_service = _get_neo4j_service(request)

    customer = await neo4j_service.get_customer(body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {body.customer_id} not found")

    investigation_id = f"INV-{uuid.uuid4().hex[:8].upper()}"
    investigation = await neo4j_service.create_investigation(
        {
            "id": investigation_id,
            "customer_id": body.customer_id,
            "title": body.title,
            "description": body.description,
            "trigger": body.trigger,
            "priority": body.priority,
        }
    )

    try:
        trace_id = await get_memory_service().start_investigation_trace(
            session_id=_session_id(investigation_id),
            task=f"Investigation: {body.title}",
        )
        await neo4j_service.link_investigation_to_trace(investigation_id, trace_id)
        investigation["trace_id"] = trace_id
    except Exception as exc:
        logger.error("Failed to open reasoning trace for %s: %s", investigation_id, exc)

    return investigation


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, request: Request) -> dict[str, Any]:
    """Get investigation details, including any linked trace ids."""
    neo4j_service = _get_neo4j_service(request)
    investigation = await neo4j_service.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return investigation


@router.post("/{investigation_id}/start")
async def start_investigation(
    investigation_id: str,
    body: StartInvestigationRequest,
    request: Request,
) -> dict[str, Any]:
    """Run the multi-agent investigation."""
    neo4j_service = _get_neo4j_service(request)
    memory_service = get_memory_service()

    investigation = await neo4j_service.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    await neo4j_service.update_investigation(investigation_id, {"status": "IN_PROGRESS"})

    requested = [
        label
        for label, enabled in [
            ("KYC verification", body.run_kyc),
            ("AML transaction analysis", body.run_aml),
            ("relationship network analysis", body.run_relationship),
            ("compliance screening", body.run_compliance),
        ]
        if enabled
    ]
    prompt = (
        f"Investigate customer {investigation.get('customer_id', 'unknown')}.\n"
        f"Title: {investigation.get('title', '')}\n"
        f"Perform: {', '.join(requested)}\n"
        f"Period: Last {body.time_period_days} days"
    )

    try:
        supervisor = get_supervisor_agent(neo4j_service, _session_id(investigation_id))
        result = await supervisor.invoke_async(prompt)
        response_text = str(result)

        await neo4j_service.update_investigation(
            investigation_id, {"status": "COMPLETED", "summary": response_text[:2000]}
        )

        for trace_id in investigation.get("trace_ids") or []:
            await memory_service.add_reasoning_step(
                trace_id=trace_id,
                agent="supervisor",
                action="conduct_investigation",
                reasoning=f"Agents: {', '.join(requested)}",
                result={"response_length": len(response_text)},
            )

        return {
            "investigation_id": investigation_id,
            "status": "COMPLETED",
            "agents_invoked": requested,
            "preliminary_response": response_text[:1000],
        }
    except Exception as exc:
        logger.error("Investigation error: %s", exc)
        await neo4j_service.update_investigation(investigation_id, {"status": "PENDING"})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{investigation_id}/audit-trail")
async def get_audit_trail(investigation_id: str, request: Request) -> dict[str, Any]:
    """Reasoning audit trail for an investigation.

    Two views of the same traces: the full step/tool-call detail, and the
    one-hop ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` projection that answers
    "which entities did this investigation act on, through which tool?".
    """
    neo4j_service = _get_neo4j_service(request)
    memory_service = get_memory_service()

    investigation = await neo4j_service.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    trace_ids = investigation.get("trace_ids") or []
    if not trace_ids:
        return {
            "investigation_id": investigation_id,
            "trace_available": False,
            "traces": [],
            "touched_entities": [],
        }

    try:
        traces = [await memory_service.get_investigation_trace(tid) for tid in trace_ids]
        touched: list[dict[str, Any]] = []
        for trace_id in trace_ids:
            touched.extend(await memory_service.audit_trail(trace_id))
        return {
            "investigation_id": investigation_id,
            "trace_available": True,
            "traces": [t for t in traces if t],
            "touched_entities": touched,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{investigation_id}/complete")
async def complete_investigation(
    investigation_id: str,
    body: CompleteInvestigationRequest,
    request: Request,
) -> dict[str, Any]:
    """Complete an investigation with a conclusion, closing its traces."""
    neo4j_service = _get_neo4j_service(request)
    investigation = await neo4j_service.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    updated = await neo4j_service.update_investigation(
        investigation_id, {"status": "COMPLETED", "conclusion": body.conclusion}
    )

    memory_service = get_memory_service()
    for trace_id in investigation.get("trace_ids") or []:
        try:
            await memory_service.complete_investigation_trace(
                trace_id,
                conclusion=body.conclusion,
                success=True,
                metrics={"recommended_actions": float(len(body.recommended_actions))},
            )
        except Exception as exc:
            logger.error("Error completing trace %s: %s", trace_id, exc)

    return updated or investigation
