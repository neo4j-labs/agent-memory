"""Supervisor Agent for orchestrating financial compliance investigations.

Coordinates multi-agent investigations by delegating to the specialists (KYC,
AML, Relationship, Compliance) via ADK's ``sub_agents`` and synthesising their
findings.

Memory: the supervisor gets ADK's ``load_memory`` for reads (served by
``Neo4jMemoryService`` on the ``Runner``) and one write tool that records
findings as ``:Fact`` nodes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, load_memory

from ..config import get_settings
from ._base import DEFAULT_MODEL
from .aml_agent import create_aml_agent
from .compliance_agent import create_compliance_agent
from .kyc_agent import create_kyc_agent
from .prompts import SUPERVISOR_INSTRUCTION
from .relationship_agent import create_relationship_agent

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)

# Global supervisor instance
_supervisor_agent: LlmAgent | None = None
_memory_service: FinancialMemoryService | None = None
_neo4j_service: Neo4jDomainService | None = None
_model: str | None = None

SUPERVISOR_DESCRIPTION = (
    "Senior Financial Compliance Supervisor that orchestrates "
    "comprehensive investigations by coordinating KYC, AML, "
    "relationship analysis, and compliance screening."
)


def create_supervisor_agent(
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
) -> LlmAgent:
    """Create the Supervisor Agent that orchestrates investigations.

    Args:
        memory_service: Memory service for context graph access.
        model: The Gemini model to use (propagated to every sub-agent).
        neo4j_service: Domain data service for Neo4j queries.

    Returns:
        Configured Supervisor Agent with sub-agents.
    """
    kyc_agent = create_kyc_agent(memory_service, model, neo4j_service=neo4j_service)
    aml_agent = create_aml_agent(memory_service, model, neo4j_service=neo4j_service)
    relationship_agent = create_relationship_agent(
        memory_service, model, neo4j_service=neo4j_service
    )
    compliance_agent = create_compliance_agent(memory_service, model, neo4j_service=neo4j_service)

    tools: list[object] = []

    if memory_service is not None:

        async def store_investigation_finding(
            content: str,
            customer_id: str | None = None,
            risk_level: str = "MEDIUM",
        ) -> str:
            """Record an investigation finding or conclusion for the audit trail.

            Args:
                content: The finding to store.
                customer_id: Related customer ID.
                risk_level: Risk level (LOW/MEDIUM/HIGH/CRITICAL).

            Returns:
                Confirmation of storage.
            """
            return await memory_service.store_finding(
                content=content,
                category="investigation_finding",
                metadata={
                    "customer_id": customer_id,
                    "risk_level": risk_level,
                    "source": "supervisor",
                },
            )

        # `load_memory` is ADK's built-in memory search, served by the
        # Neo4jMemoryService passed to Runner(memory_service=...).
        tools.extend([load_memory, FunctionTool(store_investigation_finding)])

    supervisor = LlmAgent(
        name="financial_advisor_supervisor",
        model=model,
        description=SUPERVISOR_DESCRIPTION,
        instruction=SUPERVISOR_INSTRUCTION,
        sub_agents=[kyc_agent, aml_agent, relationship_agent, compliance_agent],
        tools=tools,
    )

    logger.info("Supervisor Agent created (model=%s)", model)
    return supervisor


def get_supervisor_agent(
    memory_service: FinancialMemoryService | None = None,
    neo4j_service: Neo4jDomainService | None = None,
    model: str | None = None,
) -> LlmAgent:
    """Get or create the global Supervisor Agent instance.

    The model id comes from ``VERTEX_AI_MODEL_ID`` (via settings) unless an
    explicit ``model`` is passed, so changing that variable really does change
    the model in use — across the supervisor and all four specialists.

    Args:
        memory_service: Memory service for context graph access.
            Required on first call.
        neo4j_service: Domain data service for Neo4j queries.
        model: Optional explicit Gemini model id override.

    Returns:
        Supervisor Agent instance.
    """
    global _supervisor_agent, _memory_service, _neo4j_service, _model

    resolved_model = model or get_settings().vertex_ai.model_id

    if (
        _supervisor_agent is None
        or (memory_service and memory_service != _memory_service)
        or (neo4j_service and neo4j_service != _neo4j_service)
        or resolved_model != _model
    ):
        _memory_service = memory_service or _memory_service
        _neo4j_service = neo4j_service or _neo4j_service
        _model = resolved_model
        _supervisor_agent = create_supervisor_agent(
            _memory_service,
            resolved_model,
            neo4j_service=_neo4j_service,
        )

    return _supervisor_agent


def reset_supervisor_agent() -> None:
    """Reset the global supervisor agent instance.

    Useful for testing or when the memory service changes.
    """
    global _supervisor_agent, _memory_service, _neo4j_service, _model
    _supervisor_agent = None
    _memory_service = None
    _neo4j_service = None
    _model = None
