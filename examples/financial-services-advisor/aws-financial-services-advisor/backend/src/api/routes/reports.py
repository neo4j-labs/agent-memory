"""Reports API routes for compliance reporting.

Reports are persisted as ``(:Report)-[:ABOUT]->(:Customer)`` in Neo4j rather
than a module-level dict: a dict is empty again after ``--reload`` and is
per-invocation under the documented Lambda deployment, so a SAR created by one
request would 404 on the very next one.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...agents import get_supervisor_agent
from ...models import (
    ReportRequest,
    ReportStatus,
    RiskAssessmentReport,
    RiskFactor,
    SARReport,
)
from ...services.neo4j_service import Neo4jDomainService
from ...services.risk_service import get_risk_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

SAR_KIND = "SAR"
RISK_KIND = "RISK_ASSESSMENT"


def _get_neo4j_service(request: Request) -> Neo4jDomainService:
    svc = getattr(request.app.state, "neo4j_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, svc)


class SARCreateRequest(BaseModel):
    """Request to create a SAR."""

    investigation_id: str = Field(..., description="Investigation ID")
    customer_id: str = Field(..., description="Subject customer ID")
    suspicious_activity_type: list[str] = Field(..., description="Types of activity")
    activity_start_date: datetime = Field(..., description="Activity start date")
    activity_end_date: datetime = Field(..., description="Activity end date")
    total_amount: float = Field(..., description="Total amount involved")
    currency: str = Field(default="USD", description="Currency")
    summary: str = Field(..., description="Executive summary")
    activity_description: str = Field(..., description="Detailed description")
    prepared_by: str = Field(..., description="Preparer ID")


class RiskReportCreateRequest(BaseModel):
    """Request to create a risk assessment report."""

    customer_id: str = Field(..., description="Customer ID")
    include_network: bool = Field(default=True, description="Include network analysis")
    include_transactions: bool = Field(default=True, description="Include transaction analysis")
    prepared_by: str = Field(..., description="Preparer ID")


class ReportListResponse(BaseModel):
    """Response for report list endpoint."""

    reports: list[dict[str, Any]]
    total: int


#: Months the demo amortises a customer's transaction history over when
#: deriving a monthly volume from the fixture's ~90-day window.
_HISTORY_MONTHS = 3.0


async def _transaction_risk_inputs(
    neo4j_service: Neo4jDomainService, customer_id: str
) -> dict[str, Any]:
    """Map graph query results onto ``RiskService.assess_customer_risk`` inputs.

    The risk model is deterministic and its inputs are named ratios and counts;
    this is where the graph's answers are translated into them, so every number
    in the report traces back to a Cypher result.
    """
    stats = await neo4j_service.get_transaction_stats(customer_id)
    structuring = await neo4j_service.detect_structuring(customer_id)
    rapid = await neo4j_service.detect_rapid_movement(customer_id)
    layering = await neo4j_service.detect_layering(customer_id)

    total_volume = float(stats.get("total_volume") or 0)
    count = int(stats.get("transaction_count") or 0)
    cash_volume = sum(
        float(t.get("amount") or 0)
        for t in await neo4j_service.get_transactions(
            customer_id, days=365, transaction_type="cash_deposit"
        )
    )
    offshore_volume = sum(float(t.get("amount") or 0) for t in layering)
    return {
        **stats,
        "avg_monthly_volume": total_volume / _HISTORY_MONTHS if count else 0,
        # No expected-volume baseline in the fixture: use the observed volume so
        # the ratio is 1.0 (neutral) rather than a fabricated deviation.
        "expected_monthly_volume": (total_volume / _HISTORY_MONTHS) or 1,
        "cash_ratio": (cash_volume / total_volume) if total_volume else 0,
        "high_risk_ratio": (offshore_volume / total_volume) if total_volume else 0,
        "structuring_count": len(structuring),
        "rapid_movement_count": len(rapid),
    }


async def _network_risk_inputs(
    neo4j_service: Neo4jDomainService, customer_id: str
) -> dict[str, Any]:
    """Derive the network risk counts from ``get_network_risk``'s factors."""
    network = await neo4j_service.get_network_risk(customer_id)
    factors: list[str] = network.get("risk_factors", [])
    return {
        **network,
        "high_risk_count": sum(1 for f in factors if f.startswith("HIGH_RISK_JURISDICTION")),
        "shell_company_count": sum(1 for f in factors if f.startswith("SHELL_COMPANY")),
        "pep_count": sum(1 for f in factors if f.startswith("PEP")),
        "sanctioned_count": sum(1 for f in factors if f.startswith("SANCTIONED")),
    }


@router.get("/sar", response_model=ReportListResponse)
async def list_sar_reports(
    request: Request,
    status: ReportStatus | None = Query(None, description="Filter by status"),
    customer_id: str | None = Query(None, description="Filter by customer"),
) -> ReportListResponse:
    """List all SAR reports.

    Args:
        status: Optional status filter
        customer_id: Optional customer filter

    Returns:
        List of SAR reports
    """
    neo4j_service = _get_neo4j_service(request)
    payloads = await neo4j_service.list_reports(
        SAR_KIND,
        status=status.value if status else None,
        customer_id=customer_id,
    )
    reports = [SARReport.model_validate_json(p) for p in payloads]

    return ReportListResponse(
        reports=[
            {
                "id": r.id,
                "investigation_id": r.investigation_id,
                "customer_id": r.customer_id,
                "subject_name": r.subject_name,
                "status": r.status.value,
                "created_at": r.created_at.isoformat(),
                "filed_at": r.filed_at.isoformat() if r.filed_at else None,
                "total_amount": r.total_amount_involved,
            }
            for r in reports
        ],
        total=len(reports),
    )


@router.post("/sar", response_model=SARReport, status_code=201)
async def create_sar_report(body: SARCreateRequest, request: Request) -> SARReport:
    """Create a new Suspicious Activity Report.

    Args:
        body: SAR creation data

    Returns:
        Created SAR report
    """
    neo4j_service = _get_neo4j_service(request)
    customer = await neo4j_service.get_customer(body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {body.customer_id} not found")

    report_id = f"SAR-{uuid.uuid4().hex[:8].upper()}"
    subject_name = customer.get("name") or f"Customer {body.customer_id}"
    subject_type = "individual" if customer.get("type") in ("individual", "PERSON") else "entity"

    report = SARReport(
        id=report_id,
        investigation_id=body.investigation_id,
        customer_id=body.customer_id,
        subject_name=subject_name,
        subject_type=subject_type,
        suspicious_activity_type=body.suspicious_activity_type,
        activity_date_range={
            "start": body.activity_start_date,
            "end": body.activity_end_date,
        },
        total_amount_involved=body.total_amount,
        currency=body.currency,
        summary=body.summary,
        activity_description=body.activity_description,
        subject_information={"customer_id": body.customer_id, "name": subject_name},
        account_information=[],
        transaction_summary=[],
        prepared_by=body.prepared_by,
    )

    await neo4j_service.save_report(
        report_id,
        SAR_KIND,
        body.customer_id,
        report.model_dump_json(),
        status=report.status.value,
        investigation_id=body.investigation_id,
    )
    logger.info("Created SAR report %s", report_id)

    return report


@router.get("/sar/{report_id}", response_model=SARReport)
async def get_sar_report(report_id: str, request: Request) -> SARReport:
    """Get SAR report by ID.

    Args:
        report_id: Report identifier

    Returns:
        SAR report details
    """
    payload = await _get_neo4j_service(request).get_report(report_id, SAR_KIND)
    if payload is None:
        raise HTTPException(status_code=404, detail="SAR report not found")
    return SARReport.model_validate_json(payload)


@router.post("/sar/{report_id}/submit")
async def submit_sar_report(
    report_id: str,
    request: Request,
    reviewer_id: str = Query(..., description="Reviewer ID"),
    approver_id: str = Query(..., description="Approver ID"),
) -> dict[str, Any]:
    """Submit a SAR report for filing.

    Args:
        report_id: Report identifier
        reviewer_id: ID of reviewer
        approver_id: ID of approver

    Returns:
        Submission confirmation
    """
    neo4j_service = _get_neo4j_service(request)
    payload = await neo4j_service.get_report(report_id, SAR_KIND)
    if payload is None:
        raise HTTPException(status_code=404, detail="SAR report not found")

    report = SARReport.model_validate_json(payload)
    report.status = ReportStatus.COMPLETED
    report.filed_at = datetime.now(UTC)
    report.reviewed_by = reviewer_id
    report.approved_by = approver_id
    report.filing_reference = f"FINCEN-{uuid.uuid4().hex[:12].upper()}"

    await neo4j_service.save_report(
        report_id,
        SAR_KIND,
        report.customer_id,
        report.model_dump_json(),
        status=report.status.value,
        investigation_id=report.investigation_id,
    )

    return {
        "report_id": report_id,
        "status": "submitted",
        "filing_reference": report.filing_reference,
        "filed_at": report.filed_at.isoformat(),
    }


@router.get("/risk-assessment", response_model=ReportListResponse)
async def list_risk_reports(
    request: Request,
    customer_id: str | None = Query(None, description="Filter by customer"),
) -> ReportListResponse:
    """List all risk assessment reports.

    Args:
        customer_id: Optional customer filter

    Returns:
        List of risk assessment reports
    """
    payloads = await _get_neo4j_service(request).list_reports(RISK_KIND, customer_id=customer_id)
    reports = [RiskAssessmentReport.model_validate_json(p) for p in payloads]

    return ReportListResponse(
        reports=[
            {
                "id": r.id,
                "customer_id": r.customer_id,
                "risk_score": r.overall_risk_score,
                "risk_level": r.overall_risk_level,
                "created_at": r.created_at.isoformat(),
                "valid_until": r.valid_until.isoformat() if r.valid_until else None,
            }
            for r in reports
        ],
        total=len(reports),
    )


@router.post("/risk-assessment", response_model=RiskAssessmentReport, status_code=201)
async def create_risk_report(
    body: RiskReportCreateRequest, request: Request
) -> RiskAssessmentReport:
    """Create a new risk assessment report for a customer.

    Args:
        body: Risk report creation data

    Returns:
        Created risk assessment report
    """
    neo4j_service = _get_neo4j_service(request)
    customer = await neo4j_service.get_customer(body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {body.customer_id} not found")

    report_id = f"RISK-{uuid.uuid4().hex[:8].upper()}"

    # Feed the deterministic risk model with the real customer record plus the
    # graph analyses the agents use, instead of the hard-coded placeholders the
    # first version passed.
    transaction_stats = (
        await _transaction_risk_inputs(neo4j_service, body.customer_id)
        if body.include_transactions
        else None
    )
    network_risk = (
        await _network_risk_inputs(neo4j_service, body.customer_id)
        if body.include_network
        else None
    )
    assessment = get_risk_service().assess_customer_risk(
        customer_id=body.customer_id,
        customer_type=str(customer.get("type") or "individual"),
        jurisdiction=str(customer.get("jurisdiction") or customer.get("nationality") or "US"),
        industry=customer.get("business_type"),
        transaction_data=transaction_stats,
        network_data=network_risk,
    )

    # Build risk factors
    risk_factors = [
        RiskFactor(
            category="geographic",
            factor=factor,
            severity="medium",
            score=assessment.geographic_risk,
            description=factor,
            evidence=[],
        )
        for factor in assessment.risk_factors
        if "jurisdiction" in factor.lower()
    ]

    # Determine risk level string
    risk_level = assessment.overall_risk.value

    report = RiskAssessmentReport(
        id=report_id,
        customer_id=body.customer_id,
        status=ReportStatus.COMPLETED,
        valid_until=datetime.now(UTC) + timedelta(days=365),
        overall_risk_score=assessment.risk_score,
        overall_risk_level=risk_level,
        identity_verification_score=85.0,  # Simplified
        geographic_risk_score=assessment.geographic_risk,
        product_risk_score=50.0,  # Simplified
        transaction_risk_score=assessment.transaction_risk,
        relationship_risk_score=assessment.network_risk,
        risk_factors=risk_factors,
        customer_profile={"customer_id": body.customer_id, "name": customer.get("name")},
        transaction_analysis=transaction_stats or {"included": False},
        network_analysis=network_risk or {"included": False},
        regulatory_status={"compliant": True},
        risk_rating_recommendation=risk_level,
        enhanced_due_diligence_required=risk_level in ("high", "critical"),
        monitoring_recommendations=assessment.recommendations,
        action_items=assessment.recommendations[:3] if assessment.recommendations else [],
        prepared_by=body.prepared_by,
    )

    await neo4j_service.save_report(
        report_id,
        RISK_KIND,
        body.customer_id,
        report.model_dump_json(),
        status=report.status.value,
    )
    logger.info("Created risk assessment report %s", report_id)

    return report


@router.get("/risk-assessment/{report_id}", response_model=RiskAssessmentReport)
async def get_risk_report(report_id: str, request: Request) -> RiskAssessmentReport:
    """Get risk assessment report by ID.

    Args:
        report_id: Report identifier

    Returns:
        Risk assessment report details
    """
    payload = await _get_neo4j_service(request).get_report(report_id, RISK_KIND)
    if payload is None:
        raise HTTPException(status_code=404, detail="Risk report not found")
    return RiskAssessmentReport.model_validate_json(payload)


@router.post("/generate")
async def generate_report(body: ReportRequest, request: Request) -> dict[str, Any]:
    """Generate a narrative report with the agent.

    Runs the supervisor, which delegates report drafting to the compliance
    specialist through ``delegate_to_compliance_agent``.

    Args:
        body: Report generation request

    Returns:
        Generated report metadata
    """
    neo4j_service = _get_neo4j_service(request)
    prompt = (
        f"Generate a {body.report_type} report for customer {body.customer_id}.\n\n"
        "Include the following:\n"
        f"- Network analysis: {body.include_network}\n"
        f"- Transaction analysis: {body.include_transactions}\n"
        f"- Date range: Last {body.date_range_days} days\n"
        f"- Output format: {body.format.value}\n\n"
        "Provide a comprehensive report following regulatory requirements."
    )

    try:
        supervisor = get_supervisor_agent(neo4j_service, f"report-{body.customer_id}")
        result = str(await supervisor.invoke_async(prompt))
    except Exception as exc:
        logger.error("Report generation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "generated",
        "customer_id": body.customer_id,
        "report_type": body.report_type,
        "format": body.format.value,
        "content_preview": result[:500] + "..." if len(result) > 500 else result,
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/templates")
async def list_report_templates() -> list[dict[str, Any]]:
    """List available report templates.

    Returns:
        List of report templates with descriptions
    """
    return [
        {
            "id": "sar",
            "name": "Suspicious Activity Report (SAR)",
            "description": "Report for filing suspicious activity with FinCEN",
            "sections": [
                "subject_information",
                "suspicious_activity",
                "transaction_summary",
                "narrative",
            ],
            "regulatory_authority": "FinCEN",
        },
        {
            "id": "risk_assessment",
            "name": "Customer Risk Assessment",
            "description": "Comprehensive customer risk evaluation",
            "sections": [
                "customer_profile",
                "risk_scoring",
                "transaction_analysis",
                "network_analysis",
                "recommendations",
            ],
            "regulatory_authority": "Internal",
        },
        {
            "id": "edd",
            "name": "Enhanced Due Diligence Report",
            "description": "Detailed due diligence for high-risk customers",
            "sections": [
                "identity_verification",
                "source_of_wealth",
                "source_of_funds",
                "beneficial_ownership",
                "risk_factors",
            ],
            "regulatory_authority": "Various",
        },
        {
            "id": "periodic_review",
            "name": "Periodic Review Report",
            "description": "Regular customer review and monitoring summary",
            "sections": [
                "profile_changes",
                "transaction_summary",
                "alert_summary",
                "risk_update",
                "next_review_date",
            ],
            "regulatory_authority": "Internal",
        },
    ]
