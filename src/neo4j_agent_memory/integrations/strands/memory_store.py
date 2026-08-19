"""Strands ``MemoryStore`` backed by neo4j-agent-memory (bolt or NAMS).

Long-term recall for the agent loop. Distinct from
:class:`Neo4jSessionManager`, which persists and restores the transcript —
see the design spec's positioning section.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from typing_extensions import Unpack

try:
    from strands.memory import MemoryStore, MemoryStoreConfig
except ImportError as import_error:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents>=1.44.0 is required for the Strands memory store. "
        "Install with: pip install 'neo4j-agent-memory[strands]'"
    ) from import_error

if TYPE_CHECKING:
    from types import TracebackType

    from neo4j_agent_memory import MemoryClient, MemorySettings

logger = logging.getLogger(__name__)

#: Conversation-metadata key marking a conversation as a memory-store sink.
_STORE_KEY = "strands_memory_store"

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

        if client is not None:
            self._client: MemoryClient = client
        else:
            from neo4j_agent_memory import MemoryClient as _MemoryClient

            assert settings is not None
            self._client = _MemoryClient(settings)

    @classmethod
    def for_nams(cls, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> Neo4jMemoryStore:
        """Construct a store against hosted NAMS.

        Reads ``MEMORY_API_KEY`` (and optionally ``MEMORY_ENDPOINT``) from the
        environment, mirroring :meth:`Neo4jSessionManager.for_nams`.
        """
        if "client" not in store_config and "settings" not in store_config:
            from neo4j_agent_memory import MemorySettings

            store_config["settings"] = MemorySettings(backend="nams")
        return cls(**store_config)

    @property
    def is_nams(self) -> bool:
        return bool(getattr(self._client, "is_nams", False))

    async def initialize(self) -> None:
        """Connect the client and resolve the write sink. Idempotent."""
        if self._initialized:
            return
        if not getattr(self._client, "is_connected", False):
            await self._client.connect()
        self._initialized = True

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
