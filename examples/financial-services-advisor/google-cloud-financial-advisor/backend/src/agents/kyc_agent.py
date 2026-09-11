"""KYC Agent for identity verification and customer due diligence.

Specialises in Know Your Customer tasks: identity verification, document
checking and risk assessment. Memory reads go through ADK's ``load_memory``;
the one write tool records findings as ``:Fact`` nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from ..tools.kyc_tools import (
    assess_customer_risk,
    check_adverse_media,
    check_documents,
    verify_identity,
)
from ._base import DEFAULT_MODEL, create_specialist_agent
from .prompts import KYC_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

KYC_DESCRIPTION = (
    "KYC specialist for identity verification, document validation, "
    "and customer due diligence assessments."
)


def create_kyc_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
) -> LlmAgent:
    """Create the KYC Agent.

    Args:
        memory_service: Optional memory service for context graph access.
        model: The Gemini model to use.
        neo4j_service: Domain data service for Neo4j queries.

    Returns:
        Configured KYC Agent.
    """
    write_tools = []

    if memory_service is not None:

        async def store_kyc_finding(
            customer_id: str,
            finding: str,
            risk_level: str = "MEDIUM",
        ) -> str:
            """Record a KYC finding for the customer in the context graph."""
            return await memory_service.store_finding(
                content=finding,
                category="kyc_finding",
                metadata={"customer_id": customer_id, "risk_level": risk_level},
            )

        write_tools = [store_kyc_finding]

    return create_specialist_agent(
        name="kyc_agent",
        description=KYC_DESCRIPTION,
        instruction=KYC_AGENT_INSTRUCTION,
        functions=[
            verify_identity,
            check_documents,
            assess_customer_risk,
            check_adverse_media,
        ],
        memory_service=memory_service,
        model=model,
        neo4j_service=neo4j_service,
        write_tools=write_tools,
    )
