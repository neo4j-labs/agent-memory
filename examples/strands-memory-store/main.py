"""Neo4jMemoryStore for Strands -- long-term recall, no API keys required.

Neo4jMemoryStore is a Strands `MemoryStore`: hand it to
`MemoryManager(stores=[...])` for cross-session recall fed into the agent
loop. Neo4jSessionManager (examples/strands-session-manager/) remains for
transcript persistence -- the two are complementary, not alternatives; see
the "Pairing with the session manager" section of the guide.

    make neo4j-start
    NEO4J_PASSWORD=test-password uv run python examples/strands-memory-store/main.py
"""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
from neo4j_agent_memory.integrations.strands import (
    Neo4jMemoryStore,
    Neo4jMemoryStoreConfig,
)


def build_settings() -> MemorySettings:
    """Local sentence-transformers embedder, no LLM -- runs with no API key.

    ``ExtractorType.NONE`` disables entity extraction so the demo works
    whether or not spaCy / GLiNER extras are installed; the entity and
    preference below are seeded directly instead.
    """
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        ),
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )


async def main() -> None:
    async with MemoryClient(build_settings()) as client:
        # user_identifier: a store with a user_id recalls only that user's
        # preferences, so an unscoped one would not show up below.
        await client.long_term.add_preference("ui", "Prefers dark mode", user_identifier="alice")
        await client.long_term.add_entity("Acme Corp", "ORGANIZATION")

        store = Neo4jMemoryStore(
            Neo4jMemoryStoreConfig(name="graph", client=client, user_id="alice")
        )
        await store.initialize()

        print("search('what does the user prefer?'):")
        for entry in await store.search("what does the user prefer?"):
            kind = (entry.metadata or {}).get("kind", "?")
            print(f"  {kind:>10}: {entry.content}")

        # Default sink: written as a message with extraction on -- the one
        # write path every backend supports. metadata["kind"] routes to a
        # typed write (preference/fact/entity) instead; see the guide.
        result = await store.add("The user's deployment target is us-east-1")
        print(f"add(...): {result}")

        # Graph-native tools a MemoryManager can't provide on its own.
        print("tools:", [tool.tool_name for tool in store.get_tools()])


if __name__ == "__main__":
    asyncio.run(main())
