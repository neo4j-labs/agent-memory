"""Strands SessionManager backed by neo4j-agent-memory (bolt or NAMS).

Maps a Strands session onto one ``Conversation`` — no Strands-specific
node types are written to the graph. Persistence is memory-grade: text
turns are stored (and feed entity extraction / the shared brain);
tool-use blocks and ``agent.state`` are not round-tripped.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

try:
    from strands.hooks import (
        AfterInvocationEvent,
        HookRegistry,
        MessageAddedEvent,
    )
    from strands.session.session_manager import SessionManager
    from strands.types.exceptions import SessionException
except ImportError as e:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents is required for the Strands session manager. "
        "Install with: pip install neo4j-agent-memory[strands]"
    ) from e

if TYPE_CHECKING:
    from strands.types.content import Message as StrandsMessage

    from neo4j_agent_memory import MemoryClient, MemorySettings

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
    return {
        "role": cast("Literal['user', 'assistant']", role),
        "content": [{"text": stored.content}],
    }


def _format_entity(entity: Any) -> str:
    desc = getattr(entity, "description", None)
    suffix = f" — {desc}" if desc else ""
    return f"[entity] {entity.display_name} ({entity.type}){suffix}"


def _format_preference(preference: Any) -> str:
    return f"[preference] {preference.category}: {preference.preference}"


def _format_fact(fact: Any) -> str:
    return f"[fact] {fact.subject} {fact.predicate} {fact.object}"


class Neo4jSessionManager(SessionManager):
    """Strands SessionManager persisting conversations to neo4j-agent-memory.

    Memory-grade persistence (see design spec): text turns are stored
    and restored; tool-use blocks and ``agent.state`` are not. One
    Strands session maps to one ``Conversation``.

    Provide exactly one of ``memory_client`` (bolt or NAMS; left open on
    close unless we connected it) or ``settings`` (a client is
    constructed and owned by the manager).
    """

    def __init__(
        self,
        session_id: str,
        memory_client: MemoryClient | None = None,
        settings: MemorySettings | None = None,
        *,
        user_id: str | None = None,
        retrieval_config: Neo4jRetrievalConfig | None = None,
        extract_entities: bool = True,
        record_tool_calls: bool = False,
        request_timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        if (memory_client is None) == (settings is None):
            raise ValueError(
                "Provide exactly one of memory_client= or settings= to Neo4jSessionManager."
            )
        self.session_id = session_id
        self._user_id = user_id
        self._retrieval_config = retrieval_config
        self._extract_entities = extract_entities
        self._record_tool_calls = record_tool_calls
        self._bridge = _AsyncBridge(timeout=request_timeout)
        self._owns_client = settings is not None
        if settings is not None:
            from neo4j_agent_memory import MemoryClient

            self._client: Any = MemoryClient(settings)
        else:
            self._client = memory_client
        self._we_connected = False
        self._conversation_key: str | None = None
        self._pending: StrandsMessage | None = None  # write-behind buffer
        self._last_persisted: Any = None  # last stored Message (late redaction)
        self._trace_id: Any = None  # lazy reasoning trace (record_tool_calls)
        self._closed = False

    # --------------------------------------------------------- factory methods

    @classmethod
    def for_nams(
        cls,
        session_id: str,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        transport_mode: str = "auto",
        **kwargs: Any,
    ) -> Neo4jSessionManager:
        """Build a NAMS-backed manager using the same env-var conventions as
        ``nams_context_graph_tools()`` (MEMORY_ENDPOINT / MEMORY_API_KEY)."""
        endpoint = (
            endpoint or os.environ.get("MEMORY_ENDPOINT") or "https://memory.neo4jlabs.com/v1"
        )
        api_key = api_key or os.environ.get("MEMORY_API_KEY")
        if not api_key:
            raise ValueError("api_key is required. Pass api_key= or set MEMORY_API_KEY env var.")
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings, NamsConfig

        settings = MemorySettings(
            backend="nams",
            nams=NamsConfig(
                endpoint=endpoint,
                api_key=SecretStr(api_key),
                validate_on_connect=False,
                transport_mode=transport_mode,
            ),
        )
        return cls(session_id, settings=settings, **kwargs)

    # ------------------------------------------------------------ lifecycle

    async def _aconnect(self) -> None:
        if not self._client.is_connected:
            await self._client.connect()
            self._we_connected = True

    async def _aresolve_conversation(self) -> str:
        """Return the backend session key for short_term calls.

        NAMS issues conversation UUIDs, so we locate (or create) the
        conversation whose metadata carries our Strands session id. Bolt
        keys conversations by session_id directly (auto-created on first
        message).
        """
        if not self._client.is_nams:
            return self.session_id
        conversations = await self._client.short_term.list_conversations()
        for conversation in conversations:
            if (conversation.metadata or {}).get(_SESSION_KEY) == self.session_id:
                return str(conversation.id)
        created = await self._client.short_term.create_conversation(
            session_id=self.session_id,
            metadata={_SESSION_KEY: self.session_id, "session_type": "AGENT"},
            user_identifier=self._user_id,
        )
        return str(created.id)

    async def _ainitialize(self) -> list[StrandsMessage]:
        await self._aconnect()
        self._conversation_key = await self._aresolve_conversation()
        conversation = await self._client.short_term.get_conversation(self._conversation_key)
        return [_to_strands_message(m) for m in conversation.messages]

    def _ensure_session(self) -> str:
        """Resolve the conversation key on demand (for use outside initialize)."""
        if self._conversation_key is None:
            self._bridge.run(self._ainitialize())
        assert self._conversation_key is not None
        return self._conversation_key

    # ----------------------------------------------------- SessionManager API

    def initialize(self, agent: Any, **kwargs: Any) -> None:
        """Restore the agent's conversation history from the graph."""
        try:
            restored = self._bridge.run(self._ainitialize())
        except Exception as e:
            raise SessionException(f"Failed to initialize session {self.session_id!r}") from e
        if restored:
            agent.messages.clear()
            agent.messages.extend(restored)
        elif agent.messages:
            # New session seeded with pre-existing in-memory history.
            for message in list(agent.messages):
                self.append_message(message, agent)
            self._flush_buffer()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Wire base persistence hooks, then our flush + injection hooks.

        Order matters: Strands fires ``MessageAddedEvent`` callbacks in
        registration order, so persistence (base ``MessageAddedEvent`` ->
        append_message) always runs before context injection.  Our
        AfterInvocationEvent flush is registered after the base sync_agent;
        that event uses reverse callback order at dispatch, but since
        sync_agent is a no-op the relative order is immaterial.
        """
        super().register_hooks(registry, **kwargs)
        registry.add_callback(AfterInvocationEvent, lambda _event: self._flush_buffer())
        if self._retrieval_config is not None:
            registry.add_callback(
                MessageAddedEvent, lambda event: self._inject_context(event.message)
            )

    def _inject_context(self, message: StrandsMessage) -> None:
        """Prepend relevant long-term memories to a user message (in-memory only).

        Failures degrade: a memory lookup must never break the agent's turn.
        """
        cfg = self._retrieval_config
        if cfg is None or message.get("role") != "user":
            return
        query = _message_text(message)
        if not query:
            return
        try:
            block = self._bridge.run(self._aretrieve(query, cfg))
        except Exception as e:
            logger.warning(
                "Memory retrieval failed for session %s; continuing without injected context: %s",
                self.session_id,
                e,
            )
            return
        if not block:
            return
        tag_prefix = f"<{cfg.context_tag}>\nRelevant memory:\n"
        for content_block in message.get("content") or []:
            if isinstance(content_block, dict) and "text" in content_block:
                if content_block["text"].startswith(tag_prefix):
                    return  # already injected (event re-fired for this message)
                content_block["text"] = f"{block}\n{content_block['text']}"
                return

    async def _aretrieve(self, query: str, cfg: Neo4jRetrievalConfig) -> str:
        long_term = self._client.long_term
        searches: list[Any] = []
        formatters: list[Any] = []
        if cfg.include_entities:
            searches.append(
                long_term.search_entities(query, limit=cfg.top_k, threshold=cfg.min_score)
            )
            formatters.append(_format_entity)
        # NAMS has no preferences/facts search endpoints (NotSupportedError) —
        # skip structurally-unsupported searches instead of warning every turn.
        if cfg.include_preferences and not self._client.is_nams:
            searches.append(
                long_term.search_preferences(query, limit=cfg.top_k, threshold=cfg.min_score)
            )
            formatters.append(_format_preference)
        if cfg.include_facts and not self._client.is_nams:
            searches.append(long_term.search_facts(query, limit=cfg.top_k, threshold=cfg.min_score))
            formatters.append(_format_fact)
        results = await asyncio.gather(*searches, return_exceptions=True)
        lines: list[str] = []
        for formatter, result in zip(formatters, results):
            if isinstance(result, BaseException):
                logger.warning("Long-term memory search failed: %s", result)
                continue
            lines.extend(formatter(item) for item in result)
        if not lines:
            return ""
        body = "\n".join(f"- {line}" for line in lines)
        return f"<{cfg.context_tag}>\nRelevant memory:\n{body}\n</{cfg.context_tag}>"

    def append_message(self, message: Any, agent: Any, **kwargs: Any) -> None:
        """Buffer the new message, persisting the previously buffered one.

        The one-slot write-behind buffer exists so guardrail redaction can
        rewrite the latest message before it ever reaches the backend
        (NAMS has no message update/delete endpoint).
        """
        self._flush_buffer()
        self._pending = copy.deepcopy(message)

    def redact_latest_message(
        self, redact_message: StrandsMessage, agent: Any, **kwargs: Any
    ) -> None:
        """Replace the latest message with redacted content.

        Normal path: the latest message is still in the write-behind
        buffer, so we rewrite the buffer and the original never reaches
        the backend. Late path (buffer already flushed — defensive; the
        Strands lifecycle redacts within the same invocation): bolt
        deletes the stored message and re-adds the redacted text; NAMS
        has no delete/update endpoint, so we log a warning.
        """
        if self._pending is not None:
            self._pending = copy.deepcopy(redact_message)
            return
        if self._last_persisted is None:
            logger.warning(
                "redact_latest_message called for session %s but no message "
                "has been stored yet; nothing to redact.",
                self.session_id,
            )
            return
        if self._client.is_nams:
            logger.warning(
                "Cannot redact already-persisted message %s on NAMS (no "
                "message update/delete endpoint). The redacted content was "
                "NOT applied server-side.",
                self._last_persisted.id,
            )
            return
        text = _message_text(redact_message) or "[REDACTED]"
        try:
            self._bridge.run(self._client.short_term.delete_message(self._last_persisted.id))
            self._last_persisted = self._bridge.run(
                self._client.short_term.add_message(
                    self._ensure_session(),
                    redact_message.get("role", "user"),
                    text,
                    extract_entities=False,
                    user_identifier=self._user_id,
                    metadata={_SESSION_KEY: self.session_id},
                )
            )
        except Exception as e:
            self._last_persisted = None  # id may already be deleted; don't reuse it
            raise SessionException(
                f"Failed to redact latest message for session {self.session_id!r}"
            ) from e

    def sync_agent(self, agent: Any, **kwargs: Any) -> None:
        """Agent state is not persisted (design decision: no Strands-specific
        nodes in the graph). Also fired on MessageAddedEvent by the base
        class, so it must not flush the buffer."""
        return None

    def _flush_buffer(self) -> None:
        """Persist the buffered message, if any. Raises SessionException on failure."""
        if self._pending is None:
            return
        message, self._pending = self._pending, None
        if self._record_tool_calls:
            self._record_tool_uses(message)
        text = _message_text(message)
        if not text:
            return  # pure tool-use/result message: not memory, not stored
        try:
            key = self._ensure_session()
            self._last_persisted = self._bridge.run(
                self._client.short_term.add_message(
                    key,
                    message["role"],
                    text,
                    extract_entities=self._extract_entities,
                    user_identifier=self._user_id,
                    metadata={_SESSION_KEY: self.session_id},
                )
            )
        except Exception as e:
            raise SessionException(
                f"Failed to persist message for session {self.session_id!r}"
            ) from e

    def _record_tool_uses(self, message: StrandsMessage) -> None:
        """Mirror toolUse blocks into reasoning memory (enrichment; never raises)."""
        blocks = [
            b["toolUse"]
            for b in (message.get("content") or [])
            if isinstance(b, dict) and "toolUse" in b
        ]
        if not blocks:
            return
        try:
            key = self._ensure_session()
            self._bridge.run(self._arecord_tool_uses(key, blocks))
        except Exception as e:
            logger.warning("Failed to mirror tool calls to reasoning memory: %s", e)

    async def _arecord_tool_uses(self, key: str, blocks: list[Any]) -> None:
        if self._trace_id is None:
            trace = await self._client.reasoning.start_trace(key, task="Strands agent session")
            self._trace_id = trace.id
        for block in blocks:
            name = block.get("name") or "unknown"
            step = await self._client.reasoning.add_step(
                self._trace_id, thought=f"Tool use: {name}", action=name
            )
            await self._client.reasoning.record_tool_call(step.id, name, block.get("input") or {})

    def close(self) -> None:
        """Flush, release the client (if owned/connected by us), stop the bridge."""
        if self._closed:
            return
        self._closed = True
        try:
            self._flush_buffer()
        finally:
            try:
                if (self._owns_client or self._we_connected) and self._client.is_connected:
                    self._bridge.run(self._client.close())
            finally:
                self._bridge.close()

    def __enter__(self) -> Neo4jSessionManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
