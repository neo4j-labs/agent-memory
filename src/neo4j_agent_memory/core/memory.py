"""Base memory classes and protocols."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.extraction.base import EntityExtractor
    from neo4j_agent_memory.graph.client import Neo4jClient


T = TypeVar("T", bound="MemoryEntry")


class MemoryEntry(BaseModel):
    """Base model for all memory entries."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class MemoryStore(Protocol[T]):
    """Protocol defining the interface for memory stores."""

    async def store(self, entry: T, session_id: str | None = None) -> T:
        """Store a memory entry."""
        ...

    async def retrieve(self, entry_id: UUID) -> T | None:
        """Retrieve a memory entry by ID."""
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        threshold: float = 0.7,
        filters: dict[str, Any] | None = None,
    ) -> list[T]:
        """Search for memory entries using semantic similarity."""
        ...

    async def delete(self, entry_id: UUID) -> bool:
        """Delete a memory entry."""
        ...

    async def list(
        self,
        *,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> AsyncIterator[T]:
        """List memory entries with optional filters."""
        ...


class BaseMemory(ABC, Generic[T]):
    """Abstract base class for memory implementations."""

    def __init__(
        self,
        client: "Neo4jClient",
        embedder: "Embedder | None" = None,
        extractor: "EntityExtractor | None" = None,
    ):
        """
        Initialize the memory.

        Args:
            client: Neo4j client for database operations
            embedder: Optional embedder for generating embeddings
            extractor: Optional entity extractor for content analysis
        """
        self._client = client
        self._embedder = embedder
        self._extractor = extractor

    @property
    def client(self) -> "Neo4jClient":
        """Get the Neo4j client."""
        return self._client

    @property
    def embedder(self) -> "Embedder | None":
        """Get the embedder."""
        return self._embedder

    @property
    def extractor(self) -> "EntityExtractor | None":
        """Get the entity extractor."""
        return self._extractor

    @abstractmethod
    async def add(self, content: str, **kwargs: Any) -> T:
        """
        Add content to memory with automatic extraction.

        Args:
            content: The content to add
            **kwargs: Additional arguments specific to the memory type

        Returns:
            The created memory entry
        """
        pass

    @abstractmethod
    async def search(self, query: str, **kwargs: Any) -> list[T]:
        """
        Search memory for relevant entries.

        Args:
            query: The search query
            **kwargs: Additional search parameters

        Returns:
            List of matching memory entries
        """
        pass

    @abstractmethod
    async def get_context(self, query: str, **kwargs: Any) -> str:
        """
        Get formatted context string for LLM prompts.

        Args:
            query: The query to find relevant context for
            **kwargs: Additional parameters

        Returns:
            Formatted context string
        """
        pass

    async def _generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding for text if embedder is available."""
        if self._embedder is None:
            return None
        return await self._embedder.embed(text)

    async def _generate_embeddings(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings for multiple texts if embedder is available."""
        if self._embedder is None:
            return None
        return await self._embedder.embed_batch(texts)
