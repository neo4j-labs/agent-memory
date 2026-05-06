"""Lifecycle helpers for MCP servers running over stdio.

When an MCP client (e.g. Claude Code, Claude Desktop, Cursor) crashes or is
killed without closing its end of the stdio pipe, the spawned server is
reparented to ``init``/``launchd`` (PID 1) and keeps holding the Neo4j
connection, file handles, and memory indefinitely. This module provides a
lightweight watchdog that detects that condition and lets the server exit
cleanly so the lifespan can release its resources.

The watchdog is only meaningful for the stdio transport. For network
transports (SSE/HTTP) the server is intended to outlive its starter, so
``run_with_watchdog`` should not be used.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Awaitable

logger = logging.getLogger(__name__)


def _is_windows() -> bool:
    return sys.platform == "win32"


async def _parent_death_loop(poll_interval: float) -> None:
    """Return when the parent process has exited.

    ``getppid()`` returns 1 on Unix once the original parent dies and the
    process is reparented to init/launchd. A value of 0 is treated the same
    way for safety on platforms that report it that way under unusual
    conditions.
    """
    initial_ppid = os.getppid()
    logger.debug("MCP parent-death watcher started (initial ppid=%d)", initial_ppid)
    while True:
        await asyncio.sleep(poll_interval)
        ppid = os.getppid()
        if ppid in (0, 1):
            logger.warning(
                "MCP parent process exited (ppid=%d); initiating self-termination",
                ppid,
            )
            return


def _install_cancel_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    target: asyncio.Task[object],
) -> None:
    """Cancel ``target`` on SIGTERM/SIGHUP/SIGPIPE so the lifespan can clean up."""
    if _is_windows():
        return
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
        try:
            loop.add_signal_handler(sig, target.cancel)
        except (NotImplementedError, RuntimeError, ValueError):
            # Some environments (subinterpreters, non-main threads) can't install
            # signal handlers. The watchdog still provides parent-death coverage.
            logger.debug("Could not install handler for signal %s", sig)


async def run_with_watchdog(
    server_coro: Awaitable[None],
    *,
    poll_interval: float = 5.0,
    install_signals: bool = True,
) -> None:
    """Run ``server_coro`` alongside a parent-death watchdog.

    Returns when either:

    * the server coroutine completes (normal exit or exception), or
    * the parent process dies (``getppid()`` becomes 1 / 0), in which case
      the server task is cancelled and the surrounding lifespan exits cleanly.

    On Windows or when running outside a usable event-loop signal context,
    parts of the watchdog degrade to no-ops; the server coroutine still runs.
    """
    if _is_windows():
        # Parent-death detection via getppid() is unreliable on Windows.
        # Run the server coroutine unchanged.
        await server_coro
        return

    server_task: asyncio.Task[object] = asyncio.create_task(_await(server_coro), name="mcp-server")
    watchdog_task: asyncio.Task[None] = asyncio.create_task(
        _parent_death_loop(poll_interval), name="mcp-parent-death-watchdog"
    )

    if install_signals:
        loop = asyncio.get_running_loop()
        _install_cancel_signal_handlers(loop, server_task)

    try:
        done, pending = await asyncio.wait(
            {server_task, watchdog_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        server_task.cancel()
        watchdog_task.cancel()
        raise

    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    if watchdog_task in done and server_task not in done:
        logger.info("MCP server self-terminated: parent process is gone")
        return

    # Server completed (normally or with an error). Propagate exceptions.
    for task in done:
        if task is watchdog_task:
            continue
        exc = task.exception()
        if exc is not None:
            raise exc


async def _await(coro: Awaitable[None]) -> None:
    """Tiny shim so ``asyncio.create_task`` accepts a generic Awaitable."""
    await coro
