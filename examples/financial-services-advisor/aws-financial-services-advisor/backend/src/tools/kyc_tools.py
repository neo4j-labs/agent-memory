"""KYC (Know Your Customer) tools for identity verification and due diligence.

These tools are used by the KYC Agent to perform customer verification tasks.
All data is queried from Neo4j via the Neo4jDomainService.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


async def verify_identity(
    customer_id: str,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Verify customer identity against available records.

    Args:
        customer_id: Customer identifier, e.g. CUST-001
    """
    logger.info(f"Verifying identity for customer {customer_id}")

    customer = await neo4j_service.get_customer(customer_id)
    if not customer:
        return {
            "customer_id": customer_id,
            "status": "NOT_FOUND",
            "message": f"Customer {customer_id} not found in database",
            "verified": False,
            "timestamp": datetime.now().isoformat(),
        }

    documents = customer.get("documents", [])
    verified_docs = sum(1 for d in documents if d.get("status") == "verified")
    total_docs = len(documents)

    if customer.get("type") == "individual":
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
        "customer_name": customer.get("name"),
        "customer_type": customer.get("type"),
        "status": status,
        "verified": status == "VERIFIED",
        "documents_verified": f"{verified_docs}/{total_docs}",
        "missing_documents": missing_docs,
        "risk_factors": customer.get("risk_factors", []),
        "kyc_status": customer.get("kyc_status"),
        "timestamp": datetime.now().isoformat(),
    }


async def check_documents(
    customer_id: str,
    document_type: str | None = None,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Check document status and validity for a customer.

    Args:
        customer_id: Customer identifier, e.g. CUST-001
        document_type: Restrict to one document type (passport, utility_bill,
            certificate_of_incorporation, register_of_directors,
            proof_of_address)
    """
    logger.info(f"Checking documents for customer {customer_id}")

    customer = await neo4j_service.get_customer(customer_id)
    if not customer:
        return {
            "customer_id": customer_id,
            "status": "NOT_FOUND",
            "message": f"Customer {customer_id} not found",
            "timestamp": datetime.now().isoformat(),
        }

    documents = customer.get("documents", [])

    if document_type:
        doc = next((d for d in documents if d["type"] == document_type), None)
        if not doc:
            return {
                "customer_id": customer_id,
                "document_type": document_type,
                "status": "NOT_SUBMITTED",
                "message": f"Document '{document_type}' has not been submitted",
                "timestamp": datetime.now().isoformat(),
            }
        return {
            "customer_id": customer_id,
            "document_type": document_type,
            "status": doc.get("status", "unknown").upper(),
            "expiry_date": doc.get("expiry_date"),
            "submission_date": doc.get("submission_date"),
            "timestamp": datetime.now().isoformat(),
        }

    doc_summary = [
        {
            "type": d["type"],
            "status": d.get("status", "unknown").upper(),
            "expiry_date": d.get("expiry_date"),
            "submission_date": d.get("submission_date"),
        }
        for d in documents
    ]

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "total_documents": len(documents),
        "documents": doc_summary,
        "timestamp": datetime.now().isoformat(),
    }


async def assess_customer_risk(
    customer_id: str,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Assess overall KYC risk level for a customer.

    Args:
        customer_id: Customer identifier, e.g. CUST-001
    """
    logger.info(f"Assessing customer risk for {customer_id}")

    customer = await neo4j_service.get_customer(customer_id)
    if not customer:
        return {
            "customer_id": customer_id,
            "status": "NOT_FOUND",
            "message": f"Customer {customer_id} not found",
            "timestamp": datetime.now().isoformat(),
        }

    base_score = 20
    risk_factors = customer.get("risk_factors", [])

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
            {
                "factor": factor,
                "weight": weight,
                "description": factor.replace("_", " ").title(),
            }
        )

    documents = customer.get("documents", [])
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

    recommendations = {
        "CRITICAL": "Immediate escalation required. Consider account restriction pending investigation.",
        "HIGH": "Enhanced due diligence required. Senior review recommended.",
        "MEDIUM": "Standard enhanced monitoring. Periodic review required.",
        "LOW": "Standard monitoring procedures apply.",
    }

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "risk_score": total_score,
        "risk_level": risk_level,
        "contributing_factors": contributing_factors,
        "kyc_status": customer.get("kyc_status"),
        "recommendation": recommendations.get(risk_level, "Review required."),
        "timestamp": datetime.now().isoformat(),
    }


async def check_adverse_media(
    customer_id: str,
    include_associates: bool = False,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Screen customer for adverse media coverage.

    Args:
        customer_id: Customer identifier, e.g. CUST-001
        include_associates: Whether to screen connected parties too
    """
    logger.info(f"Checking adverse media for customer {customer_id}")

    customer = await neo4j_service.get_customer(customer_id)
    if not customer:
        return {
            "customer_id": customer_id,
            "status": "NOT_FOUND",
            "message": f"Customer {customer_id} not found",
            "timestamp": datetime.now().isoformat(),
        }

    # Adverse media remains a mock — no real media database to query
    adverse_media_database = {
        "CUST-003": [
            {
                "source": "Financial Times",
                "date": "2023-06-15",
                "headline": "BVI Shell Companies Under Scrutiny",
                "relevance": "MEDIUM",
                "category": "regulatory_concern",
            },
        ],
    }

    media_hits = adverse_media_database.get(customer_id, [])

    return {
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "screening_status": "COMPLETED",
        "hits_found": len(media_hits),
        "media_hits": media_hits,
        "risk_indicator": "HIGH" if media_hits else "LOW",
        "include_associates": include_associates,
        "timestamp": datetime.now().isoformat(),
    }
