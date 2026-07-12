"""CrewAI memory integration."""

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

_T = TypeVar("_T")

try:
    from crewai.memory.memory import Memory

    class Neo4jCrewMemory(Memory):
        """
        CrewAI memory backed by Neo4j Agent Memory.

        Provides long-term memory for CrewAI agents with:
        - Cross-task knowledge persistence
        - Entity and relationship tracking
        - Tool usage patterns

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

            async with MemoryClient(settings) as client:
                memory = Neo4jCrewMemory(
                    memory_client=client,
                    crew_id="my-crew"
                )
                # Use with CrewAI
        """

        def __init__(
            self,
            memory_client: "MemoryClient",
            crew_id: str,
        ):
            """
            Initialize CrewAI memory.

            Args:
                memory_client: Neo4j Agent Memory client
                crew_id: Crew identifier for session tracking
            """
            self._client = memory_client
            self._crew_id = crew_id

        def _run_async(self, coro: Coroutine[Any, Any, _T]) -> _T:
            """
            Run an async coroutine from sync context.

            Handles the case where we're already in an async context by
            scheduling the coroutine on the existing event loop.
            """
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                # We're in an async context - schedule on the existing loop
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                return future.result(timeout=30)
            else:
                # Not in async context - create a new loop
                return asyncio.run(coro)

        def remember(self, content: str, metadata: dict[str, Any] | None = None) -> None:
            """
            Store a memory from agent execution.

            Args:
                content: Memory content
                metadata: Optional metadata with memory type
            """
            self._run_async(self._remember_async(content, metadata))

        async def _remember_async(
            self, content: str, metadata: dict[str, Any] | None = None
        ) -> None:
            """Async implementation of remember."""
            metadata = metadata or {}
            memory_type = metadata.get("type", "short_term")

            if memory_type == "short_term":
                await self._client.short_term.add_message(self._crew_id, "assistant", content)
            elif memory_type == "fact":
                subject = metadata.get("subject", "agent")
                predicate = metadata.get("predicate", "learned")
                await self._client.long_term.add_fact(subject, predicate, content)
            elif memory_type == "preference":
                category = metadata.get("category", "general")
                await self._client.long_term.add_preference(category, content)

        def recall(self, query: str, n: int = 5) -> list[str]:
            """
            Recall relevant memories for a query.

            Args:
                query: Search query
                n: Maximum memories to return

            Returns:
                List of memory strings
            """
            return self._run_async(self._recall_async(query, n))

        async def _recall_async(self, query: str, n: int = 5) -> list[str]:
            """Async implementation of recall."""
            results = []

            # Search short-term
            messages = await self._client.short_term.search_messages(query, limit=n)
            results.extend([m.content for m in messages])

            # Search long-term
            entities = await self._client.long_term.search_entities(query, limit=n)
            for e in entities:
                if e.description:
                    results.append(f"{e.display_name}: {e.description}")

            prefs = await self._client.long_term.search_preferences(query, limit=n)
            results.extend([p.preference for p in prefs])

            return results[:n]

        def get_agent_context(self, agent_role: str, task: str) -> str:
            """
            Get context for a specific agent working on a task.

            Args:
                agent_role: The agent's role
                task: The task description

            Returns:
                Formatted context string
            """
            return self._run_async(self._get_agent_context_async(agent_role, task))

        async def _get_agent_context_async(self, agent_role: str, task: str) -> str:
            """Async implementation of get_agent_context."""
            # Get similar past traces
            traces = await self._client.reasoning.get_similar_traces(task, limit=3)

            context_parts = ["## Past experience with similar tasks:"]
            for trace in traces:
                context_parts.append(f"- Task: {trace.task}")
                if trace.outcome:
                    context_parts.append(f"  Outcome: {trace.outcome}")
                if trace.success is not None:
                    status = "Succeeded" if trace.success else "Failed"
                    context_parts.append(f"  Status: {status}")

            return "\n".join(context_parts)

except ImportError:
    # CrewAI not installed
    pass
