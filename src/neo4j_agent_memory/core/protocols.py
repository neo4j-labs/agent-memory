"""Backend-agnostic Protocols implemented by bolt and NAMS impls.

These are the shared contracts that both the direct-Neo4j (bolt) backend
and the hosted NAMS HTTP backend honor. The :class:`MemoryClient` exposes
each accessor (``client.short_term``, ``client.long_term``,
``client.reasoning``, ``client.query``) typed by the Protocol; the
concrete implementation is selected at ``connect()`` time based on
``MemorySettings.backend``.

Protocols are :func:`@runtime_checkable <typing.runtime_checkable>` so
that user code and tests can use ``isinstance(...)`` for ducktyping —
this matches the v0.3 pattern for :class:`LLMProvider` and
:class:`EmbeddingProvider`.

Method signatures use ``**kwargs`` where bolt impls accept extra,
backend-specific keyword arguments (e.g. ``extract_entities=True``,
``geocode=True``). NAMS impls silently ignore unknown kwargs; bolt
impls honor them.

The Protocol surface covers the SPEC tiers (Bronze, Silver, Gold,
Platinum). Bolt-only methods (e.g. ``add_messages_batch``,
``geocode_locations``) live on the concrete bolt classes but are NOT
declared on the Protocol — portable code uses Protocol methods only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
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


@runtime_checkable
class ShortTermProtocol(Protocol):
    """Contract for short-term memory (conversations, messages, context).

    Implementations: :class:`ShortTermMemory` (bolt),
    :class:`NamsShortTermMemory` (NAMS).
    """

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
        """Explicitly create a conversation node (without adding messages).

        ``conversation_id`` is deliberately not part of this signature:
        bolt's ``create_conversation`` takes bare ``**kwargs`` and does not
        name it, so a Protocol caller passing it would be silently dropped
        on bolt. Pass ``session_id`` only for portable code.
        """
        ...

    async def list_conversations(
        self,
        *,
        user_identifier: str | None = None,
        limit: int = 100,
    ) -> list[Conversation]:
        """List conversations; bolt may filter by user_identifier, NAMS by user_id."""
        ...

    # Platinum tier ----------------------------------------------------------

    async def bulk_add_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[Message]:
        """Bulk-insert messages in one round-trip. Server-side on NAMS.

        ``conversation_id`` is deliberately not part of this signature:
        bolt's ``bulk_add_messages`` forwards ``**kwargs`` to
        ``add_messages_batch``, which does not accept ``conversation_id``
        and raises ``TypeError`` if given it. Pass ``session_id`` only for
        portable code.
        """
        ...

    async def get_observations(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return inline observations extracted from the session (NAMS Platinum).

        Concrete return type may be a Pydantic ``Observation`` model in a
        future minor release; for v0.4 the protocol returns
        ``list[dict[str, Any]]`` to avoid prematurely locking the shape.
        """
        ...

    async def get_reflections(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return LLM-generated reflections for the session (NAMS Platinum)."""
        ...


@runtime_checkable
class LongTermProtocol(Protocol):
    """Contract for long-term memory (entities, preferences, facts).

    Implementations: :class:`LongTermMemory` (bolt),
    :class:`NamsLongTermMemory` (NAMS).
    """

    # Bronze tier ------------------------------------------------------------

    async def add_entity(
        self,
        name: str,
        entity_type: str,
        **kwargs: Any,
    ) -> Any:
        """Create or upsert an entity.

        Returns either an ``Entity`` (NAMS) or a ``(Entity, DeduplicationResult)``
        tuple (bolt). Portable code that needs a single entity should
        access ``result[0]`` after a type check; convenience helpers may
        normalize this in v0.5+.
        """
        ...

    async def add_preference(
        self,
        category: str,
        preference: str,
        **kwargs: Any,
    ) -> Preference:
        """Record a user preference."""
        ...

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object: str,
        **kwargs: Any,
    ) -> Fact:
        """Record a subject-predicate-object fact."""
        ...

    async def add_relationship(
        self,
        source_id: UUID | str,
        relationship_type: str,
        target_id: UUID | str,
        **kwargs: Any,
    ) -> None:
        """Create a typed relationship between two entities."""
        ...

    async def search_entities(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[Entity]:
        """Vector/keyword search across entities."""
        ...

    async def wait_for_extraction(self, **kwargs: Any) -> bool:
        """Await async entity extraction (NAMS) or no-op (bolt).

        Returns ``True`` once extraction has caught up (or immediately on
        bolt), ``False`` on timeout. See the NAMS implementation for the
        full keyword surface (``query``, ``expected_names``, ``min_results``,
        ``predicate``, ``timeout``, ``interval``).
        """
        ...

    async def search_preferences(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[Preference]:
        """Vector/keyword search across preferences."""
        ...

    async def search_facts(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[Fact]:
        """Vector/keyword search across facts."""
        ...

    async def get_entity_by_name(self, name: str) -> Entity | None:
        """Look up a single entity by exact (canonical) name."""
        ...

    # Silver tier ------------------------------------------------------------

    async def get_related_entities(
        self,
        entity_id: UUID | str,
        **kwargs: Any,
    ) -> Any:
        """Return entities related to the given entity (graph traversal)."""
        ...

    async def get_preferences_for(self, **kwargs: Any) -> list[Preference]:
        """Return preferences filtered by category/user_identifier."""
        ...

    async def supersede_preference(
        self,
        preference_id: UUID | str,
        **kwargs: Any,
    ) -> None:
        """Mark a preference as superseded (close its validity window)."""
        ...

    async def get_facts_about(self, entity_name: str) -> list[Fact]:
        """Return facts where the entity is the subject."""
        ...

    async def get_entity_relationships(
        self,
        entity_id: UUID | str,
    ) -> list[Relationship]:
        """Return outgoing relationships from an entity."""
        ...

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Return assembled context text from long-term memory."""
        ...

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
        **kwargs: Any,
    ) -> None:
        """Record user feedback (positive/negative) on an entity (NAMS only)."""
        ...

    async def get_entity_history(
        self,
        entity_id: UUID | str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return the edit/mention history for an entity (NAMS only)."""
        ...


@runtime_checkable
class ReasoningProtocol(Protocol):
    """Contract for reasoning memory (traces, steps, tool calls).

    Implementations: :class:`ReasoningMemory` (bolt),
    :class:`NamsReasoningMemory` (NAMS).
    """

    # Bronze tier ------------------------------------------------------------

    async def start_trace(
        self,
        session_id: str,
        task: str,
        **kwargs: Any,
    ) -> ReasoningTrace:
        """Begin recording a reasoning trace; returns the empty trace."""
        ...

    async def add_step(
        self,
        trace_id: UUID | str,
        **kwargs: Any,
    ) -> ReasoningStep:
        """Append a step (thought/action/observation) to a trace."""
        ...

    async def record_tool_call(
        self,
        step_id: UUID | str,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> ToolCall:
        """Record a tool invocation tied to a reasoning step."""
        ...

    async def complete_trace(
        self,
        trace_id: UUID | str,
        **kwargs: Any,
    ) -> None:
        """Mark a trace as complete with an optional outcome and success flag."""
        ...

    # Silver tier ------------------------------------------------------------

    async def search_steps(self, query: str, **kwargs: Any) -> list[ReasoningStep]:
        """Vector/keyword search across reasoning steps."""
        ...

    async def get_similar_traces(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[ReasoningTrace]:
        """Find traces with similar task descriptions."""
        ...

    async def get_trace(self, trace_id: UUID | str) -> ReasoningTrace | None:
        """Fetch a single trace by id (header only)."""
        ...

    async def get_trace_with_steps(
        self,
        trace_id: UUID | str,
    ) -> ReasoningTrace | None:
        """Fetch a trace with its full step + tool-call chain."""
        ...

    async def get_session_traces(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> list[ReasoningTrace]:
        """List traces for a session."""
        ...

    async def list_traces(self, **kwargs: Any) -> list[ReasoningTrace]:
        """List traces globally (paginated)."""
        ...

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Return assembled context text from reasoning memory."""
        ...

    # Gold tier --------------------------------------------------------------

    async def get_tool_stats(self, **kwargs: Any) -> Any:
        """Return aggregate tool-usage statistics.

        Bolt returns ``dict[str, ToolStats]``; NAMS returns
        ``list[ToolStats]``. Concrete normalization may happen in v0.5.
        """
        ...

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

    Implementations: :class:`BoltCypherQuery` (forwards to
    :class:`Neo4jClient.execute_read`), :class:`NamsCypherQuery`
    (forwards to ``POST /v1/query``, Platinum tier).

    Both implementations enforce read-only via a shared
    ``_is_read_only_query`` validator. Write queries raise
    :class:`ValueError` before any backend round-trip.
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
