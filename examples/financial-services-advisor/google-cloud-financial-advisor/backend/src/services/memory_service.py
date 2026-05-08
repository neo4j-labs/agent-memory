"""Memory service using Neo4jMemoryService for Google ADK agents.

This module provides a wrapper around Neo4j Agent Memory's MemoryClient
and Neo4jMemoryService, configured with Vertex AI embeddings for use
with Google ADK agents.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory import ExtractionConfig, MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    Neo4jConfig,
)
from neo4j_agent_memory.memory.long_term import DeduplicationConfig
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

from ..config import get_settings

if TYPE_CHECKING:
    from neo4j_agent_memory.integrations.google_adk.types import MemoryEntry

logger = logging.getLogger(__name__)


class FinancialMemoryService:
    """Manages Neo4j Agent Memory for the financial advisor application.

    This service provides:
    - Neo4j-backed memory storage with Vertex AI embeddings
    - ADK-compatible MemoryService interface
    - Entity extraction from conversations
    - Semantic search across memory types

    Example:
        memory_service = FinancialMemoryService()
        await memory_service.initialize()

        # Use with ADK agents
        adk_memory = memory_service.adk_memory_service

        # Search memories
        results = await memory_service.search_context("money laundering patterns")
    """

    def __init__(self, user_id: str | None = None):
        """Initialize the memory service.

        Args:
            user_id: Optional user identifier for personalization.
        """
        settings = get_settings()

        # Configure memory settings with Vertex AI embeddings
        self._memory_settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=settings.neo4j.uri,
                username=settings.neo4j.user,
                password=settings.neo4j.password,
                database=settings.neo4j.database,
            ),
            embedding=EmbeddingConfig(
                provider=EmbeddingProvider.VERTEX_AI,
                model=settings.vertex_ai.embedding_model,
                project_id=settings.vertex_ai.get_project_id(),
                location=settings.vertex_ai.location,
            ),
            extraction=ExtractionConfig(),
        )
        # DeduplicationConfig is available for preventing duplicate customer entities.
        # Configure via LongTermMemory(deduplication=DeduplicationConfig(...))
        # to auto-merge or flag similar entities during add_entity() calls.

        self._client: MemoryClient | None = None
        self._memory_service: Neo4jMemoryService | None = None
        self._user_id = user_id
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the memory client and ADK memory service.

        Must be called before using the service. Thread-safe via asyncio.Lock.
        """
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            self._client = MemoryClient(self._memory_settings)
            await self._client.connect()

            self._memory_service = Neo4jMemoryService(
                memory_client=self._client,
                user_id=self._user_id,
                include_entities=True,
                include_preferences=True,
                extract_on_store=True,
            )

            self._initialized = True
            logger.info("Financial Memory Service initialized")

    async def close(self) -> None:
        """Close all connections."""
        if self._client:
            await self._client.close()
        self._initialized = False
        logger.info("Financial Memory Service closed")

    @property
    def client(self) -> MemoryClient:
        """Get the underlying memory client.

        Raises:
            RuntimeError: If service not initialized.
        """
        if not self._client:
            raise RuntimeError("Memory service not initialized. Call initialize() first.")
        return self._client

    @property
    def adk_memory_service(self) -> Neo4jMemoryService:
        """Get the ADK-compatible memory service.

        Raises:
            RuntimeError: If service not initialized.
        """
        if not self._memory_service:
            raise RuntimeError("Memory service not initialized. Call initialize() first.")
        return self._memory_service

    async def search_context(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Search the context graph for relevant information.

        Searches across messages, entities, and preferences using
        semantic similarity.

        Args:
            query: The search query.
            limit: Maximum number of results.
            threshold: Minimum similarity threshold.

        Returns:
            List of matching memory entries with content and type.
        """
        results = await self.adk_memory_service.search_memory(
            query=query,
            limit=limit,
            threshold=threshold,
        )
        return [
            {
                "content": r.content,
                "type": r.memory_type,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ]

    async def store_finding(
        self,
        content: str,
        session_id: str = "default",
        category: str = "investigation",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store an investigation finding in memory.

        Args:
            content: The finding content.
            session_id: Session identifier.
            category: Category of the finding.
            metadata: Additional metadata.

        Returns:
            Confirmation message.
        """
        combined_metadata = {"category": category, **(metadata or {})}

        entry = await self.adk_memory_service.add_memory(
            content=content,
            memory_type="message",
            session_id=session_id,
            metadata=combined_metadata,
        )

        if entry:
            return f"Stored finding: {entry.id}"
        return "Failed to store finding"

    async def get_conversation_history(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Get conversation history for a session.

        Args:
            session_id: Session identifier.
            limit: Maximum number of messages.

        Returns:
            List of memory entries for the session.
        """
        return await self.adk_memory_service.get_memories_for_session(
            session_id=session_id,
            limit=limit,
        )

    async def add_session(
        self,
        session_id: str,
        messages: list[dict[str, str]],
    ) -> None:
        """Store a conversation session.

        Stores messages to Neo4j and triggers entity extraction
        (via extract_on_store=True) to build the knowledge graph.

        Args:
            session_id: Session identifier.
            messages: List of messages with 'role' and 'content'.
        """
        session = {
            "id": session_id,
            "messages": messages,
        }
        logger.info(
            "Storing session %s (%d messages, extract_on_store=True)",
            session_id,
            len(messages),
        )
        await self.adk_memory_service.add_session_to_memory(session)
        logger.info("Session %s stored with entity extraction triggered", session_id)

    async def clear_session(self, session_id: str) -> None:
        """Clear all memories for a session.

        Args:
            session_id: Session identifier to clear.
        """
        await self.adk_memory_service.clear_session(session_id)


async def get_initialized_memory_service() -> FinancialMemoryService:
    """Get the initialized memory service (FastAPI dependency).

    Returns:
        Initialized FinancialMemoryService instance.
    """
    service = get_memory_service()
    if not service._initialized:
        await service.initialize()
    return service


@lru_cache
def get_memory_service() -> FinancialMemoryService:
    """Get the singleton memory service instance.

    Returns:
        FinancialMemoryService singleton (cached by @lru_cache).

    Note:
        The service must be initialized by calling `initialize()` before use.
        This is typically done in the FastAPI lifespan handler.
    """
    return FinancialMemoryService()
