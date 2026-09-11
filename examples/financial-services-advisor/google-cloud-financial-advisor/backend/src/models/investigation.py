"""Investigation models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class InvestigationStatus(str, Enum):
    """Investigation status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    CLOSED = "closed"


class InvestigationType(str, Enum):
    """Type of investigation."""

    KYC_REVIEW = "kyc_review"
    AML_INVESTIGATION = "aml_investigation"
    FRAUD_INVESTIGATION = "fraud_investigation"
    SANCTIONS_REVIEW = "sanctions_review"
    COMPREHENSIVE = "comprehensive"


class InvestigationCreate(BaseModel):
    """Request model for creating an investigation."""

    customer_id: str = Field(..., description="Customer to investigate")
    type: InvestigationType = Field(
        default=InvestigationType.COMPREHENSIVE,
        description="Type of investigation",
    )
    reason: str = Field(..., description="Reason for investigation")
    priority: str = Field(default="normal", description="Priority level")
    assigned_to: str | None = Field(None, description="Assigned analyst")


class AgentFinding(BaseModel):
    """Finding from a specialized agent."""

    agent: str = Field(..., description="Agent that produced the finding")
    finding_type: str = Field(..., description="Type of finding")
    content: str = Field(..., description="Finding content")
    risk_level: str = Field(default="MEDIUM")
    confidence: float = Field(default=0.8, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)


class AuditTrailEntry(BaseModel):
    """Entry in the investigation audit trail."""

    timestamp: datetime = Field(default_factory=datetime.now)
    action: str = Field(..., description="Action taken")
    agent: str | None = Field(None, description="Agent that took action")
    details: str | None = Field(None, description="Action details")
    tool_used: str | None = Field(None, description="Tool used")
    result_summary: str | None = Field(None, description="Result summary")


class Investigation(BaseModel):
    """Full investigation model."""

    id: str = Field(..., description="Investigation identifier")
    customer_id: str = Field(..., description="Customer being investigated")
    type: InvestigationType = Field(..., description="Investigation type")
    reason: str = Field(..., description="Reason for investigation")
    status: InvestigationStatus = Field(default=InvestigationStatus.PENDING)
    priority: str = Field(default="normal")

    # Risk assessment
    overall_risk_level: str | None = Field(None)
    risk_score: int | None = Field(None, ge=0, le=100)

    # Findings
    findings: list[AgentFinding] = Field(default_factory=list)
    summary: str | None = Field(None, description="Investigation summary")
    recommendations: list[str] = Field(default_factory=list)

    # Audit trail
    audit_trail: list[AuditTrailEntry] = Field(default_factory=list)

    # Agents consulted
    agents_consulted: list[str] = Field(default_factory=list)

    # Assignment
    assigned_to: str | None = Field(None)
    reviewed_by: str | None = Field(None)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = Field(None)
    completed_at: datetime | None = Field(None)

    # Session for conversation tracking
    session_id: str | None = Field(None)

    model_config = ConfigDict(from_attributes=True)


class InvestigationResult(BaseModel):
    """Result of running an investigation."""

    investigation_id: str
    status: InvestigationStatus
    overall_risk_level: str
    risk_score: int
    summary: str
    findings_by_agent: dict[str, list[AgentFinding]]
    recommendations: list[str]
    audit_trail: list[AuditTrailEntry]
    duration_seconds: float
