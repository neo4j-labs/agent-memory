"""Core abstractions and base classes for neo4j-agent-memory."""

from neo4j_agent_memory.core.exceptions import (
    ConnectionError,
    EmbeddingError,
    ExtractionError,
    MemoryError,
    NotFoundError,
    ResolutionError,
    SchemaError,
)
from neo4j_agent_memory.core.memory import (
    BaseMemory,
    MemoryEntry,
    MemoryStore,
)

__all__ = [
    # Exceptions
    "MemoryError",
    "NotFoundError",
    "ConnectionError",
    "SchemaError",
    "ExtractionError",
    "ResolutionError",
    "EmbeddingError",
    # Base classes
    "MemoryEntry",
    "MemoryStore",
    "BaseMemory",
]
