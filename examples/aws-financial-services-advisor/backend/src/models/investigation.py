"""Investigation-related Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InvestigationStatus(str, Enum):
    """Investigation status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_INFO = "awaiting_info"
    UNDER_REVIEW = "under_review"
    COMPLETED = "completed"
    CLOSED_NO_ACTION = "closed_no_action"
    ESCALATED = "escalated"


class FindingSeverity(str, Enum):
    """Severity of investigation finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InvestigationFinding(BaseModel):
    """Individual finding within an investigation."""

    id: str = Field(..., description="Finding identifier")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Finding timestamp"
    )
    agent: str = Field(..., description="Agent that produced the finding")
    category: str = Field(..., description="Finding category")
    severity: FindingSeverity = Field(..., description="Finding severity")
    title: str = Field(..., description="Finding title")
    description: str = Field(..., description="Detailed description")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Supporting evidence"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Recommended actions"
    )


class InvestigationCreate(BaseModel):
    """Model for creating a new investigation."""

    customer_id: str = Field(..., description="Customer being investigated")
    title: str = Field(..., description="Investigation title")
    description: str = Field(..., description="Investigation description")
    trigger: str = Field(..., description="What triggered the investigation")
    alert_ids: list[str] = Field(default_factory=list, description="Related alert IDs")
    priority: str = Field(default="medium", description="Investigation priority")
    assigned_to: str | None = Field(default=None, description="Assigned analyst")


class Investigation(InvestigationCreate):
    """Full investigation model."""

    id: str = Field(..., description="Unique investigation identifier")
    status: InvestigationStatus = Field(
        default=InvestigationStatus.PENDING, description="Current status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp"
    )
    started_at: datetime | None = Field(
        default=None, description="When investigation started"
    )
    completed_at: datetime | None = Field(
        default=None, description="When investigation completed"
    )
    findings: list[InvestigationFinding] = Field(
        default_factory=list, description="Investigation findings"
    )
    conclusion: str | None = Field(default=None, description="Final conclusion")
    recommended_actions: list[str] = Field(
        default_factory=list, description="Recommended follow-up actions"
    )
    sar_filed: bool = Field(default=False, description="Whether SAR was filed")
    sar_id: str | None = Field(default=None, description="SAR reference number")


class InvestigationWorkflow(BaseModel):
    """Investigation workflow state."""

    investigation_id: str = Field(..., description="Investigation identifier")
    current_step: str = Field(..., description="Current workflow step")
    completed_steps: list[str] = Field(
        default_factory=list, description="Completed workflow steps"
    )
    pending_steps: list[str] = Field(
        default_factory=list, description="Pending workflow steps"
    )
    agent_assignments: dict[str, str] = Field(
        default_factory=dict, description="Step to agent mapping"
    )
    step_results: dict[str, Any] = Field(
        default_factory=dict, description="Results from each step"
    )


class AuditTrailEntry(BaseModel):
    """Single entry in investigation audit trail."""

    id: str = Field(..., description="Entry identifier")
    timestamp: datetime = Field(..., description="Entry timestamp")
    action: str = Field(..., description="Action taken")
    actor: str = Field(..., description="Who performed the action (user or agent)")
    actor_type: str = Field(..., description="Type of actor (human/agent)")
    details: dict[str, Any] = Field(default_factory=dict, description="Action details")
    reasoning: str | None = Field(
        default=None, description="Reasoning behind the action"
    )


class InvestigationAuditTrail(BaseModel):
    """Full audit trail for an investigation."""

    investigation_id: str = Field(..., description="Investigation identifier")
    entries: list[AuditTrailEntry] = Field(
        default_factory=list, description="Audit trail entries"
    )
    agents_involved: list[str] = Field(
        default_factory=list, description="Agents that participated"
    )
    total_duration_seconds: int | None = Field(
        default=None, description="Total investigation duration"
    )
