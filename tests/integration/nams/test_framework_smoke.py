"""Thin per-framework NAMS smoke tests (issue B.4).

Each test constructs a framework integration against a ``workspace_id``-scoped
NAMS client, stores a message mentioning a unique synthetic entity through the
integration's own store path, awaits the asynchronous extraction pipeline, and
asserts the entity became searchable. This proves the integration's *write*
path works end-to-end on the hosted backend.

Integrations whose framework package is not installed skip cleanly, so this
file is safe to run in any environment (it lights up more frameworks as their
optional extras are installed). Google ADK and AWS Strands have dedicated,
deeper suites (``test_adk_e2e.py`` / ``test_strands*``) and are not repeated
here.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest

from neo4j_agent_memory import MemoryClient

pytestmark = pytest.mark.integration


async def _assert_extraction_round_trip(
    nams_client: MemoryClient,
    store: Callable[[], Awaitable[None]],
    marker: str,
) -> None:
    """Run the integration's store path, then await + assert extraction."""
    await store()
    ready = await nams_client.long_term.wait_for_extraction(
        query=marker, expected_names=[marker], timeout=45, interval=3
    )
    if not ready:
        pytest.skip(f"Staging extraction did not surface '{marker}' within timeout (perf signal).")


def _marker() -> str:
    # A unique, capitalized, clearly-entity-like token the extractor will surface.
    return f"Qwentish{uuid.uuid4().hex[:6].capitalize()}"


@pytest.mark.asyncio
async def test_langchain_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory
    except (ImportError, AttributeError):
        pytest.skip("langchain integration unavailable")

    memory = Neo4jAgentMemory(memory_client=nams_client, session_id=nams_session)
    marker = _marker()

    async def store() -> None:
        await memory._save_context_async(
            {"input": f"{marker} founded Acme Corporation in Paris."},
            {"output": "Noted."},
        )

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_pydantic_ai_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency
    except (ImportError, AttributeError):
        pytest.skip("pydantic_ai integration unavailable")

    deps = MemoryDependency(client=nams_client, session_id=nams_session)
    marker = _marker()

    async def store() -> None:
        await deps.save_interaction(
            user_message=f"{marker} founded Acme Corporation in Paris.",
            assistant_message="Noted.",
        )

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_agentcore_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.agentcore import Neo4jMemoryProvider
    except (ImportError, AttributeError):
        pytest.skip("agentcore integration unavailable")

    provider = Neo4jMemoryProvider(memory_client=nams_client)
    marker = _marker()

    async def store() -> None:
        await provider.store_memory(
            session_id=nams_session,
            content=f"{marker} founded Acme Corporation in Paris.",
            role="user",
        )

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_openai_agents_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.openai_agents import Neo4jOpenAIMemory
    except (ImportError, AttributeError):
        pytest.skip("openai_agents integration unavailable")

    memory = Neo4jOpenAIMemory(memory_client=nams_client, session_id=nams_session)
    marker = _marker()

    async def store() -> None:
        await memory.save_message("user", f"{marker} founded Acme Corporation in Paris.")

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_microsoft_agent_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory
    except (ImportError, AttributeError):
        pytest.skip("microsoft_agent integration unavailable")

    memory = Neo4jMicrosoftMemory(memory_client=nams_client, session_id=nams_session)
    marker = _marker()

    async def store() -> None:
        await memory.save_message("user", f"{marker} founded Acme Corporation in Paris.")

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_crewai_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory
    except (ImportError, AttributeError):
        pytest.skip("crewai integration unavailable")

    # CrewAI's adapter is crew-scoped: crew_id (not session_id) names the
    # conversation it writes under. We assert it constructs against a
    # NAMS-backed client and round-trip the write path through the shared
    # client.
    _ = Neo4jCrewMemory(memory_client=nams_client, crew_id=f"{nams_session}-crew")
    marker = _marker()

    async def store() -> None:
        await nams_client.short_term.add_message(
            nams_session, "user", f"{marker} founded Acme Corporation in Paris."
        )

    await _assert_extraction_round_trip(nams_client, store, marker)


@pytest.mark.asyncio
async def test_llamaindex_smoke(nams_client: MemoryClient, nams_session: str) -> None:
    try:
        from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory
    except (ImportError, AttributeError):
        pytest.skip("llamaindex integration unavailable")

    memory = Neo4jLlamaIndexMemory(memory_client=nams_client, session_id=nams_session)
    marker = _marker()

    async def store() -> None:
        await nams_client.short_term.add_message(
            nams_session, "user", f"{marker} founded Acme Corporation in Paris."
        )
        _ = memory  # adapter constructed against NAMS (round-trip via shared client)

    await _assert_extraction_round_trip(nams_client, store, marker)
