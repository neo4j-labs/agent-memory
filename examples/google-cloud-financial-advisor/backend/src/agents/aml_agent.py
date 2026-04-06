"""AML Agent for transaction monitoring and suspicious activity detection.

This agent specializes in Anti-Money Laundering (AML) tasks including
transaction analysis, pattern detection, and SAR preparation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools.aml_tools import (
    analyze_velocity,
    detect_patterns,
    flag_suspicious_transaction,
    scan_transactions,
)
from .utils import bind_tool
from .prompts import AML_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


def create_aml_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = "gemini-2.5-flash",
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
    if neo4j_service:
        tools = [
            FunctionTool(bind_tool(scan_transactions, neo4j_service)),
            FunctionTool(bind_tool(detect_patterns, neo4j_service)),
            FunctionTool(bind_tool(flag_suspicious_transaction, neo4j_service)),
            FunctionTool(bind_tool(analyze_velocity, neo4j_service)),
        ]
    else:
        tools = [
            FunctionTool(scan_transactions),
            FunctionTool(detect_patterns),
            FunctionTool(flag_suspicious_transaction),
            FunctionTool(analyze_velocity),
        ]

    # Add memory tools if service provided
    if memory_service:

        async def search_aml_context(query: str, limit: int = 5) -> list[dict]:
            """Search for relevant AML information in the context graph."""
            return await memory_service.search_context(
                query=f"AML transaction {query}",
                limit=limit,
            )

        async def store_aml_finding(
            customer_id: str,
            finding: str,
            pattern_type: str | None = None,
        ) -> str:
            """Store an AML finding in the context graph."""
            return await memory_service.store_finding(
                content=f"AML Finding for {customer_id}: {finding}",
                category="aml",
                metadata={"customer_id": customer_id, "pattern_type": pattern_type},
            )

        async def record_suspicious_pattern(
            customer_id: str,
            pattern: str,
            transactions: list[str],
            confidence: float,
        ) -> str:
            """Record a detected suspicious pattern."""
            return await memory_service.store_finding(
                content=f"Suspicious pattern detected: {pattern} involving transactions {transactions}",
                category="aml_pattern",
                metadata={
                    "customer_id": customer_id,
                    "pattern": pattern,
                    "transactions": transactions,
                    "confidence": confidence,
                },
            )

        tools.extend(
            [
                FunctionTool(search_aml_context),
                FunctionTool(store_aml_finding),
                FunctionTool(record_suspicious_pattern),
            ]
        )

    agent = LlmAgent(
        name="aml_agent",
        model=model,
        description=(
            "AML analyst for transaction monitoring, suspicious pattern detection, "
            "and anti-money laundering investigations."
        ),
        instruction=AML_AGENT_INSTRUCTION,
        tools=tools,
    )

    logger.info("AML Agent created")
    return agent
