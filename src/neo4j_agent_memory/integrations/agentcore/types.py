"""Type definitions for AgentCore Memory Provider integration.

These types map Neo4j Agent Memory concepts to the AgentCore MemoryProvider interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """Types of memory in the Context Graph."""

    MESSAGE = "message"
    ENTITY = "entity"
    PREFERENCE = "preference"
    FACT = "fact"
    TRACE = "trace"


@dataclass
class Memory:
    """A memory record compatible with AgentCore MemoryProvider interface.

    This class represents a single memory item that can be stored in or
    retrieved from the Context Graph, formatted for AgentCore compatibility.

    Attributes:
        id: Unique identifier for the memory.
        content: The main content of the memory.
        memory_type: Type of memory (message, entity, preference, etc.).
        session_id: Session this memory belongs to.
        user_id: User this memory belongs to.
        created_at: When the memory was created.
        updated_at: When the memory was last updated.
        metadata: Additional metadata for the memory.
        score: Relevance score (when retrieved via search).
        embedding: Optional embedding vector.
    """

    id: str
    content: str
    memory_type: MemoryType = MemoryType.MESSAGE
    session_id: str | None = None
    user_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert memory to dictionary representation.

        Returns:
            Dictionary with memory fields.
        """
        result: dict[str, Any] = {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "created_at": self.created_at.isoformat(),
        }

        if self.session_id:
            result["session_id"] = self.session_id
        if self.user_id:
            result["user_id"] = self.user_id
        if self.updated_at:
            result["updated_at"] = self.updated_at.isoformat()
        if self.metadata:
            result["metadata"] = self.metadata
        if self.score is not None:
            result["score"] = self.score

        return result

    @classmethod
    def from_message(
        cls,
        message: Any,
        session_id: str | None = None,
    ) -> Memory:
        """Create a Memory from a ShortTermMemory message.

        Args:
            message: A Message object from ShortTermMemory.
            session_id: Session ID to associate.

        Returns:
            Memory instance.
        """
        return cls(
            id=str(message.id),
            content=message.content,
            memory_type=MemoryType.MESSAGE,
            session_id=session_id,
            created_at=message.created_at
            if hasattr(message, "created_at")
            else datetime.now(timezone.utc),
            metadata={
                "role": message.role.value if hasattr(message.role, "value") else str(message.role),
                **(message.metadata or {}),
            },
        )

    @classmethod
    def from_entity(cls, entity: Any) -> Memory:
        """Create a Memory from a LongTermMemory entity.

        Args:
            entity: An Entity object from LongTermMemory.

        Returns:
            Memory instance.
        """
        entity_type = entity.type.value if hasattr(entity.type, "value") else str(entity.type)

        return cls(
            id=str(entity.id),
            content=f"{entity.display_name}: {entity.description or ''}".strip(": "),
            memory_type=MemoryType.ENTITY,
            metadata={
                "entity_type": entity_type,
                "display_name": entity.display_name,
                "description": entity.description,
                "aliases": getattr(entity, "aliases", []),
            },
        )

    @classmethod
    def from_preference(cls, preference: Any) -> Memory:
        """Create a Memory from a LongTermMemory preference.

        Args:
            preference: A Preference object from LongTermMemory.

        Returns:
            Memory instance.
        """
        return cls(
            id=str(preference.id),
            content=preference.preference,
            memory_type=MemoryType.PREFERENCE,
            metadata={
                "category": preference.category,
                "context": preference.context,
                "confidence": preference.confidence,
            },
        )

    @classmethod
    def from_trace(cls, trace: Any) -> Memory:
        """Create a Memory from a ReasoningMemory trace.

        Args:
            trace: A ReasoningTrace object from ReasoningMemory.

        Returns:
            Memory instance.
        """
        return cls(
            id=str(trace.id),
            content=f"Task: {trace.task}\nOutcome: {trace.outcome or 'In progress'}",
            memory_type=MemoryType.TRACE,
            session_id=trace.session_id,
            created_at=trace.started_at
            if hasattr(trace, "started_at")
            else datetime.now(timezone.utc),
            metadata={
                "task": trace.task,
                "outcome": trace.outcome,
                "success": trace.success,
                "step_count": len(trace.steps) if hasattr(trace, "steps") else 0,
            },
        )


@dataclass
class MemorySearchResult:
    """Result from a memory search operation.

    Attributes:
        memories: List of matching memories.
        total_count: Total number of matches (may be more than returned).
        query: The original search query.
        filters_applied: Any filters that were applied.
    """

    memories: list[Memory]
    total_count: int
    query: str
    filters_applied: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "memories": [m.to_dict() for m in self.memories],
            "total_count": self.total_count,
            "query": self.query,
            "filters_applied": self.filters_applied,
        }


@dataclass
class SessionContext:
    """Context for a memory session.

    Represents the session state for AgentCore Memory operations.

    Attributes:
        session_id: Unique session identifier.
        user_id: User this session belongs to.
        namespace: Optional namespace for multi-tenant isolation.
        metadata: Additional session metadata.
        created_at: When the session was created.
    """

    session_id: str
    user_id: str | None = None
    namespace: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
