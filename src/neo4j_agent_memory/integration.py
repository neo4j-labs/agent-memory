"""High-level convenience layer for memory operations.

Provides a simplified interface over MemoryClient that both the MCP server
and create-context-graph templates can consume. Handles session identity
resolution, automatic entity extraction, and preference detection.

Example:
    from neo4j_agent_memory import MemoryIntegration

    async with MemoryIntegration(
        neo4j_uri="bolt://localhost:7687",
        neo4j_password="password",
    ) as memory:
        await memory.store_message("user", "I love Italian food")
        context = await memory.get_context()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.mcp._observer import MemoryObserver

logger = logging.getLogger(__name__)


class SessionStrategy(str, Enum):
    """Strategy for resolving session IDs."""

    PER_CONVERSATION = "per_conversation"
    """New UUID per MemoryIntegration instance (default)."""

    PER_DAY = "per_day"
    """One session per calendar day: '{user_id}-YYYY-MM-DD'."""

    PERSISTENT = "persistent"
    """Single persistent session using user_id."""


class MemoryIntegration:
    """High-level convenience wrapper over MemoryClient.

    Provides a simplified interface for the three most common operations:
    store a message (with automatic extraction), retrieve assembled context,
    and manage entities/preferences/facts.

    Can be constructed with either a pre-connected MemoryClient or connection
    parameters (in which case it manages the client lifecycle internally).

    Args:
        client: Pre-connected MemoryClient instance. If provided, the caller
            is responsible for its lifecycle.
        neo4j_uri: Neo4j connection URI (used if client is None).
        neo4j_password: Neo4j password (used if client is None).
        neo4j_user: Neo4j username (default: "neo4j").
        neo4j_database: Neo4j database name (default: "neo4j").
        session_strategy: How to resolve session IDs.
        user_id: User identifier for per_day and persistent strategies.
        auto_extract: Whether to extract entities from stored messages.
        auto_preferences: Whether to detect preferences from user messages.
    """

    def __init__(
        self,
        client: MemoryClient | None = None,
        *,
        neo4j_uri: str | None = None,
        neo4j_password: str | None = None,
        neo4j_user: str = "neo4j",
        neo4j_database: str = "neo4j",
        session_strategy: SessionStrategy | str = SessionStrategy.PER_CONVERSATION,
        user_id: str | None = None,
        auto_extract: bool = True,
        auto_preferences: bool = True,
    ):
        self._client = client
        self._owns_client = client is None
        self._neo4j_uri = neo4j_uri
        self._neo4j_password = neo4j_password
        self._neo4j_user = neo4j_user
        self._neo4j_database = neo4j_database

        if isinstance(session_strategy, str):
            session_strategy = SessionStrategy(session_strategy)
        self._strategy = session_strategy
        self._user_id = user_id
        self._auto_extract = auto_extract
        self._auto_preferences = auto_preferences

        # Per-conversation session ID (generated on first resolve)
        self._conversation_session_id: str | None = None

        # Preference detector (lazy-initialized)
        self._preference_detector = None

        # Observer (set externally by the MCP server lifespan)
        self._observer: MemoryObserver | None = None

    @property
    def client(self) -> MemoryClient:
        """Access the underlying MemoryClient.

        Raises:
            RuntimeError: If not connected.
        """
        if self._client is None:
            raise RuntimeError(
                "MemoryIntegration not connected. Use 'async with' or call connect()."
            )
        return self._client

    async def connect(self) -> None:
        """Connect to Neo4j (only needed if no client was provided)."""
        if self._client is not None:
            return

        from pydantic import SecretStr

        from neo4j_agent_memory import MemoryClient as _MemoryClient
        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=self._neo4j_uri or "bolt://localhost:7687",
                username=self._neo4j_user,
                password=SecretStr(self._neo4j_password or ""),
                database=self._neo4j_database,
            )
        )
        self._client = _MemoryClient(settings)
        await self._client.connect()

    async def close(self) -> None:
        """Close the connection (only if we own the client)."""
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> MemoryIntegration:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def resolve_session_id(self, hint: str | None = None) -> str:
        """Resolve a session ID based on the configured strategy.

        Args:
            hint: Optional explicit session ID. If provided, always used as-is.

        Returns:
            The resolved session ID string.
        """
        if hint:
            return hint

        if self._strategy == SessionStrategy.PER_CONVERSATION:
            if self._conversation_session_id is None:
                self._conversation_session_id = str(uuid4())
            return self._conversation_session_id

        if self._strategy == SessionStrategy.PER_DAY:
            user = self._user_id or "default"
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            return f"{user}-{date_str}"

        # PERSISTENT
        return self._user_id or "default"

    @property
    def observer(self) -> MemoryObserver | None:
        """Access the observer (set by MCP server lifespan)."""
        return self._observer

    @observer.setter
    def observer(self, value: MemoryObserver | None) -> None:
        self._observer = value

    def _get_preference_detector(self):
        """Lazy-initialize the preference detector."""
        if self._preference_detector is None:
            from neo4j_agent_memory.mcp._preference_detector import PreferenceDetector

            self._preference_detector = PreferenceDetector()
        return self._preference_detector

    async def _detect_and_store_preferences(self, content: str, session_id: str) -> None:
        """Detect preferences in message content and store them.

        Runs as a fire-and-forget background task. Errors are logged
        but do not propagate.
        """
        try:
            detector = self._get_preference_detector()
            detected = detector.detect(content)
            for pref in detected:
                sentiment_prefix = "" if pref.sentiment == "positive" else "Dislikes: "
                await self.client.long_term.add_preference(
                    category=pref.category,
                    preference=f"{sentiment_prefix}{pref.preference}",
                    context=f"Auto-detected from session {session_id}: {pref.source_text[:200]}",
                    confidence=pref.confidence,
                    generate_embedding=True,
                )
                logger.debug(
                    f"Auto-detected preference: [{pref.category}] "
                    f"{pref.sentiment}: {pref.preference}"
                )
        except Exception as e:
            logger.warning(f"Error in background preference detection: {e}")

    # ── Core Operations ──────────────────────────────────────────────

    async def store_message(
        self,
        role: str,
        content: str,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a message with automatic entity extraction.

        Args:
            role: Message role ('user', 'assistant', 'system').
            content: Message text content.
            session_id: Explicit session ID (uses strategy if not provided).
            metadata: Optional metadata to attach.

        Returns:
            Dict with stored message info (id, session_id, stored flag).
        """
        sid = self.resolve_session_id(session_id)
        try:
            message = await self.client.short_term.add_message(
                session_id=sid,
                role=role,
                content=content,
                metadata=metadata,
                extract_entities=self._auto_extract,
                generate_embedding=True,
            )

            # Fire-and-forget: background preference detection for user messages
            if self._auto_preferences and role == "user":
                asyncio.create_task(self._detect_and_store_preferences(content, sid))

            # Notify observer (for context tracking and compression)
            if self._observer is not None:
                asyncio.create_task(
                    self._observer.on_message_stored(
                        session_id=sid,
                        content=content,
                        message_id=str(message.id),
                        role=role,
                    )
                )

            return {
                "stored": True,
                "type": "message",
                "id": str(message.id),
                "session_id": sid,
            }
        except Exception as e:
            logger.error(f"Error storing message: {e}")
            return {"error": str(e)}

    async def get_context(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        max_items: int = 10,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
    ) -> dict[str, Any]:
        """Get assembled context from all memory types.

        Args:
            session_id: Explicit session ID (uses strategy if not provided).
            query: Search query for context retrieval.
            max_items: Maximum items per memory type.
            include_short_term: Whether to include conversation history.
            include_long_term: Whether to include entities/preferences.
            include_reasoning: Whether to include reasoning traces.

        Returns:
            Dict with context string and metadata.
        """
        sid = self.resolve_session_id(session_id)
        try:
            context = await self.client.get_context(
                query=query or "",
                session_id=sid,
                include_short_term=include_short_term,
                include_long_term=include_long_term,
                include_reasoning=include_reasoning,
                max_items=max_items,
            )
            return {
                "session_id": sid,
                "context": context,
                "has_context": bool(context),
            }
        except Exception as e:
            logger.error(f"Error getting context: {e}")
            return {"error": str(e)}

    async def search(
        self,
        query: str,
        *,
        memory_types: list[str] | None = None,
        session_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> dict[str, Any]:
        """Search across memory types.

        Args:
            query: Search query text.
            memory_types: Types to search ('messages', 'entities', 'preferences', 'traces').
            session_id: Filter messages by session.
            limit: Maximum results per type.
            threshold: Similarity threshold.

        Returns:
            Dict with results organized by memory type.
        """
        if memory_types is None:
            memory_types = ["messages", "entities", "preferences"]

        results: dict[str, list[dict[str, Any]]] = {}

        try:
            if "messages" in memory_types:
                messages = await self.client.short_term.search_messages(
                    query=query,
                    session_id=session_id,
                    limit=limit,
                    threshold=threshold,
                )
                results["messages"] = [
                    {
                        "id": str(msg.id),
                        "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                        "content": msg.content,
                        "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                        "similarity": msg.metadata.get("similarity") if msg.metadata else None,
                    }
                    for msg in messages
                ]

            if "entities" in memory_types:
                entities = await self.client.long_term.search_entities(
                    query=query,
                    limit=limit,
                )
                results["entities"] = [
                    {
                        "id": str(entity.id),
                        "name": entity.display_name,
                        "type": (
                            entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                        ),
                        "description": entity.description,
                    }
                    for entity in entities
                ]

            if "preferences" in memory_types:
                preferences = await self.client.long_term.search_preferences(
                    query=query,
                    limit=limit,
                )
                results["preferences"] = [
                    {
                        "id": str(pref.id),
                        "category": pref.category,
                        "preference": pref.preference,
                        "context": pref.context,
                    }
                    for pref in preferences
                ]

            if "traces" in memory_types:
                traces = await self.client.reasoning.get_similar_traces(
                    task=query,
                    limit=limit,
                )
                results["traces"] = [
                    {
                        "id": str(trace.id),
                        "task": trace.task,
                        "outcome": trace.outcome,
                        "success": trace.success,
                    }
                    for trace in traces
                ]

        except Exception as e:
            logger.error(f"Error in search: {e}")
            return {"error": str(e)}

        return {"results": results, "query": query}

    async def add_entity(
        self,
        name: str,
        entity_type: str,
        *,
        subtype: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update an entity with POLE+O typing.

        Runs entity resolution against existing graph to avoid duplicates.

        Args:
            name: Entity name.
            entity_type: POLE+O type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION).
            subtype: Optional subtype (e.g., VEHICLE, ADDRESS).
            description: Entity description.
            aliases: Alternative names.
            metadata: Additional metadata.

        Returns:
            Dict with entity info and deduplication result.
        """
        try:
            entity, dedup_result = await self.client.long_term.add_entity(
                name=name,
                entity_type=entity_type,
                subtype=subtype,
                description=description,
                aliases=aliases,
                metadata=metadata,
                generate_embedding=True,
            )
            result: dict[str, Any] = {
                "stored": True,
                "type": "entity",
                "id": str(entity.id),
                "name": entity.display_name,
                "entity_type": (
                    entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                ),
            }
            if dedup_result:
                result["deduplication"] = {
                    "action": dedup_result.action,
                    "matched_entity_name": dedup_result.matched_entity_name,
                    "similarity_score": dedup_result.similarity_score,
                }
            return result
        except Exception as e:
            logger.error(f"Error adding entity: {e}")
            return {"error": str(e)}

    async def add_preference(
        self,
        category: str,
        preference: str,
        *,
        context: str | None = None,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a user preference.

        Args:
            category: Preference category (e.g., 'food', 'music').
            preference: The preference text.
            context: Optional context about when/why this preference was expressed.
            confidence: Confidence score (0.0-1.0).
            metadata: Additional metadata.

        Returns:
            Dict with stored preference info.
        """
        try:
            pref = await self.client.long_term.add_preference(
                category=category,
                preference=preference,
                context=context,
                confidence=confidence,
                metadata=metadata,
                generate_embedding=True,
            )
            return {
                "stored": True,
                "type": "preference",
                "id": str(pref.id),
                "category": category,
            }
        except Exception as e:
            logger.error(f"Error adding preference: {e}")
            return {"error": str(e)}

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object_value: str,
        *,
        confidence: float = 1.0,
        valid_from: str | None = None,
        valid_until: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a subject-predicate-object fact triple.

        Args:
            subject: Fact subject.
            predicate: Relationship/predicate.
            object_value: Fact object.
            confidence: Confidence score (0.0-1.0).
            valid_from: ISO date string for temporal validity start.
            valid_until: ISO date string for temporal validity end.
            metadata: Additional metadata.

        Returns:
            Dict with stored fact info.
        """
        try:
            fact = await self.client.long_term.add_fact(
                subject=subject,
                predicate=predicate,
                obj=object_value,
                confidence=confidence,
                valid_from=valid_from,
                valid_until=valid_until,
                generate_embedding=True,
                metadata=metadata,
            )
            return {
                "stored": True,
                "type": "fact",
                "id": str(fact.id) if hasattr(fact, "id") else None,
                "triple": f"{subject} -> {predicate} -> {object_value}",
            }
        except Exception as e:
            logger.error(f"Error adding fact: {e}")
            return {"error": str(e)}
