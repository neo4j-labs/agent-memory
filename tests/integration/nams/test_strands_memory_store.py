"""Live-NAMS integration test — ``Neo4jMemoryStore`` sink resolution.

``Neo4jMemoryStore._resolve_nams_sink`` finds its existing sink conversation by
a metadata tag, so it depends on NAMS returning conversation ``metadata`` from
both ``create_conversation`` and ``list_conversations``. This asserts that
round-trip against the live service.

Creates conversations only — no messages, so NAMS extracts nothing and
``clear_session`` teardown is complete.
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
    """Conversation ids to delete afterwards, even if an assertion fails."""
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

    ``test_run_id`` is a per-invocation UUID prefix (see ``conftest.py``), so
    this store name cannot collide with a concurrent or prior run's sink.
    """
    store_name = f"{test_run_id}-store"
    user_id = f"{test_run_id}-user"

    store_a = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(name=store_name, client=nams_client, user_id=user_id)
    )
    store_b = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(name=store_name, client=nams_client, user_id=user_id)
    )

    # No sink exists yet: this creates one, tagged with metadata.
    sink_a = await store_a._resolve_sink()
    _sink_cleanup.append(sink_a)

    # An independent store instance with the same name/user_id must find that
    # sink through the tag rather than create a second one.
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
