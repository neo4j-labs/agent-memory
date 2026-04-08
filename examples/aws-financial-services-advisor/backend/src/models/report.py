"""Report-related Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReportFormat(str, Enum):
    """Output format for reports."""

    JSON = "json"
    PDF = "pdf"
    HTML = "html"
    CSV = "csv"


class ReportStatus(str, Enum):
    """Report generation status."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class RiskFactor(BaseModel):
    """Individual risk factor in an assessment."""

    category: str = Field(..., description="Risk category")
    factor: str = Field(..., description="Specific risk factor")
    severity: str = Field(..., description="Factor severity")
    score: float = Field(..., ge=0, le=100, description="Factor score")
    description: str = Field(..., description="Detailed description")
    evidence: list[str] = Field(default_factory=list, description="Supporting evidence")
    mitigation: str | None = Field(default=None, description="Suggested mitigation")


class ReportRequest(BaseModel):
    """Request to generate a report."""

    customer_id: str = Field(..., description="Customer ID for the report")
    report_type: str = Field(..., description="Type of report to generate")
    format: ReportFormat = Field(default=ReportFormat.JSON, description="Output format")
    include_network: bool = Field(default=True, description="Include network analysis")
    include_transactions: bool = Field(
        default=True, description="Include transaction analysis"
    )
    date_range_days: int = Field(default=90, description="Days of history to include")


class SARReport(BaseModel):
    """Suspicious Activity Report (SAR) model."""

    id: str = Field(..., description="SAR identifier")
    investigation_id: str = Field(..., description="Related investigation ID")
    customer_id: str = Field(..., description="Subject customer ID")
    status: ReportStatus = Field(
        default=ReportStatus.PENDING, description="Report status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp"
    )
    filed_at: datetime | None = Field(default=None, description="Filing timestamp")
    filing_reference: str | None = Field(
        default=None, description="Regulatory filing reference"
    )

    # SAR-specific fields
    subject_name: str = Field(..., description="Subject of the report")
    subject_type: str = Field(..., description="Individual or entity")
    suspicious_activity_type: list[str] = Field(
        ..., description="Types of suspicious activity"
    )
    activity_date_range: dict[str, datetime] = Field(
        ..., description="Date range of activity"
    )
    total_amount_involved: float = Field(..., description="Total amount involved")
    currency: str = Field(default="USD", description="Primary currency")

    # Narrative sections
    summary: str = Field(..., description="Executive summary")
    activity_description: str = Field(
        ..., description="Description of suspicious activity"
    )
    subject_information: dict[str, Any] = Field(..., description="Subject details")
    account_information: list[dict[str, Any]] = Field(
        ..., description="Account details"
    )
    transaction_summary: list[dict[str, Any]] = Field(
        ..., description="Transaction summary"
    )

    # Supporting information
    supporting_documents: list[str] = Field(
        default_factory=list, description="Attached document IDs"
    )
    related_subjects: list[dict[str, Any]] = Field(
        default_factory=list, description="Related parties"
    )
    law_enforcement_contact: bool = Field(
        default=False, description="Whether law enforcement was contacted"
    )

    # Metadata
    prepared_by: str = Field(..., description="Preparer ID")
    reviewed_by: str | None = Field(default=None, description="Reviewer ID")
    approved_by: str | None = Field(default=None, description="Approver ID")


class RiskAssessmentReport(BaseModel):
    """Customer risk assessment report."""

    id: str = Field(..., description="Report identifier")
    customer_id: str = Field(..., description="Customer ID")
    status: ReportStatus = Field(
        default=ReportStatus.PENDING, description="Report status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp"
    )
    assessment_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Assessment date"
    )
    valid_until: datetime | None = Field(
        default=None, description="Assessment validity period"
    )

    # Risk scores
    overall_risk_score: float = Field(
        ..., ge=0, le=100, description="Overall risk score"
    )
    overall_risk_level: str = Field(..., description="Risk level category")
    previous_risk_score: float | None = Field(
        default=None, description="Previous assessment score"
    )
    score_change: float | None = Field(default=None, description="Change from previous")

    # Component scores
    identity_verification_score: float = Field(
        ..., description="Identity verification score"
    )
    geographic_risk_score: float = Field(..., description="Geographic risk score")
    product_risk_score: float = Field(..., description="Product/service risk score")
    transaction_risk_score: float = Field(
        ..., description="Transaction pattern risk score"
    )
    relationship_risk_score: float = Field(
        ..., description="Network/relationship risk score"
    )

    # Risk factors
    risk_factors: list[RiskFactor] = Field(..., description="Identified risk factors")
    mitigating_factors: list[str] = Field(
        default_factory=list, description="Mitigating factors"
    )

    # Analysis sections
    customer_profile: dict[str, Any] = Field(
        ..., description="Customer profile summary"
    )
    transaction_analysis: dict[str, Any] = Field(
        ..., description="Transaction analysis"
    )
    network_analysis: dict[str, Any] = Field(..., description="Network analysis")
    regulatory_status: dict[str, Any] = Field(
        ..., description="Regulatory compliance status"
    )

    # Recommendations
    risk_rating_recommendation: str = Field(..., description="Recommended risk rating")
    enhanced_due_diligence_required: bool = Field(
        ..., description="Whether EDD is required"
    )
    monitoring_recommendations: list[str] = Field(
        ..., description="Monitoring recommendations"
    )
    action_items: list[str] = Field(..., description="Required action items")

    # Metadata
    prepared_by: str = Field(..., description="Preparer ID")
    methodology_version: str = Field(
        default="1.0", description="Risk methodology version"
    )
