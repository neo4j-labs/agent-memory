"""Customer-related Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CustomerType(str, Enum):
    """Type of customer."""

    INDIVIDUAL = "individual"
    CORPORATE = "corporate"
    TRUST = "trust"
    PARTNERSHIP = "partnership"


class RiskLevel(str, Enum):
    """Customer risk level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AccountType(str, Enum):
    """Type of bank account."""

    PERSONAL_CHECKING = "personal_checking"
    PERSONAL_SAVINGS = "personal_savings"
    BUSINESS_CHECKING = "business_checking"
    BUSINESS_SAVINGS = "business_savings"
    INVESTMENT = "investment"
    TRUST = "trust"


class Contact(BaseModel):
    """Contact person for a customer."""

    name: str = Field(..., description="Contact's full name")
    role: str = Field(..., description="Role/title of the contact")
    email: str | None = Field(default=None, description="Contact email")
    phone: str | None = Field(default=None, description="Contact phone number")
    pep_status: bool = Field(
        default=False, description="Politically Exposed Person status"
    )


class Account(BaseModel):
    """Bank account information."""

    id: str = Field(..., description="Unique account identifier")
    type: AccountType = Field(..., description="Type of account")
    currency: str = Field(default="USD", description="Account currency")
    status: str = Field(default="active", description="Account status")
    opened_date: datetime | None = Field(
        default=None, description="Account opening date"
    )
    balance: float | None = Field(default=None, description="Current balance")


class CustomerCreate(BaseModel):
    """Model for creating a new customer."""

    name: str = Field(..., description="Customer name")
    type: CustomerType = Field(..., description="Customer type")
    industry: str | None = Field(default=None, description="Industry sector")
    jurisdiction: str = Field(..., description="Primary jurisdiction (country code)")
    tax_id: str | None = Field(default=None, description="Tax identification number")
    contacts: list[Contact] = Field(default_factory=list, description="Contact persons")
    accounts: list[Account] = Field(
        default_factory=list, description="Associated accounts"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class Customer(CustomerCreate):
    """Full customer model with computed fields."""

    id: str = Field(..., description="Unique customer identifier")
    risk_level: RiskLevel = Field(
        default=RiskLevel.UNKNOWN, description="Current risk level"
    )
    onboarding_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Date customer was onboarded"
    )
    last_review_date: datetime | None = Field(
        default=None, description="Last KYC review date"
    )
    next_review_date: datetime | None = Field(
        default=None, description="Next scheduled review"
    )
    is_active: bool = Field(default=True, description="Whether customer is active")
    alerts_count: int = Field(default=0, description="Number of active alerts")


class CustomerRisk(BaseModel):
    """Customer risk assessment result."""

    customer_id: str = Field(..., description="Customer identifier")
    overall_risk: RiskLevel = Field(..., description="Overall risk level")
    risk_score: float = Field(
        ..., ge=0, le=100, description="Numeric risk score (0-100)"
    )
    geographic_risk: float = Field(
        ..., ge=0, le=100, description="Geographic risk component"
    )
    customer_type_risk: float = Field(
        ..., ge=0, le=100, description="Customer type risk"
    )
    transaction_risk: float = Field(
        ..., ge=0, le=100, description="Transaction pattern risk"
    )
    network_risk: float = Field(
        ..., ge=0, le=100, description="Network/relationship risk"
    )
    risk_factors: list[str] = Field(
        default_factory=list, description="Identified risk factors"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Risk mitigation recommendations"
    )
    assessment_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Assessment timestamp"
    )


class CustomerNetwork(BaseModel):
    """Customer relationship network."""

    customer_id: str = Field(..., description="Central customer identifier")
    depth: int = Field(default=2, description="Network traversal depth")
    nodes: list[dict[str, Any]] = Field(
        default_factory=list, description="Network nodes"
    )
    edges: list[dict[str, Any]] = Field(
        default_factory=list, description="Network edges"
    )
    risk_summary: dict[str, Any] = Field(
        default_factory=dict, description="Network risk summary"
    )
