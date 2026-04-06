"""Relationship Agent for network analysis and beneficial ownership investigation.

This agent specializes in analyzing entity networks, tracing beneficial
ownership, and detecting shell companies using the Neo4j Context Graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools.relationship_tools import (
    analyze_network_risk,
    detect_shell_companies,
    find_connections,
    map_beneficial_ownership,
)
from .utils import bind_tool
from .prompts import RELATIONSHIP_AGENT_INSTRUCTION

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


def create_relationship_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = "gemini-2.5-flash",
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
    if neo4j_service:
        tools = [
            FunctionTool(bind_tool(find_connections, neo4j_service)),
            FunctionTool(bind_tool(analyze_network_risk, neo4j_service)),
            FunctionTool(bind_tool(detect_shell_companies, neo4j_service)),
            FunctionTool(bind_tool(map_beneficial_ownership, neo4j_service)),
        ]
    else:
        tools = [
            FunctionTool(find_connections),
            FunctionTool(analyze_network_risk),
            FunctionTool(detect_shell_companies),
            FunctionTool(map_beneficial_ownership),
        ]

    # Add memory tools if service provided
    if memory_service:

        async def search_entity_context(entity_name: str, limit: int = 10) -> list[dict]:
            """Search for information about an entity in the context graph."""
            return await memory_service.search_context(
                query=f"entity {entity_name}",
                limit=limit,
            )

        async def store_relationship_finding(
            entity_id: str,
            finding: str,
            related_entities: list[str] | None = None,
        ) -> str:
            """Store a relationship analysis finding."""
            return await memory_service.store_finding(
                content=f"Relationship Finding for {entity_id}: {finding}",
                category="relationship",
                metadata={
                    "entity_id": entity_id,
                    "related_entities": related_entities or [],
                },
            )

        async def record_network_structure(
            root_entity: str,
            nodes: list[dict],
            edges: list[dict],
        ) -> str:
            """Record a network structure for visualization."""
            import json

            return await memory_service.store_finding(
                content=f"Network structure for {root_entity}",
                category="network_graph",
                metadata={
                    "root_entity": root_entity,
                    "nodes": json.dumps(nodes),
                    "edges": json.dumps(edges),
                },
            )

        tools.extend(
            [
                FunctionTool(search_entity_context),
                FunctionTool(store_relationship_finding),
                FunctionTool(record_network_structure),
            ]
        )

    agent = LlmAgent(
        name="relationship_agent",
        model=model,
        description=(
            "Relationship intelligence analyst for network analysis, beneficial "
            "ownership tracing, and shell company detection using the Context Graph."
        ),
        instruction=RELATIONSHIP_AGENT_INSTRUCTION,
        tools=tools,
    )

    logger.info("Relationship Agent created")
    return agent
