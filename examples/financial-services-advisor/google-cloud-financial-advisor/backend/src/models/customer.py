"""Customer models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CustomerType(str, Enum):
    """Type of customer."""

    INDIVIDUAL = "individual"
    CORPORATE = "corporate"


class RiskLevel(str, Enum):
    """Customer risk level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DocumentStatus(str, Enum):
    """Document verification status."""

    VERIFIED = "verified"
    PENDING = "pending"
    MISSING = "missing"
    EXPIRED = "expired"
    REJECTED = "rejected"


class Document(BaseModel):
    """Customer document."""

    type: str = Field(..., description="Document type (passport, utility_bill, etc.)")
    status: DocumentStatus = Field(..., description="Verification status")
    expiry_date: datetime | None = Field(None, description="Document expiry date")
    submission_date: datetime | None = Field(None, description="Date submitted")


class CustomerBase(BaseModel):
    """Base customer fields."""

    name: str = Field(..., description="Customer name")
    type: CustomerType = Field(..., description="Individual or corporate")
    email: str | None = Field(None, description="Contact email")
    phone: str | None = Field(None, description="Contact phone")


class CustomerCreate(CustomerBase):
    """Request model for creating a customer."""

    nationality: str | None = Field(None, description="Customer nationality (ISO code)")
    address: str | None = Field(None, description="Primary address")
    occupation: str | None = Field(None, description="Occupation or business type")
    employer: str | None = Field(None, description="Employer name")
    # Corporate fields
    jurisdiction: str | None = Field(None, description="Incorporation jurisdiction")
    business_type: str | None = Field(None, description="Type of business")


class Customer(CustomerBase):
    """Full customer model."""

    id: str = Field(..., description="Customer identifier")
    nationality: str | None = Field(None)
    address: str | None = Field(None)
    occupation: str | None = Field(None)
    employer: str | None = Field(None)
    jurisdiction: str | None = Field(None)
    business_type: str | None = Field(None)

    # Status fields
    kyc_status: str = Field(default="pending", description="KYC verification status")
    risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM)
    risk_score: int = Field(default=0, ge=0, le=100)

    # Documents
    documents: list[Document] = Field(default_factory=list)

    # Risk factors
    risk_factors: list[str] = Field(default_factory=list)

    # Timestamps
    account_opened: datetime = Field(default_factory=datetime.now)
    last_review: datetime | None = Field(None)
    next_review: datetime | None = Field(None)

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class CustomerRisk(BaseModel):
    """Customer risk assessment response."""

    customer_id: str
    customer_name: str
    risk_score: int = Field(..., ge=0, le=100)
    risk_level: RiskLevel
    contributing_factors: list[dict[str, Any]] = Field(default_factory=list)
    kyc_status: str
    recommendation: str
    last_assessment: datetime = Field(default_factory=datetime.now)
