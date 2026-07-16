"""Microsoft Agent Framework BaseContextProvider implementation.

Provides Neo4j-backed context injection and memory extraction for
Microsoft Agent Framework agents.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.memory.short_term import ShortTermMemory

from ..base import (
    format_context_section,
    truncate_text,
    validate_limit,
    validate_session_id,
)

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

    from .gds import GDSConfig

logger = logging.getLogger(__name__)

try:
    from agent_framework import (
        AgentSession,
        BaseContextProvider,
        Message,
        SessionContext,
        SupportsAgentRun,
    )

    class Neo4jContextProvider(BaseContextProvider):
        """
        Microsoft Agent Framework BaseContextProvider backed by Neo4j Agent Memory.

        Provides automatic context injection before agent invocation and memory
        extraction after agent responses. Supports all three memory types:
        short-term (conversation), long-term (entities/preferences), and
        reasoning (similar past traces).

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider
            from agent_framework.azure import AzureOpenAIResponsesClient

            async with MemoryClient(settings) as client:
                provider = Neo4jContextProvider(
                    memory_client=client,
                    session_id="user-123",
                    include_short_term=True,
                    include_long_term=True,
                    include_reasoning=True,
                )

                agent = chat_client.as_agent(
                    instructions="You are a helpful assistant.",
                    context_providers=[provider],
                )

                response = await agent.run("Hello!")

        Attributes:
            session_id: The session identifier for memory operations.
            user_id: Optional user identifier for personalization.
            include_short_term: Whether to include conversation history.
            include_long_term: Whether to include entities and preferences.
            include_reasoning: Whether to include similar past traces.
        """

        DEFAULT_CONTEXT_TEMPLATE = """You have access to the following memory context:

{context}

Use this information to provide personalized, contextually relevant responses."""

        def __init__(
            self,
            memory_client: MemoryClient,
            session_id: str,
            *,
            source_id: str = "neo4j-context",
            user_id: str | None = None,
            include_short_term: bool = True,
            include_long_term: bool = True,
            include_reasoning: bool = True,
            max_context_items: int = 10,
            max_recent_messages: int = 5,
            extract_entities: bool = True,
            extract_entities_async: bool = True,
            context_template: str | None = None,
            gds_config: GDSConfig | None = None,
            similarity_threshold: float = 0.7,
        ):
            """
            Initialize the Neo4j context provider.

            Args:
                memory_client: Connected MemoryClient instance.
                session_id: Session identifier for conversation tracking.
                source_id: Unique identifier for this provider in the pipeline.
                user_id: Optional user identifier for personalization.
                include_short_term: Include recent conversation in context.
                include_long_term: Include entities and preferences in context.
                include_reasoning: Include similar past reasoning traces.
                max_context_items: Maximum items per memory type in context.
                max_recent_messages: Maximum recent messages to include.
                extract_entities: Whether to extract entities from messages.
                extract_entities_async: Use background extraction (non-blocking).
                context_template: Custom template for formatting context.
                gds_config: Configuration for GDS algorithm integration.
                similarity_threshold: Minimum similarity score for retrieval.
            """
            super().__init__(source_id=source_id)
            self._client = memory_client
            self._session_id = validate_session_id(session_id)
            self._user_id = user_id
            self._include_short_term = include_short_term
            self._include_long_term = include_long_term
            self._include_reasoning = include_reasoning
            self._max_context_items = validate_limit(max_context_items, max_limit=100)
            self._max_recent_messages = validate_limit(max_recent_messages, max_limit=50)
            self._extract_entities = extract_entities
            self._extract_entities_async = extract_entities_async
            self._context_template = context_template or self.DEFAULT_CONTEXT_TEMPLATE
            self._gds_config = gds_config
            self._similarity_threshold = similarity_threshold

            # Extraction queue for background processing
            self._extraction_queue: list[tuple[str, str]] = []
            # Store background task references to prevent GC before completion
            self._background_tasks: set[asyncio.Task[None]] = set()

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

        async def before_run(
            self,
            *,
            agent: SupportsAgentRun,
            session: AgentSession,
            context: SessionContext,
            state: dict[str, Any],
        ) -> None:
            """
            Inject context from Neo4j before agent invocation.

            This method is called by the Microsoft Agent Framework just before
            the model is invoked. It retrieves relevant context from all
            configured memory types and adds it to the SessionContext.

            Args:
                agent: The agent running this invocation.
                session: The current session.
                context: The invocation context to mutate.
                state: The session's mutable state dict.
            """
            # Extract query from input messages
            query = self._extract_query_from_messages(context.input_messages)
            if not query:
                return

            # Gather context from all memory types
            context_parts: list[str] = []

            try:
                # Short-term memory (conversation history)
                if self._include_short_term:
                    short_term = await self._get_short_term_context(query)
                    if short_term:
                        context_parts.append(short_term)

                # Long-term memory (entities, preferences)
                if self._include_long_term:
                    long_term = await self._get_long_term_context(query)
                    if long_term:
                        context_parts.append(long_term)

                # Reasoning memory (similar past traces)
                if self._include_reasoning:
                    reasoning = await self._get_reasoning_context(query)
                    if reasoning:
                        context_parts.append(reasoning)

            except Exception as e:
                logger.warning(f"Error retrieving memory context: {e}")
                # Continue with whatever context we have

            if not context_parts:
                return

            # Format combined context and inject as instructions
            combined_context = "\n\n".join(context_parts)
            instructions = self._context_template.format(context=combined_context)
            context.extend_instructions(self.source_id, instructions)

        async def after_run(
            self,
            *,
            agent: SupportsAgentRun,
            session: AgentSession,
            context: SessionContext,
            state: dict[str, Any],
        ) -> None:
            """
            Extract and store memories after agent response.

            This method is called by the Microsoft Agent Framework after
            receiving a response from the model. It saves messages and
            optionally extracts entities for the knowledge graph.

            Args:
                agent: The agent running this invocation.
                session: The current session.
                context: The invocation context with response.
                state: The session's mutable state dict.
            """
            try:
                # Determine if we should extract entities synchronously
                sync_extract = self._extract_entities and not self._extract_entities_async

                # Save input messages
                for msg in context.input_messages:
                    role = msg.role
                    content = msg.text
                    if role and content:
                        await self._client.short_term.add_message(
                            session_id=self._session_id,
                            role=role,
                            content=content,
                            extract_entities=sync_extract,
                            generate_embedding=True,
                        )
                        if self._extract_entities and self._extract_entities_async:
                            self._extraction_queue.append((role, content))

                # Save response messages
                if context.response and context.response.messages:
                    for msg in context.response.messages:
                        role = msg.role
                        content = msg.text
                        if role and content:
                            await self._client.short_term.add_message(
                                session_id=self._session_id,
                                role=role,
                                content=content,
                                extract_entities=sync_extract,
                                generate_embedding=True,
                            )
                            if self._extract_entities and self._extract_entities_async:
                                self._extraction_queue.append((role, content))

                # Trigger background extraction if needed
                if self._extraction_queue and self._extract_entities_async:
                    await self._trigger_background_extraction()

            except Exception as e:
                logger.warning(f"Error saving messages to memory: {e}", exc_info=True)

        def serialize(self) -> str:
            """
            Serialize provider state for persistence.

            .. warning::
                Does NOT serialize the Neo4j connection. When deserializing,
                a fresh MemoryClient must be provided.

            Returns:
                JSON string containing serializable state.
            """
            # Flush extraction queue before serializing
            if self._extraction_queue:
                logger.debug(
                    f"Extraction queue has {len(self._extraction_queue)} items "
                    "that will be lost during serialization"
                )

            state = {
                "session_id": self._session_id,
                "source_id": self.source_id,
                "user_id": self._user_id,
                "include_short_term": self._include_short_term,
                "include_long_term": self._include_long_term,
                "include_reasoning": self._include_reasoning,
                "max_context_items": self._max_context_items,
                "max_recent_messages": self._max_recent_messages,
                "extract_entities": self._extract_entities,
                "extract_entities_async": self._extract_entities_async,
                "similarity_threshold": self._similarity_threshold,
                "gds_enabled": self._gds_config.enabled if self._gds_config else False,
            }
            return json.dumps(state)

        @classmethod
        def deserialize(
            cls,
            serialized_state: str,
            memory_client: MemoryClient,
            context_template: str | None = None,
            gds_config: GDSConfig | None = None,
        ) -> Neo4jContextProvider:
            """
            Deserialize provider from saved state.

            .. warning::
                Requires a fresh MemoryClient to be provided. Database
                connections cannot be serialized.

            Args:
                serialized_state: Previously serialized state string.
                memory_client: A connected MemoryClient instance.
                context_template: Optional custom context template.
                gds_config: Optional GDS configuration.

            Returns:
                Restored Neo4jContextProvider instance.
            """
            state = json.loads(serialized_state)

            return cls(
                memory_client=memory_client,
                session_id=state["session_id"],
                source_id=state.get("source_id", "neo4j-context"),
                user_id=state.get("user_id"),
                include_short_term=state.get("include_short_term", True),
                include_long_term=state.get("include_long_term", True),
                include_reasoning=state.get("include_reasoning", True),
                max_context_items=state.get("max_context_items", 10),
                max_recent_messages=state.get("max_recent_messages", 5),
                extract_entities=state.get("extract_entities", True),
                extract_entities_async=state.get("extract_entities_async", True),
                context_template=context_template,
                gds_config=gds_config,
                similarity_threshold=state.get("similarity_threshold", 0.7),
            )

        # --- Private helper methods ---

        def _extract_query_from_messages(self, messages: list[Message]) -> str | None:
            """Extract query string from the latest user message."""
            for msg in reversed(messages):
                if msg.role == "user" and msg.text:
                    return truncate_text(msg.text, max_length=500, suffix="")
            return None

        async def _get_short_term_context(self, query: str) -> str | None:
            """Get short-term memory context."""
            parts: list[str] = []

            # Recent conversation history
            try:
                conv = await self._client.short_term.get_conversation(
                    session_id=self._session_id,
                    limit=self._max_recent_messages,
                )
                if conv.messages:
                    recent_items = []
                    for msg in conv.messages[-self._max_recent_messages :]:
                        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                        content = truncate_text(msg.content, max_length=200)
                        recent_items.append(f"**{role}**: {content}")
                    if recent_items:
                        parts.append(format_context_section("Recent Conversation", recent_items))
            except Exception as e:
                logger.debug(f"Error getting conversation: {e}")

            # Semantically relevant past messages
            try:
                relevant = await self._client.short_term.search_messages(
                    query=query,
                    session_id=self._session_id,
                    limit=self._max_context_items // 2,
                    threshold=self._similarity_threshold,
                )
                if relevant:
                    relevant_items = []
                    for msg in relevant:
                        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                        content = truncate_text(msg.content, max_length=150)
                        score = msg.metadata.get("similarity", 0) if msg.metadata else 0
                        relevant_items.append(f"[{role}] {content} (relevance: {score:.2f})")
                    if relevant_items:
                        parts.append(
                            format_context_section("Relevant Past Messages", relevant_items)
                        )
            except Exception as e:
                logger.debug(f"Error searching messages: {e}")

            return "\n\n".join(parts) if parts else None

        async def _get_long_term_context(self, query: str) -> str | None:
            """Get long-term memory context (entities, preferences)."""
            parts: list[str] = []

            # User preferences
            try:
                preferences = await self._client.long_term.search_preferences(
                    query=query,
                    limit=self._max_context_items,
                )
                if preferences:
                    pref_items = []
                    for pref in preferences:
                        line = f"[{pref.category}] {pref.preference}"
                        if pref.context:
                            line += f" (when: {pref.context})"
                        pref_items.append(line)
                    if pref_items:
                        parts.append(format_context_section("User Preferences", pref_items))
            except Exception as e:
                logger.debug(f"Error searching preferences: {e}")

            # Relevant entities
            try:
                entities = await self._client.long_term.search_entities(
                    query=query,
                    limit=self._max_context_items,
                )
                if entities:
                    entity_items = []
                    for entity in entities:
                        # Get entity type
                        entity_type = (
                            entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                        )
                        line = f"**{entity.display_name}** ({entity_type})"
                        if entity.description:
                            line += f": {truncate_text(entity.description, 100)}"
                        entity_items.append(line)
                    if entity_items:
                        parts.append(format_context_section("Relevant Knowledge", entity_items))
            except Exception as e:
                logger.debug(f"Error searching entities: {e}")

            return "\n\n".join(parts) if parts else None

        async def _get_reasoning_context(self, query: str) -> str | None:
            """Get reasoning memory context (similar past traces)."""
            try:
                traces = await self._client.reasoning.get_similar_traces(
                    task=query,
                    limit=self._max_context_items // 3,
                )

                if not traces:
                    return None

                trace_items = []
                for trace in traces:
                    item = f"**Task**: {truncate_text(trace.task, 100)}"
                    if trace.outcome:
                        item += f" | Outcome: {truncate_text(trace.outcome, 80)}"
                    if trace.success is not None:
                        status = "Success" if trace.success else "Failed"
                        item += f" | {status}"
                    trace_items.append(item)

                if trace_items:
                    return format_context_section("Similar Past Tasks", trace_items)

            except Exception as e:
                logger.debug(f"Error getting similar traces: {e}")

            return None

        async def _trigger_background_extraction(self) -> None:
            """Trigger background entity extraction without blocking."""
            if not self._extraction_queue:
                return

            # Clear queue (we'll use session-level extraction)
            self._extraction_queue.clear()

            async def extract_batch() -> None:
                try:
                    # Session-level extraction is bolt-only; NAMS extracts
                    # entities server-side, so there's nothing to trigger here.
                    short_term = self._client.short_term
                    if isinstance(short_term, ShortTermMemory):
                        await short_term.extract_entities_from_session(
                            session_id=self._session_id,
                            batch_size=10,
                        )
                except Exception as e:
                    logger.debug(f"Background extraction error: {e}")

            task = asyncio.create_task(extract_batch())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)


except ImportError:
    # Microsoft Agent Framework not installed
    pass
