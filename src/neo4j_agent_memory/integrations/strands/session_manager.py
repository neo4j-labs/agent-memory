"""Strands SessionManager backed by neo4j-agent-memory (bolt or NAMS).

Maps a Strands session onto one ``Conversation`` — no Strands-specific
node types are written to the graph. Persistence is memory-grade: text
turns are stored (and feed entity extraction / the shared brain);
tool-use blocks and ``agent.state`` are not round-tripped.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:
    from strands.hooks import (  # used by Neo4jSessionManager.register_hooks
        AfterInvocationEvent,  # noqa: F401
        HookRegistry,  # noqa: F401
        MessageAddedEvent,  # noqa: F401
    )
    from strands.session.session_manager import SessionManager  # noqa: F401
    from strands.types.exceptions import SessionException  # noqa: F401
except ImportError as e:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents is required for the Strands session manager. "
        "Install with: pip install neo4j-agent-memory[strands]"
    ) from e

if TYPE_CHECKING:
    from strands.types.content import Message as StrandsMessage  # noqa: F401

    from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401

logger = logging.getLogger(__name__)

#: Conversation-metadata key linking a Conversation to a Strands session id.
_SESSION_KEY = "strands_session_id"


@dataclass
class Neo4jRetrievalConfig:
    """Opt-in per-turn long-term memory injection settings.

    When passed to :class:`Neo4jSessionManager`, each user message
    triggers concurrent long-term searches and the results are prepended
    to the message in-memory inside a ``<context_tag>`` block. The stored
    message is always the user's original.
    """

    top_k: int = 10
    min_score: float = 0.2
    include_entities: bool = True
    include_preferences: bool = True
    include_facts: bool = False
    context_tag: str = "user_context"


class _AsyncBridge:
    """Run coroutines from sync code on one persistent background loop.

    The loop thread starts lazily on first use and is restarted if used
    again after ``close()`` (cheap, and keeps the API forgiving).
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._thread is None or not self._thread.is_alive():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever,
                    name="neo4j-strands-session-manager",
                    daemon=True,
                )
                self._thread.start()
            return self._loop

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        """Submit ``coro`` to the background loop and block for the result."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout if timeout is not None else self._timeout)

    def close(self) -> None:
        """Stop and discard the loop thread. Safe to call repeatedly.

        Callers with a ``run()`` in flight will block until their own
        timeout fires. Drain in-flight work before closing (the session
        manager flushes its buffer first, which guarantees this).
        """
        with self._lock:
            if self._loop is None:
                return
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop.close()
            self._loop = None
            self._thread = None


# ---------------------------------------------------------------------------
# Strands ↔ NAMS message mapping helpers
# ---------------------------------------------------------------------------


def _message_text(message: StrandsMessage) -> str:
    """Concatenate the text blocks of a Strands message (tool blocks ignored)."""
    blocks = message.get("content") or []
    texts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("text")]
    return "\n".join(texts)


def _to_strands_message(stored: Any) -> StrandsMessage:
    """Convert a stored neo4j-agent-memory Message to a Strands message dict."""
    role = stored.role.value if hasattr(stored.role, "value") else str(stored.role)
    if role not in ("user", "assistant"):
        role = "assistant"
    return {"role": role, "content": [{"text": stored.content}]}


def _format_entity(entity: Any) -> str:
    desc = getattr(entity, "description", None)
    suffix = f" — {desc}" if desc else ""
    return f"[entity] {entity.display_name} ({entity.type}){suffix}"


def _format_preference(preference: Any) -> str:
    return f"[preference] {preference.category}: {preference.preference}"


def _format_fact(fact: Any) -> str:
    return f"[fact] {fact.subject} {fact.predicate} {fact.object}"
