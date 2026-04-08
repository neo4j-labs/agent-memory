"""Alert-related Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AlertType(str, Enum):
    """Type of compliance alert."""

    SUSPICIOUS_TRANSACTION = "suspicious_transaction"
    SANCTIONS_MATCH = "sanctions_match"
    PEP_MATCH = "pep_match"
    ADVERSE_MEDIA = "adverse_media"
    UNUSUAL_PATTERN = "unusual_pattern"
    HIGH_RISK_JURISDICTION = "high_risk_jurisdiction"
    STRUCTURING = "structuring"
    VELOCITY_BREACH = "velocity_breach"
    NETWORK_RISK = "network_risk"
    KYC_EXPIRY = "kyc_expiry"


class AlertSeverity(str, Enum):
    """Alert severity level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """Alert processing status."""

    NEW = "new"
    UNDER_REVIEW = "under_review"
    ESCALATED = "escalated"
    ACKNOWLEDGED = "acknowledged"
    FALSE_POSITIVE = "false_positive"
    CLOSED = "closed"


class AlertCreate(BaseModel):
    """Model for creating a new alert."""

    type: AlertType = Field(..., description="Alert type")
    severity: AlertSeverity = Field(..., description="Alert severity")
    customer_id: str = Field(..., description="Related customer ID")
    title: str = Field(..., description="Alert title")
    description: str = Field(..., description="Detailed description")
    transaction_ids: list[str] = Field(
        default_factory=list, description="Related transactions"
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Supporting evidence"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class Alert(AlertCreate):
    """Full alert model."""

    id: str = Field(..., description="Unique alert identifier")
    status: AlertStatus = Field(default=AlertStatus.NEW, description="Current status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Alert creation time"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Last update time"
    )
    assigned_to: str | None = Field(default=None, description="Assigned analyst")
    investigation_id: str | None = Field(
        default=None, description="Linked investigation"
    )
    resolution: str | None = Field(default=None, description="Resolution notes")
    resolved_at: datetime | None = Field(
        default=None, description="Resolution timestamp"
    )
    resolved_by: str | None = Field(default=None, description="Who resolved the alert")


class AlertAcknowledge(BaseModel):
    """Model for acknowledging an alert."""

    analyst_id: str = Field(..., description="Analyst ID")
    notes: str | None = Field(default=None, description="Acknowledgment notes")


class AlertClose(BaseModel):
    """Model for closing an alert."""

    analyst_id: str = Field(..., description="Analyst ID")
    resolution: str = Field(..., description="Resolution notes")
    is_false_positive: bool = Field(default=False, description="Mark as false positive")


class AlertSummary(BaseModel):
    """Summary statistics for alerts."""

    total_count: int = Field(..., description="Total alert count")
    by_status: dict[str, int] = Field(
        default_factory=dict, description="Counts by status"
    )
    by_severity: dict[str, int] = Field(
        default_factory=dict, description="Counts by severity"
    )
    by_type: dict[str, int] = Field(default_factory=dict, description="Counts by type")
    avg_resolution_time_hours: float | None = Field(
        default=None, description="Average resolution time"
    )
    oldest_unresolved: datetime | None = Field(
        default=None, description="Oldest unresolved alert timestamp"
    )
