"""Buffered fire-and-forget writes for the agent's hot path.

Memory writes (recording reasoning steps, tool calls, MENTIONS edges)
block the agent's response on every turn when run synchronously. In
production deployments where the agent must respond to the user
*before* persistence completes, callers can opt into buffered writes:

* ``MemorySettings.memory.write_mode = "buffered"`` enables a background
  drainer.
* ``client.buffered.submit(query, params)`` queues a write and returns
  immediately.
* ``client.flush()`` drains the queue (call at shutdown or between
  hot-path bursts).
* ``client.wait_for_pending()`` blocks until in-flight workers are idle.
* ``client.write_errors`` exposes failures that occurred in the
  background.

Default behavior (``write_mode="sync"``) is a thin passthrough — every
``submit`` awaits the underlying ``execute_write`` directly. This keeps
tests deterministic and avoids surprise queueing in single-user demos.

Note that this is an *opt-in fire-and-forget* API. Memory APIs that
return values (e.g. ``add_step`` returning a ``ReasoningStep``) still
construct their Pydantic model synchronously and run the
``CREATE_REASONING_STEP`` write inline; only callers who want to
explicitly fire writes into the background reach for ``client.buffered``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient

logger = logging.getLogger(__name__)


@dataclass
class BufferedWriteError:
    """A write that failed in the background drainer.

    Surfaced via ``client.write_errors`` so callers can inspect or alert
    on failures without having errors raised into the agent hot path.
    """

    query: str
    parameters: dict[str, Any]
    error: BaseException
    when: datetime = field(default_factory=datetime.utcnow)


@dataclass
class _Job:
    query: str
    parameters: dict[str, Any]


# Optional callback invoked from the drainer when a write fails. Hosts
# can use this to forward failures to logging / metrics systems without
# polling ``client.write_errors``.
ErrorCallback = Callable[[BufferedWriteError], Awaitable[None]]


class BufferedWriter:
    """Background-queue fire-and-forget writer.

    Construct with the underlying ``Neo4jClient``, ``write_mode``, and
    ``max_pending``. Started lazily on the first ``submit()`` so tests
    that never enqueue a write don't pay for the drainer task.
    """

    def __init__(
        self,
        client: Neo4jClient,
        *,
        write_mode: str = "sync",
        max_pending: int = 200,
        on_error: ErrorCallback | None = None,
    ):
        self._client = client
        self._write_mode = write_mode
        self._max_pending = max_pending
        self._on_error = on_error

        self._queue: asyncio.Queue[_Job] | None = None
        self._drainer: asyncio.Task[None] | None = None
        self._errors: list[BufferedWriteError] = []
        self._started = False
        self._stopped = False

    @property
    def is_buffered(self) -> bool:
        return self._write_mode == "buffered"

    @property
    def errors(self) -> list[BufferedWriteError]:
        """Background write errors recorded since startup (newest last)."""
        return list(self._errors)

    @property
    def pending(self) -> int:
        """Number of writes in the queue waiting for the drainer."""
        return self._queue.qsize() if self._queue is not None else 0

    async def submit(self, query: str, parameters: dict[str, Any] | None = None) -> None:
        """Submit a fire-and-forget write.

        In ``sync`` mode, awaits the underlying ``execute_write`` directly.
        In ``buffered`` mode, enqueues the write and returns immediately
        — except when the queue is full, in which case it blocks until a
        worker drains an item (intentional back-pressure).
        """
        if self._stopped:
            raise RuntimeError("BufferedWriter has been stopped (client closed)")

        if not self.is_buffered:
            # Sync mode — execute inline. Errors propagate to the caller.
            await self._client.execute_write(query, parameters or {})
            return

        await self._ensure_drainer()
        assert self._queue is not None
        await self._queue.put(_Job(query=query, parameters=parameters or {}))

    async def flush(self) -> None:
        """Wait for every queued write to be drained.

        No-op in sync mode. Safe to call multiple times. After flush
        returns, ``self.pending == 0`` (modulo races where a new submit
        races with the flush — but the typical use is flush at shutdown,
        not during steady-state).
        """
        if not self.is_buffered or self._queue is None:
            return
        await self._queue.join()

    async def wait_for_pending(self) -> None:
        """Alias of :meth:`flush` for symmetry with the PRD spec."""
        await self.flush()

    async def stop(self) -> None:
        """Drain the queue and cancel the background drainer.

        Called by ``MemoryClient.close()``. After ``stop()`` returns,
        further ``submit()`` calls raise ``RuntimeError``.
        """
        if self._stopped:
            return
        self._stopped = True
        if self._queue is not None:
            await self._queue.join()
        if self._drainer is not None:
            self._drainer.cancel()
            try:
                await self._drainer
            except (asyncio.CancelledError, Exception):
                pass

    async def _ensure_drainer(self) -> None:
        """Start the background drainer task on first submit."""
        if self._started:
            return
        self._started = True
        self._queue = asyncio.Queue(maxsize=self._max_pending)
        self._drainer = asyncio.create_task(self._drain_loop())

    async def _drain_loop(self) -> None:
        """Pull jobs from the queue and run them against Neo4j.

        Errors are captured into ``self._errors`` and the loop continues —
        a single bad query must not poison the channel for subsequent
        writes.
        """
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            try:
                await self._client.execute_write(job.query, job.parameters)
            except BaseException as e:  # capture everything
                err = BufferedWriteError(query=job.query, parameters=job.parameters, error=e)
                self._errors.append(err)
                logger.warning(
                    "Buffered write failed: %s. Error retained at client.write_errors[%d].",
                    e,
                    len(self._errors) - 1,
                )
                if self._on_error is not None:
                    try:
                        await self._on_error(err)
                    except Exception:
                        logger.exception("Buffered-write error callback raised; ignoring.")
            finally:
                self._queue.task_done()
