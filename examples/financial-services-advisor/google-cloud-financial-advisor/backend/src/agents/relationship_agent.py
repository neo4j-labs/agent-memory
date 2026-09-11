"""Relationship Agent for network analysis and beneficial ownership investigation.

Specialises in analysing entity networks, tracing beneficial ownership and
detecting shell companies using the Neo4j context graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from ..tools.relationship_tools import (
    analyze_network_risk,
    detect_shell_companies,
    find_connections,
    map_beneficial_ownership,
)
from ._base import DEFAULT_MODEL, create_specialist_agent
from .prompts import RELATIONSHIP_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

RELATIONSHIP_DESCRIPTION = (
    "Relationship intelligence analyst for network analysis, beneficial "
    "ownership tracing, and shell company detection using the Context Graph."
)


def create_relationship_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
) -> LlmAgent:
    """Create the Relationship Agent.

    Args:
        memory_service: Optional memory service for context graph access.
        model: The Gemini model to use.
        neo4j_service: Domain data service for Neo4j queries.

    Returns:
        Configured Relationship Agent.
    """
    write_tools = []

    if memory_service is not None:

        async def store_relationship_finding(
            entity_id: str,
            finding: str,
            related_entities: list[str] | None = None,
        ) -> str:
            """Record a relationship-analysis finding in the context graph."""
            return await memory_service.store_finding(
                content=finding,
                category="relationship_finding",
                metadata={
                    "entity_id": entity_id,
                    "related_entities": related_entities or [],
                },
            )

        write_tools = [store_relationship_finding]

    return create_specialist_agent(
        name="relationship_agent",
        description=RELATIONSHIP_DESCRIPTION,
        instruction=RELATIONSHIP_AGENT_INSTRUCTION,
        functions=[
            find_connections,
            analyze_network_risk,
            detect_shell_companies,
            map_beneficial_ownership,
        ],
        memory_service=memory_service,
        model=model,
        neo4j_service=neo4j_service,
        write_tools=write_tools,
    )
