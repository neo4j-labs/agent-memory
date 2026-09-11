"""Memory service wiring Neo4j Agent Memory into the Google ADK app.

Responsibilities:

* build a :class:`~neo4j_agent_memory.MemoryClient` with Vertex AI embeddings
  and a real Gemini-backed entity extractor;
* expose the ADK-native :class:`Neo4jMemoryService` so it can be handed to
  ``Runner(memory_service=...)`` — ADK's ``load_memory`` / ``preload_memory``
  tools then read and write memory without any bespoke glue;
* offer a handful of typed helpers the FastAPI routes use directly
  (conversation writes, context search, finding writes).
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory import ExtractionConfig, MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import ExtractorType, Neo4jConfig
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
from neo4j_agent_memory.llm import from_provider
from neo4j_agent_memory.memory.long_term import DeduplicationConfig

from ..config import get_settings

if TYPE_CHECKING:
    from neo4j_agent_memory.integrations.google_adk.types import MemoryEntry
    from neo4j_agent_memory.memory.short_term import Message

logger = logging.getLogger(__name__)


class FinancialMemoryService:
    """Manages Neo4j Agent Memory for the financial advisor application.

    Example:
        memory_service = FinancialMemoryService()
        await memory_service.initialize()

        # Hand the ADK-native service to the Runner
        runner = Runner(
            agent=supervisor,
            app_name="financial_advisor",
            session_service=session_service,
            memory_service=memory_service.adk_memory_service,
        )

        # Or search the layers directly
        results = await memory_service.search_context("money laundering patterns")
    """

    def __init__(self, user_id: str | None = None):
        """Initialize the memory service.

        Args:
            user_id: Optional user identifier for personalization.
        """
        settings = get_settings()

        # Vertex AI embeddings. `dimensions=` pins the output dimensionality so
        # the Neo4j vector indexes keep a stable shape — gemini-embedding-001 is
        # natively 3072-d and supports truncation.
        embedding_provider = from_provider(
            f"vertex_ai/{settings.vertex_ai.embedding_model}",
            kind="embedding",
            project_id=settings.vertex_ai.get_project_id(),
            location=settings.vertex_ai.location,
            dimensions=settings.vertex_ai.embedding_dimensions,
        )

        # Entity extraction needs an LLM. Reuse the Gemini credentials the
        # agents already have rather than introducing a second provider: the
        # resolved provider string is `vertex_ai/<model>` or `gemini/<model>`.
        # With neither credential present we disable extraction *explicitly*
        # instead of letting the pipeline fall back to a silent NoOpExtractor.
        llm_provider_string = settings.vertex_ai.llm_provider_string()
        extraction_enabled = settings.memory_features.enable_extraction and bool(
            llm_provider_string
        )

        llm: Any = None
        if extraction_enabled and llm_provider_string:
            llm_kwargs: dict[str, Any] = {}
            if llm_provider_string.startswith("vertex_ai/"):
                llm_kwargs["vertex_project"] = settings.vertex_ai.get_project_id()
                llm_kwargs["vertex_location"] = settings.vertex_ai.location
            else:
                llm_kwargs["api_key"] = settings.vertex_ai.get_api_key()
            llm = from_provider(llm_provider_string, kind="llm", **llm_kwargs)
        elif settings.memory_features.enable_extraction:
            logger.warning(
                "Entity extraction requested but no Gemini credentials found "
                "(set GOOGLE_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI=true with "
                "GOOGLE_CLOUD_PROJECT). Extraction is disabled: no :Entity "
                "nodes will be created."
            )

        self._memory_settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=settings.neo4j.uri,
                username=settings.neo4j.user,
                password=settings.neo4j.password,
                database=settings.neo4j.database,
            ),
            embedding=embedding_provider,
            llm=llm,
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.LLM if extraction_enabled else ExtractorType.NONE,
                enable_spacy=False,
                enable_gliner=False,
                enable_llm_fallback=extraction_enabled,
            ),
        )

        # Customer names arrive from several agents and spellings; auto-merge
        # near-identical entities and flag the merely-similar ones for review.
        self._dedup_config = DeduplicationConfig(
            enabled=settings.memory_features.enable_deduplication,
            auto_merge_threshold=settings.memory_features.dedup_auto_merge_threshold,
            flag_threshold=settings.memory_features.dedup_flag_threshold,
            match_same_type_only=True,
        )

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

            # The library has no settings-level knob for deduplication yet, so
            # apply the configured thresholds to the long-term layer directly.
            self._client.long_term._deduplication = self._dedup_config

            self._memory_service = Neo4jMemoryService(
                memory_client=self._client,
                user_id=self._user_id,
                include_entities=True,
                include_preferences=True,
                extract_on_store=True,
            )

            self._initialized = True
            self._log_resolved_components()

    def _log_resolved_components(self) -> None:
        """Log which extractor and embedder actually got wired up.

        A silent ``NoOpExtractor`` is the single most common reason an agent
        memory demo has no entities in it, so name the class at startup.
        """
        client = self.client
        # No public accessor for the resolved extractor/embedder yet, so read
        # the client's internals defensively — this is diagnostics only.
        extractor = getattr(client, "_extractor", None)
        extractor_name = type(extractor).__name__ if extractor is not None else "None"
        embedder = getattr(client, "_embedder", None)
        embedder_name = type(embedder).__name__ if embedder is not None else "None"
        logger.info(
            "Financial Memory Service initialized (extractor=%s, embedder=%s, dedup=%s)",
            extractor_name,
            embedder_name,
            "on" if self._dedup_config.enabled else "off",
        )
        if extractor is None or extractor_name in {"NoOpExtractor", "None"}:
            logger.warning(
                "No entity extractor is active — conversations will be stored "
                "without :Entity nodes or MENTIONS edges."
            )

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

    # ── Search ─────────────────────────────────────────────────────────

    async def search_context(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Search the context graph across all three memory layers.

        Calls the memory layers directly rather than going through the ADK
        ``BaseMemoryService`` contract: ``search_memory()`` returns an ADK
        ``SearchMemoryResponse`` whose entries carry only ``content`` and
        ``author``, while the REST/UI surface wants the score, the memory type
        and the metadata as well.

        Args:
            query: The search query.
            limit: Maximum number of results *per layer*.
            threshold: Minimum similarity threshold.

        Returns:
            List of matching memory entries with content, type, score, metadata.
        """
        client = self.client
        results: list[dict[str, Any]] = []

        messages = await client.short_term.search_messages(query, limit=limit, threshold=threshold)
        for message in messages:
            results.append(
                {
                    "content": message.content,
                    "type": "message",
                    "score": None,
                    "metadata": {
                        "role": message.role.value,
                        "conversation_id": str(message.conversation_id)
                        if message.conversation_id
                        else None,
                        "message_id": str(message.id),
                    },
                }
            )

        entities = await client.long_term.search_entities(query, limit=limit, threshold=threshold)
        for entity in entities:
            results.append(
                {
                    "content": f"{entity.name}: {entity.description or entity.full_type}",
                    "type": "entity",
                    "score": entity.confidence,
                    "metadata": {
                        "name": entity.name,
                        "entity_type": entity.full_type,
                        "entity_id": str(entity.id),
                    },
                }
            )

        preferences = await client.long_term.search_preferences(
            query, limit=limit, threshold=threshold
        )
        for preference in preferences:
            results.append(
                {
                    "content": preference.preference,
                    "type": "preference",
                    "score": preference.confidence,
                    "metadata": {"category": preference.category},
                }
            )

        return results

    # ── Writes ─────────────────────────────────────────────────────────

    async def store_finding(
        self,
        content: str,
        session_id: str = "default",
        category: str = "investigation",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store an investigation finding as a first-class graph object.

        The finding becomes a ``:Fact`` (subject = the customer or entity it is
        about, predicate = the finding category) so it is queryable and
        deduplicated, instead of a free-text message nobody can join on.

        Args:
            content: The finding content.
            session_id: Session identifier (kept for call-site compatibility).
            category: Category of the finding, used as the fact predicate.
            metadata: Additional metadata.

        Returns:
            Confirmation message.
        """
        meta = dict(metadata or {})
        subject = (
            meta.get("customer_id")
            or meta.get("entity_id")
            or meta.get("entity_name")
            or session_id
        )

        fact = await self.client.long_term.add_fact(
            subject=str(subject),
            predicate=category.upper(),
            obj=content,
            confidence=float(meta.get("confidence", 0.9)),
            metadata={"session_id": session_id, **meta},
        )
        return f"Stored finding as fact {fact.id} ({subject} -[{category.upper()}]-> …)"

    async def store_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> Message:
        """Store one conversation message and return it.

        Returned so the caller can pass ``message.id`` as
        ``triggered_by_message_id`` on a reasoning trace, which is what creates
        the ``(:ReasoningTrace)-[:INITIATED_BY]->(:Message)`` audit edge.
        """
        return await self.client.short_term.add_message(
            session_id,
            role,
            content,
            extract_entities=True,
            extract_relations=True,
        )

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
        """Store a conversation session (batch form).

        Args:
            session_id: Session identifier.
            messages: List of messages with 'role' and 'content'.
        """
        session = {"id": session_id, "messages": messages}
        logger.info("Storing session %s (%d messages)", session_id, len(messages))
        await self.adk_memory_service.add_session_to_memory(session)

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
