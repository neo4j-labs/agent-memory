"""Live-NAMS integration smoke — one happy path through all three memory types.

If this fails, something is fundamentally wrong with the v0.4 client.
The TCK tier suites (``test_tck_bronze.py`` onwards) exercise each
method individually for diagnostic value; this file is the single
overall "does anything work at all" smoke.

Note on NAMS semantics
======================

Unlike bolt — which creates conversations implicitly on the first
``add_message`` — NAMS (Platinum tier) requires explicit conversation
creation. The ``nams_session`` fixture in ``conftest.py`` handles this:
it calls ``create_conversation()`` first and returns the canonical id
NAMS expects for subsequent calls.
"""

from __future__ import annotations

import uuid

import pytest

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.core.exceptions import AuthenticationError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_smoke_full_flow(nams_client: MemoryClient, nams_session: str) -> None:
    """Connect → short-term + long-term + reasoning + cypher.

    Uses ``nams_session`` (pre-created conversation) so we follow the
    NAMS Platinum pattern from the start.
    """
    # Short-term: append a message to the pre-created conversation.
    msg = await nams_client.short_term.add_message(nams_session, "user", "Smoke test hello")
    assert msg.content == "Smoke test hello"

    conv = await nams_client.short_term.get_conversation(nams_session)
    assert len(conv.messages) >= 1

    # Long-term: standalone entity creation (no conversation needed).
    entity_name = f"SmokeTest-{uuid.uuid4().hex[:8]}"
    entity = await nams_client.long_term.add_entity(entity_name, "PERSON")
    entity = entity[0] if isinstance(entity, tuple) else entity
    assert entity.name.strip()
    if entity.name != entity_name:
        # Similar smoke-test names can merge across runs in the shared sandbox.
        resolution = entity.metadata.get("nams_resolution", {})
        assert resolution.get("resolution") == "merged"
        assert resolution.get("merged_into") == str(entity.id)
        assert resolution.get("fallback") is False

    # Reasoning: tied to the session.
    trace = await nams_client.reasoning.start_trace(nams_session, "smoke task")
    await nams_client.reasoning.complete_trace(trace.id, outcome="ok", success=True)

    # Cypher: pure read. NAMS may gate /v1/query behind an "internal access"
    # tier — sandbox keys frequently get 403 here. That's a deployment-level
    # authorization concern, not a client bug, so we accept either:
    #   * 200 with a list payload, or
    #   * AuthenticationError from a 403 "internal access required" response.
    try:
        rows = await nams_client.query.cypher("MATCH (n) RETURN count(n) AS n LIMIT 1")
    except AuthenticationError as exc:
        # Surface a clear skip reason so CI logs make this distinction obvious.
        pytest.skip(f"NAMS /v1/query gated on this sandbox key: {exc}")
    else:
        assert isinstance(rows, list)
