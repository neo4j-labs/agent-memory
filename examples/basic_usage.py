#!/usr/bin/env python3
"""
Basic usage example for neo4j-agent-memory.

This example demonstrates the core functionality of the memory system:
- Adding messages to short-term memory
- Storing preferences in long-term memory
- Recording reasoning traces in reasoning memory
- Getting combined context for LLM prompts

Requirements:
    - Neo4j running (or set NEO4J_URI in .env)
    - pip install neo4j-agent-memory[openai]
    - OPENAI_API_KEY environment variable set (or in .env)

Environment variables can be set in examples/.env file.
"""

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr


def load_env_files():
    """Load environment variables from .env files."""
    # Try to load from dotenv if available
    try:
        from dotenv import load_dotenv

        # Load from examples/.env (same directory as this script)
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            print(f"Loaded environment from {env_file}")

        # Also try parent directory .env
        parent_env = Path(__file__).parent.parent / ".env"
        if parent_env.exists():
            load_dotenv(parent_env)
            print(f"Loaded environment from {parent_env}")
    except ImportError:
        # dotenv not installed, try manual parsing
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


# Load environment variables before anything else
load_env_files()

from neo4j_agent_memory import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
    GeocodingConfig,
    GeocodingProvider,
    MemoryClient,
    MemorySettings,
    MessageRole,
    Neo4jConfig,
    ToolCallStatus,
)


async def main():
    # Configure embedding provider based on available API keys
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if openai_api_key:
        # Use OpenAI embeddings if API key is available
        embedding_config = EmbeddingConfig(
            provider=EmbeddingProvider.OPENAI,
            model="text-embedding-3-small",
        )
        extraction_config = ExtractionConfig(
            extractor_type=ExtractorType.LLM,
        )
        print("Using OpenAI embeddings and LLM extraction")
    else:
        # Fall back to sentence-transformers (requires: pip install neo4j-agent-memory[sentence-transformers])
        try:
            import sentence_transformers  # noqa: F401

            embedding_config = EmbeddingConfig(
                provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
                model="all-MiniLM-L6-v2",
                dimensions=384,
            )
            extraction_config = ExtractionConfig(
                extractor_type=ExtractorType.NONE,  # Disable extraction without LLM
            )
            print("Using sentence-transformers embeddings (no OPENAI_API_KEY found)")
            print("Note: Entity extraction disabled without OpenAI API key")
        except ImportError:
            print("ERROR: No embedding provider available!")
            print("Either:")
            print("  1. Set OPENAI_API_KEY environment variable, or")
            print(
                "  2. Install sentence-transformers: pip install neo4j-agent-memory[sentence-transformers]"
            )
            return

    # Configure geocoding (optional - enable to add coordinates to LOCATION entities)
    # Uses Nominatim (OpenStreetMap) by default - free but rate-limited to 1 req/sec
    # For higher accuracy, use GeocodingProvider.GOOGLE with an API key
    geocoding_config = GeocodingConfig(
        enabled=True,
        provider=GeocodingProvider.NOMINATIM,
        cache_results=True,  # Cache results to avoid repeated API calls
    )

    # Configure the memory client
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        embedding=embedding_config,
        extraction=extraction_config,
        geocoding=geocoding_config,
    )

    async with MemoryClient(settings) as memory:
        session_id = "demo-session"

        print("=" * 60)
        print("Neo4j Agent Memory - Basic Usage Demo")
        print("=" * 60)

        # =================================================================
        # SHORT-TERM MEMORY: Conversation History
        # =================================================================
        print("\n📝 Adding messages to short-term memory...")

        await memory.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Hi! I'm looking for restaurant recommendations. I love Italian food.",
        )

        await memory.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "I'd be happy to help you find Italian restaurants! Do you have any specific preferences like price range or location?",
        )

        await memory.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Something mid-range in downtown. I'm vegetarian.",
        )

        # Retrieve conversation
        conversation = await memory.short_term.get_conversation(session_id)
        print(f"✅ Stored {len(conversation.messages)} messages")

        # =================================================================
        # LONG-TERM MEMORY: Facts and Preferences
        # =================================================================
        print("\n🧠 Adding facts and preferences to long-term memory...")

        # Add user preferences
        await memory.long_term.add_preference(
            category="food",
            preference="Loves Italian cuisine",
            context="Restaurant recommendations",
        )

        await memory.long_term.add_preference(
            category="dietary",
            preference="Vegetarian diet",
            context="All meals",
        )

        await memory.long_term.add_preference(
            category="budget",
            preference="Prefers mid-range restaurants",
        )

        # Add entities (using POLE+O types)
        # Entities are stored with type and subtype as PascalCase Neo4j node labels for efficient querying.
        # Example: This creates a node with labels (:Entity:Location:Landmark)
        # You can then query with: MATCH (l:Location:Landmark) RETURN l
        # add_entity returns (entity, dedup_result); discard the dedup_result here.
        _, _ = await memory.long_term.add_entity(
            name="Downtown",
            entity_type="LOCATION",  # POLE+O type (becomes :Location label)
            subtype="LANDMARK",  # Optional subtype (becomes :Landmark label)
            description="User's preferred dining area",
        )

        # Custom entity types also become PascalCase labels (not just POLE+O types)
        # Example: This creates a node with labels (:Entity:Cuisine:Italian)
        _, _ = await memory.long_term.add_entity(
            name="Italian Food",
            entity_type="CUISINE",  # Custom type -> becomes :Cuisine label
            subtype="ITALIAN",  # Custom subtype -> becomes :Italian label
            description="User's favorite cuisine type",
        )
        # Query custom types with: MATCH (c:Cuisine) RETURN c

        # Add facts
        await memory.long_term.add_fact(
            subject="User",
            predicate="dietary_restriction",
            obj="vegetarian",
        )

        print("✅ Stored preferences, entities, and facts")

        # =================================================================
        # GEOCODING: Automatic coordinate lookup for LOCATION entities
        # =================================================================
        print("\n🌍 Demonstrating location geocoding...")

        # Add a location entity - it will be automatically geocoded
        # because we enabled geocoding in the settings
        location_entity, _ = await memory.long_term.add_entity(
            name="Empire State Building, New York",
            entity_type="LOCATION",
            subtype="LANDMARK",
            description="Famous skyscraper in Manhattan",
        )

        # Check if coordinates were added
        coords = await memory.long_term.get_location_coordinates(location_entity.id)
        if coords:
            lat, lon = coords
            print(f"✅ Geocoded: {location_entity.name}")
            print(f"   Coordinates: {lat:.4f}, {lon:.4f}")

            # Search for nearby locations (within 10km)
            nearby = await memory.long_term.search_locations_near(
                latitude=lat,
                longitude=lon,
                radius_km=10.0,
                limit=5,
            )
            print(f"   Found {len(nearby)} location(s) within 10km")
        else:
            print("   (Geocoding may be rate-limited or location not found)")

        # Get all locations with coordinates
        all_locations = await memory.get_locations(
            has_coordinates=True,
            limit=100,
        )
        print(f"   Total locations with coordinates: {len(all_locations)}")

        # Get locations filtered by session (conversation-scoped)
        # This is useful for map views that show only locations mentioned in a conversation
        session_locations = await memory.get_locations(
            session_id=session_id,  # Only locations mentioned in this conversation
            has_coordinates=True,
            limit=100,
        )
        print(f"   Locations in session '{session_id}': {len(session_locations)}")

        # Search preferences
        print("\n🔍 Searching for food-related preferences...")
        food_prefs = await memory.long_term.search_preferences("food", limit=5)
        for pref in food_prefs:
            print(f"   [{pref.category}] {pref.preference}")

        # =================================================================
        # REASONING MEMORY: Reasoning Traces
        # =================================================================
        print("\n⚙️  Recording reasoning trace...")

        # Get the last user message to link the trace to it
        last_message = conversation.messages[-1] if conversation.messages else None

        # Start a trace linked to the triggering message
        trace = await memory.reasoning.start_trace(
            session_id,
            task="Find vegetarian Italian restaurant in downtown",
            triggered_by_message_id=last_message.id if last_message else None,
        )

        # Add reasoning steps
        step1 = await memory.reasoning.add_step(
            trace.id,
            thought="I need to search for Italian restaurants in downtown that offer vegetarian options",
            action="search_restaurants",
        )

        # Record tool call linked to the message that triggered it
        await memory.reasoning.record_tool_call(
            step1.id,
            tool_name="restaurant_search_api",
            arguments={
                "cuisine": "Italian",
                "location": "downtown",
                "dietary": "vegetarian",
            },
            result=[
                {"name": "La Trattoria Verde", "rating": 4.5},
                {"name": "Pasta Paradise", "rating": 4.3},
            ],
            status=ToolCallStatus.SUCCESS,
            duration_ms=250,
            message_id=last_message.id if last_message else None,  # Link tool call to message
        )

        step2 = await memory.reasoning.add_step(
            trace.id,
            thought="Found two good options. La Trattoria Verde has better ratings.",
            action="recommend",
            observation="La Trattoria Verde is highly rated and fits all criteria",
        )

        # Complete the trace
        await memory.reasoning.complete_trace(
            trace.id,
            outcome="Recommended La Trattoria Verde",
            success=True,
        )

        print("✅ Recorded reasoning trace with 2 steps")

        # =================================================================
        # COMBINED CONTEXT
        # =================================================================
        print("\n📋 Getting combined context for LLM prompt...")

        context = await memory.get_context(
            "restaurant recommendation",
            session_id=session_id,
        )

        print("-" * 40)
        print(context)
        print("-" * 40)

        # =================================================================
        # NEW FEATURES: Batch Loading
        # =================================================================
        print("\n🚀 Demonstrating batch message loading...")

        bulk_session = "bulk-demo-session"
        messages = [
            {
                "role": "user",
                "content": "What's the weather like?",
                "metadata": {"topic": "weather"},
            },
            {
                "role": "assistant",
                "content": "It's sunny and 72°F today!",
                "metadata": {"topic": "weather"},
            },
            {
                "role": "user",
                "content": "Great! What should I wear?",
                "metadata": {"topic": "fashion"},
            },
            {
                "role": "assistant",
                "content": "Light clothing would be perfect.",
                "metadata": {"topic": "fashion"},
            },
        ]

        loaded_messages = await memory.short_term.add_messages_batch(
            bulk_session,
            messages,
            batch_size=2,
            generate_embeddings=True,
            extract_entities=False,  # Skip for speed
        )
        print(f"✅ Bulk loaded {len(loaded_messages)} messages")

        # =================================================================
        # NEW FEATURES: Session Listing
        # =================================================================
        print("\n📋 Listing sessions...")

        sessions = await memory.short_term.list_sessions(
            limit=10,
            order_by="updated_at",
            order_dir="desc",
        )
        print(f"Found {len(sessions)} sessions:")
        for s in sessions[:3]:
            print(f"   - {s.session_id}: {s.message_count} messages")

        # =================================================================
        # NEW FEATURES: Metadata Search
        # =================================================================
        print("\n🔎 Searching messages with metadata filters...")

        weather_messages = await memory.short_term.search_messages(
            "weather",
            session_id=bulk_session,
            metadata_filters={"topic": "weather"},
            limit=5,
        )
        print(f"Found {len(weather_messages)} messages about weather")

        # =================================================================
        # NEW FEATURES: StreamingTraceRecorder
        # =================================================================
        print("\n⏱️  Using StreamingTraceRecorder for trace recording...")

        from neo4j_agent_memory import StreamingTraceRecorder

        async with StreamingTraceRecorder(
            memory.reasoning, session_id, "Process customer inquiry"
        ) as recorder:
            # Record steps during streaming
            step = await recorder.start_step(
                thought="Analyzing customer request",
                action="process_inquiry",
            )
            await recorder.record_tool_call(
                "analyze_text",
                {"text": "Customer asking about returns"},
                {"intent": "return_policy", "confidence": 0.95},
            )
            await recorder.add_observation("Customer wants to know about return policy")

        print("✅ Streaming trace recorded automatically with timing")

        # =================================================================
        # NEW FEATURES: List Traces
        # =================================================================
        print("\n📊 Listing reasoning traces...")

        traces = await memory.reasoning.list_traces(
            session_id=session_id,
            success_only=True,
            limit=5,
        )
        print(f"Found {len(traces)} successful traces for session")

        # =================================================================
        # NEW FEATURES: Tool Stats (optimized)
        # =================================================================
        print("\n🔧 Getting optimized tool statistics...")

        tool_stats = await memory.reasoning.get_tool_stats()
        print(f"Found stats for {len(tool_stats)} tools:")
        for stat in tool_stats[:3]:
            print(f"   - {stat.name}: {stat.total_calls} calls, {stat.success_rate:.0%} success")

        # =================================================================
        # NEW FEATURES: Message Linking (FIRST_MESSAGE, NEXT_MESSAGE)
        # =================================================================
        print("\n🔗 Message linking in graph...")

        # Messages are now automatically linked sequentially in the graph:
        # - FIRST_MESSAGE: Conversation -> first Message
        # - NEXT_MESSAGE: Message -> Message (sequential chain)
        # This enables efficient traversal and temporal queries.

        # The bulk session already has linked messages - verify the chain
        bulk_conv = await memory.short_term.get_conversation(bulk_session)
        print(f"Bulk session has {len(bulk_conv.messages)} messages linked sequentially")
        print("Graph structure: (Conversation)-[:FIRST_MESSAGE]->(M1)-[:NEXT_MESSAGE]->(M2)->...")

        # For existing data without links, use the migration utility:
        # migrated = await memory.short_term.migrate_message_links()
        # print(f"Migrated {len(migrated)} conversations to use message linking")

        # =================================================================
        # NEW FEATURES: Graph Export
        # =================================================================
        print("\n🌐 Exporting memory graph...")

        graph = await memory.get_graph(
            memory_types=["short_term", "long_term"],
            session_id=session_id,
            include_embeddings=False,
            limit=100,
        )
        print(f"Graph export: {len(graph.nodes)} nodes, {len(graph.relationships)} relationships")

        # Count message linking relationships in the exported graph
        next_message_rels = [r for r in graph.relationships if r.type == "NEXT_MESSAGE"]
        first_message_rels = [r for r in graph.relationships if r.type == "FIRST_MESSAGE"]
        initiated_by_rels = [r for r in graph.relationships if r.type == "INITIATED_BY"]
        triggered_by_rels = [r for r in graph.relationships if r.type == "TRIGGERED_BY"]
        print(f"   FIRST_MESSAGE relationships: {len(first_message_rels)}")
        print(f"   NEXT_MESSAGE relationships: {len(next_message_rels)}")
        print(f"   INITIATED_BY (trace->message): {len(initiated_by_rels)}")
        print(f"   TRIGGERED_BY (tool_call->message): {len(triggered_by_rels)}")

        # =================================================================
        # MEMORY STATS
        # =================================================================
        print("\n📊 Memory statistics:")
        stats = await memory.get_stats()
        print(f"   Conversations: {stats.get('conversations', 0)}")
        print(f"   Messages: {stats.get('messages', 0)}")
        print(f"   Entities: {stats.get('entities', 0)}")
        print(f"   Preferences: {stats.get('preferences', 0)}")
        print(f"   Facts: {stats.get('facts', 0)}")
        print(f"   Reasoning Traces: {stats.get('traces', 0)}")

        print("\n✅ Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
