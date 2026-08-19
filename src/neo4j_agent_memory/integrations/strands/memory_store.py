"""Strands ``MemoryStore`` backed by neo4j-agent-memory (bolt or NAMS).

Long-term recall for the agent loop. Distinct from
:class:`Neo4jSessionManager`, which persists and restores the transcript —
see the design spec's positioning section.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from typing_extensions import Unpack

try:
    from strands.memory import MemoryEntry, MemoryStore, MemoryStoreConfig, SearchOptions
except ImportError as import_error:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents>=1.44.0 is required for the Strands memory store. "
        "Install with: pip install 'neo4j-agent-memory[strands]'"
    ) from import_error

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries

if TYPE_CHECKING:
    from types import TracebackType

    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.nams.endpoints import TransportMode

logger = logging.getLogger(__name__)

#: Conversation-metadata key marking a conversation as a memory-store sink.
_STORE_KEY = "strands_memory_store"

#: Strands' own per-store default when neither caller nor store sets a limit
#: (mirrors ``strands.memory.memory_manager.DEFAULT_MAX_SEARCH_RESULTS``).
_DEFAULT_MAX_SEARCH_RESULTS = 3

__all__ = ["Neo4jMemoryStore", "Neo4jMemoryStoreConfig"]


class Neo4jMemoryStoreConfig(MemoryStoreConfig, total=False):
    """Configuration for :class:`Neo4jMemoryStore`.

    Extends Strands' ``MemoryStoreConfig`` (``name``, ``description``,
    ``max_search_results``, ``writable``, ``extraction``) with the Neo4j
    connection, scoping, and search knobs.
    """

    client: MemoryClient
    settings: MemorySettings
    conversation_id: str
    user_id: str
    include_entities: bool
    include_preferences: bool
    include_facts: bool
    min_score: float
    graph_tools: bool


class Neo4jMemoryStore(MemoryStore):
    """Long-term memory recall and ingestion over a Neo4j context graph."""

    def __init__(self, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> None:
        name = store_config.get("name")
        if not name:
            raise ValueError("Neo4jMemoryStore: 'name' is required and must be non-empty")

        client = store_config.get("client")
        settings = store_config.get("settings")
        if (client is None) == (settings is None):
            raise ValueError(
                "Neo4jMemoryStore: pass exactly one of 'client' (borrowed, left open) "
                "or 'settings' (a client is constructed and owned by the store)"
            )

        self.name = name
        self.description = store_config.get(
            "description", f"Neo4j context graph '{name}': entities, preferences and facts."
        )
        self.max_search_results = store_config.get("max_search_results")
        self.writable = store_config.get("writable", True)
        self.extraction = store_config.get("extraction", False)

        self.user_id = store_config.get("user_id")
        self.graph_tools = store_config.get("graph_tools", True)
        self._include_entities = store_config.get("include_entities", True)
        self._include_preferences = store_config.get("include_preferences", True)
        self._include_facts = store_config.get("include_facts", True)
        self._min_score = store_config.get("min_score", 0.2)

        self._conversation_id = store_config.get("conversation_id")
        self._sink_key: str | None = self._conversation_id
        self._owns_client = client is None
        self._run_id = uuid.uuid4().hex
        self._written: set[tuple[str, int]] = set()
        self._initialized = False
        self._warned_unsupported_kinds: set[str] = set()

        if client is not None:
            self._client: MemoryClient = client
        else:
            from neo4j_agent_memory import MemoryClient as _MemoryClient

            assert settings is not None
            self._client = _MemoryClient(settings)

    @classmethod
    def for_nams(
        cls,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        transport_mode: TransportMode = "auto",
        **store_config: Unpack[Neo4jMemoryStoreConfig],
    ) -> Neo4jMemoryStore:
        """Construct a store against hosted NAMS.

        Reads ``MEMORY_API_KEY`` (and optionally ``MEMORY_ENDPOINT``) from the
        environment when not passed explicitly.
        """
        from neo4j_agent_memory.integrations.strands.config import (
            build_nams_settings,
            resolve_nams_connection,
        )

        endpoint, api_key = resolve_nams_connection(endpoint, api_key)
        store_config["settings"] = build_nams_settings(endpoint, api_key, transport_mode)
        return cls(**store_config)

    @property
    def is_nams(self) -> bool:
        return bool(getattr(self._client, "is_nams", False))

    @property
    def _sink_name(self) -> str:
        """Deterministic sink name, stable across processes and restarts."""
        return f"strands-memory-store/{self.user_id or '_'}/{self.name}"

    async def _resolve_sink(self) -> str:
        """Return the conversation key writes go to, creating the sink if needed.

        An explicit ``conversation_id`` is used verbatim. Otherwise: bolt keys
        conversations by ``session_id`` and ``add_message``/``add_messages_batch``
        both auto-create the sink via ``_ensure_conversation`` on first write, so
        the deterministic sink name *is* the whole contract — no backend call is
        made here, and none is needed. Bolt's ``CREATE_CONVERSATION`` query also
        has no metadata property, so tagging one is not possible even if we
        called ``create_conversation`` eagerly. NAMS mints its own conversation
        ids, so metadata is the only portable handle there — list and match
        ``_STORE_KEY`` metadata, else create. Same split as
        ``Neo4jSessionManager._aresolve_conversation``.
        """
        if self._sink_key is not None:
            return self._sink_key

        if not self.is_nams:
            self._sink_key = self._sink_name
            return self._sink_key

        short_term = self._client.short_term
        conversations = await short_term.list_conversations(
            user_identifier=self.user_id, limit=1000
        )
        for conversation in conversations:
            if (conversation.metadata or {}).get(_STORE_KEY) == self._sink_name:
                self._sink_key = str(conversation.id)
                return self._sink_key

        created = await short_term.create_conversation(
            session_id=self._sink_name,
            metadata={_STORE_KEY: self._sink_name, "session_type": "MEMORY_STORE"},
            user_identifier=self.user_id,
        )
        self._sink_key = str(created.id)
        return self._sink_key

    async def initialize(self) -> None:
        """Connect the client if not already connected. Idempotent."""
        if self._initialized:
            return
        if not getattr(self._client, "is_connected", False):
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
            subject, predicate, obj = (
                meta.get("subject"),
                meta.get("predicate"),
                meta.get("object"),
            )
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
