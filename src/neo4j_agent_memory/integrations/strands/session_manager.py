"""Strands SessionManager backed by neo4j-agent-memory (bolt or NAMS).

Maps a Strands session onto one ``Conversation`` — no Strands-specific
node types are written to the graph. Persistence is memory-grade: text
turns are stored (and feed entity extraction / the shared brain);
tool-use blocks and ``agent.state`` are not round-tripped.

Sibling modules own the non-session concerns: ``_messages`` (Strands ↔
Memory message mapping), ``_retrieval`` (long-term search + context-block
formatting), and ``integrations.base`` (the sync↔async bridge).
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from neo4j_agent_memory.integrations.base import AsyncBridge as _AsyncBridge
from neo4j_agent_memory.integrations.strands._messages import (
    _message_text,
    _to_strands_message,
)
from neo4j_agent_memory.integrations.strands._retrieval import (
    Neo4jRetrievalConfig,
    _retrieve_context,
)

try:
    from strands.hooks import (
        AfterInvocationEvent,
        HookRegistry,
        MessageAddedEvent,
    )
    from strands.session.session_manager import SessionManager
    from strands.types.exceptions import SessionException
except ImportError as import_error:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents is required for the Strands session manager. "
        "Install with: pip install neo4j-agent-memory[strands]"
    ) from import_error

if TYPE_CHECKING:
    from types import TracebackType

    from strands import Agent
    from strands.types.content import Message as StrandsMessage
    from strands.types.tools import ToolUse

    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.core.protocols import ShortTermProtocol
    from neo4j_agent_memory.memory.short_term import Message as StoredMessage
    from neo4j_agent_memory.nams.endpoints import TransportMode

logger = logging.getLogger(__name__)

#: Conversation-metadata key linking a Conversation to a Strands session id.
_SESSION_KEY = "strands_session_id"

__all__ = ["Neo4jRetrievalConfig", "Neo4jSessionManager"]


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
        restore_limit: int | None = None,
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
        self._restore_limit = restore_limit
        self._bridge = _AsyncBridge(timeout=request_timeout)
        # True when this manager owns the client lifecycle (settings-constructed
        # client, or when we performed the connect on an externally-supplied client).
        self._should_close_client: bool = settings is not None
        self._client: MemoryClient
        if settings is not None:
            from neo4j_agent_memory import MemoryClient as _MemoryClient

            self._client = _MemoryClient(settings)
        else:
            assert memory_client is not None  # guaranteed by the XOR check above
            self._client = memory_client
        self._conversation_key: str | None = None
        self._pending: StrandsMessage | None = None  # write-behind buffer
        self._last_persisted: StoredMessage | None = None  # last stored (late redaction)
        self._trace_id: UUID | None = None  # lazy reasoning trace (record_tool_calls)
        self._closed = False

    @property
    def _is_nams(self) -> bool:
        """Single home for the backend dichotomy: NAMS lacks preference/fact
        search and message delete, and keys conversations by server-issued
        UUID. Replace with a client-level capability probe when one exists."""
        return bool(self._client.is_nams)

    # --------------------------------------------------------- factory methods

    @classmethod
    def for_nams(
        cls,
        session_id: str,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        transport_mode: TransportMode = "auto",
        **kwargs: Any,
    ) -> Neo4jSessionManager:
        """Build a NAMS-backed manager using the same env-var conventions as
        ``nams_context_graph_tools()`` (MEMORY_ENDPOINT / MEMORY_API_KEY)."""
        from neo4j_agent_memory.integrations.strands.config import (
            build_nams_settings,
            resolve_nams_connection,
        )

        endpoint, api_key = resolve_nams_connection(endpoint, api_key)
        settings = build_nams_settings(endpoint, api_key, transport_mode)
        return cls(session_id, settings=settings, **kwargs)

    # ------------------------------------------------------------ lifecycle

    async def _aconnect(self) -> None:
        if not self._client.is_connected:
            await self._client.connect()
            self._should_close_client = True

    async def _aresolve_conversation(self) -> str:
        """Return the backend session key for short_term calls.

        NAMS issues conversation UUIDs, so we locate (or create) the
        conversation whose metadata carries our Strands session id. Bolt
        keys conversations by session_id directly (auto-created on first
        message).
        """
        if not self._is_nams:
            return self.session_id
        # Deferred core fix (tracked as a follow-up): ``MemoryClient.short_term``
        # is statically the concrete bolt ``ShortTermMemory``, but
        # ``list_conversations`` / ``create_conversation`` live on
        # ``ShortTermProtocol`` (implemented by the NAMS backend; bolt omits them).
        # Until the property return type is widened to ``ShortTermProtocol``, this
        # cast is required on the NAMS path. Bolt and the protocol are not in one
        # inheritance hierarchy, so reinterpret via ``object`` — the
        # checker-sanctioned form for a deliberate cross-type cast.
        nams_short_term = cast("ShortTermProtocol", cast(object, self._client.short_term))
        # Narrow server-side where possible; explicit limit extends coverage
        # beyond the server's default page (full pagination isn't exposed by
        # the API).
        conversations = await nams_short_term.list_conversations(
            user_identifier=self._user_id, limit=1000
        )
        for conversation in conversations:
            if (conversation.metadata or {}).get(_SESSION_KEY) == self.session_id:
                return str(conversation.id)
        created = await nams_short_term.create_conversation(
            session_id=self.session_id,
            metadata={_SESSION_KEY: self.session_id, "session_type": "AGENT"},
            user_identifier=self._user_id,
        )
        return str(created.id)

    async def _aresolve_key(self) -> str:
        """Connect and resolve the conversation key (without loading history)."""
        await self._aconnect()
        key = self._conversation_key
        if key is None:
            key = await self._aresolve_conversation()
            self._conversation_key = key
        return key

    async def _ainitialize(self) -> list[StrandsMessage]:
        key = await self._aresolve_key()
        conversation = await self._client.short_term.get_conversation(
            key, limit=self._restore_limit
        )
        return [_to_strands_message(m) for m in conversation.messages]

    def _ensure_session(self) -> str:
        """Resolve the conversation key on demand (lazy; does NOT load history)."""
        return self._conversation_key or self._bridge.run(self._aresolve_key())

    # ----------------------------------------------------- SessionManager API

    def initialize(self, agent: Agent, **kwargs: Any) -> None:
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
        append_message) always runs before context injection.
        AfterInvocationEvent dispatches in reverse registration order, so our
        flush runs before the base no-op sync_agent. That same reverse ordering
        means any user hooks registered after this manager run BEFORE our flush,
        so external AfterInvocationEvent hooks observe the final turn while it is
        still un-persisted; hooks that need the persisted message should read it
        on the next turn instead.
        """
        super().register_hooks(registry, **kwargs)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)
        if self._retrieval_config is not None:
            registry.add_callback(MessageAddedEvent, self._on_message_added)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        self._flush_buffer()

    def _on_message_added(self, event: MessageAddedEvent) -> None:
        self._inject_context(event.message)

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
        target = next(
            (b for b in message.get("content") or [] if isinstance(b, dict) and "text" in b),
            None,
        )
        if target is None or target["text"].startswith(f"<{cfg.context_tag}>\nRelevant memory:\n"):
            return  # no text block, or already injected (event re-fired)
        try:
            block = self._bridge.run(
                _retrieve_context(self._client.long_term, query, cfg, nams=self._is_nams)
            )
        except Exception as e:
            logger.warning(
                "Memory retrieval failed for session %s; continuing without injected context: %s",
                self.session_id,
                e,
            )
            return
        if block:
            target["text"] = f"{block}\n{target['text']}"

    def append_message(self, message: StrandsMessage, agent: Agent, **kwargs: Any) -> None:
        """Buffer the new message, persisting the previously buffered one.

        The one-slot write-behind buffer exists so guardrail redaction can
        rewrite the latest message before it ever reaches the backend
        (NAMS has no message update/delete endpoint).
        """
        self._flush_buffer()
        self._pending = copy.deepcopy(message)

    def redact_latest_message(
        self, redact_message: StrandsMessage, agent: Agent, **kwargs: Any
    ) -> None:
        """Replace the latest message with redacted content.

        Normal path: the latest message is still in the write-behind
        buffer, so we rewrite the buffer and the original never reaches
        the backend. Late path (buffer already flushed — defensive; the
        Strands lifecycle redacts within the same invocation): bolt
        deletes the stored message and re-adds the redacted text (the
        re-added message gets a fresh timestamp, so it moves to the end
        of restored history); NAMS has no delete/update endpoint, so we
        log a warning.
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
        if self._is_nams:
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
            self._store_message(redact_message.get("role", "user"), text, extract=False)
        except Exception as e:
            self._last_persisted = None  # id may already be deleted; don't reuse it
            raise SessionException(
                f"Failed to redact latest message for session {self.session_id!r}"
            ) from e

    def sync_agent(self, agent: Agent, **kwargs: Any) -> None:
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
            self._store_message(message["role"], text, extract=self._extract_entities)
        except Exception as e:
            raise SessionException(
                f"Failed to persist message for session {self.session_id!r}"
            ) from e

    def _store_message(self, role: str, text: str, *, extract: bool) -> None:
        """Resolve the session key and persist one message (tracked for late redaction)."""
        self._last_persisted = self._bridge.run(
            self._client.short_term.add_message(
                self._ensure_session(),
                role,
                text,
                extract_entities=extract,
                user_identifier=self._user_id,
                metadata={_SESSION_KEY: self.session_id},
            )
        )

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

    async def _arecord_tool_uses(self, key: str, blocks: list[ToolUse]) -> None:
        trace_id = self._trace_id
        if trace_id is None:
            trace = await self._client.reasoning.start_trace(key, task="Strands agent session")
            trace_id = trace.id
            self._trace_id = trace_id
        for block in blocks:
            name = block.get("name") or "unknown"
            step = await self._client.reasoning.add_step(
                trace_id, thought=f"Tool use: {name}", action=name
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
                if self._should_close_client and self._client.is_connected:
                    self._bridge.run(self._client.close())
            finally:
                self._bridge.close()

    def __enter__(self) -> Neo4jSessionManager:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
