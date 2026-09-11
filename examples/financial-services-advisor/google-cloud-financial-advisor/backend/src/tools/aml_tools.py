"""AML (Anti-Money Laundering) tools for transaction monitoring and pattern detection.

These tools are used by the AML Agent to analyze transactions and detect
suspicious activity patterns. All data is queried from Neo4j.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


async def scan_transactions(
    customer_id: str,
    days: int = 90,
    min_amount: float | None = None,
    transaction_type: str | None = None,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Scan customer transactions for the specified period."""
    logger.info(f"Scanning transactions for customer {customer_id}, last {days} days")

    transactions = await neo4j_service.get_transactions(
        customer_id,
        days=days,
        min_amount=min_amount,
        transaction_type=transaction_type,
    )
    if not transactions:
        return {
            "customer_id": customer_id,
            "status": "NO_TRANSACTIONS",
            "message": "No transactions found for this customer",
            "timestamp": datetime.now().isoformat(),
        }

    total_amount = sum(t["amount"] for t in transactions)
    deposits = sum(t["amount"] for t in transactions if "deposit" in t["type"] or "in" in t["type"])
    withdrawals = sum(
        t["amount"] for t in transactions if "withdrawal" in t["type"] or "out" in t["type"]
    )
    counterparties = list({t["counterparty"] for t in transactions if t.get("counterparty")})

    return {
        "customer_id": customer_id,
        "period_days": days,
        "transaction_count": len(transactions),
        "total_volume": total_amount,
        "total_deposits": deposits,
        "total_withdrawals": withdrawals,
        "unique_counterparties": len(counterparties),
        "counterparties": counterparties,
        "transactions": transactions,
        "timestamp": datetime.now().isoformat(),
    }


async def detect_patterns(
    customer_id: str,
    pattern_types: list[str] | None = None,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Detect suspicious transaction patterns."""
    logger.info(f"Detecting patterns for customer {customer_id}")

    transactions = await neo4j_service.get_transactions(customer_id)
    if not transactions:
        return {
            "customer_id": customer_id,
            "status": "NO_DATA",
            "patterns_detected": [],
            "timestamp": datetime.now().isoformat(),
        }

    patterns_detected = []

    # Structuring detection via Neo4j
    structuring_txns = await neo4j_service.detect_structuring(customer_id)
    if len(structuring_txns) >= 2:
        patterns_detected.append(
            {
                "pattern": "STRUCTURING",
                "confidence": 0.85,
                "description": "Multiple cash deposits just under $10,000 reporting threshold",
                "evidence": [t["id"] for t in structuring_txns],
                "total_amount": sum(t["amount"] for t in structuring_txns),
                "risk_level": "HIGH",
            }
        )

    # Rapid movement detection via Neo4j
    rapid_pairs = await neo4j_service.detect_rapid_movement(customer_id)
    if rapid_pairs:
        first = rapid_pairs[0]
        patterns_detected.append(
            {
                "pattern": "RAPID_MOVEMENT",
                "confidence": 0.75,
                "description": "Funds moved quickly after receipt with minimal change",
                "evidence": [first["inbound"]["id"], first["outbound"]["id"]],
                "in_amount": first["inbound"]["amount"],
                "out_amount": first["outbound"]["amount"],
                "risk_level": "MEDIUM",
            }
        )

    # Layering detection via Neo4j
    layering_txns = await neo4j_service.detect_layering(customer_id)
    if len(layering_txns) >= 2:
        patterns_detected.append(
            {
                "pattern": "LAYERING",
                "confidence": 0.70,
                "description": "Multiple transactions with offshore/high-risk jurisdictions",
                "evidence": [t["id"] for t in layering_txns],
                "jurisdictions": list({t["counterparty"] for t in layering_txns}),
                "risk_level": "HIGH",
            }
        )

    return {
        "customer_id": customer_id,
        "transactions_analyzed": len(transactions),
        "patterns_detected": patterns_detected,
        "overall_risk": "HIGH" if patterns_detected else "LOW",
        "timestamp": datetime.now().isoformat(),
    }


async def flag_suspicious_transaction(
    transaction_id: str,
    reason: str,
    severity: str = "MEDIUM",
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Flag a specific transaction as suspicious."""
    logger.info(f"Flagging transaction {transaction_id} as suspicious")

    # Find the transaction and its customer
    query = """
    MATCH (c:Customer)-[:HAS_TRANSACTION]->(t:Transaction {id: $txn_id})
    RETURN c.id AS customer_id, t {.*} AS transaction
    """
    results = await neo4j_service.read(query, {"txn_id": transaction_id})
    if not results:
        return {
            "transaction_id": transaction_id,
            "status": "NOT_FOUND",
            "message": "Transaction not found",
            "timestamp": datetime.now().isoformat(),
        }

    row = results[0]
    customer_id = row["customer_id"]
    txn = row["transaction"]

    # Create alert in Neo4j
    alert = await neo4j_service.create_alert(
        {
            "customer_id": customer_id,
            "type": "AML",
            "severity": severity,
            "status": "NEW",
            "title": f"Suspicious Transaction: {transaction_id}",
            "description": reason,
            "evidence": [f"{transaction_id}: ${txn['amount']:,.0f} {txn.get('type', '')}"],
            "requires_sar": severity in ["HIGH", "CRITICAL"],
        }
    )

    return {
        "alert_id": alert.get("id", "ALERT-NEW"),
        "transaction_id": transaction_id,
        "customer_id": customer_id,
        "transaction_amount": txn["amount"],
        "reason": reason,
        "severity": severity,
        "status": "FLAGGED",
        "requires_sar": severity in ["HIGH", "CRITICAL"],
        "timestamp": datetime.now().isoformat(),
    }


async def analyze_velocity(
    customer_id: str,
    metric: str = "all",
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Analyze transaction velocity patterns."""
    logger.info(f"Analyzing velocity for customer {customer_id}")

    metrics = await neo4j_service.get_velocity_metrics(customer_id)
    if metrics["total_transactions"] == 0:
        return {
            "customer_id": customer_id,
            "status": "NO_DATA",
            "timestamp": datetime.now().isoformat(),
        }

    type_counts = metrics["transactions_by_type"]
    type_amounts = metrics["volume_by_type"]

    anomalies = []

    cash_count = sum(v for k, v in type_counts.items() if "cash" in k)
    if cash_count >= 3:
        anomalies.append(
            {
                "type": "HIGH_CASH_FREQUENCY",
                "description": f"{cash_count} cash transactions in period",
                "risk_level": "MEDIUM",
            }
        )

    wire_amount = sum(v for k, v in type_amounts.items() if "wire" in k)
    if wire_amount > 100000:
        anomalies.append(
            {
                "type": "HIGH_WIRE_VOLUME",
                "description": f"${wire_amount:,.0f} in wire transfers",
                "risk_level": "MEDIUM",
            }
        )

    # Check for large individual transactions
    large_txns_query = """
    MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t:Transaction)
    WHERE t.amount > 50000
    RETURN t.id AS id
    """
    large_results = await neo4j_service.read(large_txns_query, {"id": customer_id})
    if large_results:
        anomalies.append(
            {
                "type": "LARGE_TRANSACTIONS",
                "description": f"{len(large_results)} transactions over $50,000",
                "transactions": [r["id"] for r in large_results],
                "risk_level": "HIGH",
            }
        )

    return {
        "customer_id": customer_id,
        "period_analyzed": "90 days",
        "metrics": {
            "total_transactions": metrics["total_transactions"],
            "total_volume": metrics["total_volume"],
            "average_transaction": round(metrics["average_transaction"], 2),
            "transactions_by_type": type_counts,
            "volume_by_type": type_amounts,
        },
        "anomalies_detected": anomalies,
        "velocity_risk": ("HIGH" if len(anomalies) >= 2 else "MEDIUM" if anomalies else "LOW"),
        "timestamp": datetime.now().isoformat(),
    }
