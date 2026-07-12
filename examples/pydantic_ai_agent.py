#!/usr/bin/env python3
"""
Pydantic AI integration example for neo4j-agent-memory.

This example shows how to use Neo4j Agent Memory with Pydantic AI:
- Using MemoryDependency for context injection
- Using memory tools for agent memory operations

Requirements:
    - Neo4j running (or set NEO4J_URI in .env)
    - pip install neo4j-agent-memory[pydantic-ai,openai]
    - OPENAI_API_KEY environment variable set (or in .env)

Environment variables can be set in examples/.env file.
"""

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr


def load_env_files() -> None:
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


async def main() -> None:
    # v0.3+: when you have a PydanticAI model already configured for your
    # agent, hand it to memory via llm_provider_from_pydantic_ai. The
    # extractor will use the same provider for entity extraction.
    llm_provider = None
    try:
        from pydantic_ai.models.openai import OpenAIChatModel

        from neo4j_agent_memory.integrations.pydantic_ai import (
            llm_provider_from_pydantic_ai,
        )

        agent_model = OpenAIChatModel("gpt-4o-mini")
        llm_provider = llm_provider_from_pydantic_ai(agent_model)
        print("Using shared PydanticAI model for memory extraction")
    except ImportError:
        # pydantic-ai not installed; fall through to the legacy default.
        pass

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        embedding="openai/text-embedding-3-small",
        llm=llm_provider,
    )

    async with MemoryClient(settings) as client:
        # Pre-populate some memories
        session_id = "pydantic-ai-demo"

        await client.long_term.add_preference("communication", "Prefers concise responses")
        await client.long_term.add_preference("food", "Vegetarian, loves Indian cuisine")

        print("=" * 60)
        print("Neo4j Agent Memory - Pydantic AI Integration Demo")
        print("=" * 60)

        # Import Pydantic AI integration
        from neo4j_agent_memory.integrations.pydantic_ai import (
            MemoryDependency,
            create_memory_tools,
        )

        # =================================================================
        # Using MemoryDependency
        # =================================================================
        print("\n📝 Using MemoryDependency...")

        deps = MemoryDependency(client=client, session_id=session_id)

        # Get context
        context = await deps.get_context("restaurant recommendation")
        print("Context for LLM:")
        print("-" * 40)
        print(context if context else "(no relevant context found)")
        print("-" * 40)

        # Save a preference
        await deps.add_preference(
            category="location",
            preference="Prefers restaurants in downtown area",
        )
        print("\n✅ Added location preference")

        # Search preferences
        prefs = await deps.search_preferences("food")
        print(f"\n🔍 Found {len(prefs)} food-related preferences:")
        for p in prefs:
            print(f"   [{p['category']}] {p['preference']}")

        # =================================================================
        # Using Memory Tools
        # =================================================================
        print("\n⚙️  Creating memory tools...")

        tools = create_memory_tools(client)
        print(f"Created {len(tools)} tools:")
        for tool in tools:
            print(f"   - {getattr(tool, '__name__', str(tool))}")

        # Use the search_memory tool
        search_result = await tools[0]("vegetarian food")
        print(f"\n🔍 Search result for 'vegetarian food':")
        print(search_result)

        # Use the save_preference tool
        save_result = await tools[1]("cuisine", "Also enjoys Mediterranean food")
        print(f"\n✅ {save_result}")

        # Use the recall_preferences tool
        recall_result = await tools[2]("food")
        print(f"\n📋 Recalled preferences for 'food':")
        print(recall_result)

        # =================================================================
        # Example with Pydantic AI Agent (if installed)
        # =================================================================
        try:
            from pydantic_ai import Agent, RunContext

            print("\n🤖 Creating Pydantic AI agent with memory...")

            agent = Agent(
                "openai:gpt-4o-mini",
                deps_type=MemoryDependency,
            )

            @agent.system_prompt
            async def system_prompt(ctx: RunContext[MemoryDependency]) -> str:
                # This would be called with the user's input
                context = await ctx.deps.get_context("restaurant")
                base_prompt = "You are a helpful restaurant recommendation assistant."
                if context:
                    return f"{base_prompt}\n\nContext from memory:\n{context}"
                return base_prompt

            print("✅ Agent created with dynamic memory-aware system prompt")

            # Note: To actually run the agent, you would do:
            # result = await agent.run("Find me a good restaurant", deps=deps)

        except ImportError:
            print("\n⚠️  Pydantic AI not fully installed for agent demo")
            print("   Install with: pip install pydantic-ai")

        # =================================================================
        # NEW FEATURE: record_agent_trace()
        # =================================================================
        print("\n📊 Demonstrating record_agent_trace()...")
        print("   This function automatically records a PydanticAI RunResult as a reasoning trace.")
        print("   Example usage:")
        print("   ")
        print("   from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace")
        print("   ")
        print("   result = await agent.run('Find me a restaurant', deps=deps)")
        print("   trace = await record_agent_trace(")
        print("       client.reasoning,")
        print("       session_id='user-123',")
        print("       result=result,")
        print("       task='Find restaurant recommendation',")
        print("   )")
        print("   ")
        print("   # The trace now contains all tool calls from the agent run!")

        # Import to show it's available
        from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace  # noqa: F401

        print("\n✅ record_agent_trace() is available for automatic trace recording")

        print("\n✅ Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
