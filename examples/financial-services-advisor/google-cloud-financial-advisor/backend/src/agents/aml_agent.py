"""AML Agent for transaction monitoring and suspicious activity detection.

Specialises in Anti-Money Laundering tasks: transaction analysis, pattern
detection and SAR preparation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from ..tools.aml_tools import (
    analyze_velocity,
    detect_patterns,
    flag_suspicious_transaction,
    scan_transactions,
)
from ._base import DEFAULT_MODEL, create_specialist_agent
from .prompts import AML_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

AML_DESCRIPTION = (
    "AML analyst for transaction monitoring, suspicious pattern detection, "
    "and anti-money laundering investigations."
)


def create_aml_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
) -> LlmAgent:
    """Create the AML Agent.

    Args:
        memory_service: Optional memory service for context graph access.
        model: The Gemini model to use.
        neo4j_service: Domain data service for Neo4j queries.

    Returns:
        Configured AML Agent.
    """
    write_tools = []

    if memory_service is not None:

        async def store_aml_finding(
            customer_id: str,
            finding: str,
            pattern_type: str | None = None,
        ) -> str:
            """Record an AML finding for the customer in the context graph."""
            return await memory_service.store_finding(
                content=finding,
                category="aml_finding",
                metadata={"customer_id": customer_id, "pattern_type": pattern_type},
            )

        async def record_suspicious_pattern(
            customer_id: str,
            pattern: str,
            transactions: list[str],
            confidence: float,
        ) -> str:
            """Record a detected suspicious transaction pattern."""
            return await memory_service.store_finding(
                content=f"{pattern} involving transactions {', '.join(transactions)}",
                category="aml_pattern",
                metadata={
                    "customer_id": customer_id,
                    "pattern": pattern,
                    "transactions": transactions,
                    "confidence": confidence,
                },
            )

        write_tools = [store_aml_finding, record_suspicious_pattern]

    return create_specialist_agent(
        name="aml_agent",
        description=AML_DESCRIPTION,
        instruction=AML_AGENT_INSTRUCTION,
        functions=[
            scan_transactions,
            detect_patterns,
            flag_suspicious_transaction,
            analyze_velocity,
        ],
        memory_service=memory_service,
        model=model,
        neo4j_service=neo4j_service,
        write_tools=write_tools,
    )
