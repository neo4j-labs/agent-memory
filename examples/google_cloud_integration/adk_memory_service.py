#!/usr/bin/env python3
"""Google ADK ``Neo4jMemoryService`` — the two narratives unique to this folder.

**Start with [`examples/google_adk_demo/`](../google_adk_demo/)** if you want the
full ADK wiring: that example runs a real ``Runner`` with ``LlmAgent`` and ADK's
``load_memory`` tool, two turns across two sessions, and a scripted model so it
works without Google credentials. This script deliberately does *not* duplicate
it. What remains here are the two things the sibling example does not narrate:

1. **Entity extraction** — what ``add_session_to_memory()`` pulls out of a
   conversation and how it lands in the Neo4j knowledge graph.
2. **Preferences** — how an explicit preference is written through the ADK
   adapter (``add_memory(memory_type="preference")``) and read back.

Both phases read search results the ADK 2.x way: ``search_memory()`` returns a
``SearchMemoryResponse``, so callers iterate ``response.memories`` and each
entry's ``content`` is a ``google.genai.types.Content`` (a list of parts, not a
string).

Requirements::

    pip install "neo4j-agent-memory[google-adk]"

Runs with no API key: without ``OPENAI_API_KEY`` the shared settings helper uses
a local sentence-transformers embedder and a local spaCy/GLiNER extraction
pipeline. Set ``MEMORY_API_KEY`` to run against the hosted NAMS backend instead.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from _common import build_extractor, build_settings, describe_settings, load_env

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

USER_ID = "adk-demo-user"


def entries(response: Any) -> list[tuple[str, str, str]]:
    """Flatten a ``SearchMemoryResponse`` into ``(memory_type, author, text)``.

    ADK's ``MemoryEntry`` has no ``.memory_type`` and its ``.content`` is not a
    string — the type and similarity score live in ``custom_metadata``.
    """
    rows: list[tuple[str, str, str]] = []
    for entry in response.memories:
        parts = getattr(entry.content, "parts", None) or []
        text = "".join(getattr(part, "text", None) or "" for part in parts).strip()
        meta = entry.custom_metadata or {}
        rows.append((str(meta.get("memory_type", "?")), str(entry.author or "?"), text))
    return rows


async def show_search(service: Neo4jMemoryService, query: str, *, limit: int = 3) -> None:
    """Run one search and print it, honestly reporting an empty result."""
    response = await service.search_memory(query=query, limit=limit)
    print(f"\n  Query: '{query}'")
    if not response.memories:
        # `if response:` would be truthy — SearchMemoryResponse is a pydantic
        # model, so always check `.memories`.
        print("    No results found")
        return
    for memory_type, author, text in entries(response):
        print(f"    [{memory_type}/{author}] {text[:60].replace(chr(10), ' ')}...")


async def demo_entity_extraction(client: MemoryClient) -> None:
    """What ``add_session_to_memory()`` extracts into the knowledge graph."""
    print("=" * 60)
    print("ADK Memory Service - Entity Extraction")
    print("=" * 60)
    print()

    service = Neo4jMemoryService(
        memory_client=client,
        user_id=USER_ID,
        include_entities=True,
        include_preferences=False,
    )
    # The service's configuration is what the caller passed in — the adapter
    # exposes `user_id` and `memory_client` as properties; the include_* flags
    # are private, so print what we configured.
    print(f"  user_id: {service.user_id}")
    print("  include_entities: True, include_preferences: False")
    print()

    session_id = f"entity-demo-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    await service.add_session_to_memory(
        {
            "id": session_id,
            "messages": [
                {
                    "role": "user",
                    "content": "I work at Acme Corp with my manager Sarah Chen. "
                    "Our office is in San Francisco on Market Street.",
                },
                {
                    "role": "assistant",
                    "content": "Nice! Acme Corp in San Francisco. How long have you "
                    "been working with Sarah Chen?",
                },
                {
                    "role": "user",
                    "content": "About two years. Before that, I was at TechStart Inc "
                    "in New York, working with David Martinez.",
                },
            ],
        }
    )
    print(f"  Stored session {session_id}")

    # Read the graph directly — this is the ground truth for "did extraction
    # actually happen?", independent of what search returns.
    rows = await client.query.cypher(
        """
        MATCH (c:Conversation {session_id: $session})-[:HAS_MESSAGE]->(m:Message)
              -[:MENTIONS]->(e:Entity)
        RETURN e.name AS name, e.type AS type, count(m) AS mentions
        ORDER BY mentions DESC, name
        """,
        {"session": session_id},
    )
    print(f"  Entities extracted and linked: {len(rows)}")
    for row in rows:
        print(f"    {row['name']} ({row['type']}) — {row['mentions']} mention(s)")
    if not rows:
        print("    (none — check the extractor printed above; a NoOpExtractor")
        print("     fallback means the extraction extras are missing)")

    print()
    print("Searching for the people and places mentioned:")
    print("-" * 40)
    for query in ("Sarah Chen", "Acme Corp", "San Francisco"):
        await show_search(service, query, limit=2)
    print()


async def demo_preferences(client: MemoryClient) -> None:
    """Writing and reading preferences through the ADK adapter."""
    print("=" * 60)
    print("ADK Memory Service - Preferences")
    print("=" * 60)
    print()

    service = Neo4jMemoryService(
        memory_client=client,
        user_id=USER_ID,
        include_entities=False,
        include_preferences=True,
    )

    # `add_session_to_memory` stores messages and extracts entities — it does
    # NOT detect preferences. Two ways to record one:
    #
    #   1. explicitly, through the adapter (below);
    #   2. automatically, with MemoryIntegration(auto_preferences=True), which
    #      runs the library's regex PreferenceDetector — see
    #      examples/google_adk_demo/demo.py, phase 3.
    print("Writing an explicit preference through add_memory()...")
    pref = await service.add_memory(
        content="Prefers async/await over callbacks",
        memory_type="preference",
        category="programming",
    )
    if pref is not None:
        print(f"  [{pref.memory_type}] {pref.content}")
    print()

    print("Reading it back through search_memory():")
    print("-" * 40)
    # Recall is semantic with a 0.7 similarity floor, so a paraphrase can miss
    # with a small local embedder — query close to how the preference is worded.
    await show_search(service, "async/await over callbacks", limit=3)
    print()

    print("Preferences are long-term memory, so they are workspace-wide —")
    print("they come back regardless of which session asked.")
    print()


async def main() -> None:
    """Run the two ADK narratives."""
    load_env()

    print("\n" + "=" * 60)
    print("Neo4j Agent Memory - Google ADK MemoryService")
    print("=" * 60 + "\n")
    print("For the full ADK Runner + load_memory loop, see")
    print("  examples/google_adk_demo/demo.py")
    print()

    settings = build_settings()
    extractor = build_extractor(settings)
    print("Configuration:")
    describe_settings(settings, extractor)
    print()

    async with MemoryClient(settings, extractor=extractor) as client:
        print(f"Connected backend: {client.backend}")
        print()
        await demo_entity_extraction(client)
        await demo_preferences(client)

    print("=" * 60)
    print("Demo complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
