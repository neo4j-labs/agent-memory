"""Compliance Agent for regulatory screening and report generation.

Specialises in sanctions screening, PEP verification and regulatory
compliance reporting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from ..tools.compliance_tools import (
    assess_regulatory_requirements,
    check_sanctions,
    generate_sar_report,
    verify_pep_status,
)
from ._base import DEFAULT_MODEL, create_specialist_agent
from .prompts import COMPLIANCE_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

COMPLIANCE_DESCRIPTION = (
    "Regulatory compliance specialist for sanctions screening, "
    "PEP verification, and regulatory report preparation."
)


def create_compliance_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
) -> LlmAgent:
    """Create the Compliance Agent.

    Args:
        memory_service: Optional memory service for context graph access.
        model: The Gemini model to use.
        neo4j_service: Domain data service for Neo4j queries.

    Returns:
        Configured Compliance Agent.
    """
    write_tools = []

    if memory_service is not None:

        async def store_compliance_finding(
            entity_name: str,
            finding: str,
            screening_type: str,
            match_found: bool = False,
        ) -> str:
            """Record a compliance screening finding in the context graph."""
            return await memory_service.store_finding(
                content=finding,
                category="compliance_finding",
                metadata={
                    "entity_name": entity_name,
                    "screening_type": screening_type,
                    "match_found": match_found,
                },
            )

        async def record_regulatory_filing(
            filing_type: str,
            reference: str,
            customer_id: str,
            status: str,
        ) -> str:
            """Record a regulatory filing for the audit trail."""
            return await memory_service.store_finding(
                content=f"{filing_type} filing {reference}: {status}",
                category="regulatory_filing",
                metadata={
                    "customer_id": customer_id,
                    "filing_type": filing_type,
                    "reference": reference,
                    "status": status,
                },
            )

        write_tools = [store_compliance_finding, record_regulatory_filing]

    return create_specialist_agent(
        name="compliance_agent",
        description=COMPLIANCE_DESCRIPTION,
        instruction=COMPLIANCE_AGENT_INSTRUCTION,
        functions=[
            check_sanctions,
            verify_pep_status,
            generate_sar_report,
            assess_regulatory_requirements,
        ],
        memory_service=memory_service,
        model=model,
        neo4j_service=neo4j_service,
        write_tools=write_tools,
    )
