"""Microsoft Agent Framework unified memory interface.

Provides a convenience class that combines ContextProvider and HistoryProvider
for easy integration with Microsoft Agent Framework agents.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..base import validate_session_id

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

    from .chat_store import Neo4jChatMessageStore
    from .context_provider import Neo4jContextProvider
    from .gds import GDSConfig, GDSIntegration

logger = logging.getLogger(__name__)

try:
    from agent_framework import Message

    from .chat_store import Neo4jChatMessageStore
    from .context_provider import Neo4jContextProvider
    from .gds import GDSConfig, GDSIntegration

    class Neo4jMicrosoftMemory:
        """
        Unified memory interface for Microsoft Agent Framework.

        Combines ContextProvider and HistoryProvider functionality into
        a single convenient interface. Provides direct access to all three
        memory types (short-term, long-term, reasoning) and GDS algorithms.

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory
            from agent_framework.azure import AzureOpenAIResponsesClient

            async with MemoryClient(settings) as client:
                memory = Neo4jMicrosoftMemory.from_memory_client(
                    memory_client=client,
                    session_id="user-123",
                )

                agent = chat_client.as_agent(
                    instructions="You are a helpful assistant.",
                    context_providers=[memory.context_provider],
                )

                # Direct memory operations
                context = await memory.get_context("user query")
                await memory.save_message("user", "Hello!")

                # GDS operations
                if memory.gds:
                    path = await memory.gds.find_shortest_path("Alice", "Bob")

        Attributes:
            session_id: The session identifier.
            user_id: Optional user identifier.
            context_provider: The underlying Neo4jContextProvider.
            chat_store: The underlying Neo4jChatMessageStore.
            gds: Optional GDS integration for graph algorithms.
        """

        def __init__(
            self,
            memory_client: MemoryClient,
            session_id: str,
            *,
            user_id: str | None = None,
            include_short_term: bool = True,
            include_long_term: bool = True,
            include_reasoning: bool = True,
            max_context_items: int = 10,
            max_recent_messages: int = 5,
            extract_entities: bool = True,
            extract_entities_async: bool = True,
            gds_config: GDSConfig | None = None,
            similarity_threshold: float = 0.7,
        ):
            """
            Initialize unified memory interface.

            Args:
                memory_client: Connected MemoryClient instance.
                session_id: Session identifier for memory operations.
                user_id: Optional user identifier for personalization.
                include_short_term: Include conversation in context.
                include_long_term: Include entities/preferences in context.
                include_reasoning: Include similar traces in context.
                max_context_items: Maximum items per memory type.
                max_recent_messages: Maximum recent messages in context.
                extract_entities: Whether to extract entities from messages.
                extract_entities_async: Use background entity extraction.
                gds_config: Configuration for GDS algorithms.
                similarity_threshold: Minimum similarity for retrieval.
            """
            self._client = memory_client
            self._session_id = validate_session_id(session_id)
            self._user_id = user_id

            # Create context provider
            self._context_provider = Neo4jContextProvider(
                memory_client=memory_client,
                session_id=session_id,
                user_id=user_id,
                include_short_term=include_short_term,
                include_long_term=include_long_term,
                include_reasoning=include_reasoning,
                max_context_items=max_context_items,
                max_recent_messages=max_recent_messages,
                extract_entities=extract_entities,
                extract_entities_async=extract_entities_async,
                gds_config=gds_config,
                similarity_threshold=similarity_threshold,
            )

            # Create chat message store
            self._chat_store = Neo4jChatMessageStore(
                memory_client=memory_client,
                session_id=session_id,
                extract_entities=not extract_entities_async,  # Sync if not async
                generate_embeddings=True,
            )

            # Create GDS integration if configured
            self._gds: GDSIntegration | None = None
            if gds_config and gds_config.enabled:
                self._gds = GDSIntegration(memory_client, gds_config)

        @classmethod
        def from_memory_client(
            cls,
            memory_client: MemoryClient,
            session_id: str,
            **kwargs: Any,
        ) -> Neo4jMicrosoftMemory:
            """
            Factory method to create memory from a MemoryClient.

            Args:
                memory_client: Connected MemoryClient instance.
                session_id: Session identifier.
                **kwargs: Additional configuration options.

            Returns:
                Configured Neo4jMicrosoftMemory instance.
            """
            return cls(memory_client=memory_client, session_id=session_id, **kwargs)

        @property
        def session_id(self) -> str:
            """Get the session ID."""
            return self._session_id

        @property
        def user_id(self) -> str | None:
            """Get the user ID."""
            return self._user_id

        @property
        def memory_client(self) -> MemoryClient:
            """Get the underlying memory client."""
            return self._client

        @property
        def context_provider(self) -> Neo4jContextProvider:
            """Get the context provider for use with Agent."""
            return self._context_provider

        @property
        def chat_store(self) -> Neo4jChatMessageStore:
            """Get the chat message store."""
            return self._chat_store

        @property
        def gds(self) -> GDSIntegration | None:
            """Get the GDS integration (if enabled)."""
            return self._gds

        # --- Convenience methods delegating to underlying components ---

        async def get_context(
            self,
            query: str,
            include_short_term: bool | None = None,
            include_long_term: bool | None = None,
            include_reasoning: bool | None = None,
            max_items: int | None = None,
        ) -> str:
            """
            Get combined context from all memory types.

            Args:
                query: Query to find relevant context for.
                include_short_term: Override short-term inclusion.
                include_long_term: Override long-term inclusion.
                include_reasoning: Override reasoning inclusion.
                max_items: Override max items per type.

            Returns:
                Formatted context string for system prompts.
            """
            return await self._client.get_context(
                query=query,
                session_id=self._session_id,
                include_short_term=include_short_term
                if include_short_term is not None
                else self._context_provider._include_short_term,
                include_long_term=include_long_term
                if include_long_term is not None
                else self._context_provider._include_long_term,
                include_reasoning=include_reasoning
                if include_reasoning is not None
                else self._context_provider._include_reasoning,
                max_items=max_items or self._context_provider._max_context_items,
            )

        async def save_message(
            self,
            role: str,
            content: str,
            **kwargs: Any,
        ) -> Any:
            """
            Save a conversation message.

            Args:
                role: Message role (user, assistant, system, tool).
                content: Message content.
                **kwargs: Additional message metadata.

            Returns:
                The saved message object.
            """
            return await self._client.short_term.add_message(
                session_id=self._session_id,
                role=role,
                content=content,
                **kwargs,
            )

        async def get_conversation(
            self,
            limit: int = 50,
        ) -> list[Message]:
            """
            Get conversation history as Message objects.

            Args:
                limit: Maximum messages to retrieve.

            Returns:
                List of Message objects.
            """
            return await self._chat_store.list_messages()

        async def search_memory(
            self,
            query: str,
            include_messages: bool = True,
            include_entities: bool = True,
            include_preferences: bool = True,
            limit: int = 10,
        ) -> dict[str, list[Any]]:
            """
            Search across all memory types.

            Args:
                query: Search query.
                include_messages: Search conversation history.
                include_entities: Search entity knowledge.
                include_preferences: Search user preferences.
                limit: Maximum results per type.

            Returns:
                Dict with results by memory type.
            """
            results: dict[str, list[Any]] = {}

            if include_messages:
                messages = await self._client.short_term.search_messages(
                    query=query,
                    session_id=self._session_id,
                    limit=limit,
                )
                results["messages"] = [
                    {
                        "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                        "content": m.content,
                        "id": str(m.id),
                    }
                    for m in messages
                ]

            if include_entities:
                entities = await self._client.long_term.search_entities(
                    query=query,
                    limit=limit,
                )
                results["entities"] = [
                    {
                        "name": e.display_name,
                        "type": e.type.value if hasattr(e.type, "value") else str(e.type),
                        "description": e.description,
                        "id": str(e.id),
                    }
                    for e in entities
                ]

            if include_preferences:
                prefs = await self._client.long_term.search_preferences(
                    query=query,
                    limit=limit,
                )
                results["preferences"] = [
                    {
                        "category": p.category,
                        "preference": p.preference,
                        "context": p.context,
                        "id": str(p.id),
                    }
                    for p in prefs
                ]

            return results

        async def add_preference(
            self,
            category: str,
            preference: str,
            context: str | None = None,
        ) -> Any:
            """
            Add a user preference to long-term memory.

            Args:
                category: Preference category.
                preference: The preference text.
                context: Optional context for when it applies.

            Returns:
                The saved preference object.
            """
            return await self._client.long_term.add_preference(
                category=category,
                preference=preference,
                context=context,
            )

        async def add_fact(
            self,
            subject: str,
            predicate: str,
            obj: str,
        ) -> Any:
            """
            Add a fact to long-term memory.

            Args:
                subject: Subject of the fact.
                predicate: Relationship/attribute.
                obj: Object of the fact.

            Returns:
                The saved fact object.
            """
            return await self._client.long_term.add_fact(
                subject=subject,
                predicate=predicate,
                obj=obj,
            )

        async def get_similar_traces(
            self,
            task: str,
            limit: int = 5,
        ) -> list[Any]:
            """
            Find similar past reasoning traces.

            Args:
                task: Task description to find similar traces for.
                limit: Maximum traces to return.

            Returns:
                List of similar ReasoningTrace objects.
            """
            return await self._client.reasoning.get_similar_traces(
                task=task,
                limit=limit,
            )

        async def clear_session(self) -> None:
            """Clear all messages in the current session."""
            await self._client.short_term.clear_session(self._session_id)

        # --- GDS convenience methods ---

        async def find_entity_path(
            self,
            source: str,
            target: str,
            max_hops: int = 5,
        ) -> dict[str, Any] | None:
            """
            Find the shortest path between two entities.

            Args:
                source: Source entity name or ID.
                target: Target entity name or ID.
                max_hops: Maximum path length.

            Returns:
                Path information or None if no path exists.
            """
            if not self._gds:
                # Use basic Cypher if GDS not configured
                gds = GDSIntegration(self._client, GDSConfig(fallback_to_basic=True))
                return await gds.find_shortest_path(source, target, max_hops)
            return await self._gds.find_shortest_path(source, target, max_hops)

        async def find_similar_entities(
            self,
            entity: str,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            """
            Find entities similar to the given entity.

            Args:
                entity: Entity name or ID.
                limit: Maximum results.

            Returns:
                List of similar entities with scores.
            """
            if not self._gds:
                gds = GDSIntegration(self._client, GDSConfig(fallback_to_basic=True))
                return await gds.find_similar_entities(entity, limit)
            return await self._gds.find_similar_entities(entity, limit)

        async def get_important_entities(
            self,
            limit: int = 10,
        ) -> list[dict[str, Any]]:
            """
            Get the most important/central entities.

            Args:
                limit: Maximum results.

            Returns:
                List of important entities with scores.
            """
            if not self._gds:
                gds = GDSIntegration(self._client, GDSConfig(fallback_to_basic=True))
                return await gds.get_central_entities(limit=limit)
            return await self._gds.get_central_entities(limit=limit)


except ImportError:
    # Microsoft Agent Framework not installed
    pass
