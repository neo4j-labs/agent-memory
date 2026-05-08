#!/usr/bin/env python3
"""
LangChain integration example for neo4j-agent-memory.

This example shows how to use Neo4j Agent Memory with LangChain:
- Using Neo4jAgentMemory as agent memory
- Using Neo4jMemoryRetriever for RAG

Requirements:
    - Neo4j running (or set NEO4J_URI in .env)
    - pip install neo4j-agent-memory[langchain,openai]
    - OPENAI_API_KEY environment variable set (or in .env)

Environment variables can be set in examples/.env file.
"""

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr


def load_env_files():
    """Load environment variables from .env files."""
    try:
        from dotenv import load_dotenv

        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            print(f"Loaded environment from {env_file}")

        parent_env = Path(__file__).parent.parent / ".env"
        if parent_env.exists():
            load_dotenv(parent_env)
    except ImportError:
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("\"'")
                        if key and key not in os.environ:
                            os.environ[key] = value
            print(f"Loaded environment from {env_file}")


load_env_files()

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig


async def main():
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        )
    )

    async with MemoryClient(settings) as client:
        # Pre-populate some memories
        session_id = "langchain-demo"

        await client.short_term.add_message(session_id, "user", "I prefer spicy food")
        await client.long_term.add_preference(
            "food", "Loves spicy dishes", context="Dining preferences"
        )
        # Add entity - type and subtype become PascalCase Neo4j node labels for efficient querying
        # This creates a node with labels (:Entity:Organization)
        # You can query with: MATCH (o:Organization) RETURN o
        # add_entity returns (entity, dedup_result); see enrichment_example.py for dedup usage.
        _, _ = await client.long_term.add_entity(
            name="Thai Kitchen",
            entity_type="ORGANIZATION",  # Becomes a node label
            subtype="RESTAURANT",  # Also becomes a node label (if valid POLE+O subtype)
            description="Favorite Thai restaurant",
        )

        print("=" * 60)
        print("Neo4j Agent Memory - LangChain Integration Demo")
        print("=" * 60)

        # Try to import LangChain
        try:
            from neo4j_agent_memory.integrations.langchain import (
                Neo4jAgentMemory,
                Neo4jMemoryRetriever,
            )
        except ImportError:
            print("\n❌ LangChain not installed.")
            print("   Install with: pip install neo4j-agent-memory[langchain]")
            return

        # =================================================================
        # Using Neo4jAgentMemory
        # =================================================================
        print("\n📝 Using Neo4jAgentMemory...")

        memory = Neo4jAgentMemory(
            memory_client=client,
            session_id=session_id,
            include_episodic=True,
            include_semantic=True,
            include_reasoning=True,
        )

        # Load memory variables (using async method directly since we're in async context)
        variables = await memory._load_memory_variables_async(
            {"input": "restaurant recommendation"}
        )

        print("Memory variables:")
        for key, value in variables.items():
            print(f"\n  {key}:")
            if isinstance(value, str):
                print(f"    {value[:200]}..." if len(value) > 200 else f"    {value}")
            else:
                print(f"    {value}")

        # Save new context (using async method directly)
        await memory._save_context_async(
            {"input": "What's a good Thai restaurant?"},
            {"output": "Based on your preferences, I recommend Thai Kitchen!"},
        )
        print("\n✅ Saved new interaction to memory")

        # =================================================================
        # Using Neo4jMemoryRetriever
        # =================================================================
        print("\n🔍 Using Neo4jMemoryRetriever...")

        retriever = Neo4jMemoryRetriever(
            memory_client=client,
            search_episodic=True,
            search_semantic=True,
            k=5,
        )

        # Retrieve relevant documents (using async method directly since we're in async context)
        docs = await retriever._get_relevant_documents_async("spicy food preferences")

        print(f"Retrieved {len(docs)} documents:")
        for doc in docs:
            print(f"\n  Type: {doc.metadata.get('type')}")
            print(f"  Content: {doc.page_content[:100]}...")

        print("\n✅ Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
