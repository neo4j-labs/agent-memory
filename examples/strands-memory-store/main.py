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

    ``backend="bolt"`` is pinned explicitly: without it the backend is
    inferred, and a ``MEMORY_API_KEY`` left in the environment would
    silently redirect these writes to hosted NAMS (where preference and
    fact recall, and the ``get_user_preferences`` tool, are unavailable).

    ``ExtractorType.NONE`` disables entity extraction so the demo works
    whether or not spaCy / GLiNER extras are installed; the entity and
    preference below are seeded directly instead.
    """
    return MemorySettings(
        backend="bolt",
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
        # One seed per recalled kind, so the fan-out below has something of
        # each to return. Subtypes are pinned so the printed type is stable.
        await client.long_term.add_entity("Acme Corp", "ORGANIZATION", subtype="COMPANY")
        await client.long_term.add_fact("Acme Corp", "HEADQUARTERED_IN", "Berlin")

        # min_score is a Neo4j vector-index score, not a raw cosine
        # similarity: the index reports (1 + cosine) / 2, so 0.5 means
        # "orthogonal" and the 0.2 default filters nothing at all.
        # 0.75 == cosine 0.5.
        store = Neo4jMemoryStore(
            Neo4jMemoryStoreConfig(name="graph", client=client, user_id="alice", min_score=0.75)
        )
        await store.initialize()

        # Entity and fact recall are similarity-gated; preference recall for a
        # user-scoped store is a listing of that user's active preferences, so
        # it answers both queries regardless of relevance.
        for query in ("what does the user prefer?", "Acme Corp"):
            print(f"search({query!r}):")
            for entry in await store.search(query):
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
