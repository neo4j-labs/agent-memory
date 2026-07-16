"""Backend-agnostic memory contracts.

These Protocols define the shared, implementation-independent contracts
for short-term, long-term, and reasoning memory access, plus a unified
read-only Cypher accessor. The :class:`MemoryClient` exposes each
accessor (``client.short_term``, ``client.long_term``,
``client.reasoning``, ``client.query``) typed by the Protocol.

Protocols are :func:`@runtime_checkable <typing.runtime_checkable>` so
that user code and tests can use ``isinstance(...)`` for ducktyping —
this matches the v0.3 pattern for :class:`LLMProvider` and
:class:`EmbeddingProvider`.

The Protocol surface covers the SPEC tiers (Bronze, Silver, Gold,
Platinum). Portable code should rely only on the methods declared here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from neo4j_agent_memory.core.memory import ToolCallStatus

if TYPE_CHECKING:
    from datetime import datetime

    from neo4j_agent_memory.memory.long_term import (
        DeduplicationStats,
        Entity,
        Fact,
        Preference,
        Relationship,
    )
    from neo4j_agent_memory.memory.reasoning import (
        ReasoningStep,
        ReasoningTrace,
        Tool,
        ToolCall,
        ToolStats,
    )
    from neo4j_agent_memory.memory.short_term import (
        Conversation,
        ConversationSummary,
        Message,
        SessionInfo,
    )


@runtime_checkable
class ShortTermProtocol(Protocol):
    """Contract for short-term memory (conversations, messages, context)."""

    # Bronze tier ------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        conversation_id: str | None = None,
    ) -> Message:
        """Append a message to a session and return the stored Message."""
        ...

    async def get_conversation(
        self,
        session_id: str,
        *,
        conversation_id: str | None = None,
    ) -> Conversation:
        """Return the conversation (header + messages) for a session."""
        ...

    async def search_messages(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[Message]:
        """Vector/keyword search across messages (optionally scoped to session_id)."""
        ...

    async def list_sessions(self, *, limit: int = 100) -> list[SessionInfo]:
        """List sessions known to the backend."""
        ...

    # Silver tier ------------------------------------------------------------

    async def delete_message(self, message_id: UUID | str) -> bool:
        """Delete a single message; returns True if deleted."""
        ...

    async def clear_session(self, session_id: str) -> None:
        """Delete every message in a session."""
        ...

    async def get_context(self, query: str) -> str:
        """Return assembled context text for a query."""
        ...

    async def get_conversation_summary(
        self,
        session_id: str,
    ) -> ConversationSummary:
        """Generate (or fetch) a summary of a conversation."""
        ...

    # Gold tier --------------------------------------------------------------

    async def create_conversation(
        self,
        session_id: str,
    ) -> Conversation:
        """Explicitly create a conversation node for a session, without adding messages."""
        ...

    async def list_conversations(
        self,
        *,
        user_identifier: str | None = None,
        limit: int = 100,
    ) -> list[Conversation]:
        """List conversations, optionally scoped to a user identifier."""
        ...

    # Platinum tier ----------------------------------------------------------

    async def bulk_add_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[Message]:
        """Bulk-insert messages for a session in one round-trip, preserving order."""
        ...

    async def get_observations(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return inline observations extracted from the session."""
        ...

    async def get_reflections(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return generated reflections for the session."""
        ...


@runtime_checkable
class BoltShortTermProtocol(ShortTermProtocol, Protocol):
    """Self-hosted (bolt) short-term surface: base contract plus bolt-only methods."""

    async def search(self, query: str, **kwargs: Any) -> list[Message]:
        """Search for messages (delegates to search_messages)."""
        ...

    async def add_messages_batch(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        batch_size: int = 100,
        generate_embeddings: bool = True,
        extract_entities: bool = False,
        extract_relations: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
        on_batch_complete: Callable[[int, list[Message]], None] | None = None,
    ) -> list[Message]:
        """Bulk-load messages with transaction batching, returning the stored messages."""
        ...

    async def extract_entities_from_session(
        self,
        session_id: str,
        *,
        batch_size: int = 50,
        skip_existing: bool = True,
        extract_relations: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Extract entities and relations from all messages in a session."""
        ...

    async def generate_embeddings_batch(
        self,
        session_id: str,
        *,
        batch_size: int = 100,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Generate embeddings for session messages that don't have them yet."""
        ...

    async def migrate_message_links(self) -> dict[str, int]:
        """Backfill FIRST_MESSAGE/NEXT_MESSAGE links for pre-existing messages."""
        ...


@runtime_checkable
class LongTermProtocol(Protocol):
    """Contract for long-term memory (entities, preferences, facts)."""

    # Bronze tier ------------------------------------------------------------

    async def add_entity(
        self,
        name: str,
        entity_type: str,
        *,
        description: str | None = None,
    ) -> Any:
        """Create or upsert an entity by name and type.

        Returns the created or updated entity, either alone or paired
        with a deduplication result. Portable code that needs a single
        entity should narrow the return value's type before use.
        """
        ...

    async def add_preference(
        self,
        category: str,
        preference: str,
        *,
        context: str | None = None,
        confidence: float = 1.0,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
        user_identifier: str | None = None,
        applies_to: list[Any] | None = None,
    ) -> Preference:
        """Record a user preference under a category, with optional context, confidence, and metadata."""
        ...

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        /,
        *,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Fact:
        """Record a subject-predicate-object fact.

        The third argument (the object of the fact) is positional-only.
        """
        ...

    async def add_relationship(
        self,
        source: Entity | UUID,
        target: Entity | UUID,
        relationship_type: str,
        *,
        description: str | None = None,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Relationship:
        """Create a typed relationship between two entities."""
        ...

    async def search_entities(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[Entity]:
        """Vector/keyword search across entities, limited to at most `limit` results."""
        ...

    async def wait_for_extraction(self) -> bool:
        """Wait for any pending asynchronous entity extraction to complete.

        Returns True once extraction has settled (or immediately if there
        is nothing to await).
        """
        ...

    async def search_preferences(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Preference]:
        """Vector/keyword search across preferences, optionally filtered by category."""
        ...

    async def search_facts(
        self,
        query: str,
        *,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Fact]:
        """Vector/keyword search across facts."""
        ...

    async def get_entity_by_name(self, name: str) -> Entity | None:
        """Look up a single entity by exact (canonical) name."""
        ...

    # Silver tier ------------------------------------------------------------

    async def get_related_entities(
        self,
        entity_id: UUID,
        /,
    ) -> Any:
        """Return entities related to the given entity via graph traversal.

        The entity is identified positional-only, by its UUID.
        """
        ...

    async def get_preferences_for(
        self,
        *,
        user_identifier: str,
        applies_to: Any | None = None,
        active_only: bool = True,
        as_of: datetime | None = None,
    ) -> list[Preference]:
        """Return preferences scoped to a user, optionally filtered further."""
        ...

    async def get_facts_about(
        self,
        subject: str,
        /,
    ) -> list[Fact]:
        """Return facts where the given entity is the subject.

        The subject is identified positional-only.
        """
        ...

    async def get_context(self, query: str) -> str:
        """Return assembled context text from long-term memory for a query."""
        ...

    # ``supersede_preference`` and ``get_entity_relationships`` are
    # deliberately absent: both have a real, differently-shaped signature on
    # each implementation (arity or return-type divergence, not just an
    # optional extra), so no single Protocol signature is honest for both
    # without an implementation change.

    # Gold tier --------------------------------------------------------------

    async def get_entity_provenance(
        self,
        entity_id: UUID | str,
    ) -> dict[str, Any]:
        """Return source messages + extractors that produced this entity."""
        ...

    # Platinum tier ----------------------------------------------------------

    async def set_entity_feedback(
        self,
        entity_id: UUID | str,
        feedback: str,
    ) -> None:
        """Record user feedback (positive/negative) on an entity."""
        ...

    async def get_entity_history(
        self,
        entity_id: UUID | str,
    ) -> list[dict[str, Any]]:
        """Return the edit/mention history for an entity."""
        ...


@runtime_checkable
class BoltLongTermProtocol(LongTermProtocol, Protocol):
    """Self-hosted (bolt) long-term surface: base contract plus bolt-only methods."""

    async def search(self, query: str, **kwargs: Any) -> list[Entity]:
        """Search for entities (delegates to search_entities)."""
        ...

    # Deduplication ------------------------------------------------------

    async def find_potential_duplicates(
        self,
        *,
        limit: int = 100,
    ) -> list[tuple[Entity, Entity, float]]:
        """Return (entity1, entity2, confidence) triples pending duplicate review."""
        ...

    async def merge_duplicate_entities(
        self,
        source_id: UUID,
        target_id: UUID,
    ) -> tuple[Entity, Entity] | None:
        """Merge source into target, transferring relationships; None if not found."""
        ...

    async def review_duplicate(
        self,
        source_id: UUID,
        target_id: UUID,
        *,
        confirm: bool,
    ) -> bool:
        """Confirm (merge) or reject a flagged duplicate pair."""
        ...

    async def get_same_as_cluster(
        self,
        entity_id: UUID,
    ) -> list[Entity]:
        """Return every entity in the given entity's SAME_AS cluster, including itself."""
        ...

    async def get_deduplication_stats(self) -> DeduplicationStats:
        """Return aggregate counts describing entity deduplication state."""
        ...

    # Provenance ----------------------------------------------------------

    async def register_extractor(
        self,
        name: str,
        *,
        version: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update the Extractor node identified by ``name``."""
        ...

    async def link_entity_to_message(
        self,
        entity: Entity | UUID,
        message_id: UUID | str,
        *,
        confidence: float = 1.0,
        start_pos: int | None = None,
        end_pos: int | None = None,
        context: str | None = None,
    ) -> bool:
        """Record an EXTRACTED_FROM provenance link from entity to source message."""
        ...

    async def link_entity_to_extractor(
        self,
        entity: Entity | UUID,
        extractor_name: str,
        *,
        confidence: float = 1.0,
        extraction_time_ms: float | None = None,
    ) -> bool:
        """Record an EXTRACTED_BY provenance link from entity to extractor."""
        ...

    async def get_entities_from_message(
        self,
        message_id: UUID | str,
    ) -> list[tuple[Entity, dict[str, Any]]]:
        """Return (entity, extraction_info) pairs extracted from a message, by position."""
        ...

    async def get_entities_by_extractor(
        self,
        extractor_name: str,
        *,
        limit: int = 100,
    ) -> list[tuple[Entity, dict[str, Any]]]:
        """Return (entity, extraction_info) pairs produced by the named extractor."""
        ...

    async def list_extractors(self) -> list[dict[str, Any]]:
        """List every registered extractor with its entity count."""
        ...

    async def get_extraction_stats(self) -> dict[str, Any]:
        """Return overall extraction stats: total entities, source messages, extractors."""
        ...

    async def get_extractor_stats(self) -> list[dict[str, Any]]:
        """Return per-extractor stats: entity count and average confidence."""
        ...

    async def delete_entity_provenance(
        self,
        entity: Entity | UUID,
    ) -> int:
        """Delete all provenance links for an entity; returns the number removed."""
        ...

    # Preferences / relationships ------------------------------------------

    async def get_preferences_by_category(
        self,
        category: str,
        *,
        limit: int = 100,
    ) -> list[Preference]:
        """Return every preference recorded under ``category``."""
        ...

    async def supersede_preference(
        self,
        old_preference_id: UUID | str,
        new_preference_id: UUID | str,
    ) -> None:
        """Mark ``old`` as superseded by ``new`` for time-travel queries. Idempotent."""
        ...

    async def get_entity_relationships(
        self,
        entity_name: str,
    ) -> list[tuple[Entity, Relationship]]:
        """Return (related_entity, relationship) pairs for the named entity."""
        ...

    # Geospatial ------------------------------------------------------------

    async def geocode_locations(
        self,
        *,
        batch_size: int = 50,
        skip_existing: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Geocode Location entities lacking coordinates; returns processed/geocoded counts."""
        ...

    async def search_locations_near(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_km: float = 10.0,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[Entity]:
        """Return Location entities within ``radius_km`` of a point, nearest first."""
        ...

    async def search_locations_in_bounding_box(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[Entity]:
        """Return Location entities within the given latitude/longitude bounding box."""
        ...

    async def get_location_coordinates(
        self,
        entity_id: UUID | str,
    ) -> tuple[float, float] | None:
        """Return (latitude, longitude) for a Location entity, or None if not geocoded."""
        ...


@runtime_checkable
class ReasoningProtocol(Protocol):
    """Contract for reasoning memory (traces, steps, tool calls).

    Tool-call and trace-completion helpers, tool-usage statistics, and
    audit-edge writes are backend-capability extensions, not part of
    this base contract.
    """

    # Bronze tier ------------------------------------------------------------

    async def start_trace(
        self,
        session_id: str,
        task: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ReasoningTrace:
        """Begin recording a reasoning trace; returns the empty trace."""
        ...

    async def add_step(
        self,
        trace_id: UUID | str,
        *,
        thought: str | None = None,
        action: str | None = None,
        observation: str | None = None,
    ) -> ReasoningStep:
        """Append a step (thought/action/observation) to a trace."""
        ...

    async def record_tool_call(
        self,
        step_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        result: Any | None = None,
        status: ToolCallStatus = ToolCallStatus.SUCCESS,
        duration_ms: int | None = None,
    ) -> ToolCall:
        """Record a tool invocation tied to a reasoning step."""
        ...

    async def complete_trace(
        self,
        trace_id: UUID | str,
        *,
        outcome: str | None = None,
        success: bool | None = None,
    ) -> Any:
        """Mark a trace as complete with an optional outcome and success flag.

        May return the completed trace, or ``None`` if the
        implementation does not materialize one. Portable code that
        needs the trace back should re-fetch it via ``get_trace()``.
        """
        ...

    # Silver tier ------------------------------------------------------------

    async def search_steps(
        self,
        query: str,
        *,
        limit: int = 10,
        success_only: bool = True,
        threshold: float = 0.7,
    ) -> list[Any]:
        """Vector/keyword search across reasoning steps.

        Returns implementation-defined step records; narrow the
        element type before use.
        """
        ...

    async def get_similar_traces(
        self,
        query: str,
        /,
        *,
        limit: int = 5,
        success_only: bool = True,
        threshold: float = 0.7,
    ) -> list[ReasoningTrace]:
        """Find traces with similar task descriptions.

        The query is identified positional-only.
        """
        ...

    async def get_trace(self, trace_id: UUID | str) -> ReasoningTrace | None:
        """Fetch a single trace by id (header only)."""
        ...

    async def get_trace_with_steps(
        self,
        trace_id: UUID,
    ) -> ReasoningTrace | None:
        """Fetch a trace with its full step + tool-call chain."""
        ...

    async def get_session_traces(
        self,
        session_id: str,
    ) -> list[ReasoningTrace]:
        """List traces for a session."""
        ...

    async def list_traces(
        self,
        *,
        session_id: str | None = None,
        success_only: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Literal["started_at", "completed_at"] = "started_at",
        order_dir: Literal["asc", "desc"] = "desc",
    ) -> list[ReasoningTrace]:
        """List traces globally, filtered and paginated."""
        ...

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Return assembled context text from reasoning memory."""
        ...

    # Gold tier --------------------------------------------------------------

    async def link_trace_to_message(
        self,
        trace_id: UUID | str,
        message_id: UUID | str,
    ) -> None:
        """Link a reasoning trace to the message that triggered it."""
        ...


@runtime_checkable
class BoltReasoningProtocol(ReasoningProtocol, Protocol):
    """Self-hosted (bolt) reasoning surface: base contract plus bolt-only methods."""

    async def search(self, query: str, **kwargs: Any) -> list[ReasoningStep]:
        """Search reasoning steps (not supported on this backend; returns empty)."""
        ...

    async def get_tool_usage_stats(
        self,
        tool_name: str | None = None,
    ) -> dict[str, Tool]:
        """Return tool name to :class:`Tool` mapping, computed from ToolCall nodes.

        Deprecated in favor of :meth:`get_tool_stats`, which reads
        pre-aggregated statistics.
        """
        ...

    async def get_tool_stats(
        self,
        tool_name: str | None = None,
    ) -> list[ToolStats]:
        """Return pre-aggregated per-tool statistics, ordered by total calls descending."""
        ...

    async def migrate_tool_stats(self) -> dict[str, int]:
        """Backfill pre-aggregated Tool stats from existing ToolCall nodes."""
        ...


@runtime_checkable
class CypherQueryProtocol(Protocol):
    """Unified read-only Cypher accessor (``client.query``).

    Executes read-only Cypher and returns result rows. Write queries
    raise :class:`ValueError` before any backend round-trip.
    """

    async def cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read-only Cypher query and return result rows."""
        ...


__all__ = [
    "ShortTermProtocol",
    "LongTermProtocol",
    "ReasoningProtocol",
    "BoltShortTermProtocol",
    "BoltLongTermProtocol",
    "BoltReasoningProtocol",
    "CypherQueryProtocol",
]
