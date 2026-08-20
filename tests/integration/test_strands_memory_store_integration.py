"""Neo4jMemoryStore against a real Neo4j (bolt)."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

#: Dimensionality of ``tests/conftest.py``'s ``MockEmbedder``, which
#: ``clean_memory_client`` wires in for this module's tests.
_EXPECTED_DIMENSIONS = 1536

#: Same set as ``SchemaManager._MANAGED_VECTOR_INDEXES``
#: (``src/neo4j_agent_memory/graph/schema.py``).
_MANAGED_VECTOR_INDEXES = (
    "entity_embedding_idx",
    "fact_embedding_idx",
    "message_embedding_idx",
    "preference_embedding_idx",
    "step_embedding_idx",
    "task_embedding_idx",
)


@pytest.fixture(autouse=True)
def _skip_on_incompatible_vector_indexes(neo4j_connection_info) -> None:
    """Skip with a clear reason if the container's schema won't fit this suite.

    The docker-compose Neo4j container (``docker-compose.test.yml``) uses a
    *named* volume: data and vector indexes survive ``make neo4j-stop`` /
    ``neo4j-start`` (only ``make neo4j-clean`` wipes it). If
    ``examples/strands-memory-store/`` (a 384-dim sentence-transformers
    embedder) was run against the same container, its vector indexes are
    sized for 384 dimensions. ``clean_memory_client`` then connects with the
    1536-dim ``MockEmbedder`` and ``MemoryClient.connect()`` raises
    ``EmbeddingDimensionMismatchError`` deep inside that fixture's generic
    ``except Exception: pytest.skip(f"Neo4j not available: {e}")`` — a
    reason that reads as "no Neo4j" when the real story is "wrong schema".

    This checks the raw index dimensions first (bypassing ``MemoryClient``
    and its embedder entirely) so the skip names the actual cause and the
    fix. It only reads ``SHOW VECTOR INDEXES`` — it never drops or modifies
    anything, since these indexes may serve other embedders the developer
    is intentionally running against this container.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_connection_info["uri"],
        auth=(neo4j_connection_info["username"], neo4j_connection_info["password"]),
    )
    try:
        with driver.session() as session:
            rows = session.run(
                "SHOW VECTOR INDEXES YIELD name, options RETURN name AS name, options AS options"
            ).data()
    finally:
        driver.close()

    mismatched = []
    for row in rows:
        name = row.get("name")
        if name not in _MANAGED_VECTOR_INDEXES:
            continue
        config = (row.get("options") or {}).get("indexConfig") or {}
        dims = config.get("vector.dimensions")
        if isinstance(dims, int) and dims != _EXPECTED_DIMENSIONS:
            mismatched.append(f"{name} ({dims}d)")

    if mismatched:
        pytest.skip(
            f"Neo4j at {neo4j_connection_info['uri']!r} has vector indexes sized "
            f"for a different embedder than this suite's {_EXPECTED_DIMENSIONS}-dim "
            f"MockEmbedder: {', '.join(mismatched)}. Likely cause: a prior run of "
            "examples/strands-memory-store/ (384-dim sentence-transformers) against "
            "this same persistent container. Fix: `make neo4j-clean` (drops the "
            "container's data volume), then `make neo4j-start`."
        )


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
