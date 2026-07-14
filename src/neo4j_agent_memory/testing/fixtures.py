"""Test fixtures and data factories for neo4j-agent-memory."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

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
)
from neo4j_agent_memory.memory.short_term import (
    Conversation,
    Message,
    MessageRole,
    SessionInfo,
)


class MemoryFixtures:
    """Factory methods for creating test data fixtures.

    Provides convenient methods to create test instances of all memory types
    with sensible defaults that can be overridden.

    Example:
        # Create a simple message
        msg = MemoryFixtures.message(content="Hello, world!")

        # Create a conversation with messages
        conv = MemoryFixtures.conversation(message_count=5)

        # Create a reasoning trace with steps
        trace = MemoryFixtures.reasoning_trace(step_count=3, include_tool_calls=True)
    """

    # Default values for generating test data
    _message_counter = 0
    _entity_counter = 0

    @classmethod
    def message(
        cls,
        *,
        id: UUID | None = None,
        role: MessageRole | str = MessageRole.USER,
        content: str | None = None,
        conversation_id: UUID | None = None,
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> Message:
        """Create a test Message instance.

        Args:
            id: Message UUID (auto-generated if not provided)
            role: Message role (user, assistant, system, tool)
            content: Message content (auto-generated if not provided)
            conversation_id: Parent conversation ID
            created_at: Creation timestamp
            metadata: Optional metadata dict
            embedding: Optional embedding vector

        Returns:
            Message instance with specified or default values
        """
        if isinstance(role, str):
            role = MessageRole(role)

        cls._message_counter += 1

        if content is None:
            if role == MessageRole.USER:
                content = f"Test user message #{cls._message_counter}"
            elif role == MessageRole.ASSISTANT:
                content = f"Test assistant response #{cls._message_counter}"
            elif role == MessageRole.SYSTEM:
                content = "You are a helpful assistant."
            else:
                content = f"Test message #{cls._message_counter}"

        return Message(
            id=id or uuid4(),
            role=role,
            content=content,
            conversation_id=conversation_id or uuid4(),
            created_at=created_at or datetime.now(timezone.utc),
            metadata=metadata or {},
            embedding=embedding,
        )

    @classmethod
    def conversation(
        cls,
        *,
        id: UUID | None = None,
        session_id: str | None = None,
        title: str | None = None,
        message_count: int = 0,
        messages: list[Message] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        alternating_roles: bool = True,
    ) -> Conversation:
        """Create a test Conversation instance.

        Args:
            id: Conversation UUID (auto-generated if not provided)
            session_id: Session identifier (auto-generated if not provided)
            title: Conversation title
            message_count: Number of messages to generate (if messages not provided)
            messages: Pre-built list of messages
            created_at: Creation timestamp
            updated_at: Last update timestamp
            alternating_roles: If True, alternate user/assistant roles

        Returns:
            Conversation instance with messages
        """
        conv_id = id or uuid4()
        base_time = created_at or datetime.now(timezone.utc)

        if messages is None and message_count > 0:
            messages = []
            for i in range(message_count):
                if alternating_roles:
                    role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
                else:
                    role = MessageRole.USER

                msg = cls.message(
                    role=role,
                    conversation_id=conv_id,
                    created_at=base_time + timedelta(seconds=i * 10),
                )
                messages.append(msg)
        elif messages is None:
            messages = []

        return Conversation(
            id=conv_id,
            session_id=session_id or f"test-session-{uuid4().hex[:8]}",
            title=title,
            messages=messages,
            created_at=base_time,
            updated_at=updated_at or (base_time if not messages else messages[-1].created_at),
        )

    @classmethod
    def session_info(
        cls,
        *,
        session_id: str | None = None,
        title: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        message_count: int = 5,
        first_message_preview: str | None = None,
        last_message_preview: str | None = None,
    ) -> SessionInfo:
        """Create a test SessionInfo instance."""
        return SessionInfo(
            session_id=session_id or f"test-session-{uuid4().hex[:8]}",
            title=title,
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at,
            message_count=message_count,
            first_message_preview=first_message_preview or "First test message...",
            last_message_preview=last_message_preview or "Last test message...",
        )

    @classmethod
    def entity(
        cls,
        *,
        id: UUID | None = None,
        name: str | None = None,
        entity_type: EntityType | str = EntityType.PERSON,
        description: str | None = None,
        canonical_name: str | None = None,
        confidence: float = 1.0,
        created_at: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> Entity:
        """Create a test Entity instance.

        Args:
            id: Entity UUID
            name: Entity name (auto-generated if not provided)
            entity_type: Entity type (person, organization, location, etc.)
            description: Optional description
            canonical_name: Canonical/normalized name
            confidence: Extraction confidence (0.0-1.0)
            created_at: Creation timestamp
            embedding: Optional embedding vector

        Returns:
            Entity instance
        """
        if isinstance(entity_type, str):
            entity_type = EntityType(entity_type.lower())

        cls._entity_counter += 1

        if name is None:
            type_names = {
                EntityType.PERSON: f"Test Person {cls._entity_counter}",
                EntityType.ORGANIZATION: f"Test Org {cls._entity_counter}",
                EntityType.LOCATION: f"Test Location {cls._entity_counter}",
                EntityType.EVENT: f"Test Event {cls._entity_counter}",
                EntityType.OBJECT: f"Test Object {cls._entity_counter}",
            }
            name = type_names.get(entity_type, f"Test Entity {cls._entity_counter}")

        return Entity(
            id=id or uuid4(),
            name=name,
            type=entity_type,
            description=description,
            canonical_name=canonical_name or name,
            confidence=confidence,
            created_at=created_at or datetime.now(timezone.utc),
            embedding=embedding,
        )

    @classmethod
    def preference(
        cls,
        *,
        id: UUID | None = None,
        category: str = "general",
        preference: str | None = None,
        context: str | None = None,
        confidence: float = 1.0,
        created_at: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> Preference:
        """Create a test Preference instance."""
        return Preference(
            id=id or uuid4(),
            category=category,
            preference=preference or f"Test preference for {category}",
            context=context,
            confidence=confidence,
            created_at=created_at or datetime.now(timezone.utc),
            embedding=embedding,
        )

    @classmethod
    def fact(
        cls,
        *,
        id: UUID | None = None,
        subject: str = "Test Subject",
        predicate: str = "relates to",
        object_: str = "Test Object",
        confidence: float = 1.0,
        created_at: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> Fact:
        """Create a test Fact instance."""
        return Fact(
            id=id or uuid4(),
            subject=subject,
            predicate=predicate,
            object=object_,
            confidence=confidence,
            created_at=created_at or datetime.now(timezone.utc),
            embedding=embedding,
        )

    @classmethod
    def reasoning_trace(
        cls,
        *,
        id: UUID | None = None,
        session_id: str | None = None,
        task: str | None = None,
        step_count: int = 0,
        include_tool_calls: bool = False,
        outcome: str | None = None,
        success: bool | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> ReasoningTrace:
        """Create a test ReasoningTrace instance.

        Args:
            id: Trace UUID
            session_id: Session identifier
            task: Task description
            step_count: Number of reasoning steps to generate
            include_tool_calls: Whether to add tool calls to steps
            outcome: Final outcome description
            success: Whether task succeeded
            started_at: Start timestamp
            completed_at: Completion timestamp

        Returns:
            ReasoningTrace with optional steps
        """
        trace_id = id or uuid4()
        base_time = started_at or datetime.now(timezone.utc)

        steps = []
        for i in range(step_count):
            step = cls.reasoning_step(
                trace_id=trace_id,
                step_number=i + 1,
                include_tool_call=include_tool_calls,
                created_at=base_time + timedelta(seconds=i * 5),
            )
            steps.append(step)

        return ReasoningTrace(
            id=trace_id,
            session_id=session_id or f"test-session-{uuid4().hex[:8]}",
            task=task or "Test reasoning task",
            steps=steps,
            outcome=outcome,
            success=success,
            started_at=base_time,
            completed_at=completed_at,
        )

    @classmethod
    def reasoning_step(
        cls,
        *,
        id: UUID | None = None,
        trace_id: UUID | None = None,
        step_number: int = 1,
        thought: str | None = None,
        action: str | None = None,
        observation: str | None = None,
        include_tool_call: bool = False,
        created_at: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> ReasoningStep:
        """Create a test ReasoningStep instance."""
        step_id = id or uuid4()

        tool_calls = []
        if include_tool_call:
            tool_calls.append(
                cls.tool_call(
                    step_id=step_id,
                    tool_name=f"test_tool_{step_number}",
                )
            )

        return ReasoningStep(
            id=step_id,
            trace_id=trace_id or uuid4(),
            step_number=step_number,
            thought=thought or f"Thinking about step {step_number}",
            action=action or f"Taking action {step_number}",
            observation=observation or f"Observed result {step_number}",
            tool_calls=tool_calls,
            created_at=created_at or datetime.now(timezone.utc),
            embedding=embedding,
        )

    @classmethod
    def tool_call(
        cls,
        *,
        id: UUID | None = None,
        step_id: UUID | None = None,
        tool_name: str = "test_tool",
        arguments: dict[str, Any] | None = None,
        result: Any | None = None,
        status: ToolCallStatus = ToolCallStatus.SUCCESS,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> ToolCall:
        """Create a test ToolCall instance."""
        return ToolCall(
            id=id or uuid4(),
            step_id=step_id or uuid4(),
            tool_name=tool_name,
            arguments=arguments or {"query": "test"},
            result=result or {"data": "test result"},
            status=status,
            duration_ms=duration_ms or 100,
            error=error,
        )

    @classmethod
    def embedding(cls, dimensions: int = 1536) -> list[float]:
        """Generate a random-like embedding vector for testing.

        Args:
            dimensions: Number of dimensions (default 1536 for OpenAI)

        Returns:
            List of floats representing an embedding
        """
        import hashlib

        # Use hash for reproducible "random" values
        seed = str(cls._message_counter).encode()
        hash_bytes = hashlib.sha256(seed).digest()

        # Generate normalized values
        values = []
        for i in range(dimensions):
            byte_idx = i % 32
            val = (hash_bytes[byte_idx] / 255.0) * 2 - 1  # Range [-1, 1]
            values.append(val)

        # Normalize to unit length
        magnitude = sum(v * v for v in values) ** 0.5
        return [v / magnitude for v in values]

    @classmethod
    def reset_counters(cls) -> None:
        """Reset internal counters for clean test runs."""
        cls._message_counter = 0
        cls._entity_counter = 0
