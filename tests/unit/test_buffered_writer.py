"""Unit tests for ``BufferedWriter`` (v0.4 P1.1)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from neo4j_agent_memory.memory.buffered import BufferedWriter


class FakeNeo4jClient:
    """A minimal stand-in for ``Neo4jClient`` used by the buffered writer."""

    def __init__(self, *, fail_on: set[str] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail_on = fail_on or set()
        # ``call_complete`` lets tests await individual writes
        # deterministically instead of sleeping.
        self.call_complete = asyncio.Event()

    async def execute_write(self, query: str, parameters: dict[str, Any]) -> list:
        # Yield to the event loop so the test can interleave checks if it
        # wants to. This isn't strictly necessary but matches Neo4j's
        # typical async behavior.
        await asyncio.sleep(0)
        if any(needle in query for needle in self._fail_on):
            raise RuntimeError(f"Fake failure on query containing: {query[:32]!r}")
        self.calls.append((query, parameters))
        self.call_complete.set()


class TestBufferedWriterSyncMode:
    @pytest.mark.asyncio
    async def test_submit_runs_inline_in_sync_mode(self):
        """In ``sync`` mode, ``submit`` awaits the underlying write directly."""
        fake = FakeNeo4jClient()
        writer = BufferedWriter(fake, write_mode="sync")

        await writer.submit("CREATE (n:Test)", {"x": 1})

        assert fake.calls == [("CREATE (n:Test)", {"x": 1})]
        # No drainer task should have started.
        assert writer.pending == 0
        assert writer.errors == []

    @pytest.mark.asyncio
    async def test_sync_mode_propagates_errors(self):
        fake = FakeNeo4jClient(fail_on={"TEST"})
        writer = BufferedWriter(fake, write_mode="sync")

        with pytest.raises(RuntimeError, match="Fake failure"):
            await writer.submit("MATCH (n:TEST)", {})

    @pytest.mark.asyncio
    async def test_flush_is_noop_in_sync_mode(self):
        fake = FakeNeo4jClient()
        writer = BufferedWriter(fake, write_mode="sync")
        await writer.flush()  # must not hang or raise


class TestBufferedWriterBufferedMode:
    @pytest.mark.asyncio
    async def test_submit_returns_immediately_and_flush_drains(self):
        fake = FakeNeo4jClient()
        writer = BufferedWriter(fake, write_mode="buffered", max_pending=10)
        try:
            for i in range(5):
                await writer.submit("CREATE (n:Test {i: $i})", {"i": i})

            # Writes have been queued but the drainer hasn't necessarily
            # finished yet. Queue size depends on scheduling — we only
            # require that flush blocks until the queue is empty.
            await writer.flush()

            assert len(fake.calls) == 5
            assert all(c[0] == "CREATE (n:Test {i: $i})" for c in fake.calls)
            assert sorted(c[1]["i"] for c in fake.calls) == [0, 1, 2, 3, 4]
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_wait_for_pending_is_alias_of_flush(self):
        fake = FakeNeo4jClient()
        writer = BufferedWriter(fake, write_mode="buffered", max_pending=4)
        try:
            await writer.submit("CREATE (n)", {})
            await writer.wait_for_pending()
            assert len(fake.calls) == 1
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_failed_writes_recorded_and_loop_continues(self):
        fake = FakeNeo4jClient(fail_on={"FAIL"})
        writer = BufferedWriter(fake, write_mode="buffered", max_pending=4)
        try:
            await writer.submit("CREATE (n:GOOD)", {"x": 1})
            await writer.submit("CREATE (n:FAIL)", {"x": 2})
            await writer.submit("CREATE (n:GOOD)", {"x": 3})
            await writer.flush()

            # Both good writes ran.
            good_calls = [c for c in fake.calls if "GOOD" in c[0]]
            assert len(good_calls) == 2

            # The bad write was captured.
            assert len(writer.errors) == 1
            err = writer.errors[0]
            assert "FAIL" in err.query
            assert isinstance(err.error, RuntimeError)
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_error_callback_fired(self):
        fake = FakeNeo4jClient(fail_on={"FAIL"})
        callback_seen: list = []

        async def cb(err):
            callback_seen.append(err)

        writer = BufferedWriter(fake, write_mode="buffered", max_pending=4, on_error=cb)
        try:
            await writer.submit("CREATE (n:FAIL)", {})
            await writer.flush()
            assert len(callback_seen) == 1
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_submit_after_stop_raises(self):
        fake = FakeNeo4jClient()
        writer = BufferedWriter(fake, write_mode="buffered")
        await writer.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            await writer.submit("CREATE (n)", {})

    @pytest.mark.asyncio
    async def test_back_pressure_when_queue_full(self):
        """When ``max_pending`` is reached, ``submit`` blocks until a worker
        drains an item — back-pressure rather than dropping writes.

        With ``max_pending=1`` and a stalled drainer:
        * submit(a) puts 'a' on the queue; drainer pulls it (qsize=0) and stalls.
        * submit(b) puts 'b' on the queue (qsize=1); the drainer can't pull
          it because it's still blocked on 'a'.
        * submit(c) tries to put 'c' but qsize==maxsize → blocks.
        """

        class BlockingClient(FakeNeo4jClient):
            def __init__(self):
                super().__init__()
                self._release = asyncio.Event()
                self._first_call_seen = asyncio.Event()

            async def execute_write(self, query: str, parameters: dict):
                if not self._first_call_seen.is_set():
                    self._first_call_seen.set()
                    await self._release.wait()
                self.calls.append((query, parameters))

        blocking = BlockingClient()
        writer = BufferedWriter(blocking, write_mode="buffered", max_pending=1)
        try:
            await writer.submit("CREATE (a)", {})
            # Wait until the drainer is committed to processing 'a'.
            await asyncio.wait_for(blocking._first_call_seen.wait(), timeout=2.0)
            # qsize is 0 here; a second submit fills the queue (qsize=1).
            await writer.submit("CREATE (b)", {})
            assert writer.pending == 1

            # Third submit must block — qsize == maxsize.
            third = asyncio.create_task(writer.submit("CREATE (c)", {}))
            await asyncio.sleep(0.05)
            assert not third.done(), "submit should block when queue is full"

            # Release the drainer → it processes 'a', then pulls 'b' (qsize=0)
            # → 'c' can now go in (qsize=1).
            blocking._release.set()
            await asyncio.wait_for(third, timeout=2.0)
            await writer.flush()

            queries = [c[0] for c in blocking.calls]
            assert sorted(queries) == [
                "CREATE (a)",
                "CREATE (b)",
                "CREATE (c)",
            ]
        finally:
            await writer.stop()
