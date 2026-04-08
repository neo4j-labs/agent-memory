"""Transaction-related Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TransactionType(str, Enum):
    """Type of financial transaction."""

    WIRE_TRANSFER = "wire_transfer"
    ACH_TRANSFER = "ach_transfer"
    CHECK_DEPOSIT = "check_deposit"
    CASH_DEPOSIT = "cash_deposit"
    CASH_WITHDRAWAL = "cash_withdrawal"
    INTERNAL_TRANSFER = "internal_transfer"
    CARD_PAYMENT = "card_payment"
    FOREX = "forex"
    CRYPTO = "crypto"


class Beneficiary(BaseModel):
    """Transaction beneficiary information."""

    name: str = Field(..., description="Beneficiary name")
    account_number: str | None = Field(
        default=None, description="Beneficiary account number"
    )
    bank_name: str | None = Field(default=None, description="Beneficiary bank name")
    bank_country: str | None = Field(default=None, description="Bank jurisdiction")
    jurisdiction: str | None = Field(
        default=None, description="Beneficiary jurisdiction"
    )


class TransactionCreate(BaseModel):
    """Model for creating a new transaction record."""

    from_account: str = Field(..., description="Source account ID")
    to_account: str | None = Field(default=None, description="Destination account ID")
    amount: float = Field(..., gt=0, description="Transaction amount")
    currency: str = Field(default="USD", description="Transaction currency")
    type: TransactionType = Field(..., description="Transaction type")
    description: str | None = Field(default=None, description="Transaction description")
    beneficiary: Beneficiary | None = Field(
        default=None, description="Beneficiary details"
    )
    reference: str | None = Field(default=None, description="Reference number")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class Transaction(TransactionCreate):
    """Full transaction model."""

    id: str = Field(..., description="Unique transaction identifier")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Transaction timestamp"
    )
    status: str = Field(default="completed", description="Transaction status")
    risk_score: float | None = Field(default=None, description="Transaction risk score")
    flagged: bool = Field(default=False, description="Whether transaction is flagged")
    flag_reasons: list[str] = Field(
        default_factory=list, description="Reasons for flagging"
    )


class TransactionPattern(BaseModel):
    """Detected transaction pattern."""

    pattern_type: str = Field(..., description="Type of pattern detected")
    description: str = Field(..., description="Pattern description")
    confidence: float = Field(..., ge=0, le=1, description="Detection confidence")
    transactions: list[str] = Field(..., description="Transaction IDs involved")
    time_range: dict[str, datetime] = Field(..., description="Time range of pattern")
    total_amount: float = Field(..., description="Total amount involved")
    risk_indicators: list[str] = Field(
        default_factory=list, description="Risk indicators"
    )


class TransactionSummary(BaseModel):
    """Summary of customer transactions."""

    customer_id: str = Field(..., description="Customer identifier")
    period_start: datetime = Field(..., description="Summary period start")
    period_end: datetime = Field(..., description="Summary period end")
    total_transactions: int = Field(..., description="Total transaction count")
    total_inflow: float = Field(..., description="Total incoming amount")
    total_outflow: float = Field(..., description="Total outgoing amount")
    by_type: dict[str, int] = Field(default_factory=dict, description="Counts by type")
    by_currency: dict[str, float] = Field(
        default_factory=dict, description="Amounts by currency"
    )
    flagged_count: int = Field(default=0, description="Number of flagged transactions")
    high_risk_jurisdictions: list[str] = Field(
        default_factory=list, description="High-risk jurisdictions involved"
    )
