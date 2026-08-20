"""Strands ``MemoryStore`` backed by neo4j-agent-memory (bolt or NAMS).

Long-term recall for the agent loop. Distinct from
:class:`Neo4jSessionManager`, which persists and restores the transcript —
see the design spec's positioning section.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

try:
    from strands.memory import (
        AddMessagesContext,
        MemoryEntry,
        MemoryStore,
        SearchOptions,
    )
except ImportError as import_error:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents>=1.44.0 is required for the Strands memory store. "
        "Install with: pip install 'neo4j-agent-memory[strands]'"
    ) from import_error

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.integrations.strands._messages import _message_text
from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries

if TYPE_CHECKING:
    from types import TracebackType

    from strands.memory import ExtractionConfig
    from strands.types.content import Message as StrandsMessage
    from strands.types.tools import AgentTool

    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.nams.endpoints import TransportMode

logger = logging.getLogger(__name__)

#: Conversation-metadata key marking a conversation as a memory-store sink.
_STORE_KEY = "strands_memory_store"

#: Strands' own per-store default when neither caller nor store sets a limit
#: (mirrors ``strands.memory.memory_manager.DEFAULT_MAX_SEARCH_RESULTS``).
_DEFAULT_MAX_SEARCH_RESULTS = 3

#: NAMS caps bulk message writes; chunk to stay inside it on both backends.
_BULK_CHUNK = 100

__all__ = ["Neo4jMemoryStore", "Neo4jMemoryStoreConfig"]


@dataclass
class Neo4jMemoryStoreConfig:
    """Configuration for :class:`Neo4jMemoryStore`.

    A plain dataclass, not a ``TypedDict``: every field is a checked
    attribute (``config.user_id``, never ``config.get("user_id")``), so a
    typo is a ``mypy --strict`` error rather than a silently-``None`` read.
    Reuse one config across several stores — personal / team / org — with
    ``dataclasses.replace(config, name="team")``.

    ``name``, ``description``, ``max_search_results``, ``writable`` and
    ``extraction`` are the fields ``MemoryStore``'s protocol requires the
    store to expose as instance attributes; the rest are Neo4j connection,
    scoping, and search knobs.
    """

    name: str
    client: MemoryClient | None = None
    settings: MemorySettings | None = None
    description: str | None = None
    max_search_results: int | None = None
    writable: bool = True
    extraction: ExtractionConfig | bool = False
    conversation_id: str | None = None
    user_id: str | None = None
    include_entities: bool = True
    include_preferences: bool = True
    include_facts: bool = True
    min_score: float = 0.2
    graph_tools: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Neo4jMemoryStore: 'name' is required and must be non-empty")
        # Only the unambiguous mistake (both given) is checkable here. A
        # config en route to `for_nams` legitimately has neither set yet —
        # `for_nams` completes it with `settings` before construction — so
        # "neither given" is checked in `Neo4jMemoryStore.__init__` instead,
        # once we know no such completion step is coming.
        if self.client is not None and self.settings is not None:
            raise ValueError(
                "Neo4jMemoryStore: pass exactly one of 'client' (borrowed, left open) "
                "or 'settings' (a client is constructed and owned by the store)"
            )


class Neo4jMemoryStore(MemoryStore):
    """Long-term memory recall and ingestion over a Neo4j context graph.

    Example:
        store = Neo4jMemoryStore(
            Neo4jMemoryStoreConfig(
                name="graph",
                client=client,  # or settings=MemorySettings(...)
                user_id="alice",
            )
        )
    """

    def __init__(self, config: Neo4jMemoryStoreConfig) -> None:
        if config.client is None and config.settings is None:
            raise ValueError(
                "Neo4jMemoryStore: pass exactly one of 'client' (borrowed, left open) "
                "or 'settings' (a client is constructed and owned by the store)"
            )

        # The five attributes MemoryStore's protocol requires the store to expose.
        self.name = config.name
        self.description = config.description or (
            f"Neo4j context graph '{config.name}': entities, preferences and facts."
        )
        self.max_search_results = config.max_search_results
        self.writable = config.writable
        self.extraction = config.extraction

        self.user_id = config.user_id
        self.graph_tools = config.graph_tools
        self._include_entities = config.include_entities
        self._include_preferences = config.include_preferences
        self._include_facts = config.include_facts
        self._min_score = config.min_score

        self._conversation_id = config.conversation_id
        self._sink_key: str | None = config.conversation_id
        self._owns_client = config.client is None
        self._run_id = uuid.uuid4().hex
        self._written: set[tuple[str, int]] = set()
        self._initialized = False
        self._warned_unsupported_kinds: set[str] = set()

        if config.client is not None:
            self._client: MemoryClient = config.client
        else:
            from neo4j_agent_memory import MemoryClient as _MemoryClient

            assert config.settings is not None
            self._client = _MemoryClient(config.settings)

    @classmethod
    def for_nams(
        cls,
        config: Neo4jMemoryStoreConfig,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        transport_mode: TransportMode = "auto",
    ) -> Neo4jMemoryStore:
        """Construct a store against hosted NAMS.

        Reads ``MEMORY_API_KEY`` (and optionally ``MEMORY_ENDPOINT``) from the
        environment when not passed explicitly. ``config`` is not mutated:
        ``dataclasses.replace`` returns a copy with ``settings`` injected.
        """
        from neo4j_agent_memory.integrations.strands.config import (
            build_nams_settings,
            resolve_nams_connection,
        )

        endpoint, api_key = resolve_nams_connection(endpoint, api_key)
        merged = replace(config, settings=build_nams_settings(endpoint, api_key, transport_mode))
        return cls(merged)

    @property
    def is_nams(self) -> bool:
        return self._client.is_nams

    @property
    def _sink_name(self) -> str:
        """Deterministic sink name, stable across processes and restarts."""
        return f"strands-memory-store/{self.user_id or '_'}/{self.name}"

    async def _resolve_sink(self) -> str:
        """Return the conversation key writes go to, creating the sink if needed.

        An explicit ``conversation_id`` is used verbatim. Otherwise: bolt keys
        conversations by ``session_id`` and ``add_message``/``add_messages_batch``
        both auto-create the sink (and tenant-link it via ``user_identifier``)
        via ``_ensure_conversation`` on first write, so the deterministic sink
        name *is* the whole contract — no backend call is made here, and none
        is needed. NAMS mints its own conversation ids, so metadata is the
        only portable handle there — list and match ``_STORE_KEY`` metadata,
        else create. Same split as ``Neo4jSessionManager._aresolve_conversation``.
        """
        if self._sink_key is not None:
            return self._sink_key

        if not self.is_nams:
            sink_key = self._sink_name
            self._sink_key = sink_key
            return sink_key

        short_term = self._client.short_term
        conversations = await short_term.list_conversations(
            user_identifier=self.user_id, limit=1000
        )
        for conversation in conversations:
            if (conversation.metadata or {}).get(_STORE_KEY) == self._sink_name:
                sink_key = str(conversation.id)
                self._sink_key = sink_key
                return sink_key

        created = await short_term.create_conversation(
            session_id=self._sink_name,
            metadata={_STORE_KEY: self._sink_name, "session_type": "MEMORY_STORE"},
            user_identifier=self.user_id,
        )
        sink_key = str(created.id)
        self._sink_key = sink_key
        return sink_key

    async def initialize(self) -> None:
        """Connect the client if not already connected. Idempotent."""
        if self._initialized:
            return
        if not self._client.is_connected:
            await self._client.connect()
        self._initialized = True

    async def search(self, query: str, options: SearchOptions | None = None) -> list[MemoryEntry]:
        """Search long-term memory. No sink resolution: reads don't need one.

        Limit precedence: per-call option, then ``self.max_search_results``,
        then Strands' own default. Per-kind failures are isolated in
        ``_retrieve_entries``; a total failure here propagates so
        ``MemoryManager.search`` can log a dead store rather than see an
        empty, misleadingly-successful result.
        """
        await self.initialize()
        limit = (options or {}).get("max_search_results")
        if limit is None:
            limit = self.max_search_results
        if limit is None:
            limit = _DEFAULT_MAX_SEARCH_RESULTS

        rows = await _retrieve_entries(
            self._client.long_term,
            query,
            limit=limit,
            min_score=self._min_score,
            include_entities=self._include_entities,
            include_preferences=self._include_preferences,
            include_facts=self._include_facts,
            nams=self.is_nams,
        )
        return [MemoryEntry(content=row.content, metadata=row.metadata) for row in rows]

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add one piece of content.

        Default sink: a message in the store's conversation, written with
        extraction on — the one path available on every backend. ``metadata["kind"]``
        opts into a typed write (``preference`` / ``fact`` / ``entity``); on a
        backend that does not expose it, the write falls back to the default sink
        so the memory is never silently dropped.

        Extraction writes are at-least-once, so this tolerates duplicates.
        """
        if not self.writable:
            raise ValueError(
                f"Neo4jMemoryStore '{self.name}': store is not writable. "
                "Set writable=True to enable add()."
            )
        if not content.strip():
            raise ValueError(f"Neo4jMemoryStore '{self.name}': content must not be empty")

        await self.initialize()
        meta = metadata or {}
        kind = meta.get("kind")

        if kind in ("preference", "fact", "entity"):
            try:
                return await self._add_typed(kind, content, meta)
            except NotSupportedError as error:
                if kind not in self._warned_unsupported_kinds:
                    self._warned_unsupported_kinds.add(kind)
                    logger.warning(
                        "Neo4jMemoryStore '%s': %s unsupported on this backend (%s); "
                        "falling back to the message sink. (This warning is logged "
                        "once per store; further %s writes fall back silently.)",
                        self.name,
                        kind,
                        error,
                        kind,
                    )

        return await self._add_to_sink(content)

    async def _add_typed(self, kind: str, content: str, meta: dict[str, Any]) -> dict[str, Any]:
        long_term = self._client.long_term
        if kind == "preference":
            preference = await long_term.add_preference(meta.get("category", "memory"), content)
            return {"kind": "preference", "id": str(preference.id)}
        if kind == "fact":
            subject = meta.get("subject")
            predicate = meta.get("predicate")
            obj = meta.get("object")
            if not (subject and predicate and obj):
                raise ValueError(
                    f"Neo4jMemoryStore '{self.name}': kind='fact' requires "
                    "subject, predicate and object in metadata"
                )
            fact = await long_term.add_fact(subject, predicate, obj)
            return {"kind": "fact", "id": str(fact.id)}
        if kind == "entity":
            # add_entity returns (Entity, DeduplicationResult) on bolt but a bare
            # Entity on NAMS (no dedup pipeline there).
            entity_result = await long_term.add_entity(
                meta.get("name", content), meta.get("type", "OBJECT")
            )
            entity = entity_result[0] if isinstance(entity_result, tuple) else entity_result
            return {"kind": "entity", "id": str(entity.id)}
        raise ValueError(f"Neo4jMemoryStore '{self.name}': unknown kind '{kind}'")

    async def _add_to_sink(self, content: str) -> dict[str, Any]:
        sink = await self._resolve_sink()
        message = await self._client.short_term.add_message(
            sink,
            "user",
            content,
            extract_entities=True,
            user_identifier=self.user_id,
        )
        return {"kind": "message", "id": str(message.id)}

    async def add_messages(
        self,
        messages: list[StrandsMessage],
        context: AddMessagesContext | None = None,
    ) -> dict[str, Any]:
        """Ingest a batch of conversation turns into the sink conversation.

        The backend extracts them — server-side on NAMS, inline on bolt — so no
        model call happens here. Extraction writes are at-least-once and
        ``AddMessagesContext.sequence_numbers`` repeat on a retry, so a
        ``(run_id, sequence_number)`` set skips turns already written by this
        instance. The dedupe is in-process only: sequence numbers reset each run,
        so there is nothing durable to key on.
        """
        if not self.writable:
            raise ValueError(
                f"Neo4jMemoryStore '{self.name}': store is not writable. "
                "Set writable=True to enable add_messages()."
            )
        await self.initialize()

        sequence_numbers = (context.sequence_numbers if context else None) or []
        payload: list[dict[str, Any]] = []
        tokens: list[tuple[str, int] | None] = []
        skipped = 0

        for index, message in enumerate(messages):
            text = _message_text(message)
            if not text.strip():
                skipped += 1
                continue
            token: tuple[str, int] | None = None
            if index < len(sequence_numbers):
                token = (self._run_id, sequence_numbers[index])
                if token in self._written:
                    skipped += 1
                    continue
            payload.append({"role": message.get("role", "user"), "content": text})
            tokens.append(token)

        if not payload:
            return {"written": 0, "skipped": skipped}

        sink = await self._resolve_sink()
        for start in range(0, len(payload), _BULK_CHUNK):
            await self._client.short_term.bulk_add_messages(
                sink,
                payload[start : start + _BULK_CHUNK],
                extract_entities=True,
                user_identifier=self.user_id,
            )
        self._written.update(token for token in tokens if token is not None)
        return {"written": len(payload), "skipped": skipped}

    def get_tools(self) -> list[AgentTool]:
        """Graph-native tools registered alongside the manager's own tools.

        Empty when ``graph_tools=False``. Never includes ``search_memory`` or
        ``add_memory`` — those belong to the ``MemoryManager``.
        """
        if not self.graph_tools:
            return []
        from neo4j_agent_memory.integrations.strands._store_tools import build_store_tools

        return build_store_tools(self)

    async def aclose(self) -> None:
        """Close the client only when the store constructed it."""
        if self._owns_client:
            await self._client.close()

    async def __aenter__(self) -> Neo4jMemoryStore:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
