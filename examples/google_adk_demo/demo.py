#!/usr/bin/env python3
"""Google ADK Memory Demo.

Demonstrates using Neo4j Agent Memory with Google's Agent Development Kit.
"""

import asyncio
import os
from datetime import datetime

from pydantic import SecretStr

# Check for required environment variables
if not os.environ.get("NEO4J_PASSWORD"):
    print("Warning: NEO4J_PASSWORD not set. Using default 'password'")


async def main():
    """Run the Google ADK memory demo."""
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    # Configuration
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            username=os.environ.get("NEO4J_USER", "neo4j"),
            password=SecretStr(os.environ.get("NEO4J_PASSWORD", "password")),
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )
    )

    print("=" * 60)
    print("Neo4j Agent Memory - Google ADK Demo")
    print("=" * 60)
    print()

    async with MemoryClient(settings) as client:
        # Create memory service
        memory_service = Neo4jMemoryService(
            memory_client=client,
            user_id="demo-user",
            include_entities=True,
            include_preferences=True,
        )

        print("1. Storing conversation sessions...")
        print("-" * 40)

        # Session 1: Project discussion
        session1 = {
            "id": f"demo-session-{datetime.now().strftime('%Y%m%d%H%M%S')}-1",
            "messages": [
                {
                    "role": "user",
                    "content": "I'm working on Project Alpha with Sarah and John. "
                    "The deadline is next Friday.",
                },
                {
                    "role": "assistant",
                    "content": "Got it! Project Alpha with Sarah and John, deadline next Friday. "
                    "What's the current status?",
                },
                {
                    "role": "user",
                    "content": "We're about 70% done. Sarah is handling the frontend "
                    "and John is working on the backend API.",
                },
            ],
        }
        await memory_service.add_session_to_memory(session1)
        print(f"  Stored session: {session1['id']}")

        # Session 2: Preferences
        session2 = {
            "id": f"demo-session-{datetime.now().strftime('%Y%m%d%H%M%S')}-2",
            "messages": [
                {
                    "role": "user",
                    "content": "I prefer morning meetings and I like dark mode in all my apps.",
                },
                {
                    "role": "assistant",
                    "content": "Noted! I'll remember that you prefer morning meetings and dark mode.",
                },
            ],
        }
        await memory_service.add_session_to_memory(session2)
        print(f"  Stored session: {session2['id']}")

        print()
        print("2. Searching memories...")
        print("-" * 40)

        # Search for project-related memories
        print("\n  Query: 'project deadline'")
        results = await memory_service.search_memory(
            query="project deadline",
            limit=5,
        )
        for entry in results:
            print(f"    [{entry.memory_type}] {entry.content[:80]}...")

        # Search for people
        print("\n  Query: 'Sarah'")
        results = await memory_service.search_memory(
            query="Sarah",
            limit=5,
        )
        for entry in results:
            print(f"    [{entry.memory_type}] {entry.content[:80]}...")

        print()
        print("3. Retrieving session history...")
        print("-" * 40)

        history = await memory_service.get_memories_for_session(session1["id"])
        print(f"  Session {session1['id']} has {len(history)} messages")
        for entry in history:
            role = entry.metadata.get("role", "unknown") if entry.metadata else "unknown"
            print(f"    [{role}] {entry.content[:60]}...")

        print()
        print("4. Adding individual memories...")
        print("-" * 40)

        # Add a preference directly
        pref_entry = await memory_service.add_memory(
            content="Prefers Python over JavaScript",
            memory_type="preference",
            category="programming",
        )
        if pref_entry:
            print(f"  Added preference: {pref_entry.content}")

        # Add a message directly
        msg_entry = await memory_service.add_memory(
            content="Remember to review the API documentation",
            memory_type="message",
            session_id="notes",
            role="user",
        )
        if msg_entry:
            print(f"  Added message: {msg_entry.content}")

        print()
        print("5. Final memory search...")
        print("-" * 40)

        # Search for programming preferences
        print("\n  Query: 'programming preferences'")
        results = await memory_service.search_memory(
            query="programming preferences",
            limit=5,
        )
        for entry in results:
            print(f"    [{entry.memory_type}] {entry.content[:80]}...")

        print()
        print("=" * 60)
        print("Demo complete!")
        print("=" * 60)
        print()
        print("The memories are now stored in Neo4j. You can:")
        print("- Query them via Cypher in Neo4j Browser")
        print("- Use the MCP server for tool-based access")
        print("- Build your own ADK agent with this memory backend")

    # Alternative: Use MemoryIntegration for high-level operations
    # MemoryIntegration provides session strategies, auto-extraction,
    # and preference detection in a single convenience layer.
    #
    # integration = MemoryIntegration(
    #     neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
    #     neo4j_password=os.environ.get("NEO4J_PASSWORD", "password"),
    #     session_strategy=SessionStrategy.PER_DAY,
    #     auto_preferences=True,
    # )
    # async with integration:
    #     await integration.store_message("user", "I prefer dark mode")
    #     context = await integration.get_context()
    #     results = await integration.search("user preferences")


if __name__ == "__main__":
    asyncio.run(main())
