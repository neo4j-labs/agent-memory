"""Supervisor agent for financial compliance investigations.

Builds one Strands ``Agent`` per chat session. The supervisor delegates to four
specialist sub-agents (KYC, AML, Relationship, Compliance) whose 16 tools all
read real data from Neo4j through :class:`Neo4jDomainService`, and it also gets
the library's own Context Graph tools from
``neo4j_agent_memory.integrations.strands``.

Why per-session and not one global agent: a Strands ``Agent`` owns its
``messages`` history, and ``Agent.stream_async`` takes a non-blocking lock that
raises ``ConcurrencyException`` on a second concurrent invocation. A single
shared instance therefore merges every user's transcript *and* fails the second
concurrent request.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from strands import Agent, tool
from strands.models import BedrockModel

from ..config import Settings, get_settings
from ..services.neo4j_service import Neo4jDomainService
from ..tools import bind_tool
from ..tools.aml_tools import (
    analyze_velocity,
    detect_patterns,
    flag_suspicious_transaction,
    scan_transactions,
)
from ..tools.compliance_tools import (
    assess_regulatory_requirements,
    check_sanctions,
    generate_sar_report,
    verify_pep_status,
)
from ..tools.kyc_tools import (
    assess_customer_risk,
    check_adverse_media,
    check_documents,
    verify_identity,
)
from ..tools.relationship_tools import (
    analyze_network_risk,
    detect_shell_companies,
    find_connections,
    map_beneficial_ownership,
)
from .prompts import (
    AML_AGENT_SYSTEM_PROMPT,
    COMPLIANCE_AGENT_SYSTEM_PROMPT,
    KYC_AGENT_SYSTEM_PROMPT,
    RELATIONSHIP_AGENT_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
)

if TYPE_CHECKING:
    from strands.types.tools import AgentTool

logger = logging.getLogger(__name__)

#: Bounded per-session agent cache: session_id -> (agent, last_used_epoch).
_AGENT_CACHE: OrderedDict[str, tuple[Agent, float]] = OrderedDict()
_CACHE_MAX_SESSIONS = 64
_CACHE_TTL_SECONDS = 60 * 60


def _truncate(value: Any, max_len: int = 500) -> str:
    """Truncate a value to a string for logging and SSE payloads."""
    text = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    return text[:max_len] + "..." if len(text) > max_len else text


def _bedrock_model(settings: Settings) -> BedrockModel:
    """Build the Bedrock model both the supervisor and sub-agents use.

    ``settings.bedrock.model_id`` defaults to the cross-region
    inference-profile id resolved by the library's
    ``integrations.strands.bedrock_llm_model()`` helper — current Claude models
    on Bedrock are invoked through an inference profile, not the bare
    foundation-model id.
    """
    return BedrockModel(model_id=settings.bedrock.model_id, region_name=settings.aws.region)


def _sub_agent(system_prompt: str, tools: list[AgentTool], settings: Settings) -> Agent:
    # `Agent(tools=...)` is annotated with an invariant list type, so a
    # list[AgentTool] is rejected even though every element is accepted.
    return Agent(
        model=_bedrock_model(settings),
        tools=list(tools),  # type: ignore[arg-type]
        system_prompt=system_prompt,
    )


def create_supervisor_agent(
    neo4j_service: Neo4jDomainService,
    *,
    settings: Settings | None = None,
) -> Agent:
    """Build a supervisor agent wired to the Neo4j-backed specialist agents."""
    settings = settings or get_settings()

    kyc_agent = _sub_agent(
        KYC_AGENT_SYSTEM_PROMPT,
        [
            bind_tool(verify_identity, neo4j_service),
            bind_tool(check_documents, neo4j_service),
            bind_tool(assess_customer_risk, neo4j_service),
            bind_tool(check_adverse_media, neo4j_service),
        ],
        settings,
    )
    aml_agent = _sub_agent(
        AML_AGENT_SYSTEM_PROMPT,
        [
            bind_tool(scan_transactions, neo4j_service),
            bind_tool(detect_patterns, neo4j_service),
            bind_tool(flag_suspicious_transaction, neo4j_service),
            bind_tool(analyze_velocity, neo4j_service),
        ],
        settings,
    )
    relationship_agent = _sub_agent(
        RELATIONSHIP_AGENT_SYSTEM_PROMPT,
        [
            bind_tool(find_connections, neo4j_service),
            bind_tool(analyze_network_risk, neo4j_service),
            bind_tool(detect_shell_companies, neo4j_service),
            bind_tool(map_beneficial_ownership, neo4j_service),
        ],
        settings,
    )
    compliance_agent = _sub_agent(
        COMPLIANCE_AGENT_SYSTEM_PROMPT,
        [
            bind_tool(check_sanctions, neo4j_service),
            bind_tool(verify_pep_status, neo4j_service),
            bind_tool(generate_sar_report, neo4j_service),
            bind_tool(assess_regulatory_requirements, neo4j_service),
        ],
        settings,
    )

    @tool
    async def delegate_to_kyc_agent(
        customer_id: str,
        task: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Delegate a KYC task to the KYC Agent.

        Use this tool when you need to verify customer identity, check documents,
        or perform customer due diligence.

        Args:
            customer_id: The customer identifier to investigate
            task: Specific KYC task to perform
            context: Additional context for the task
        """
        prompt = f"Perform KYC task for customer {customer_id}: {task}"
        if context:
            prompt += f"\nContext: {context}"
        result = await kyc_agent.invoke_async(prompt)
        return {
            "agent": "kyc",
            "customer_id": customer_id,
            "task": task,
            "findings": _truncate(str(result), 2000),
            "status": "completed",
        }

    @tool
    async def delegate_to_aml_agent(
        customer_id: str,
        task: str,
        time_period_days: int = 90,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Delegate an AML task to the AML Agent.

        Use this tool when you need to analyze transactions, detect suspicious
        patterns, or investigate potential money laundering activity.

        Args:
            customer_id: The customer identifier to investigate
            task: Specific AML task to perform
            time_period_days: Number of days of history to analyze
            context: Additional context for the task
        """
        prompt = (
            f"Perform AML task for customer {customer_id}: {task}. "
            f"Time period: last {time_period_days} days."
        )
        if context:
            prompt += f"\nContext: {context}"
        result = await aml_agent.invoke_async(prompt)
        return {
            "agent": "aml",
            "customer_id": customer_id,
            "task": task,
            "findings": _truncate(str(result), 2000),
            "status": "completed",
        }

    @tool
    async def delegate_to_relationship_agent(
        customer_id: str,
        task: str,
        depth: int = 2,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Delegate a relationship analysis task to the Relationship Agent.

        Use this tool when you need to analyze customer networks, find connections,
        or trace beneficial ownership.

        Args:
            customer_id: The customer identifier to investigate
            task: Specific relationship task to perform
            depth: Network traversal depth (1-3)
            context: Additional context for the task
        """
        prompt = f"Analyze relationships for {customer_id}: {task}. Network depth: {depth} hops."
        if context:
            prompt += f"\nContext: {context}"
        result = await relationship_agent.invoke_async(prompt)
        return {
            "agent": "relationship",
            "customer_id": customer_id,
            "task": task,
            "findings": _truncate(str(result), 2000),
            "status": "completed",
        }

    @tool
    async def delegate_to_compliance_agent(
        customer_id: str,
        task: str,
        report_type: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Delegate a compliance task to the Compliance Agent.

        Use this tool for sanctions screening, PEP checks, or report generation.

        Args:
            customer_id: The customer identifier to check
            task: Specific compliance task to perform
            report_type: Type of report to generate if applicable
            context: Additional context for the task
        """
        prompt = f"Perform compliance task for customer {customer_id}: {task}."
        if report_type:
            prompt += f" Report type: {report_type}."
        if context:
            prompt += f"\nContext: {context}"
        result = await compliance_agent.invoke_async(prompt)
        return {
            "agent": "compliance",
            "customer_id": customer_id,
            "task": task,
            "findings": _truncate(str(result), 2000),
            "status": "completed",
        }

    @tool
    def summarize_investigation(
        customer_id: str,
        kyc_findings: str | None = None,
        aml_findings: str | None = None,
        relationship_findings: str | None = None,
        compliance_findings: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize findings from all agents into a comprehensive investigation summary.

        Args:
            customer_id: The customer under investigation
            kyc_findings: Findings from KYC agent
            aml_findings: Findings from AML agent
            relationship_findings: Findings from Relationship agent
            compliance_findings: Findings from Compliance agent
        """
        sections = [
            ("## KYC Findings", kyc_findings),
            ("## AML Findings", aml_findings),
            ("## Relationship Analysis", relationship_findings),
            ("## Compliance Findings", compliance_findings),
        ]
        combined = "".join(f"{heading}\n{body}\n\n" for heading, body in sections if body)

        lower = combined.lower()
        if "critical" in lower or "sanctions" in lower:
            overall_risk = "CRITICAL"
        elif "high" in lower or "suspicious" in lower:
            overall_risk = "HIGH"
        else:
            overall_risk = "MEDIUM"

        return {
            "customer_id": customer_id,
            "overall_risk": overall_risk,
            "summary": combined,
            "agents_consulted": [
                name
                for name, findings in [
                    ("kyc", kyc_findings),
                    ("aml", aml_findings),
                    ("relationship", relationship_findings),
                    ("compliance", compliance_findings),
                ]
                if findings
            ],
            "status": "synthesized",
        }

    all_tools: list[Any] = [
        delegate_to_kyc_agent,
        delegate_to_aml_agent,
        delegate_to_relationship_agent,
        delegate_to_compliance_agent,
        summarize_investigation,
    ]

    # The library's own Strands tools: semantic search over the Context Graph,
    # entity/fact writes, and graph traversal. Already @tool-decorated.
    try:
        from neo4j_agent_memory.integrations.strands import StrandsConfig, context_graph_tools

        config = StrandsConfig.from_env()
        all_tools.extend(context_graph_tools(**config.to_dict()))
    except Exception as exc:  # pragma: no cover - depends on the environment
        logger.warning("Context Graph tools unavailable: %s", exc)

    return Agent(
        model=_bedrock_model(settings),
        tools=all_tools,
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
    )


def get_supervisor_agent(
    neo4j_service: Neo4jDomainService,
    session_id: str,
    *,
    settings: Settings | None = None,
) -> Agent:
    """Return the agent for ``session_id``, building it on first use.

    The cache is bounded (LRU, :data:`_CACHE_MAX_SESSIONS`) and entries expire
    after :data:`_CACHE_TTL_SECONDS`, so a long-running process does not grow
    without limit. Each session gets its own conversation history and its own
    concurrency lock.
    """
    now = time.monotonic()
    for stale in [
        sid for sid, (_, seen) in _AGENT_CACHE.items() if now - seen > _CACHE_TTL_SECONDS
    ]:
        _AGENT_CACHE.pop(stale, None)

    cached = _AGENT_CACHE.get(session_id)
    if cached is not None:
        _AGENT_CACHE[session_id] = (cached[0], now)
        _AGENT_CACHE.move_to_end(session_id)
        return cached[0]

    agent = create_supervisor_agent(neo4j_service, settings=settings)
    _AGENT_CACHE[session_id] = (agent, now)
    _AGENT_CACHE.move_to_end(session_id)
    while len(_AGENT_CACHE) > _CACHE_MAX_SESSIONS:
        _AGENT_CACHE.popitem(last=False)
    return agent


def reset_supervisor_agent() -> None:
    """Drop every cached agent (lifespan startup/shutdown, and tests)."""
    _AGENT_CACHE.clear()
