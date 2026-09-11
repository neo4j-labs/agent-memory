"""Alert API routes.

All alert data is queried from Neo4j via the Neo4jDomainService.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


def _get_neo4j_service(request: Request) -> Neo4jDomainService:
    svc = getattr(request.app.state, "neo4j_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, svc)


def _to_python_datetime(value: Any) -> datetime | None:
    """Accept an ISO string, a Python datetime, or a Neo4j DateTime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        native = to_native()
        return native if isinstance(native, datetime) else None
    return None


class AlertResponse(BaseModel):
    id: str
    customer_id: str = ""
    customer_name: str | None = None
    type: str = "AML"
    severity: str = "MEDIUM"
    status: str = "NEW"
    title: str = ""
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    requires_sar: bool = False
    auto_generated: bool = False
    created_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class AlertSummaryResponse(BaseModel):
    total: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    critical_unresolved: int = 0
    high_unresolved: int = 0


class AlertCreateRequest(BaseModel):
    customer_id: str
    type: str = "AML"
    severity: str = "MEDIUM"
    title: str
    description: str = ""
    evidence: list[str] = Field(default_factory=list)


class AlertUpdateRequest(BaseModel):
    status: str | None = None
    severity: str | None = None
    assigned_to: str | None = None
    resolution_notes: str | None = None


def _alert_from_dict(data: dict) -> AlertResponse:
    return AlertResponse(
        id=data.get("id", ""),
        customer_id=data.get("customer_id", ""),
        customer_name=data.get("customer_name"),
        type=data.get("type", "AML"),
        severity=(data.get("severity") or "MEDIUM").upper(),
        status=(data.get("status") or "NEW").upper(),
        title=data.get("title", ""),
        description=data.get("description", ""),
        evidence=data.get("evidence") or [],
        requires_sar=data.get("requires_sar", False),
        auto_generated=data.get("auto_generated", False),
        created_at=_to_python_datetime(data.get("created_at")),
        acknowledged_at=_to_python_datetime(data.get("acknowledged_at")),
        resolved_at=_to_python_datetime(data.get("resolved_at")),
    )


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    request: Request,
    status: str | None = Query(None),
    severity: str | None = Query(None),
    alert_type: str | None = Query(None),
    customer_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[AlertResponse]:
    """List alerts with optional filtering."""
    neo4j_service = _get_neo4j_service(request)
    rows = await neo4j_service.list_alerts(
        status=status.upper() if status else None,
        severity=severity.upper() if severity else None,
        alert_type=alert_type.upper() if alert_type else None,
        customer_id=customer_id,
        limit=limit,
        offset=offset,
    )
    return [_alert_from_dict(r) for r in rows]


@router.get("/summary", response_model=AlertSummaryResponse)
async def get_alert_summary(request: Request) -> AlertSummaryResponse:
    """Get summary statistics for alerts."""
    neo4j_service = _get_neo4j_service(request)
    summary = await neo4j_service.get_alert_summary()
    return AlertSummaryResponse(
        total=summary.get("total", 0),
        by_severity=summary.get("by_severity", {}),
        by_status=summary.get("by_status", {}),
        critical_unresolved=summary.get("critical_unresolved", 0),
        high_unresolved=summary.get("high_unresolved", 0),
    )


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(request: Request, alert_id: str) -> AlertResponse:
    """Get a specific alert."""
    neo4j_service = _get_neo4j_service(request)
    data = await neo4j_service.get_alert(alert_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return _alert_from_dict(data)


@router.post("", response_model=AlertResponse)
async def create_alert(request: Request, body: AlertCreateRequest) -> AlertResponse:
    """Create a new alert."""
    neo4j_service = _get_neo4j_service(request)

    customer = await neo4j_service.get_customer(body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {body.customer_id} not found")

    alert_id = f"ALERT-{uuid.uuid4().hex[:6].upper()}"
    data = await neo4j_service.create_alert(
        {
            "id": alert_id,
            "customer_id": body.customer_id,
            "type": body.type.upper(),
            "severity": body.severity.upper(),
            "status": "NEW",
            "title": body.title,
            "description": body.description,
            "evidence": body.evidence,
            "requires_sar": body.severity.upper() in ["CRITICAL", "HIGH"],
            "auto_generated": False,
        }
    )
    return _alert_from_dict(data)


@router.patch("/{alert_id}", response_model=AlertResponse)
async def update_alert(request: Request, alert_id: str, body: AlertUpdateRequest) -> AlertResponse:
    """Update an alert."""
    neo4j_service = _get_neo4j_service(request)

    existing = await neo4j_service.get_alert(alert_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")

    updates = {}
    if body.status:
        updates["status"] = body.status.upper()
    if body.severity:
        updates["severity"] = body.severity.upper()
    if body.assigned_to:
        updates["assigned_to"] = body.assigned_to
    if body.resolution_notes:
        updates["resolution_notes"] = body.resolution_notes

    if updates:
        data = await neo4j_service.update_alert(alert_id, updates)
    else:
        data = existing

    if not data:
        raise HTTPException(status_code=500, detail="Failed to update alert")
    return _alert_from_dict(data)
