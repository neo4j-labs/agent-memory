"""Route an explicit message search inside the same Neo4j-backed client."""

import asyncio
from uuid import uuid4

from common import settings


async def exercise(client):
    from neo4j_agent_memory.integrations.agentcore import HybridMemoryProvider

    provider = HybridMemoryProvider(
        client,
        namespace="docs",
        routing_strategy="explicit",
        extract_entities=False,
        sync_entities=False,
    )
    session_id = f"docs-hybrid-{uuid4().hex[:8]}"
    content = "The fictional project code is cedar-lantern-47."
    stored = await provider.store_memory(session_id, content, memory_type="message")
    history = await provider.get_session_memories(session_id)
    if not any(item.id == stored.id and item.content == content for item in history):
        raise RuntimeError(f"Provider readback failed for {session_id}")
    # Message search is not scoped by session on Bolt, so no session_id is passed.
    result = await provider.search_memory(
        "project code",
        memory_types=["message"],
        include_entities=False,
        include_preferences=False,
        include_relationships=False,
        threshold=0.0,
    )
    if not result.memories:
        raise RuntimeError("Message search returned nothing; check the vector index and dimensions")
    print(f"Verified stored message; session={session_id}")
    print(f"Route searched: {result.filters_applied['memory_types_searched']}")
    print(f"Search returned {len(result.memories)} candidate(s) from the whole database")
    return result


async def main():
    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as client:
        await exercise(client)


if __name__ == "__main__":
    asyncio.run(main())
