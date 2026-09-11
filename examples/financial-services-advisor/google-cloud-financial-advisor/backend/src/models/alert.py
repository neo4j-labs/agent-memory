"""Alert models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertSeverity(str, Enum):
    """Alert severity level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertType(str, Enum):
    """Type of alert."""

    TRANSACTION = "transaction"
    KYC = "kyc"
    AML = "aml"
    SANCTIONS = "sanctions"
    PEP = "pep"
    NETWORK = "network"
    COMPLIANCE = "compliance"


class AlertStatus(str, Enum):
    """Alert status."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class AlertCreate(BaseModel):
    """Request model for creating an alert."""

    customer_id: str = Field(..., description="Related customer")
    type: AlertType = Field(..., description="Alert type")
    severity: AlertSeverity = Field(..., description="Alert severity")
    title: str = Field(..., description="Alert title")
    description: str = Field(..., description="Alert description")
    transaction_id: str | None = Field(None, description="Related transaction")
    evidence: list[str] = Field(default_factory=list)


class Alert(BaseModel):
    """Full alert model."""

    id: str = Field(..., description="Alert identifier")
    customer_id: str = Field(..., description="Related customer")
    customer_name: str | None = Field(None)
    type: AlertType = Field(..., description="Alert type")
    severity: AlertSeverity = Field(..., description="Alert severity")
    status: AlertStatus = Field(default=AlertStatus.NEW)

    title: str = Field(..., description="Alert title")
    description: str = Field(..., description="Alert description")

    # Related entities
    transaction_id: str | None = Field(None)
    investigation_id: str | None = Field(None)

    # Evidence and notes
    evidence: list[str] = Field(default_factory=list)
    notes: list[dict[str, Any]] = Field(default_factory=list)

    # Assignment
    assigned_to: str | None = Field(None)
    resolved_by: str | None = Field(None)
    resolution_notes: str | None = Field(None)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    acknowledged_at: datetime | None = Field(None)
    resolved_at: datetime | None = Field(None)

    # Auto-generated
    requires_sar: bool = Field(default=False)
    auto_generated: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)


class AlertUpdate(BaseModel):
    """Request model for updating an alert."""

    status: AlertStatus | None = Field(None)
    severity: AlertSeverity | None = Field(None)
    assigned_to: str | None = Field(None)
    notes: str | None = Field(None)
    resolution_notes: str | None = Field(None)


class AlertSummary(BaseModel):
    """Summary of alerts for dashboard."""

    total: int = Field(default=0)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    critical_unresolved: int = Field(default=0)
    high_unresolved: int = Field(default=0)
