"""Mock implementations of memory classes for testing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

from neo4j_agent_memory.memory.long_term import (
    Entity,
    EntityType,
    Fact,
    Preference,
)
from neo4j_agent_memory.memory.reasoning import (
    ReasoningStep,
    ReasoningTrace,
    ToolCall,
    ToolCallStatus,
    ToolStats,
)
from neo4j_agent_memory.memory.short_term import (
    Conversation,
    ConversationSummary,
    Message,
    MessageRole,
    SessionInfo,
)


class MockShortTermMemory:
    """In-memory mock of ShortTermMemory for unit testing."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, Message] = {}

    async def add_message(
        self,
        session_id: str,
        role: MessageRole | str,
        content: str,
        *,
        generate_embedding: bool = True,
        extract_entities: bool = False,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> Message:
        """Add a message to the mock store."""
        if isinstance(role, str):
            role = MessageRole(role)

        # Get or create conversation
        if session_id not in self._conversations:
            self._conversations[session_id] = Conversation(
                id=uuid4(),
                session_id=session_id,
                messages=[],
                created_at=datetime.now(timezone.utc),
            )

        conv = self._conversations[session_id]

        message = Message(
            id=uuid4(),
            role=role,
            content=content,
            conversation_id=conv.id,
            created_at=timestamp or datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        conv.messages.append(message)
        conv.updated_at = datetime.now(timezone.utc)
        self._messages[str(message.id)] = message

        return message

    async def add_messages_batch(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        batch_size: int = 100,
        generate_embeddings: bool = True,
        extract_entities: bool = False,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[Message]:
        """Batch add messages to the mock store."""
        result = []
        total = len(messages)

        for i, msg_data in enumerate(messages):
            role = msg_data.get("role", "user")
            content = msg_data.get("content", "")
            metadata = msg_data.get("metadata")
            timestamp = msg_data.get("timestamp")

            message = await self.add_message(
                session_id,
                role,
                content,
                metadata=metadata,
                timestamp=timestamp,
                generate_embedding=generate_embeddings,
                extract_entities=extract_entities,
            )
            result.append(message)

            if on_progress and (i + 1) % batch_size == 0:
                on_progress(i + 1, total)

        if on_progress:
            on_progress(total, total)

        return result

    async def get_conversation(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> Conversation:
        """Get conversation by session ID."""
        if session_id not in self._conversations:
            return Conversation(
                id=uuid4(),
                session_id=session_id,
                messages=[],
                created_at=datetime.now(timezone.utc),
            )

        conv = self._conversations[session_id]
        if limit:
            messages = conv.messages[-limit:]
            return Conversation(
                id=conv.id,
                session_id=conv.session_id,
                messages=messages,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
        return conv

    async def search_messages(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[Message]:
        """Search messages (simple string matching for mock)."""
        results = []
        query_lower = query.lower()

        for conv in self._conversations.values():
            if session_id and conv.session_id != session_id:
                continue

            for msg in conv.messages:
                if query_lower in msg.content.lower():
                    # Add similarity score to metadata
                    msg_copy = Message(
                        id=msg.id,
                        role=msg.role,
                        content=msg.content,
                        conversation_id=msg.conversation_id,
                        created_at=msg.created_at,
                        metadata={**msg.metadata, "similarity": 0.9},
                    )
                    results.append(msg_copy)

                    if len(results) >= limit:
                        return results

        return results

    async def delete_message(
        self,
        message_id: UUID | str,
        *,
        cascade: bool = True,
    ) -> bool:
        """Delete a message by ID."""
        message_id_str = str(message_id)

        if message_id_str not in self._messages:
            return False

        del self._messages[message_id_str]

        # Remove from conversation
        for conv in self._conversations.values():
            conv.messages = [m for m in conv.messages if str(m.id) != message_id_str]

        return True

    async def list_sessions(
        self,
        *,
        prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Literal["created_at", "updated_at", "message_count"] = "updated_at",
        order_dir: Literal["asc", "desc"] = "desc",
    ) -> list[SessionInfo]:
        """List sessions."""
        sessions = []

        for session_id, conv in self._conversations.items():
            if prefix and not session_id.startswith(prefix):
                continue

            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    title=conv.title,
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                    message_count=len(conv.messages),
                    first_message_preview=conv.messages[0].content[:100] if conv.messages else None,
                    last_message_preview=conv.messages[-1].content[:100] if conv.messages else None,
                )
            )

        # Sort
        reverse = order_dir == "desc"
        if order_by == "created_at":
            sessions.sort(key=lambda s: s.created_at, reverse=reverse)
        elif order_by == "updated_at":
            sessions.sort(key=lambda s: s.updated_at or s.created_at, reverse=reverse)
        elif order_by == "message_count":
            sessions.sort(key=lambda s: s.message_count, reverse=reverse)

        return sessions[offset : offset + limit]

    async def get_conversation_summary(
        self,
        session_id: str,
        *,
        max_tokens: int = 500,
        include_entities: bool = True,
        summarizer: Callable[[str], str] | None = None,
    ) -> ConversationSummary:
        """Generate a conversation summary."""
        conv = await self.get_conversation(session_id)

        if not conv.messages:
            return ConversationSummary(
                session_id=session_id,
                summary="No messages in this conversation.",
                message_count=0,
            )

        # Build basic summary
        summary = f"Conversation with {len(conv.messages)} messages."
        if conv.messages:
            first_msg = conv.messages[0].content[:100]
            summary += f' Started with: "{first_msg}..."'

        return ConversationSummary(
            session_id=session_id,
            summary=summary,
            message_count=len(conv.messages),
            time_range=(conv.messages[0].created_at, conv.messages[-1].created_at)
            if conv.messages
            else None,
            key_entities=[],
            key_topics=[],
        )

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Get conversation context."""
        session_id = kwargs.get("session_id")
        if session_id and session_id in self._conversations:
            conv = self._conversations[session_id]
            lines = [f"**{m.role.value}**: {m.content}" for m in conv.messages[-10:]]
            return "\n".join(lines)
        return ""

    async def clear_session(self, session_id: str) -> None:
        """Clear all data for a session."""
        if session_id in self._conversations:
            del self._conversations[session_id]


class MockLongTermMemory:
    """In-memory mock of LongTermMemory for unit testing."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._preferences: dict[str, Preference] = {}
        self._facts: dict[str, Fact] = {}

    async def add_entity(
        self,
        name: str,
        entity_type: EntityType | str,
        *,
        description: str | None = None,
        generate_embedding: bool = True,
    ) -> Entity:
        """Add an entity."""
        if isinstance(entity_type, str):
            entity_type = EntityType(entity_type.lower())

        entity = Entity(
            id=uuid4(),
            name=name,
            type=entity_type,
            description=description,
            canonical_name=name,
            created_at=datetime.now(timezone.utc),
        )

        self._entities[str(entity.id)] = entity
        return entity

    async def add_preference(
        self,
        category: str,
        preference: str,
        *,
        context: str | None = None,
        confidence: float = 1.0,
        generate_embedding: bool = True,
    ) -> Preference:
        """Add a preference."""
        pref = Preference(
            id=uuid4(),
            category=category,
            preference=preference,
            context=context,
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )

        self._preferences[str(pref.id)] = pref
        return pref

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object_: str,
        *,
        confidence: float = 1.0,
        generate_embedding: bool = True,
    ) -> Fact:
        """Add a fact."""
        fact = Fact(
            id=uuid4(),
            subject=subject,
            predicate=predicate,
            object=object_,
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )

        self._facts[str(fact.id)] = fact
        return fact

    async def search_entities(
        self,
        query: str,
        *,
        entity_types: list[EntityType | str] | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Entity]:
        """Search entities (simple string matching)."""
        results = []
        query_lower = query.lower()

        for entity in self._entities.values():
            if entity_types:
                # Check if entity type matches any in the list
                entity_type_str = (
                    entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                )
                type_matches = any(
                    (t.value if hasattr(t, "value") else str(t)) == entity_type_str
                    for t in entity_types
                )
                if not type_matches:
                    continue
            if query_lower in entity.name.lower():
                results.append(entity)
                if len(results) >= limit:
                    break

        return results

    async def search_preferences(
        self,
        query: str,
        *,
        limit: int = 10,
        category: str | None = None,
    ) -> list[Preference]:
        """Search preferences (simple string matching)."""
        results = []
        query_lower = query.lower()

        for pref in self._preferences.values():
            if category and pref.category != category:
                continue
            if query_lower in pref.preference.lower():
                results.append(pref)
                if len(results) >= limit:
                    break

        return results

    async def get_preferences_by_category(self, category: str) -> list[Preference]:
        """Get preferences by category."""
        return [p for p in self._preferences.values() if p.category == category]

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Get long-term context."""
        entities = await self.search_entities(query, limit=5)
        prefs = await self.search_preferences(query, limit=5)

        parts = []
        if entities:
            parts.append("### Entities")
            for e in entities:
                parts.append(f"- {e.name} ({e.type})")

        if prefs:
            parts.append("### Preferences")
            for p in prefs:
                parts.append(f"- [{p.category}] {p.preference}")

        return "\n".join(parts)


class MockReasoningMemory:
    """In-memory mock of ReasoningMemory for unit testing."""

    def __init__(self) -> None:
        self._traces: dict[str, ReasoningTrace] = {}
        self._steps: dict[str, ReasoningStep] = {}
        self._tool_calls: dict[str, ToolCall] = {}
        self._tool_stats: dict[str, ToolStats] = {}

    async def start_trace(
        self,
        session_id: str,
        task: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ReasoningTrace:
        """Start a new reasoning trace."""
        trace = ReasoningTrace(
            id=uuid4(),
            session_id=session_id,
            task=task,
            started_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        self._traces[str(trace.id)] = trace
        return trace

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
        """Add a reasoning step."""
        trace = self._traces.get(str(trace_id))
        step_number = len(trace.steps) + 1 if trace else 1

        step = ReasoningStep(
            id=uuid4(),
            trace_id=trace_id,
            step_number=step_number,
            thought=thought,
            action=action,
            observation=observation,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        self._steps[str(step.id)] = step

        if trace:
            trace.steps.append(step)

        return step

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
    ) -> ToolCall:
        """Record a tool call."""
        tool_call = ToolCall(
            id=uuid4(),
            step_id=step_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=status,
            duration_ms=duration_ms,
            error=error,
        )

        self._tool_calls[str(tool_call.id)] = tool_call

        # Update step's tool_calls
        step = self._steps.get(str(step_id))
        if step:
            step.tool_calls.append(tool_call)

        # Update tool stats
        if tool_name not in self._tool_stats:
            self._tool_stats[tool_name] = ToolStats(
                name=tool_name,
                total_calls=0,
                successful_calls=0,
                failed_calls=0,
            )

        stats = self._tool_stats[tool_name]
        stats.total_calls += 1
        if status == ToolCallStatus.SUCCESS:
            stats.successful_calls += 1
        elif status in (ToolCallStatus.ERROR, ToolCallStatus.FAILURE):
            stats.failed_calls += 1

        stats.success_rate = (
            stats.successful_calls / stats.total_calls if stats.total_calls > 0 else 0.0
        )

        return tool_call

    async def complete_trace(
        self,
        trace_id: UUID | str,
        *,
        outcome: str | None = None,
        success: bool | None = None,
        generate_step_embeddings: bool = False,
    ) -> ReasoningTrace:
        """Complete a reasoning trace."""
        trace = self._traces.get(str(trace_id))
        if trace is None:
            raise ValueError(f"Trace not found: {trace_id}")

        trace.outcome = outcome
        trace.success = success
        trace.completed_at = datetime.now(timezone.utc)

        return trace

    async def get_trace(self, trace_id: UUID | str) -> ReasoningTrace | None:
        """Get a trace by ID."""
        return self._traces.get(str(trace_id))

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
        """List traces with filters."""
        traces = list(self._traces.values())

        # Apply filters
        if session_id:
            traces = [t for t in traces if t.session_id == session_id]
        if success_only is not None:
            traces = [t for t in traces if t.success == success_only]
        if since:
            traces = [t for t in traces if t.started_at >= since]
        if until:
            traces = [t for t in traces if t.started_at <= until]

        # Sort
        reverse = order_dir == "desc"
        if order_by == "started_at":
            traces.sort(key=lambda t: t.started_at, reverse=reverse)
        elif order_by == "completed_at":
            traces.sort(key=lambda t: t.completed_at or t.started_at, reverse=reverse)

        return traces[offset : offset + limit]

    async def get_tool_stats(
        self,
        tool_name: str | None = None,
    ) -> list[ToolStats]:
        """Get tool statistics."""
        if tool_name:
            stats = self._tool_stats.get(tool_name)
            return [stats] if stats else []
        return list(self._tool_stats.values())

    async def get_similar_traces(
        self,
        task: str,
        *,
        limit: int = 5,
        success_only: bool = True,
        threshold: float = 0.7,
    ) -> list[ReasoningTrace]:
        """Find similar traces (simple string matching)."""
        results = []
        task_lower = task.lower()

        for trace in self._traces.values():
            if success_only and not trace.success:
                continue
            if task_lower in trace.task.lower():
                results.append(trace)
                if len(results) >= limit:
                    break

        return results

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Get reasoning context."""
        traces = await self.get_similar_traces(query, limit=3)
        if not traces:
            return ""

        lines = ["### Similar Past Tasks"]
        for trace in traces:
            status = "succeeded" if trace.success else "failed"
            lines.append(f"- {trace.task} ({status})")

        return "\n".join(lines)


class MockMemoryClient:
    """
    In-memory mock of MemoryClient for unit testing.

    Provides mock implementations of all three memory types that store
    data in memory rather than Neo4j.

    Example:
        async def test_my_function():
            client = MockMemoryClient()

            # Use like the real MemoryClient
            await client.short_term.add_message("session-1", "user", "Hello")
            conv = await client.short_term.get_conversation("session-1")

            assert len(conv.messages) == 1
    """

    def __init__(self) -> None:
        self.short_term = MockShortTermMemory()
        self.long_term = MockLongTermMemory()
        self.reasoning = MockReasoningMemory()

    async def __aenter__(self) -> MockMemoryClient:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        pass

    async def connect(self) -> None:
        """No-op for mock."""
        pass

    async def close(self) -> None:
        """No-op for mock."""
        pass

    async def get_context(
        self,
        query: str,
        *,
        session_id: str | None = None,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
    ) -> str:
        """Get combined context from all memory types."""
        parts = []

        if include_short_term:
            short_term_ctx = await self.short_term.get_context(query, session_id=session_id)
            if short_term_ctx:
                parts.append(short_term_ctx)

        if include_long_term:
            long_term_ctx = await self.long_term.get_context(query)
            if long_term_ctx:
                parts.append(long_term_ctx)

        if include_reasoning:
            reasoning_ctx = await self.reasoning.get_context(query)
            if reasoning_ctx:
                parts.append(reasoning_ctx)

        return "\n\n".join(parts)

    def clear_all(self) -> None:
        """Clear all mock data."""
        self.short_term = MockShortTermMemory()
        self.long_term = MockLongTermMemory()
        self.reasoning = MockReasoningMemory()


# Backward compatibility alias (deprecated)
MockProceduralMemory = MockReasoningMemory
