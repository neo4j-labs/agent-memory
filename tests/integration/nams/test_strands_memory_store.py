"""Live-NAMS integration test — ``Neo4jMemoryStore`` sink resolution.

Verifies the metadata round-trip a manual live probe confirmed (see
``.superpowers/sdd/2026-08-19-strands-memory-store/nams-live-verification-report.md``):
NAMS returns conversation ``metadata`` on both ``create`` and
``list_conversations``, which is exactly what
``Neo4jMemoryStore._resolve_nams_sink`` depends on to find its existing
sink across restarts instead of minting a fresh conversation every time.

The spec's Testing table (``docs/superpowers/specs/2026-08-19-strands-memory-store-design.md``)
promised this suite as "key-gated NAMS ... not yet implemented" — this
closes that gap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

pytest.importorskip("strands", reason="strands-agents not installed")

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore, Neo4jMemoryStoreConfig
from neo4j_agent_memory.integrations.strands.memory_store import _STORE_KEY

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def _sink_cleanup(nams_client: MemoryClient) -> AsyncIterator[list[str]]:
    """Tracks conversation ids this test resolves/creates; best-effort teardown.

    Runs even when an assertion above fails, so a broken test never leaves
    a conversation behind in the shared sandbox workspace.
    """
    created: list[str] = []
    try:
        yield created
    finally:
        for conversation_id in created:
            try:
                await nams_client.short_term.clear_session(conversation_id)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass


@pytest.mark.asyncio
async def test_resolve_sink_reuses_metadata_tagged_conversation(
    nams_client: MemoryClient,
    test_run_id: str,
    _sink_cleanup: list[str],
) -> None:
    """A second store with the same ``name``/``user_id`` resolves the same sink.

    ``test_run_id`` is a fresh UUID-suffixed prefix per test invocation (see
    ``conftest.py``), so the store name here can't collide with, or be
    mistaken for, another concurrent or prior run's sink.
    """
    store_name = f"{test_run_id}-store"
    user_id = f"{test_run_id}-user"

    store_a = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(name=store_name, client=nams_client, user_id=user_id)
    )
    store_b = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(name=store_name, client=nams_client, user_id=user_id)
    )

    # First store: no existing sink yet -- creates one, tagged with metadata.
    sink_a = await store_a._resolve_sink()
    _sink_cleanup.append(sink_a)

    # Second, independent store instance with the same name/user_id: must
    # find the same sink via the metadata tag rather than creating another.
    sink_b = await store_b._resolve_sink()

    assert sink_a == sink_b

    conversations = await nams_client.short_term.list_conversations(
        user_identifier=user_id, limit=1000
    )
    matching = [
        conversation
        for conversation in conversations
        if (conversation.metadata or {}).get(_STORE_KEY) == store_a._sink_name
    ]
    assert len(matching) == 1, (
        f"expected exactly one conversation tagged with this store's sink, found {len(matching)}"
    )
    assert str(matching[0].id) == sink_a
