#!/usr/bin/env python3
"""Guided tour of `neo4j-agent-memory` against a local Neo4j (bolt backend).

Twelve numbered sections, each printing its own banner, in the order you would
meet them building an agent:

     1. Short-term memory — a conversation, message by message
     2. Long-term memory — preferences, POLE+O entities, facts
     3. Geocoding LOCATION entities        (optional: ENABLE_GEOCODING=1)
     4. Reasoning memory — a trace with a structured ``TraceOutcome``
     5. Combined context for an LLM prompt
     6. Batch message loading
     7. Session listing
     8. Metadata-filtered message search
     9. ``StreamingTraceRecorder`` — traces recorded as work happens
    10. Trace listing and tool statistics
    11. Sequential message links (FIRST_MESSAGE / NEXT_MESSAGE)
    12. Graph export and memory statistics

Every section is skippable: anything whose prerequisite is missing prints a
``SKIPPED`` line and the tour continues, so the script always reaches
``Demo complete!``.

Bolt-only: sections 3, 11 and 12 use `get_locations()`, `search_locations_near()`,
`get_graph()` and `get_stats()`, which the hosted NAMS backend does not expose.
For the hosted path see `examples/nams-quickstart/main.py`, which runs the same
core flow over `NamsSettings` with only an API key.

Requirements:
    - A Neo4j to talk to: `make neo4j-start` (or set NEO4J_URI / NEO4J_PASSWORD)
    - `uv sync --extra sentence-transformers` (local embeddings, no API key), or
      `uv sync --extra openai` plus OPENAI_API_KEY for OpenAI embeddings and
      LLM entity extraction

Configuration comes from `examples/.env` — copy `examples/.env.example`.

Run:
    uv run python examples/basic_usage.py
"""

from __future__ import annotations

import asyncio
import os

from _env import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME, OPENAI_API_KEY
from pydantic import SecretStr

from neo4j_agent_memory import (
    BoltMemoryClient,
    Conversation,
    Entity,
    ExtractionConfig,
    ExtractorType,
    GeocodingConfig,
    GeocodingProvider,
    Neo4jConfig,
    StreamingTraceRecorder,
    ToolCallStatus,
    connect,
)
from neo4j_agent_memory.config.settings import BoltSettings

# EntityRef and TraceOutcome are not re-exported from the package root.
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome

SESSION_ID = "demo-session"
BULK_SESSION_ID = "bulk-demo-session"
TOTAL_SECTIONS = 12


# =====================================================================
# Output helpers — each section announces itself so a skipped section is
# obvious in the transcript.
# =====================================================================
def section(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)


def skipped(number: int, title: str, reason: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)
    print(f"SKIPPED — {reason}")


# =====================================================================
# 1. SHORT-TERM MEMORY: conversation history
# =====================================================================
async def short_term_memory(memory: BoltMemoryClient) -> Conversation:
    section(1, "Short-term memory: conversation history")

    # Roles are plain strings throughout the examples ("user", "assistant",
    # "system"). The MessageRole enum is accepted too, and is what reads back
    # off a Message.
    await memory.short_term.add_message(
        SESSION_ID,
        "user",
        "Hi! I'm looking for restaurant recommendations. I love Italian food.",
    )
    await memory.short_term.add_message(
        SESSION_ID,
        "assistant",
        "I'd be happy to help! Any preferences on price range or location?",
    )
    await memory.short_term.add_message(
        SESSION_ID,
        "user",
        "Something mid-range in downtown. I'm vegetarian.",
    )

    conversation = await memory.short_term.get_conversation(SESSION_ID)
    print(f"Stored {len(conversation.messages)} messages")
    for message in conversation.messages:
        print(f"   {message.role.value:>9}: {message.content[:60]}")
    return conversation


# =====================================================================
# 2. LONG-TERM MEMORY: preferences, entities, facts
# =====================================================================
async def long_term_memory(memory: BoltMemoryClient) -> Entity:
    section(2, "Long-term memory: preferences, entities, facts")

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

    # Entities carry their type and subtype as PascalCase Neo4j labels, so the
    # node below is (:Entity:Location:Landmark) and you can query it with
    # `MATCH (l:Location:Landmark) RETURN l`.
    #
    # add_entity returns (entity, dedup_result): the second element reports
    # whether the write merged into a near-identical existing entity.
    downtown, dedup = await memory.long_term.add_entity(
        name="Downtown",
        entity_type="LOCATION",  # POLE+O type -> :Location label
        subtype="LANDMARK",  # Optional subtype -> :Landmark label
        description="User's preferred dining area",
    )
    if dedup.action == "merged":
        print(f"   'Downtown' merged into existing entity {dedup.matched_entity_name!r}")

    # Custom types become labels too, not just the five POLE+O types:
    # this is (:Entity:Cuisine:Italian), queried with `MATCH (c:Cuisine) RETURN c`.
    _, _ = await memory.long_term.add_entity(
        name="Italian Food",
        entity_type="CUISINE",
        subtype="ITALIAN",
        description="User's favorite cuisine type",
    )

    await memory.long_term.add_fact(
        subject="User",
        predicate="dietary_restriction",
        obj="vegetarian",
    )

    print("Stored 3 preferences, 2 entities, 1 fact")

    food_prefs = await memory.long_term.search_preferences("food", limit=5)
    print(f"search_preferences('food') -> {len(food_prefs)} hit(s)")
    for pref in food_prefs:
        print(f"   [{pref.category}] {pref.preference}")

    return downtown


# =====================================================================
# 3. GEOCODING: coordinates on LOCATION entities (optional)
# =====================================================================
async def geocoding(memory: BoltMemoryClient, *, enabled: bool) -> None:
    title = "Geocoding LOCATION entities"
    if not enabled:
        skipped(
            3,
            title,
            "set ENABLE_GEOCODING=1 to geocode via Nominatim "
            "(live network calls, 1 req/sec, and OpenStreetMap asks for a "
            "contact User-Agent in GEOCODING_USER_AGENT)",
        )
        return

    section(3, title)

    # Geocoding happens on write because GeocodingConfig(enabled=True) is in
    # the settings below.
    landmark, _ = await memory.long_term.add_entity(
        name="Empire State Building, New York",
        entity_type="LOCATION",
        subtype="LANDMARK",
        description="Famous skyscraper in Manhattan",
    )

    coords = await memory.long_term.get_location_coordinates(landmark.id)
    if coords:
        lat, lon = coords
        print(f"Geocoded {landmark.name}: {lat:.4f}, {lon:.4f}")
        nearby = await memory.long_term.search_locations_near(
            latitude=lat,
            longitude=lon,
            radius_km=10.0,
            limit=5,
        )
        print(f"   {len(nearby)} location(s) within 10km")
    else:
        print("   No coordinates returned (rate-limited, or no match)")

    all_locations = await memory.get_locations(has_coordinates=True, limit=100)
    print(f"   Locations with coordinates (all sessions): {len(all_locations)}")

    # Conversation-scoped: what a map view for one thread would show.
    session_locations = await memory.get_locations(
        session_id=SESSION_ID,
        has_coordinates=True,
        limit=100,
    )
    print(f"   Locations in session '{SESSION_ID}': {len(session_locations)}")


# =====================================================================
# 4. REASONING MEMORY: a trace, linked to the message that caused it
# =====================================================================
async def reasoning_memory(
    memory: BoltMemoryClient,
    conversation: Conversation,
    location: Entity,
) -> None:
    section(4, "Reasoning memory: trace, steps, tool call, outcome")

    last_message = conversation.messages[-1] if conversation.messages else None

    trace = await memory.reasoning.start_trace(
        SESSION_ID,
        task="Find vegetarian Italian restaurant in downtown",
        # Creates (ReasoningTrace)-[:INITIATED_BY]->(Message)
        triggered_by_message_id=last_message.id if last_message else None,
    )

    step1 = await memory.reasoning.add_step(
        trace.id,
        thought="Search for Italian restaurants downtown with vegetarian options",
        action="search_restaurants",
    )

    await memory.reasoning.record_tool_call(
        step1.id,
        tool_name="restaurant_search_api",
        arguments={"cuisine": "Italian", "location": "downtown", "dietary": "vegetarian"},
        result=[
            {"name": "La Trattoria Verde", "rating": 4.5},
            {"name": "Pasta Paradise", "rating": 4.3},
        ],
        status=ToolCallStatus.SUCCESS,
        duration_ms=250,
        # (ToolCall)-[:TRIGGERED_BY]->(Message)
        message_id=last_message.id if last_message else None,
        # (ReasoningStep)-[:TOUCHED]->(Entity) — the audit edge that answers
        # "which steps touched this entity?" in one hop. See examples/audit-trail/
        # for the full audit surface (@on_tool_call_recorded, error_kind).
        touched_entities=[EntityRef(name=location.name, type=location.type)],
    )

    step2 = await memory.reasoning.add_step(
        trace.id,
        thought="Two good options; La Trattoria Verde is rated higher.",
        action="recommend",
        observation="La Trattoria Verde is highly rated and fits all criteria",
    )

    # A structured TraceOutcome beats a bare outcome string: error_kind is
    # indexed for filtering, related_entities make the trace retrievable by
    # entity, and metrics ride along for evaluation.
    completed = await memory.reasoning.complete_trace(
        trace.id,
        outcome=TraceOutcome(
            success=True,
            summary="Recommended La Trattoria Verde",
            related_entities=[EntityRef(name=location.name, type=location.type)],
            metrics={"tools_called": 1.0, "candidates_considered": 2.0},
        ),
    )

    print(f"Recorded trace {completed.id} with 2 steps ({step1.id}, {step2.id})")
    print(f"   success={completed.success} outcome={completed.outcome!r}")


# =====================================================================
# 5. COMBINED CONTEXT for an LLM prompt
# =====================================================================
async def combined_context(memory: BoltMemoryClient) -> None:
    section(5, "Combined context for an LLM prompt")

    context = await memory.get_context("restaurant recommendation", session_id=SESSION_ID)
    print(context)


# =====================================================================
# 6. BATCH MESSAGE LOADING
# =====================================================================
async def batch_loading(memory: BoltMemoryClient) -> None:
    section(6, "Batch message loading")

    messages = [
        {"role": "user", "content": "What's the weather like?", "metadata": {"topic": "weather"}},
        {
            "role": "assistant",
            "content": "It's sunny and 72F today!",
            "metadata": {"topic": "weather"},
        },
        {"role": "user", "content": "Great! What should I wear?", "metadata": {"topic": "fashion"}},
        {
            "role": "assistant",
            "content": "Light clothing would be perfect.",
            "metadata": {"topic": "fashion"},
        },
    ]

    loaded = await memory.short_term.add_messages_batch(
        BULK_SESSION_ID,
        messages,
        batch_size=2,
        generate_embeddings=True,
        extract_entities=False,  # Skip extraction for speed
    )
    print(f"Bulk loaded {len(loaded)} messages into '{BULK_SESSION_ID}'")


# =====================================================================
# 7. SESSION LISTING
# =====================================================================
async def session_listing(memory: BoltMemoryClient) -> None:
    section(7, "Session listing")

    sessions = await memory.short_term.list_sessions(
        limit=10,
        order_by="updated_at",
        order_dir="desc",
    )
    print(f"{len(sessions)} session(s), most recently updated first:")
    for summary in sessions[:3]:
        print(f"   {summary.session_id}: {summary.message_count} messages")


# =====================================================================
# 8. METADATA-FILTERED MESSAGE SEARCH
# =====================================================================
async def metadata_search(memory: BoltMemoryClient) -> None:
    section(8, "Metadata-filtered message search")

    weather_messages = await memory.short_term.search_messages(
        "weather",
        session_id=BULK_SESSION_ID,
        metadata_filters={"topic": "weather"},
        limit=5,
    )
    print(f"{len(weather_messages)} message(s) matching 'weather' with metadata topic=weather")


# =====================================================================
# 9. STREAMING TRACE RECORDER
# =====================================================================
async def streaming_trace(memory: BoltMemoryClient) -> None:
    section(9, "StreamingTraceRecorder")

    # The recorder opens a trace on enter, times each step, and completes the
    # trace on exit — handy while tokens are still streaming.
    async with StreamingTraceRecorder(
        memory.reasoning, SESSION_ID, "Process customer inquiry"
    ) as recorder:
        step = await recorder.start_step(
            thought="Analyzing customer request",
            action="process_inquiry",
        )
        await recorder.record_tool_call(
            "analyze_text",
            {"text": "Customer asking about returns"},
            result={"intent": "return_policy", "confidence": 0.95},
        )
        await recorder.add_observation("Customer wants to know about return policy")

    print(f"Trace auto-completed with timing (step {step.id})")


# =====================================================================
# 10. TRACE LISTING AND TOOL STATISTICS
# =====================================================================
async def traces_and_tool_stats(memory: BoltMemoryClient) -> None:
    section(10, "Trace listing and tool statistics")

    traces = await memory.reasoning.list_traces(
        session_id=SESSION_ID,
        success_only=True,
        limit=5,
    )
    print(f"{len(traces)} successful trace(s) in session '{SESSION_ID}'")

    tool_stats = await memory.reasoning.get_tool_stats()
    print(f"Stats for {len(tool_stats)} tool(s):")
    for stat in tool_stats[:3]:
        print(f"   {stat.name}: {stat.total_calls} calls, {stat.success_rate:.0%} success")


# =====================================================================
# 11. SEQUENTIAL MESSAGE LINKS (verified with read-only Cypher)
# =====================================================================
async def message_linking(memory: BoltMemoryClient) -> None:
    section(11, "Sequential message links")

    # Messages are linked as they are written:
    #   (Conversation)-[:FIRST_MESSAGE]->(M1)-[:NEXT_MESSAGE]->(M2)->...
    # which makes ordered traversal and temporal queries cheap. For data
    # written before this existed, run `await memory.short_term.migrate_message_links()`.
    bulk_conversation = await memory.short_term.get_conversation(BULK_SESSION_ID)
    print(f"'{BULK_SESSION_ID}' has {len(bulk_conversation.messages)} messages")

    # `memory.query.cypher` is the read-only Cypher escape hatch (it works on
    # both backends and rejects writes before the round-trip).
    rows = await memory.query.cypher(
        """
        MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(:Message)-[link:NEXT_MESSAGE]->(:Message)
        RETURN first.content AS first_message, count(DISTINCT link) AS next_links
        """,
        {"session_id": BULK_SESSION_ID},
    )
    for row in rows:
        print(f"   FIRST_MESSAGE -> {row['first_message']!r}")
        print(f"   NEXT_MESSAGE links in the chain: {row['next_links']}")


# =====================================================================
# 12. GRAPH EXPORT AND MEMORY STATISTICS
# =====================================================================
async def graph_export_and_stats(memory: BoltMemoryClient) -> None:
    section(12, "Graph export and memory statistics")

    # get_graph() is the payload a visualization library (e.g. NVL) consumes.
    graph = await memory.get_graph(
        memory_types=["short_term", "long_term", "reasoning"],
        session_id=SESSION_ID,
        include_embeddings=False,
        limit=100,
    )
    print(f"Exported {len(graph.nodes)} nodes, {len(graph.relationships)} relationships")
    rel_counts: dict[str, int] = {}
    for rel in graph.relationships:
        rel_counts[rel.type] = rel_counts.get(rel.type, 0) + 1
    for rel_type, count in sorted(rel_counts.items()):
        print(f"   {rel_type}: {count}")

    # The cross-memory links from section 4 are not part of the export shape,
    # so count them directly. One hop each way is the whole point of storing
    # memory in a graph.
    link_rows = await memory.query.cypher(
        """
        MATCH (rt:ReasoningTrace {session_id: $session_id})
        OPTIONAL MATCH (rt)-[initiated:INITIATED_BY]->(:Message)
        OPTIONAL MATCH (rt)-[:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->
                       (:ToolCall)-[triggered:TRIGGERED_BY]->(:Message)
        OPTIONAL MATCH (rt)-[:HAS_STEP]->(:ReasoningStep)-[touched:TOUCHED]->(e:Entity)
        RETURN count(DISTINCT initiated) AS initiated_by,
               count(DISTINCT triggered) AS triggered_by,
               collect(DISTINCT e.name) AS touched_entities
        """,
        {"session_id": SESSION_ID},
    )
    for row in link_rows:
        print(f"   INITIATED_BY (trace -> message): {row['initiated_by']}")
        print(f"   TRIGGERED_BY (tool call -> message): {row['triggered_by']}")
        print(f"   TOUCHED (step -> entity): {row['touched_entities']}")

    stats = await memory.get_stats()
    print("Memory statistics:")
    for key in ("conversations", "messages", "entities", "preferences", "facts", "traces"):
        print(f"   {key}: {stats.get(key, 0)}")


# =====================================================================
# Configuration
# =====================================================================
def build_settings() -> BoltSettings | None:
    """Build BoltSettings, or return None when no embedder is available."""
    # v0.3+: the embedding model is a provider-string and the factory resolves
    # it to the best available adapter.
    if OPENAI_API_KEY:
        embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
        extraction = ExtractionConfig(extractor_type=ExtractorType.LLM)
        print(f"Embeddings: {embedding_model} | entity extraction: LLM")
    else:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            print("ERROR: no embedding provider available. Either:")
            print("  1. set OPENAI_API_KEY (see examples/.env.example), or")
            print("  2. uv sync --extra sentence-transformers")
            return None
        embedding_model = os.getenv(
            "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        extraction = ExtractionConfig(extractor_type=ExtractorType.NONE)
        print(f"Embeddings: {embedding_model} (no OPENAI_API_KEY) | entity extraction: off")

    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=SecretStr(NEO4J_PASSWORD),
        ),
        embedding=embedding_model,
        extraction=extraction,
        geocoding=GeocodingConfig(
            enabled=os.getenv("ENABLE_GEOCODING") == "1",
            provider=GeocodingProvider.NOMINATIM,
            cache_results=True,
            # OpenStreetMap's usage policy requires an identifying User-Agent
            # with a contact address; the default here is deliberately obvious.
            user_agent=os.getenv(
                "GEOCODING_USER_AGENT",
                "neo4j-agent-memory-example (set GEOCODING_USER_AGENT)",
            ),
        ),
    )


async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory — guided tour (bolt backend)")
    print("=" * 60)

    settings = build_settings()
    if settings is None:
        return

    # This tour exercises bolt-only features, so it connects through the typed
    # factory to get a concrete `BoltMemoryClient` rather than the
    # base-Protocol-typed `MemoryClient`.
    memory = await connect(settings)
    try:
        conversation = await short_term_memory(memory)
        location = await long_term_memory(memory)
        await geocoding(memory, enabled=settings.geocoding.enabled)
        await reasoning_memory(memory, conversation, location)
        await combined_context(memory)
        await batch_loading(memory)
        await session_listing(memory)
        await metadata_search(memory)
        await streaming_trace(memory)
        await traces_and_tool_stats(memory)
        await message_linking(memory)
        await graph_export_and_stats(memory)

        print("\nDemo complete!")
    finally:
        await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
