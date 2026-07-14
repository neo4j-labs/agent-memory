"""AgentCore MemoryProvider implementation backed by Neo4j Context Graphs.

This module provides a MemoryProvider implementation that can be used with
AWS Bedrock AgentCore Runtime to provide memory capabilities backed by
Neo4j Agent Memory's Context Graphs.

Example:
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.integrations.agentcore import Neo4jMemoryProvider

    async with MemoryClient(settings) as client:
        provider = Neo4jMemoryProvider(
            memory_client=client,
            namespace="my-app",
        )

        # Store a memory
        memory = await provider.store_memory(
            session_id="session-123",
            content="The user prefers dark mode",
            metadata={"type": "preference"},
        )

        # Search memories
        results = await provider.search_memory(
            query="dark mode preference",
            session_id="session-123",
        )
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations.agentcore.types import (
    Memory,
    MemorySearchResult,
    MemoryType,
    SessionContext,
)

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.graph.client import Neo4jClient

logger = logging.getLogger(__name__)


class Neo4jMemoryProvider:
    """AgentCore MemoryProvider backed by Neo4j Context Graphs.

    This class implements a memory provider interface compatible with AWS
    Bedrock AgentCore, using Neo4j Agent Memory's Context Graphs as the
    backing store.

    The provider supports:
    - Storing memories with automatic entity extraction
    - Searching memories using hybrid vector + graph search
    - Managing session-scoped memories
    - Cross-session entity and preference retrieval

    Attributes:
        memory_client: The MemoryClient instance for Neo4j operations.
        namespace: Namespace for multi-tenant isolation.
        extract_entities: Whether to extract entities when storing memories.
        extraction_model: Model to use for entity extraction.

    Example:
        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.integrations.agentcore import Neo4jMemoryProvider

        settings = MemorySettings(
            neo4j=Neo4jConfig(uri="neo4j+s://...", password="..."),
            embedding=EmbeddingConfig(provider="bedrock"),
        )

        async with MemoryClient(settings) as client:
            provider = Neo4jMemoryProvider(
                memory_client=client,
                namespace="my-app",
                extract_entities=True,
            )

            # Use with AgentCore Runtime
            runtime = AgentCoreRuntime(memory_provider=provider)
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        *,
        namespace: str = "default",
        extract_entities: bool = True,
        extraction_model: str | None = None,
        generate_embeddings: bool = True,
    ) -> None:
        """Initialize the Neo4j Memory Provider.

        Args:
            memory_client: A connected MemoryClient instance.
            namespace: Namespace for multi-tenant isolation.
            extract_entities: Whether to extract entities from stored memories.
            extraction_model: Model to use for entity extraction (uses client default if None).
            generate_embeddings: Whether to generate embeddings for stored memories.
        """
        self._client = memory_client
        self._namespace = namespace
        self._extract_entities = extract_entities
        self._extraction_model = extraction_model
        self._generate_embeddings = generate_embeddings
        self._sessions: dict[str, SessionContext] = {}

    def _neo4j(self) -> Neo4jClient:
        """Return the underlying connected Neo4jClient, narrowing the None state."""
        neo4j_client = self._client._client
        if neo4j_client is None:
            raise RuntimeError("MemoryClient is not connected; call connect() first.")
        return neo4j_client

    @property
    def namespace(self) -> str:
        """Get the current namespace."""
        return self._namespace

    async def store_memory(
        self,
        session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        memory_type: str = "message",
        user_id: str | None = None,
        role: str = "user",
    ) -> Memory:
        """Store content and extract entities to Context Graph.

        This method stores the provided content as a memory in the Context
        Graph. If entity extraction is enabled, entities and relationships
        are automatically extracted and stored.

        Args:
            session_id: Session ID for the memory.
            content: The content to store.
            metadata: Optional metadata to attach.
            memory_type: Type of memory ("message", "preference", "fact").
            user_id: Optional user ID for the memory.
            role: Role for message type memories ("user", "assistant", "system").

        Returns:
            The stored Memory object.

        Example:
            memory = await provider.store_memory(
                session_id="session-123",
                content="I prefer Italian food, especially pasta.",
                metadata={"source": "chat"},
            )
        """
        # Ensure session exists
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionContext(
                session_id=session_id,
                user_id=user_id,
                namespace=self._namespace,
            )

        effective_metadata = {
            "namespace": self._namespace,
            **(metadata or {}),
        }

        if memory_type == "message":
            # Store as a message in short-term memory
            message = await self._client.short_term.add_message(
                session_id=session_id,
                role=role,
                content=content,
                metadata=effective_metadata,
                extract_entities=self._extract_entities,
                generate_embedding=self._generate_embeddings,
            )

            return Memory.from_message(message, session_id=session_id)

        elif memory_type == "preference":
            # Store as a preference in long-term memory
            category = (metadata or {}).get("category", "general")
            context = (metadata or {}).get("context")

            preference = await self._client.long_term.add_preference(
                category=category,
                preference=content,
                context=context,
                generate_embedding=self._generate_embeddings,
            )

            return Memory.from_preference(preference)

        elif memory_type == "fact":
            # Store as a fact/triple in long-term memory
            subject = (metadata or {}).get("subject", "Unknown")
            predicate = (metadata or {}).get("predicate", "relates_to")
            obj = (metadata or {}).get("object", content)

            fact = await self._client.long_term.add_fact(
                subject=subject,
                predicate=predicate,
                obj=obj,
            )

            # Create a memory representation for the fact
            return Memory(
                id=str(fact.id) if hasattr(fact, "id") else str(uuid.uuid4()),
                content=f"{subject} -> {predicate} -> {obj}",
                memory_type=MemoryType.FACT,
                session_id=session_id,
                metadata={
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    **effective_metadata,
                },
            )

        else:
            raise ValueError(f"Unknown memory_type: {memory_type}")

    async def search_memory(
        self,
        query: str,
        session_id: str | None = None,
        user_id: str | None = None,
        top_k: int = 10,
        *,
        memory_types: list[str] | None = None,
        threshold: float = 0.5,
        include_entities: bool = True,
        include_preferences: bool = True,
    ) -> MemorySearchResult:
        """Search Context Graph for relevant memories.

        Performs hybrid vector + graph search across the Context Graph to
        find memories relevant to the query.

        Args:
            query: The search query.
            session_id: Optional session ID to scope the search.
            user_id: Optional user ID to scope the search.
            top_k: Maximum number of results to return.
            memory_types: Types of memory to search (defaults to all).
            threshold: Minimum similarity threshold.
            include_entities: Whether to include entity matches.
            include_preferences: Whether to include preference matches.

        Returns:
            MemorySearchResult with matching memories.

        Example:
            results = await provider.search_memory(
                query="Italian restaurants",
                session_id="session-123",
                top_k=5,
            )
            for memory in results.memories:
                print(f"{memory.memory_type}: {memory.content}")
        """
        memories: list[Memory] = []
        search_types = memory_types or ["message", "entity", "preference"]
        filters: dict[str, Any] = {"threshold": threshold}

        if session_id:
            filters["session_id"] = session_id

        # Search messages
        if "message" in search_types:
            try:
                messages = await self._client.short_term.search_messages(
                    query=query,
                    session_id=session_id,
                    limit=top_k,
                    threshold=threshold,
                )
                for msg in messages:
                    memory = Memory.from_message(msg, session_id=session_id)
                    if msg.metadata and "similarity" in msg.metadata:
                        memory.score = msg.metadata["similarity"]
                    memories.append(memory)
            except Exception as e:
                logger.debug(f"Message search failed: {e}")

        # Search entities
        if include_entities and "entity" in search_types:
            try:
                entities = await self._client.long_term.search_entities(
                    query=query,
                    limit=top_k,
                )
                for entity in entities:
                    memories.append(Memory.from_entity(entity))
            except Exception as e:
                logger.debug(f"Entity search failed: {e}")

        # Search preferences
        if include_preferences and "preference" in search_types:
            try:
                preferences = await self._client.long_term.search_preferences(
                    query=query,
                    limit=top_k,
                )
                for pref in preferences:
                    memories.append(Memory.from_preference(pref))
            except Exception as e:
                logger.debug(f"Preference search failed: {e}")

        # Sort by score (if available) and limit
        memories_with_scores = [(m, m.score or 0.0) for m in memories]
        memories_with_scores.sort(key=lambda x: x[1], reverse=True)
        memories = [m for m, _ in memories_with_scores[:top_k]]

        return MemorySearchResult(
            memories=memories,
            total_count=len(memories),
            query=query,
            filters_applied=filters,
        )

    async def get_session_memories(
        self,
        session_id: str,
        *,
        limit: int = 50,
        include_entities: bool = False,
    ) -> list[Memory]:
        """Get all memories for a session.

        Retrieves the conversation history and optionally related entities
        for a specific session.

        Args:
            session_id: The session ID to retrieve memories for.
            limit: Maximum number of messages to retrieve.
            include_entities: Whether to include related entities.

        Returns:
            List of Memory objects for the session.

        Example:
            memories = await provider.get_session_memories("session-123")
            for memory in memories:
                print(f"[{memory.metadata.get('role')}] {memory.content}")
        """
        memories: list[Memory] = []

        try:
            conversation = await self._client.short_term.get_conversation(
                session_id=session_id,
                limit=limit,
            )

            for msg in conversation.messages:
                memories.append(Memory.from_message(msg, session_id=session_id))

        except Exception as e:
            logger.error(f"Failed to get session memories: {e}")

        return memories

    async def delete_memory(
        self,
        memory_id: str,
    ) -> bool:
        """Delete a specific memory.

        Args:
            memory_id: The ID of the memory to delete.

        Returns:
            True if the memory was deleted, False otherwise.

        Note:
            This method attempts to delete from all memory stores.
            It returns True if the memory was found and deleted from
            any store.
        """
        deleted = False

        # Try deleting from short-term memory
        try:
            # Use Cypher to delete the message
            query = """
            MATCH (m:Message {id: $memory_id})
            DETACH DELETE m
            RETURN count(m) AS deleted
            """
            result = await self._neo4j().execute_write(
                query,
                {"memory_id": memory_id},
            )
            if result and result[0].get("deleted", 0) > 0:
                deleted = True
        except Exception as e:
            logger.debug(f"Message deletion failed: {e}")

        # Try deleting from entity store
        if not deleted:
            try:
                query = """
                MATCH (e:Entity {id: $memory_id})
                DETACH DELETE e
                RETURN count(e) AS deleted
                """
                result = await self._neo4j().execute_write(
                    query,
                    {"memory_id": memory_id},
                )
                if result and result[0].get("deleted", 0) > 0:
                    deleted = True
            except Exception as e:
                logger.debug(f"Entity deletion failed: {e}")

        # Try deleting from preference store
        if not deleted:
            try:
                query = """
                MATCH (p:Preference {id: $memory_id})
                DETACH DELETE p
                RETURN count(p) AS deleted
                """
                result = await self._neo4j().execute_write(
                    query,
                    {"memory_id": memory_id},
                )
                if result and result[0].get("deleted", 0) > 0:
                    deleted = True
            except Exception as e:
                logger.debug(f"Preference deletion failed: {e}")

        return deleted

    async def clear_session(
        self,
        session_id: str,
    ) -> int:
        """Clear all memories for a session.

        Args:
            session_id: The session ID to clear.

        Returns:
            Number of memories deleted.
        """
        count = 0

        try:
            query = """
            MATCH (m:Message {sessionId: $session_id})
            DETACH DELETE m
            RETURN count(m) AS deleted
            """
            result = await self._neo4j().execute_write(
                query,
                {"session_id": session_id},
            )
            if result:
                count = result[0].get("deleted", 0)

            # Remove from local session cache
            self._sessions.pop(session_id, None)

        except Exception as e:
            logger.error(f"Failed to clear session: {e}")

        return count

    async def get_context(
        self,
        query: str,
        session_id: str | None = None,
        *,
        max_tokens: int = 2000,
    ) -> str:
        """Get formatted context for LLM prompts.

        This is a convenience method that searches for relevant context
        and formats it as a string suitable for including in LLM prompts.

        Args:
            query: The query to find relevant context for.
            session_id: Optional session to include recent history from.
            max_tokens: Approximate maximum tokens for the context.

        Returns:
            Formatted context string.
        """
        return await self._client.get_context(
            query=query,
            session_id=session_id,
        )

    def get_session_context(self, session_id: str) -> SessionContext | None:
        """Get the context for a session.

        Args:
            session_id: The session ID.

        Returns:
            SessionContext if the session exists, None otherwise.
        """
        return self._sessions.get(session_id)
