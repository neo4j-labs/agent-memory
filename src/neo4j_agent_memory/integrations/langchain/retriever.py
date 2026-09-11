"""LangChain retriever backed by Neo4j Agent Memory.

``Neo4jMemoryRetriever`` is a real :class:`langchain_core.retrievers.BaseRetriever`,
so ``invoke`` / ``ainvoke`` / ``batch`` and the LangSmith callbacks all work as
LangChain users expect.

The async hook is ``_aget_relevant_documents`` — the name LangChain actually
calls. Before 0.6 the coroutine was named ``_get_relevant_documents_async``,
which LangChain never sees: ``BaseRetriever.__init_subclass__`` synthesised an
``_aget_relevant_documents`` that ran the *sync* path in a worker thread, so
``await retriever.ainvoke(...)`` ended up calling ``asyncio.run()`` on a fresh
loop against a Neo4j driver bound to the caller's loop. The old name is kept as
a deprecated alias for one release.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from neo4j_agent_memory.core.exceptions import NotSupportedError

if TYPE_CHECKING:
    from neo4j_agent_memory.memory.long_term import Entity, Preference
    from neo4j_agent_memory.memory.reasoning import ReasoningTrace
    from neo4j_agent_memory.memory.short_term import Message

logger = logging.getLogger(__name__)


class Neo4jMemoryRetriever(BaseRetriever):
    """LangChain retriever that searches across all memory types.

    Example::

        from neo4j_agent_memory import MemoryClient
        from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever

        async with MemoryClient(settings) as client:
            retriever = Neo4jMemoryRetriever(
                memory_client=client, session_id="user-123"
            )
            docs = await retriever.ainvoke("Italian restaurants")

    Attributes:
        memory_client: A connected :class:`~neo4j_agent_memory.MemoryClient`.
        session_id: Optional session scope for message search. Required on the
            hosted NAMS backend, whose message search is conversation-scoped.
        search_short_term: Search conversation messages.
        search_long_term: Search entities and preferences.
        search_reasoning: Search reasoning traces.
        k: Maximum documents to return.
        threshold: Minimum similarity for a hit.
    """

    memory_client: Any
    session_id: str | None = None
    search_short_term: bool = True
    search_long_term: bool = True
    search_reasoning: bool = True
    k: int = 10
    threshold: float = 0.7

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Sync retrieval. Only usable outside a running event loop.

        Neo4j Agent Memory is async and its driver is loop-bound, so rather than
        spawning a thread with its own loop (which races the caller's driver) we
        refuse and point at ``ainvoke``.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._retrieve(query))

        msg = (
            "Neo4jMemoryRetriever.invoke() cannot be used from inside a running "
            "event loop. Use `await retriever.ainvoke(query)` instead."
        )
        raise RuntimeError(msg)

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: AsyncCallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Async retrieval hook — the one LangChain's ``ainvoke`` calls."""
        return await self._retrieve(query)

    async def _retrieve(self, query: str) -> list[Document]:
        """Search every enabled memory layer and merge the hits by similarity."""
        documents: list[Document] = []

        if self.search_short_term:
            documents.extend(await self._search_messages(query))

        if self.search_long_term:
            documents.extend(await self._search_entities(query))
            documents.extend(await self._search_preferences(query))

        if self.search_reasoning:
            documents.extend(await self._search_traces(query))

        documents.sort(key=lambda d: d.metadata.get("similarity", 0), reverse=True)
        return documents[: self.k]

    # Deprecated one-release alias for the pre-0.6 coroutine name.
    _get_relevant_documents_async = _retrieve

    # ----------------------------------------------------------- per-layer
    async def _search_messages(self, query: str) -> list[Document]:
        try:
            messages: list[Message] = await self.memory_client.short_term.search_messages(
                query,
                session_id=self.session_id,
                limit=self.k,
                threshold=self.threshold,
            )
        except NotSupportedError:
            logger.debug("search_messages unsupported on this backend; skipping")
            return []
        except ValueError:
            # NAMS message search is conversation-scoped and rejects a missing
            # session id. Skip the layer rather than failing the whole retrieval.
            logger.warning(
                "Message search needs a session_id on this backend; "
                "pass session_id= to Neo4jMemoryRetriever to include messages."
            )
            return []
        return [
            Document(
                page_content=msg.content,
                metadata={
                    "type": "message",
                    "role": msg.role.value,
                    "id": str(msg.id),
                    "similarity": msg.metadata.get("similarity", 0),
                },
            )
            for msg in messages
        ]

    async def _search_entities(self, query: str) -> list[Document]:
        try:
            entities: list[Entity] = await self.memory_client.long_term.search_entities(
                query, limit=self.k, threshold=self.threshold
            )
        except NotSupportedError:
            logger.debug("search_entities unsupported on this backend; skipping")
            return []
        documents = []
        for entity in entities:
            content = entity.display_name
            if entity.description:
                content += f": {entity.description}"
            # entity.type may be a string or an enum
            entity_type = entity.type.value if hasattr(entity.type, "value") else str(entity.type)
            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "type": "entity",
                        "entity_type": entity_type,
                        "id": str(entity.id),
                        "similarity": entity.metadata.get("similarity", 0),
                    },
                )
            )
        return documents

    async def _search_preferences(self, query: str) -> list[Document]:
        try:
            preferences: list[Preference] = await self.memory_client.long_term.search_preferences(
                query, limit=self.k, threshold=self.threshold
            )
        except NotSupportedError:
            logger.debug("search_preferences unsupported on this backend; skipping")
            return []
        documents = []
        for pref in preferences:
            content = f"[{pref.category}] {pref.preference}"
            if pref.context:
                content += f" (context: {pref.context})"
            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "type": "preference",
                        "category": pref.category,
                        "id": str(pref.id),
                        "similarity": pref.metadata.get("similarity", 0),
                    },
                )
            )
        return documents

    async def _search_traces(self, query: str) -> list[Document]:
        try:
            traces: list[ReasoningTrace] = await self.memory_client.reasoning.get_similar_traces(
                query, limit=max(1, self.k // 2), threshold=self.threshold
            )
        except NotSupportedError:
            logger.debug("get_similar_traces unsupported on this backend; skipping")
            return []
        documents = []
        for trace in traces:
            content = f"Task: {trace.task}"
            if trace.outcome:
                content += f"\nOutcome: {trace.outcome}"
            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "type": "trace",
                        "success": trace.success,
                        "id": str(trace.id),
                        "similarity": trace.metadata.get("similarity", 0),
                    },
                )
            )
        return documents
