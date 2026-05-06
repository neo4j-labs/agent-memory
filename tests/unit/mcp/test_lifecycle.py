"""Unit tests for MCP server lifecycle / orphan-detection watchdog."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from neo4j_agent_memory.mcp._lifecycle import (
    _parent_death_loop,
    run_with_watchdog,
)


@pytest.mark.skipif(sys.platform == "win32", reason="getppid() semantics differ on Windows")
class TestParentDeathLoop:
    """Tests for the parent-death detection coroutine."""

    async def test_returns_when_ppid_becomes_one(self, monkeypatch):
        """Loop should return when getppid() == 1 (orphaned to init/launchd)."""
        readings = iter([os.getppid(), 1])
        monkeypatch.setattr(os, "getppid", lambda: next(readings))
        # Should return well within timeout since poll_interval is tiny.
        await asyncio.wait_for(_parent_death_loop(0.01), timeout=1.0)

    async def test_returns_when_ppid_is_zero(self, monkeypatch):
        """Some platforms report ppid=0 for orphans — also treat as orphaned."""
        readings = iter([os.getppid(), 0])
        monkeypatch.setattr(os, "getppid", lambda: next(readings))
        await asyncio.wait_for(_parent_death_loop(0.01), timeout=1.0)

    async def test_keeps_polling_while_parent_alive(self, monkeypatch):
        """Loop should keep polling while the parent is alive (ppid > 1)."""
        monkeypatch.setattr(os, "getppid", lambda: 12345)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_parent_death_loop(0.01), timeout=0.1)


@pytest.mark.skipif(sys.platform == "win32", reason="watchdog is a no-op passthrough on Windows")
class TestRunWithWatchdog:
    """Tests for the run_with_watchdog orchestrator."""

    async def test_returns_normally_when_server_completes(self):
        """If the server coroutine finishes, run_with_watchdog returns cleanly."""

        async def fake_server() -> None:
            await asyncio.sleep(0.01)

        await asyncio.wait_for(
            run_with_watchdog(fake_server(), poll_interval=10, install_signals=False),
            timeout=1.0,
        )

    async def test_propagates_server_exceptions(self):
        """Exceptions raised by the server must be surfaced, not swallowed."""

        async def fake_server() -> None:
            await asyncio.sleep(0.01)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await asyncio.wait_for(
                run_with_watchdog(fake_server(), poll_interval=10, install_signals=False),
                timeout=1.0,
            )

    async def test_cancels_server_when_parent_dies(self, monkeypatch):
        """When the watchdog detects an orphan, the server task is cancelled."""
        cancelled = asyncio.Event()

        async def fake_server() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        readings = iter([os.getppid(), 1])
        monkeypatch.setattr(os, "getppid", lambda: next(readings))

        await asyncio.wait_for(
            run_with_watchdog(fake_server(), poll_interval=0.01, install_signals=False),
            timeout=1.0,
        )
        assert cancelled.is_set()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only passthrough check")
class TestWindowsBehavior:
    async def test_passthrough_on_windows(self):
        """run_with_watchdog runs the server unchanged on Windows."""
        ran = False

        async def fake_server() -> None:
            nonlocal ran
            ran = True

        await asyncio.wait_for(
            run_with_watchdog(fake_server(), poll_interval=0.01),
            timeout=1.0,
        )
        assert ran
