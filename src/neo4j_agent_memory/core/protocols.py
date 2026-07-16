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

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from neo4j_agent_memory.core.memory import ToolCallStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from neo4j_agent_memory.memory.long_term import (
        Entity,
        Fact,
        Preference,
        Relationship,
    )
    from neo4j_agent_memory.memory.reasoning import (
        ReasoningStep,
        ReasoningTrace,
        ToolCall,
    )
    from neo4j_agent_memory.memory.short_term import (
        Conversation,
        ConversationSummary,
        Message,
        SessionInfo,
    )
    from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome


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
        extract_entities: bool = True,
        extract_relations: bool = True,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
        extraction_mode: Literal["auto", "skip", "explicit"] = "auto",
        explicit_mentions: list[Any] | None = None,
        user_identifier: str | None = None,
    ) -> Message:
        """Append a message to a session and return the stored Message.

        ``extract_entities``/``extract_relations`` control automatic
        entity and relationship extraction from ``content``;
        ``extraction_mode`` further narrows extraction behavior
        (``"explicit"`` restricts extraction to ``explicit_mentions``).
        ``generate_embedding`` controls whether a vector embedding is
        computed for the message. ``metadata`` attaches arbitrary
        key/value data. ``user_identifier`` scopes the message to a
        user identity (multi-tenant).
        """
        ...

    async def get_conversation(
        self,
        session_id: str,
        *,
        conversation_id: str | None = None,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> Conversation:
        """Return the conversation (header + messages) for a session.

        ``limit`` bounds the number of messages returned; ``since``
        restricts to messages after a given time.
        """
        ...

    async def search_messages(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[Message]:
        """Vector/keyword search across messages.

        Optionally scoped to ``session_id``, filtered by a minimum
        similarity ``threshold``, and narrowed further by
        ``metadata_filters``.
        """
        ...

    async def list_sessions(
        self,
        *,
        prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Literal["created_at", "updated_at", "message_count"] = "updated_at",
        order_dir: Literal["asc", "desc"] = "desc",
    ) -> list[SessionInfo]:
        """List sessions known to the backend, filtered and paginated."""
        ...

    # Silver tier ------------------------------------------------------------

    async def delete_message(self, message_id: UUID | str) -> bool:
        """Delete a single message; returns True if deleted."""
        ...

    async def clear_session(self, session_id: str) -> None:
        """Delete every message in a session."""
        ...

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Return assembled context text for a query."""
        ...

    async def get_conversation_summary(
        self,
        session_id: str,
        *,
        max_tokens: int = 500,
        include_entities: bool = True,
        summarizer: Callable[[str], str | Awaitable[str]] | None = None,
    ) -> ConversationSummary:
        """Generate (or fetch) a summary of a conversation.

        ``max_tokens`` hints the target summary length,
        ``include_entities`` includes key entities in the result, and
        ``summarizer`` supplies a custom summarization function in place
        of the default.
        """
        ...

    # Gold tier --------------------------------------------------------------

    async def create_conversation(
        self,
        session_id: str,
        *,
        user_identifier: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        """Explicitly create a conversation node for a session, without adding messages.

        ``user_identifier``, when provided, scopes the conversation to a
        user identity (multi-tenant). ``metadata`` attaches arbitrary
        key/value data to the conversation.
        """
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
class LongTermProtocol(Protocol):
    """Contract for long-term memory (entities, preferences, facts)."""

    # Bronze tier ------------------------------------------------------------

    async def add_entity(
        self,
        name: str,
        entity_type: str,
        *,
        subtype: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        resolve: bool = True,
        generate_embedding: bool = True,
        deduplicate: bool = True,
        geocode: bool = True,
        enrich: bool = True,
        coordinates: tuple[float, float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Create or upsert an entity by name and type.

        ``subtype`` further classifies the entity (e.g. ``VEHICLE`` for
        ``OBJECT``). ``aliases`` records alternative names; ``attributes``
        and ``metadata`` attach arbitrary key/value data. ``resolve``,
        ``deduplicate``, ``geocode``, and ``enrich`` toggle resolution
        against existing entities, duplicate detection, location
        geocoding, and background enrichment respectively.
        ``coordinates`` sets a location's (latitude, longitude) directly.
        ``generate_embedding`` controls whether a vector embedding is
        computed for the entity.

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
        *,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Fact:
        """Record a subject-predicate-object fact."""
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
        entity_types: list[str] | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Entity]:
        """Vector/keyword search across entities, limited to at most `limit` results.

        ``entity_types``, when provided, restricts results to the given
        POLE+O types. ``threshold`` sets the minimum similarity score.
        """
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

    async def get_context(self, query: str, **kwargs: Any) -> str:
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
class ReasoningProtocol(Protocol):
    """Contract for reasoning memory (traces, steps, tool calls).

    Tool-usage statistics and audit-edge writes are backend-capability
    extensions, not part of this base contract.
    """

    # Bronze tier ------------------------------------------------------------

    async def start_trace(
        self,
        session_id: str,
        task: str,
        *,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
        triggered_by_message_id: UUID | str | None = None,
        user_identifier: str | None = None,
    ) -> ReasoningTrace:
        """Begin recording a reasoning trace; returns the empty trace.

        ``generate_embedding`` controls whether a task embedding is
        computed. ``metadata`` attaches arbitrary key/value data.
        ``triggered_by_message_id``, when provided, links the trace to
        the message that initiated it. ``user_identifier`` scopes the
        trace to a user identity (multi-tenant).
        """
        ...

    async def add_step(
        self,
        trace_id: UUID | str,
        *,
        thought: str | None = None,
        action: str | None = None,
        observation: str | None = None,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ReasoningStep:
        """Append a step (thought/action/observation) to a trace.

        ``generate_embedding`` controls whether a step embedding is
        computed; ``metadata`` attaches arbitrary key/value data.
        """
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
        error: str | None = None,
        auto_observation: bool = False,
        message_id: UUID | str | None = None,
        touched_entities: list[EntityRef] | None = None,
    ) -> ToolCall:
        """Record a tool invocation tied to a reasoning step.

        ``error`` records a failure message. ``auto_observation``, when
        true, sets the parent step's observation from the tool result.
        ``message_id`` links the call to the message that triggered it.
        ``touched_entities`` records entities this call affected, for
        one-hop audit traversal.
        """
        ...

    async def complete_trace(
        self,
        trace_id: UUID | str,
        *,
        outcome: str | TraceOutcome | None = None,
        success: bool | None = None,
        generate_step_embeddings: bool = False,
    ) -> Any:
        """Mark a trace as complete with an optional outcome and success flag.

        ``outcome`` accepts either a free-text summary or a structured
        :class:`TraceOutcome`. ``generate_step_embeddings`` batch-generates
        embeddings for any steps recorded without one.

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
        task: str,
        *,
        limit: int = 5,
        success_only: bool = True,
        threshold: float = 0.7,
    ) -> list[ReasoningTrace]:
        """Find traces with similar task descriptions."""
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
        *,
        limit: int = 100,
    ) -> list[ReasoningTrace]:
        """List traces for a session, capped at ``limit``."""
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
    "CypherQueryProtocol",
]
