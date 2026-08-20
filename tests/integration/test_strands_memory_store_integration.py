"""Neo4jMemoryStore against a real Neo4j (bolt)."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_round_trip_recall(clean_memory_client) -> None:
    from neo4j_agent_memory.integrations.strands import (
        Neo4jMemoryStore,
        Neo4jMemoryStoreConfig,
    )

    await clean_memory_client.long_term.add_preference("ui", "Prefers dark mode")

    store = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=clean_memory_client))
    await store.initialize()
    entries = await store.search("dark mode")

    assert any("dark mode" in entry.content for entry in entries)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_add_messages_reuses_the_same_sink_across_instances(clean_memory_client) -> None:
    """Two store instances with the same name/user_id resolve to one sink.

    On bolt the sink name is deterministic (``strands-memory-store/{user}/{name}``)
    and *is* the conversation key — no backend lookup is needed to find it, unlike
    NAMS where ids are server-minted and the sink is found by matching metadata.
    This proves the bolt path: no duplicate sink conversation is created, and both
    instances' writes land in the one conversation.
    """
    from neo4j_agent_memory.integrations.strands import (
        Neo4jMemoryStore,
        Neo4jMemoryStoreConfig,
    )

    batch = [{"role": "user", "content": [{"text": "I work at Acme Corp"}]}]

    first = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=clean_memory_client))
    await first.initialize()
    await first.add_messages(batch, None)

    second = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=clean_memory_client))
    await second.initialize()
    await second.add_messages(batch, None)

    sink_name = await first._resolve_sink()
    assert await second._resolve_sink() == sink_name

    conversations = await clean_memory_client.short_term.list_conversations(limit=100)
    matching = [c for c in conversations if c.session_id == sink_name]
    assert len(matching) == 1, "exactly one sink conversation, not one per store instance"

    conversation = await clean_memory_client.short_term.get_conversation(sink_name)
    assert len(conversation.messages) == 2, "both instances' batches landed in the one sink"
