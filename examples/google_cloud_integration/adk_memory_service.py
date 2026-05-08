#!/usr/bin/env python3
"""Google ADK Memory Service Demo.

Demonstrates using Neo4jMemoryService with Google's Agent Development Kit (ADK).

Features demonstrated:
- Neo4jMemoryService initialization
- Session storage and retrieval
- Semantic memory search
- Entity extraction from conversations
- Preference learning
- Integration patterns for ADK agents

Requirements:
    pip install neo4j-agent-memory[google-adk]
"""

import asyncio
import os
from datetime import datetime

from pydantic import SecretStr

# SessionStrategy can be used to configure how session IDs are resolved
# when using MemoryIntegration with Google ADK agents.
# Available strategies: PER_CONVERSATION, PER_DAY, PERSISTENT
from neo4j_agent_memory.integration import SessionStrategy


async def demo_basic_usage():
    """Demonstrate basic ADK memory service operations."""
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    print("=" * 60)
    print("ADK Memory Service - Basic Usage")
    print("=" * 60)
    print()

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            username=os.environ.get("NEO4J_USER", "neo4j"),
            password=SecretStr(os.environ.get("NEO4J_PASSWORD", "password")),
        )
    )

    async with MemoryClient(settings) as client:
        # Create memory service
        memory_service = Neo4jMemoryService(
            memory_client=client,
            user_id="adk-demo-user",
            include_entities=True,
            include_preferences=True,
        )

        print("Neo4jMemoryService initialized:")
        print(f"  User ID: {memory_service.user_id}")
        print(f"  Entity extraction: {memory_service.include_entities}")
        print(f"  Preference learning: {memory_service.include_preferences}")
        print()

        # Store a session
        print("1. Storing Conversation Session")
        print("-" * 40)

        session_id = f"adk-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        session = {
            "id": session_id,
            "messages": [
                {
                    "role": "user",
                    "content": "I'm planning a trip to Tokyo with my friend Maria. "
                    "We're interested in visiting temples and trying local food.",
                },
                {
                    "role": "assistant",
                    "content": "Tokyo is wonderful for both! I recommend visiting "
                    "Senso-ji Temple in Asakusa, and for food, try the "
                    "Tsukiji Outer Market. When are you planning to go?",
                },
                {
                    "role": "user",
                    "content": "We're thinking about cherry blossom season in April. "
                    "Also, I prefer vegetarian food options.",
                },
                {
                    "role": "assistant",
                    "content": "Great timing! April is beautiful with sakura. For "
                    "vegetarian options, look for shojin ryori (Buddhist temple "
                    "cuisine). T's TanTan in Tokyo Station has excellent options.",
                },
            ],
        }

        await memory_service.add_session_to_memory(session)
        print(f"  Stored session: {session_id}")
        print(f"  Messages: {len(session['messages'])}")
        print()

        # Search memories
        print("2. Searching Memories")
        print("-" * 40)

        queries = [
            "travel plans",
            "Maria",
            "food preferences",
            "temples in Tokyo",
        ]

        for query in queries:
            print(f"\n  Query: '{query}'")
            results = await memory_service.search_memory(
                query=query,
                limit=3,
            )
            if results:
                for entry in results:
                    content_preview = entry.content[:60].replace("\n", " ")
                    print(f"    [{entry.memory_type}] {content_preview}...")
            else:
                print("    No results found")
        print()

        # Get session history
        print("3. Retrieving Session History")
        print("-" * 40)

        history = await memory_service.get_memories_for_session(session_id)
        print(f"  Session {session_id[:30]}... has {len(history)} entries")
        for entry in history:
            role = entry.metadata.get("role", "?") if entry.metadata else "?"
            print(f"    [{role}] {entry.content[:50]}...")
        print()


async def demo_entity_extraction():
    """Demonstrate entity extraction from conversations."""
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    print("=" * 60)
    print("ADK Memory Service - Entity Extraction")
    print("=" * 60)
    print()

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            username=os.environ.get("NEO4J_USER", "neo4j"),
            password=SecretStr(os.environ.get("NEO4J_PASSWORD", "password")),
        )
    )

    async with MemoryClient(settings) as client:
        memory_service = Neo4jMemoryService(
            memory_client=client,
            user_id="adk-entity-demo",
            include_entities=True,
        )

        print("Storing conversation with rich entity content...")
        print()

        session = {
            "id": f"entity-demo-{datetime.now().strftime('%Y%m%d%H%M%S')}",
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

        await memory_service.add_session_to_memory(session)
        print("  Session stored with entity extraction enabled")
        print()

        # Search for extracted entities
        print("Searching for entities mentioned in conversation:")
        print("-" * 40)

        entity_queries = [
            "Sarah Chen",
            "Acme Corp",
            "San Francisco",
            "David Martinez",
        ]

        for query in entity_queries:
            print(f"\n  Entity: '{query}'")
            results = await memory_service.search_memory(
                query=query,
                limit=2,
            )
            for entry in results:
                print(f"    [{entry.memory_type}] {entry.content[:50]}...")
        print()

        print("Note: Entities are stored in Neo4j's knowledge graph and can be")
        print("explored using Cypher queries or the graph_query MCP tool.")
        print()


async def demo_preference_learning():
    """Demonstrate preference learning from conversations."""
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    print("=" * 60)
    print("ADK Memory Service - Preference Learning")
    print("=" * 60)
    print()

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            username=os.environ.get("NEO4J_USER", "neo4j"),
            password=SecretStr(os.environ.get("NEO4J_PASSWORD", "password")),
        )
    )

    async with MemoryClient(settings) as client:
        memory_service = Neo4jMemoryService(
            memory_client=client,
            user_id="adk-pref-demo",
            include_preferences=True,
        )

        print("Storing conversations that express preferences...")
        print()

        # Session with preferences
        session = {
            "id": f"pref-demo-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "messages": [
                {
                    "role": "user",
                    "content": "I prefer dark mode in all my applications. "
                    "Also, I like to schedule meetings in the morning.",
                },
                {
                    "role": "assistant",
                    "content": "Noted! Dark mode and morning meetings. Any other "
                    "preferences I should know about?",
                },
                {
                    "role": "user",
                    "content": "Yes, I'm a fan of Python over JavaScript, and I "
                    "prefer concise explanations rather than lengthy ones.",
                },
            ],
        }

        await memory_service.add_session_to_memory(session)
        print("  Session stored with preference extraction enabled")
        print()

        # Add explicit preference
        print("Adding explicit preference...")
        pref = await memory_service.add_memory(
            content="Prefers async/await over callbacks",
            memory_type="preference",
            category="programming",
        )
        if pref:
            print(f"  Added: {pref.content}")
        print()

        # Search for preferences
        print("Searching for learned preferences:")
        print("-" * 40)

        pref_queries = [
            "UI preferences",
            "meeting scheduling",
            "programming language",
            "communication style",
        ]

        for query in pref_queries:
            print(f"\n  Query: '{query}'")
            results = await memory_service.search_memory(
                query=query,
                limit=2,
            )
            for entry in results:
                print(f"    [{entry.memory_type}] {entry.content[:50]}...")
        print()


async def demo_adk_agent_pattern():
    """Demonstrate the recommended pattern for using with ADK agents."""
    print("=" * 60)
    print("ADK Memory Service - Agent Integration Pattern")
    print("=" * 60)
    print()

    print("Recommended pattern for integrating with Google ADK agents:")
    print()

    code_example = '''
from google.adk import Agent
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy

# Initialize memory
settings = MemorySettings(...)
memory_client = MemoryClient(settings)
await memory_client.connect()

# Create memory service
memory_service = Neo4jMemoryService(
    memory_client=memory_client,
    user_id="user-123",
    include_entities=True,
    include_preferences=True,
)

# Use with ADK Agent
agent = Agent(
    name="my-agent",
    memory=memory_service,  # Pass as memory provider
)

# The agent will automatically:
# - Store conversation sessions
# - Extract entities and preferences
# - Search memories for context

# SessionStrategy configuration for session ID resolution:
# - SessionStrategy.PER_CONVERSATION: New UUID per instance (default)
# - SessionStrategy.PER_DAY: Daily continuity ("user-123-2026-04-02")
# - SessionStrategy.PERSISTENT: Fixed user_id for maximum continuity
#
# Example with MemoryIntegration:
# async with MemoryIntegration(
#     neo4j_uri="bolt://localhost:7687",
#     neo4j_password="password",
#     session_strategy=SessionStrategy.PER_DAY,
#     user_id="user-123",
# ) as memory:
#     await memory.store_message("user", "Hello!")

# For custom memory operations in tools:
@agent.tool
async def remember_fact(fact: str) -> str:
    """Store a fact for later recall."""
    entry = await memory_service.add_memory(
        content=fact,
        memory_type="message",
    )
    return f"Remembered: {fact}"

@agent.tool
async def recall_memories(query: str) -> list[str]:
    """Search memories for relevant information."""
    results = await memory_service.search_memory(query, limit=5)
    return [entry.content for entry in results]
'''

    print(code_example)
    print()


async def main():
    """Run all ADK memory service demos."""
    print("\n" + "=" * 60)
    print("Neo4j Agent Memory - Google ADK Integration Demo")
    print("=" * 60 + "\n")

    try:
        await demo_basic_usage()
        await demo_entity_extraction()
        await demo_preference_learning()
        demo_adk_agent_pattern()  # This one is sync (just prints)

    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure you have:")
        print("  1. Neo4j running: docker run -p 7687:7687 neo4j:5")
        print("  2. Set environment variables: NEO4J_URI, NEO4J_PASSWORD")
        raise

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
