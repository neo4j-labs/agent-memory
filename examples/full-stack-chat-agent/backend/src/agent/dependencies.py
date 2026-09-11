"""Agent dependencies extending MemoryDependency."""

from dataclasses import dataclass
from typing import cast

from neo4j import AsyncDriver

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency


@dataclass
class AgentDeps(MemoryDependency):
    """Extended agent dependencies with news graph access.

    Inherits from the library's shipped PydanticAI dependency so the agent gets
    `get_context()` / `save_interaction()` / `add_preference()` for free, and
    adds the second (news graph) Neo4j driver.
    """

    news_driver: AsyncDriver | None = None
    news_database: str = "neo4j"
    memory_enabled: bool = True

    @classmethod
    def create(
        cls,
        memory: MemoryClient | None,
        session_id: str,
        news_driver: AsyncDriver | None = None,
        news_database: str = "neo4j",
        memory_enabled: bool = True,
    ) -> "AgentDeps":
        """Create agent dependencies with memory client and news driver.

        ``memory`` may be None when Neo4j is unreachable; every memory call
        site is guarded by ``memory_enabled``, so the cast is safe and keeps
        the library's non-optional ``client`` annotation intact.
        """
        return cls(
            client=cast(MemoryClient, memory),
            session_id=session_id,
            news_driver=news_driver,
            news_database=news_database,
            memory_enabled=memory_enabled and memory is not None,
        )
