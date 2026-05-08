"""Integration tests for buffered writes against a real Neo4j (v0.4 P1.1)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from neo4j_agent_memory import MemoryClient


@pytest.fixture
async def buffered_client(
    memory_settings, mock_embedder, mock_extractor, mock_resolver
) -> AsyncGenerator:
    """A connected MemoryClient with ``write_mode='buffered'``."""
    settings = memory_settings.model_copy(deep=True)
    settings.memory.write_mode = "buffered"
    settings.memory.max_pending = 100

    client = MemoryClient(
        settings,
        embedder=mock_embedder,
        extractor=mock_extractor,
        resolver=mock_resolver,
    )
    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")

    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    yield client

    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
class TestBufferedWritesEndToEnd:
    async def test_buffered_submit_persists_after_flush(self, buffered_client):
        """Writes submitted via the buffered API land in Neo4j after flush."""
        client = buffered_client

        for i in range(10):
            await client.buffered.submit(
                """
                MERGE (t:BufferTest {id: $i})
                ON CREATE SET t.created_at = datetime()
                """,
                {"i": i},
            )

        # Drain.
        await client.flush()

        rows = await client.graph.execute_read("MATCH (t:BufferTest) RETURN count(t) AS cnt")
        assert rows[0]["cnt"] == 10
        assert client.write_errors == []

    async def test_close_drains_buffered_writes(self, memory_settings, mock_embedder):
        """``client.close()`` must drain queued writes before disconnecting."""
        settings = memory_settings.model_copy(deep=True)
        settings.memory.write_mode = "buffered"

        client = MemoryClient(settings, embedder=mock_embedder)
        await client.connect()
        try:
            await client._client.execute_write("MATCH (n:DrainTest) DETACH DELETE n")
        except Exception:
            pass
        for i in range(5):
            await client.buffered.submit("CREATE (n:DrainTest {i: $i})", {"i": i})
        await client.close()

        # Reopen and verify all 5 writes landed.
        client2 = MemoryClient(settings, embedder=mock_embedder)
        try:
            await client2.connect()
            rows = await client2.graph.execute_read("MATCH (n:DrainTest) RETURN count(n) AS cnt")
            assert rows[0]["cnt"] == 5
            await client2._client.execute_write("MATCH (n:DrainTest) DETACH DELETE n")
        finally:
            await client2.close()

    async def test_sync_mode_passthrough(self, clean_memory_client):
        """``write_mode='sync'`` (default) means submit awaits inline."""
        client = clean_memory_client
        await client.buffered.submit("CREATE (s:SyncTest {x: $x})", {"x": 42})
        # No flush needed in sync mode.
        rows = await client.graph.execute_read("MATCH (s:SyncTest) RETURN s.x AS x")
        assert rows[0]["x"] == 42
