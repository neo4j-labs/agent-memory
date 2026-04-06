"""KYC Agent for identity verification and customer due diligence.

This agent specializes in Know Your Customer (KYC) tasks including
identity verification, document checking, and risk assessment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools.kyc_tools import (
    assess_customer_risk,
    check_adverse_media,
    check_documents,
    verify_identity,
)
from .utils import bind_tool
from .prompts import KYC_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


def create_kyc_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = "gemini-2.5-flash",
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
    if neo4j_service:
        tools = [
            FunctionTool(bind_tool(verify_identity, neo4j_service)),
            FunctionTool(bind_tool(check_documents, neo4j_service)),
            FunctionTool(bind_tool(assess_customer_risk, neo4j_service)),
            FunctionTool(bind_tool(check_adverse_media, neo4j_service)),
        ]
    else:
        tools = [
            FunctionTool(verify_identity),
            FunctionTool(check_documents),
            FunctionTool(assess_customer_risk),
            FunctionTool(check_adverse_media),
        ]

    # Add memory tools if service provided
    if memory_service:

        async def search_kyc_context(query: str, limit: int = 5) -> list[dict]:
            """Search for relevant KYC information in the context graph."""
            return await memory_service.search_context(
                query=f"KYC {query}",
                limit=limit,
            )

        async def store_kyc_finding(
            customer_id: str,
            finding: str,
            risk_level: str = "MEDIUM",
        ) -> str:
            """Store a KYC finding in the context graph."""
            return await memory_service.store_finding(
                content=f"KYC Finding for {customer_id}: {finding}",
                category="kyc",
                metadata={"customer_id": customer_id, "risk_level": risk_level},
            )

        tools.extend(
            [
                FunctionTool(search_kyc_context),
                FunctionTool(store_kyc_finding),
            ]
        )

    agent = LlmAgent(
        name="kyc_agent",
        model=model,
        description=(
            "KYC specialist for identity verification, document validation, "
            "and customer due diligence assessments."
        ),
        instruction=KYC_AGENT_INSTRUCTION,
        tools=tools,
    )

    logger.info("KYC Agent created")
    return agent
