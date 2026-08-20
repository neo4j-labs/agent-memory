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


@pytest.mark.integration
def test_an_owned_client_survives_the_loop_change_strands_forces(neo4j_connection_info) -> None:
    """The Quick Start's shape: ``settings=``, driven by synchronous ``Agent(...)``.

    ``Agent.__init__`` calls ``MemoryManager.init_agent`` -> ``store.initialize()``
    through ``strands._async.run_async`` (``asyncio.run`` in a throwaway thread),
    and every ``Agent.__call__`` runs on a *different* loop. The neo4j async
    driver stays bound to the loop that opened it, so before the rebind in
    ``initialize()`` this raised ``RuntimeError: Task ... attached to a
    different loop`` from deep inside the driver.

    Real work, not ``search()``: with no embedder ``search()`` short-circuits
    to ``[]`` without touching the driver, so it would pass either way.
    """
    from pydantic import SecretStr
    from strands._async import run_async

    from neo4j_agent_memory import MemorySettings
    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.integrations.strands import (
        Neo4jMemoryStore,
        Neo4jMemoryStoreConfig,
    )

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=neo4j_connection_info["uri"],
            username=neo4j_connection_info["username"],
            password=SecretStr(neo4j_connection_info["password"]),
        )
    )
    store = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="loop-rebind", settings=settings))

    # Loop A — what Agent.__init__ does.
    run_async(store.initialize)
    connecting_loop = store._connected_loop
    assert connecting_loop is not None

    # Loop B — what Agent.__call__ does.
    async def real_work() -> list[dict[str, object]]:
        await store.initialize()
        return await store._client.query.cypher("RETURN 1 AS n")

    try:
        assert run_async(real_work) == [{"n": 1}]
        assert store._connected_loop is not connecting_loop, "the store rebound to the new loop"
    finally:
        run_async(store.aclose)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_entity_graph_reports_what_the_bolt_stack_can_actually_report(
    clean_memory_client,
) -> None:
    """Pins the edge payload against a real Neo4j — and a library defect under it.

    ``Neo4jClient.execute_read`` returns ``result.data()``, which renders a
    relationship as ``(start_props, type, end_props)`` and drops its
    properties entirely. ``LongTermMemory.get_related_entities`` therefore
    falls through to ``type="RELATED_TO"`` for every hit even though
    ``CREATE_ENTITY_RELATIONSHIP`` stored ``r.type = "WORKS_AT"``, and it
    hardcodes ``source_id`` to the centre because
    ``GET_ENTITY_RELATIONSHIPS`` matches undirected.

    That defect is the library's, not the store's, and is deliberately left
    for a separate change; this test pins today's behaviour so a fix shows
    up here (and in ``strands_fakes.FakeLongTerm.get_related_entities``,
    which mirrors it) rather than silently changing what the tool tells the
    model.
    """
    from neo4j_agent_memory.integrations.strands._store_tools import _entity_graph

    long_term = clean_memory_client.long_term
    acme, _ = await long_term.add_entity("Acme Corp", "ORGANIZATION", deduplicate=False)
    ada, _ = await long_term.add_entity("Ada Lovelace", "PERSON", deduplicate=False)
    await long_term.add_relationship(ada, acme, "WORKS_AT")

    result = await _entity_graph(clean_memory_client, "Acme Corp", depth=2, nams=False)

    assert result["center"] == "Acme Corp"
    assert result["edges"] == [
        {"from": "Acme Corp", "relationship": "RELATED_TO", "to": "Ada Lovelace"}
    ]
