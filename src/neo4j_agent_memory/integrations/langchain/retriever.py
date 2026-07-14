"""LangChain retriever integration."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

try:
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class Neo4jMemoryRetriever(BaseRetriever):
        """
        LangChain retriever that searches across all memory types.

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever

            async with MemoryClient(settings) as client:
                retriever = Neo4jMemoryRetriever(memory_client=client)
                docs = retriever.invoke("Italian restaurants")
        """

        memory_client: Any  # MemoryClient
        search_short_term: bool = True
        search_long_term: bool = True
        search_reasoning: bool = True
        k: int = 10
        threshold: float = 0.7

        class Config:
            """Pydantic configuration."""

            arbitrary_types_allowed = True

        def _get_relevant_documents(
            self,
            query: str,
            *,
            run_manager: CallbackManagerForRetrieverRun | None = None,
        ) -> list[Document]:
            """
            Retrieve relevant memory documents.

            This is a sync wrapper that creates an event loop if needed.
            """
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    coro = self._get_relevant_documents_async(query)
                    future = executor.submit(lambda: asyncio.run(coro))
                    return future.result()
            else:
                return asyncio.run(self._get_relevant_documents_async(query))

        async def _get_relevant_documents_async(self, query: str) -> list[Document]:
            """Async implementation of _get_relevant_documents."""
            documents = []

            if self.search_short_term:
                messages = await self.memory_client.short_term.search_messages(
                    query, limit=self.k, threshold=self.threshold
                )
                for msg in messages:
                    documents.append(
                        Document(
                            page_content=msg.content,
                            metadata={
                                "type": "message",
                                "role": msg.role.value,
                                "id": str(msg.id),
                                "similarity": msg.metadata.get("similarity", 0),
                            },
                        )
                    )

            if self.search_long_term:
                entities = await self.memory_client.long_term.search_entities(
                    query, limit=self.k, threshold=self.threshold
                )
                for entity in entities:
                    content = f"{entity.display_name}"
                    if entity.description:
                        content += f": {entity.description}"
                    # entity.type may be a string or enum
                    entity_type = (
                        entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                    )
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

                preferences = await self.memory_client.long_term.search_preferences(
                    query, limit=self.k, threshold=self.threshold
                )
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

            if self.search_reasoning:
                traces = await self.memory_client.reasoning.get_similar_traces(
                    query, limit=self.k // 2, threshold=self.threshold
                )
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

            # Sort by similarity
            documents.sort(key=lambda d: d.metadata.get("similarity", 0), reverse=True)
            return documents[: self.k]

except ImportError:
    # LangChain not installed
    pass
