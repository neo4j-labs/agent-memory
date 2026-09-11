"""Customer API routes.

All customer data is queried from Neo4j via the Neo4jDomainService.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customers", tags=["customers"])


def _get_neo4j_service(request: Request) -> Neo4jDomainService:
    """Get Neo4jDomainService from app state."""
    svc = getattr(request.app.state, "neo4j_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, svc)


def _compute_risk(customer: dict) -> dict[str, Any]:
    """Compute risk score and level from customer data."""
    base_score = 20
    risk_factors = customer.get("risk_factors", []) or []

    risk_weights = {
        "offshore_jurisdiction": 25,
        "nominee_directors": 20,
        "shell_company_indicators": 30,
        "high_risk_business": 15,
        "international_transactions": 10,
        "pep_connection": 25,
        "adverse_media": 20,
    }

    total_score = base_score
    contributing_factors = []

    for factor in risk_factors:
        weight = risk_weights.get(factor, 10)
        total_score += weight
        contributing_factors.append(
            {"factor": factor, "weight": weight, "description": factor.replace("_", " ").title()}
        )

    documents = customer.get("documents", []) or []
    pending_docs = sum(1 for d in documents if d.get("status") != "verified")
    if pending_docs > 0:
        total_score += pending_docs * 5
        contributing_factors.append(
            {
                "factor": "incomplete_documentation",
                "weight": pending_docs * 5,
                "description": f"{pending_docs} documents pending verification",
            }
        )

    total_score = min(total_score, 100)

    if total_score >= 75:
        risk_level = "CRITICAL"
    elif total_score >= 50:
        risk_level = "HIGH"
    elif total_score >= 30:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "risk_score": total_score,
        "risk_level": risk_level,
        "contributing_factors": contributing_factors,
    }


class CustomerResponse(BaseModel):
    id: str
    name: str
    type: str
    nationality: str | None = None
    address: str | None = None
    occupation: str | None = None
    employer: str | None = None
    jurisdiction: str | None = None
    business_type: str | None = None
    kyc_status: str = "pending"
    risk_level: str = "UNKNOWN"
    risk_score: int = 0
    risk_factors: list[str] = Field(default_factory=list)
    alerts_count: int = 0


class CustomerListResponse(BaseModel):
    customers: list[CustomerResponse]
    total: int


class CustomerRiskResponse(BaseModel):
    customer_id: str
    customer_name: str
    risk_score: int
    risk_level: str
    contributing_factors: list[dict[str, Any]]
    kyc_status: str
    recommendation: str


def _customer_response(cust: dict, risk: dict) -> CustomerResponse:
    return CustomerResponse(
        id=cust.get("id", ""),
        name=cust.get("name", "Unknown"),
        type=cust.get("type", "individual"),
        nationality=cust.get("nationality"),
        address=cust.get("address") or cust.get("registered_address"),
        occupation=cust.get("occupation"),
        employer=cust.get("employer"),
        jurisdiction=cust.get("jurisdiction"),
        business_type=cust.get("business_type"),
        kyc_status=cust.get("kyc_status", "pending"),
        risk_level=risk["risk_level"],
        risk_score=risk["risk_score"],
        risk_factors=cust.get("risk_factors") or [],
    )


@router.get("", response_model=CustomerListResponse)
async def list_customers(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    risk_level: str | None = Query(None, description="Filter by risk level"),
    search: str | None = Query(None, description="Search by name"),
) -> CustomerListResponse:
    """List all customers with optional filtering."""
    neo4j_service = _get_neo4j_service(request)
    rows = await neo4j_service.list_customers(limit=1000)

    customers = []
    for cust in rows:
        risk = _compute_risk(cust)
        if risk_level and risk["risk_level"].lower() != risk_level.lower():
            continue
        if search and search.lower() not in cust.get("name", "").lower():
            continue
        customers.append(_customer_response(cust, risk))

    total = len(customers)
    start = (page - 1) * page_size
    return CustomerListResponse(customers=customers[start : start + page_size], total=total)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(request: Request, customer_id: str) -> CustomerResponse:
    """Get a specific customer by ID."""
    neo4j_service = _get_neo4j_service(request)
    cust = await neo4j_service.get_customer(customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")
    risk = _compute_risk(cust)
    return _customer_response(cust, risk)


@router.get("/{customer_id}/risk", response_model=CustomerRiskResponse)
async def get_customer_risk(request: Request, customer_id: str) -> CustomerRiskResponse:
    """Get risk assessment for a customer."""
    neo4j_service = _get_neo4j_service(request)
    cust = await neo4j_service.get_customer(customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    risk = _compute_risk(cust)
    recommendations = {
        "CRITICAL": "Immediate escalation required. Consider account restriction pending investigation.",
        "HIGH": "Enhanced due diligence required. Senior review recommended.",
        "MEDIUM": "Standard enhanced monitoring. Periodic review required.",
        "LOW": "Standard monitoring procedures apply.",
    }

    return CustomerRiskResponse(
        customer_id=customer_id,
        customer_name=cust.get("name", "Unknown"),
        risk_score=risk["risk_score"],
        risk_level=risk["risk_level"],
        contributing_factors=risk["contributing_factors"],
        kyc_status=cust.get("kyc_status", "pending"),
        recommendation=recommendations.get(risk["risk_level"], "Review required"),
    )


@router.get("/{customer_id}/network")
async def get_customer_network(
    request: Request,
    customer_id: str,
    depth: int = Query(2, ge=1, le=3),
) -> dict[str, Any]:
    """Get the relationship network for a customer."""
    neo4j_service = _get_neo4j_service(request)
    cust = await neo4j_service.get_customer(customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    conn_data = await neo4j_service.find_connections(customer_id, depth=depth)

    nodes = [
        {
            "id": customer_id,
            "label": cust.get("name", customer_id),
            "type": cust.get("type", "UNKNOWN"),
            "isRoot": True,
        }
    ]
    edges = []

    for conn in conn_data.get("connections", []):
        entity = conn.get("entity", {})
        entity_id = entity.get("id") or entity.get("name")
        rel_types = conn.get("rel_types", [])

        nodes.append(
            {
                "id": entity_id,
                "label": entity.get("name"),
                "type": entity.get("type"),
                "jurisdiction": entity.get("jurisdiction"),
                "distance": conn.get("distance", 1),
            }
        )
        edges.append(
            {
                "from": customer_id if conn.get("distance", 1) == 1 else None,
                "to": entity_id,
                "relationship": rel_types[0] if rel_types else "CONNECTED_TO",
            }
        )

    return {
        "customer_id": customer_id,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "total_connections": len(conn_data.get("connections", [])),
    }


@router.get("/{customer_id}/verify")
async def verify_customer(request: Request, customer_id: str) -> dict[str, Any]:
    """Verify customer identity."""
    neo4j_service = _get_neo4j_service(request)
    cust = await neo4j_service.get_customer(customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    documents = cust.get("documents", []) or []
    verified_docs = sum(1 for d in documents if d.get("status") == "verified")

    if cust.get("type") == "individual":
        required_docs = ["passport", "utility_bill"]
    else:
        required_docs = [
            "certificate_of_incorporation",
            "register_of_directors",
            "proof_of_address",
        ]

    doc_types_verified = {d["type"] for d in documents if d.get("status") == "verified"}
    missing_docs = [doc for doc in required_docs if doc not in doc_types_verified]
    status = "VERIFIED" if not missing_docs else "PENDING"

    return {
        "customer_id": customer_id,
        "customer_name": cust.get("name"),
        "customer_type": cust.get("type"),
        "status": status,
        "verified": status == "VERIFIED",
        "documents_verified": f"{verified_docs}/{len(documents)}",
        "missing_documents": missing_docs,
        "risk_factors": cust.get("risk_factors", []),
        "kyc_status": cust.get("kyc_status"),
    }


@router.post("", response_model=CustomerResponse)
async def create_customer(name: str, type: str = "individual") -> CustomerResponse:
    """Create a new customer (demo - not persisted to Neo4j)."""
    customer_id = f"CUST-{uuid.uuid4().hex[:6].upper()}"
    return CustomerResponse(
        id=customer_id, name=name, type=type, risk_level="MEDIUM", risk_score=20
    )
