"""Tests for agent construction and the Neo4j tool binding."""

from __future__ import annotations

import inspect

from google.adk.memory import BaseMemoryService
from google.adk.tools import load_memory
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

from src.agents import bind_tool, create_kyc_agent, create_specialist_agent
from src.agents.supervisor import create_supervisor_agent, reset_supervisor_agent


def test_neo4j_memory_service_satisfies_the_adk_contract() -> None:
    """Passing it to ``Runner(memory_service=...)`` requires this."""
    assert issubclass(Neo4jMemoryService, BaseMemoryService)


def test_bind_tool_hides_neo4j_service_from_the_signature() -> None:
    async def sample_tool(customer_id: str, *, neo4j_service=None) -> str:
        return f"{customer_id}:{neo4j_service}"

    bound = bind_tool(sample_tool, "SERVICE")
    params = inspect.signature(bound).parameters
    assert "neo4j_service" not in params
    assert "customer_id" in params
    assert bound.__name__ == "sample_tool"


async def test_bind_tool_passes_the_service_through() -> None:
    async def sample_tool(customer_id: str, *, neo4j_service=None) -> str:
        return f"{customer_id}:{neo4j_service}"

    bound = bind_tool(sample_tool, "SERVICE")
    assert await bound("CUST-001") == "CUST-001:SERVICE"


def test_specialist_without_memory_has_only_domain_tools() -> None:
    async def tool_a(customer_id: str, *, neo4j_service=None) -> str:
        return customer_id

    agent = create_specialist_agent(
        name="test_agent",
        description="d",
        instruction="i",
        functions=[tool_a],
        memory_service=None,
    )
    assert [tool.name for tool in agent.tools] == ["tool_a"]


def test_specialist_with_memory_gets_adk_load_memory(fake_memory_service) -> None:
    async def tool_a(customer_id: str, *, neo4j_service=None) -> str:
        return customer_id

    async def store_thing(finding: str) -> str:
        return finding

    agent = create_specialist_agent(
        name="test_agent",
        description="d",
        instruction="i",
        functions=[tool_a],
        memory_service=fake_memory_service,
        write_tools=[store_thing],
    )
    names = [tool.name for tool in agent.tools]
    assert "load_memory" in names, "memory reads should use ADK's native tool"
    assert "store_thing" in names
    assert not any(name.startswith("search_") for name in names), (
        "hand-rolled per-agent search tools were replaced by load_memory"
    )


def test_kyc_agent_uses_the_requested_model(fake_memory_service) -> None:
    agent = create_kyc_agent(fake_memory_service, "gemini-3-pro-preview")
    assert agent.model == "gemini-3-pro-preview"
    assert agent.name == "kyc_agent"


def test_supervisor_propagates_the_model_to_sub_agents(fake_memory_service) -> None:
    reset_supervisor_agent()
    supervisor = create_supervisor_agent(fake_memory_service, "gemini-3-pro-preview")
    assert supervisor.model == "gemini-3-pro-preview"
    assert {agent.name for agent in supervisor.sub_agents} == {
        "kyc_agent",
        "aml_agent",
        "relationship_agent",
        "compliance_agent",
    }
    for sub_agent in supervisor.sub_agents:
        assert sub_agent.model == "gemini-3-pro-preview"
    assert load_memory in supervisor.tools


def test_get_supervisor_agent_reads_the_model_from_settings(
    fake_memory_service, monkeypatch
) -> None:
    from src import config
    from src.agents.supervisor import get_supervisor_agent

    monkeypatch.setenv("VERTEX_AI_MODEL_ID", "gemini-2.5-pro")
    config.get_settings.cache_clear()
    reset_supervisor_agent()
    try:
        supervisor = get_supervisor_agent(fake_memory_service)
        assert supervisor.model == "gemini-2.5-pro"
    finally:
        reset_supervisor_agent()
        config.get_settings.cache_clear()
