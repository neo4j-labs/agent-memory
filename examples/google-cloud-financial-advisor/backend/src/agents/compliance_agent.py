"""Compliance Agent for regulatory screening and report generation.

This agent specializes in sanctions screening, PEP verification,
and regulatory compliance reporting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools.compliance_tools import (
    assess_regulatory_requirements,
    check_sanctions,
    generate_sar_report,
    verify_pep_status,
)
from .utils import bind_tool
from .prompts import COMPLIANCE_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


def create_compliance_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = "gemini-2.5-flash",
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
    if neo4j_service:
        tools = [
            FunctionTool(bind_tool(check_sanctions, neo4j_service)),
            FunctionTool(bind_tool(verify_pep_status, neo4j_service)),
            FunctionTool(bind_tool(generate_sar_report, neo4j_service)),
            FunctionTool(bind_tool(assess_regulatory_requirements, neo4j_service)),
        ]
    else:
        tools = [
            FunctionTool(check_sanctions),
            FunctionTool(verify_pep_status),
            FunctionTool(generate_sar_report),
            FunctionTool(assess_regulatory_requirements),
        ]

    # Add memory tools if service provided
    if memory_service:

        async def search_compliance_context(query: str, limit: int = 5) -> list[dict]:
            """Search for relevant compliance information in the context graph."""
            return await memory_service.search_context(
                query=f"compliance regulatory {query}",
                limit=limit,
            )

        async def store_compliance_finding(
            entity_name: str,
            finding: str,
            screening_type: str,
            match_found: bool = False,
        ) -> str:
            """Store a compliance screening finding."""
            return await memory_service.store_finding(
                content=f"Compliance Finding for {entity_name}: {finding}",
                category="compliance",
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
            """Record a regulatory filing for audit trail."""
            return await memory_service.store_finding(
                content=f"Regulatory filing {filing_type} ({reference}) for {customer_id}: {status}",
                category="regulatory_filing",
                metadata={
                    "filing_type": filing_type,
                    "reference": reference,
                    "customer_id": customer_id,
                    "status": status,
                },
            )

        tools.extend(
            [
                FunctionTool(search_compliance_context),
                FunctionTool(store_compliance_finding),
                FunctionTool(record_regulatory_filing),
            ]
        )

    agent = LlmAgent(
        name="compliance_agent",
        model=model,
        description=(
            "Regulatory compliance specialist for sanctions screening, "
            "PEP verification, and regulatory report preparation."
        ),
        instruction=COMPLIANCE_AGENT_INSTRUCTION,
        tools=tools,
    )

    logger.info("Compliance Agent created")
    return agent
