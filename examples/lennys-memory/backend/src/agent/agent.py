"""PydanticAI podcast exploration agent."""

import json
import os
from functools import lru_cache

from pydantic_ai import Agent, RunContext

from src.agent.dependencies import AgentDeps
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import (
    PREFERENCE_CATEGORIES,
    calculate_location_distances,
    # NEW: Entity management tools
    find_duplicate_entities,
    find_location_path,
    find_locations_near,
    find_related_entities,
    find_similar_past_queries,
    # NEW: Conversation & summary tools
    get_conversation_context,
    get_entity_context,
    get_entity_provenance,
    # Podcast search tools
    get_episode_list,
    get_episode_locations,
    get_episode_summary,
    get_location_clusters,
    get_memory_stats,
    get_most_mentioned_entities,
    get_session_reasoning_history,
    get_speaker_list,
    get_tool_usage_patterns,
    # Preferences and reasoning memory tools
    get_user_preferences,
    # NEW: Enhanced reasoning memory tools
    learn_from_similar_task,
    list_podcast_sessions,
    # NEW: Memory graph search
    memory_graph_search,
    search_by_episode,
    search_by_speaker,
    # Entity query tools
    search_entities,
    # Location query tools
    search_locations,
    search_podcast_content,
    trigger_entity_enrichment,
)
from src.config import get_settings


@lru_cache
def get_podcast_agent() -> Agent[AgentDeps, str]:
    """Create and return the podcast agent (cached)."""
    # Ensure OpenAI API key is set in environment
    settings = get_settings()
    if settings.openai_api_key.get_secret_value():
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key.get_secret_value()

    # Model id comes from AGENT_MODEL (default ``openai:gpt-5-mini``). On
    # PydanticAI 2.x the ``openai:`` prefix selects the Responses API; use
    # ``openai-chat:`` for Chat Completions.
    agent = Agent(
        settings.agent_model,
        deps_type=AgentDeps,
        instructions=SYSTEM_PROMPT,
    )

    # Add dynamic memory context via decorator (``instructions`` rather than
    # ``system_prompt``: instructions are re-evaluated per run and are not
    # replayed from ``message_history``).
    @agent.instructions
    async def add_memory_context(ctx: RunContext[AgentDeps]) -> str:
        """Add memory context to instructions: preferences and similar traces."""
        if not ctx.deps.memory_enabled or ctx.deps.client is None:
            return ""

        context_parts = []

        try:
            # Get general memory context. The query drives a vector search, so
            # pass the actual user turn -- an empty string embeds nothing useful.
            memory_context = await ctx.deps.get_context(query=ctx.deps.current_query or "")
            if memory_context:
                context_parts.append(f"""## Conversation Memory

The following information is from your memory about this conversation and user:

{memory_context}""")
        except Exception:
            pass

        try:
            # Enumerate stored preferences by category. ``search_preferences``
            # is a vector search and cannot express "list all preferences".
            preferences = []
            for category in PREFERENCE_CATEGORIES:
                preferences.extend(
                    await ctx.deps.client.long_term.get_preferences_by_category(category, limit=5)
                )
            if preferences:
                pref_list = [f"- {p.category}: {p.preference}" for p in preferences]
                context_parts.append(f"""## User Preferences

The user has expressed these preferences:
{chr(10).join(pref_list)}

Tailor your responses to match these preferences where relevant.""")
        except Exception:
            pass

        try:
            # Get similar past traces if there's a current query
            if ctx.deps.current_query:
                similar_traces = await ctx.deps.client.reasoning.get_similar_traces(
                    task=ctx.deps.current_query,
                    limit=2,
                    success_only=True,
                )
                if similar_traces:
                    trace_items = [
                        f"- Query: {t.task}, Outcome: {t.outcome}" for t in similar_traces
                    ]
                    context_parts.append(f"""## Relevant Past Interactions

You've successfully handled similar queries before. Consider these approaches:
{chr(10).join(trace_items)}""")
        except Exception:
            pass

        if context_parts:
            return "\n\n".join(context_parts)
        return ""

    # Register tools
    @agent.tool
    async def tool_search_podcast(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 10,
    ) -> str:
        """Search podcast transcripts using semantic/vector search.

        Uses AI embeddings to find semantically similar content, not just keyword matches.
        Good for finding discussions about topics even when exact words aren't used.

        Args:
            query: Natural language search (e.g., "how to find product market fit", "scaling teams", "founder mental health")
            limit: Maximum number of results to return
        """
        result = await search_podcast_content(ctx, query, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_search_by_speaker(
        ctx: RunContext[AgentDeps],
        speaker: str,
        topic: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search for what a specific speaker said, with semantic topic search.

        Best results when you provide BOTH speaker AND topic - uses vector search on topic.
        Supports fuzzy speaker matching (e.g., "Chesky" matches "Brian Chesky").

        Args:
            speaker: Speaker name - partial matches work (e.g., "Chesky", "Lenny", "Andy")
            topic: Topic to search for semantically (e.g., "hiring mistakes", "scaling challenges")
            limit: Maximum number of results
        """
        result = await search_by_speaker(ctx, speaker, topic, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_search_episode(
        ctx: RunContext[AgentDeps],
        guest_name: str,
        topic: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search within a specific episode by guest name.

        Use this to explore what was discussed in a particular episode.

        Args:
            guest_name: Name of the podcast guest (e.g., "Brian Chesky", "Andy Johns")
            topic: Optional topic to search for within the episode
            limit: Maximum number of results
        """
        result = await search_by_episode(ctx, guest_name, topic, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_list_episodes(ctx: RunContext[AgentDeps]) -> str:
        """Get list of all podcast episodes available.

        Use this to see which episodes/guests are available to explore.
        """
        result = await get_episode_list(ctx)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_list_speakers(ctx: RunContext[AgentDeps]) -> str:
        """Get list of all speakers who appear in the podcast.

        This includes Lenny and all guests.
        """
        result = await get_speaker_list(ctx)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_stats(ctx: RunContext[AgentDeps]) -> str:
        """Get statistics about the loaded podcast data.

        Use this to understand how much content is available.
        """
        result = await get_memory_stats(ctx)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Entity Query Tools
    # ==========================================================================

    @agent.tool
    async def tool_search_entities(
        ctx: RunContext[AgentDeps],
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search for entities using semantic/vector search with fuzzy matching.

        Finds people, companies, concepts, or events even with partial names or synonyms.
        Use this to discover entity names before calling tool_get_entity_context.

        Args:
            query: Search term - can be partial name or concept (e.g., "Chesky", "Y Combinator", "product growth")
            entity_type: Optional filter - PERSON, ORGANIZATION, LOCATION, EVENT, CONCEPT
            limit: Maximum number of results
        """
        result = await search_entities(ctx, query, entity_type, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_entity_context(
        ctx: RunContext[AgentDeps],
        entity_name: str,
    ) -> str:
        """Get detailed context about an entity with Wikipedia enrichment.

        Supports fuzzy name matching - partial names like "Chesky" will find "Brian Chesky".
        Returns enriched data (Wikipedia summary, image, URL) if available, plus podcast mentions.

        Args:
            entity_name: Full or partial entity name (e.g., "Brian Chesky", "Chesky", "Airbnb")
        """
        result = await get_entity_context(ctx, entity_name)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_find_related_entities(
        ctx: RunContext[AgentDeps],
        entity_name: str,
        limit: int = 10,
    ) -> str:
        """Find entities related to a given entity through co-occurrence.

        Use this to explore connections between people, companies, and concepts.

        Args:
            entity_name: Starting entity (e.g., "Airbnb", "growth")
            limit: Maximum number of related entities
        """
        result = await find_related_entities(ctx, entity_name, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_top_entities(
        ctx: RunContext[AgentDeps],
        entity_type: str | None = None,
        limit: int = 10,
    ) -> str:
        """Get the most frequently mentioned entities across all podcasts.

        Use this to discover key themes, influential figures, or important companies.

        Args:
            entity_type: Filter by PERSON, ORGANIZATION, CONCEPT, LOCATION, etc.
            limit: Number of results (default 10)
        """
        result = await get_most_mentioned_entities(ctx, entity_type, limit)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Location Query Tools (Map View Integration)
    # ==========================================================================

    @agent.tool
    async def tool_search_locations(
        ctx: RunContext[AgentDeps],
        query: str | None = None,
        episode_guest: str | None = None,
        limit: int = 20,
    ) -> str:
        """Search for locations mentioned in podcasts.

        Returns locations with coordinates for map visualization.
        The frontend can display these with markers, clusters, or heatmap.

        Args:
            query: Optional search term (e.g., "Silicon Valley", "Europe")
            episode_guest: Optional guest name to filter by episode
            limit: Maximum number of results
        """
        result = await search_locations(ctx, query, episode_guest, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_find_locations_near(
        ctx: RunContext[AgentDeps],
        location_name: str,
        radius_km: float = 500.0,
        limit: int = 20,
    ) -> str:
        """Find other locations mentioned near a given location.

        Useful for understanding geographic context of discussions.
        Results can be visualized on the map with a radius overlay.

        Args:
            location_name: Reference location (e.g., "San Francisco")
            radius_km: Search radius in kilometers (default 500km)
            limit: Maximum number of results
        """
        result = await find_locations_near(ctx, location_name, radius_km, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_episode_locations(
        ctx: RunContext[AgentDeps],
        episode_guest: str,
    ) -> str:
        """Get all locations mentioned in a specific episode.

        Returns a geographic profile of the episode's content.

        Args:
            episode_guest: Guest name (e.g., "Brian Chesky")
        """
        result = await get_episode_locations(ctx, episode_guest)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_find_location_path(
        ctx: RunContext[AgentDeps],
        from_location: str,
        to_location: str,
    ) -> str:
        """Find the shortest path between two locations in the knowledge graph.

        Shows how locations are connected through entities, messages, and conversations.
        The frontend can visualize this as a path overlay on the map.

        Args:
            from_location: Starting location name
            to_location: Destination location name
        """
        result = await find_location_path(ctx, from_location, to_location)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_location_clusters(
        ctx: RunContext[AgentDeps],
        episode_guest: str | None = None,
    ) -> str:
        """Analyze geographic clusters of locations mentioned in podcasts.

        Returns location density by country/region, useful for understanding
        which geographic areas are most discussed. Works with the heatmap view.

        Args:
            episode_guest: Optional guest to filter by
        """
        result = await get_location_clusters(ctx, episode_guest)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_calculate_distances(
        ctx: RunContext[AgentDeps],
        locations: list[str],
    ) -> str:
        """Calculate distances between multiple locations.

        Returns pairwise distances in kilometers using great-circle calculation.
        Useful for understanding the geographic scope of discussions.

        Args:
            locations: List of location names to measure between (e.g., ["San Francisco", "New York", "London"])
        """
        result = await calculate_location_distances(ctx, locations)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Preferences and Reasoning Memory Tools
    # ==========================================================================

    @agent.tool
    async def tool_get_user_preferences(ctx: RunContext[AgentDeps]) -> str:
        """Get the current user's stored preferences.

        Use this to understand user interests and personalize responses.
        Returns preferences about content interests, format preferences, etc.
        """
        result = await get_user_preferences(ctx)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_find_similar_queries(
        ctx: RunContext[AgentDeps],
        current_query: str,
        limit: int = 3,
    ) -> str:
        """Find similar queries from past conversations and their outcomes.

        Use this to learn from successful past interactions.

        Args:
            current_query: The current user query
            limit: Maximum number of similar traces to return
        """
        result = await find_similar_past_queries(ctx, current_query, limit)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Enhanced Reasoning Memory Tools (NEW)
    # ==========================================================================

    @agent.tool
    async def tool_learn_from_similar_task(
        ctx: RunContext[AgentDeps],
        task_description: str,
        limit: int = 1,
    ) -> str:
        """Get full reasoning traces from similar past tasks for few-shot learning.

        Returns complete reasoning steps so you can learn the approach that worked.
        Use this when facing a complex or unfamiliar task.

        Args:
            task_description: Description of the current task
            limit: Number of similar traces to return
        """
        result = await learn_from_similar_task(ctx, task_description, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_tool_patterns(
        ctx: RunContext[AgentDeps],
        tool_name: str | None = None,
        limit: int = 10,
    ) -> str:
        """Analyze tool usage patterns to understand which tools are most effective.

        Returns success rates, average durations, and recommendations.
        Use this to optimize your tool selection strategy.

        Args:
            tool_name: Optional specific tool to analyze
            limit: Maximum number of tools to include
        """
        result = await get_tool_usage_patterns(ctx, tool_name, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_reasoning_history(
        ctx: RunContext[AgentDeps],
        session_id: str | None = None,
        limit: int = 10,
    ) -> str:
        """Get reasoning traces from a session to understand conversation history.

        Use this to see what reasoning approaches were used previously.

        Args:
            session_id: Session ID to query (defaults to current session)
            limit: Maximum number of traces to return
        """
        result = await get_session_reasoning_history(ctx, session_id, limit)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Entity Management Tools (NEW)
    # ==========================================================================

    @agent.tool
    async def tool_find_duplicates(
        ctx: RunContext[AgentDeps],
        entity_type: str | None = None,
        limit: int = 20,
    ) -> str:
        """Find potential duplicate entities that may need merging.

        Useful for data quality analysis and entity resolution.

        Args:
            entity_type: Filter by type (PERSON, ORGANIZATION, etc.)
            limit: Maximum number of duplicate pairs to return
        """
        result = await find_duplicate_entities(ctx, entity_type, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_entity_provenance(
        ctx: RunContext[AgentDeps],
        entity_name: str,
    ) -> str:
        """Get the source/provenance information for an entity.

        Shows which messages the entity was extracted from.
        Use this to understand where information came from.

        Args:
            entity_name: Name of the entity to get provenance for
        """
        result = await get_entity_provenance(ctx, entity_name)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_check_enrichment(
        ctx: RunContext[AgentDeps],
        entity_name: str,
        provider: str = "wikimedia",
    ) -> str:
        """Check enrichment status for an entity or request enrichment.

        Shows if entity has Wikipedia data or needs enrichment.

        Args:
            entity_name: Name of the entity to check
            provider: Enrichment provider ("wikimedia" or "diffbot")
        """
        result = await trigger_entity_enrichment(ctx, entity_name, provider)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Conversation & Summary Tools (NEW)
    # ==========================================================================

    @agent.tool
    async def tool_get_conversation_context(
        ctx: RunContext[AgentDeps],
        limit: int = 10,
    ) -> str:
        """Get recent conversation history for context.

        Use this to recall what was discussed earlier in the conversation.

        Args:
            limit: Maximum number of messages to return
        """
        result = await get_conversation_context(ctx, limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_list_podcast_sessions(
        ctx: RunContext[AgentDeps],
        sort_by: str = "message_count",
        limit: int = 20,
    ) -> str:
        """List available podcast sessions with metadata.

        Shows episodes with message counts and timestamps.

        Args:
            sort_by: Sort field ("message_count", "created_at", "updated_at")
            limit: Maximum number of sessions
        """
        result = await list_podcast_sessions(ctx, sort_by, "desc", limit)
        return json.dumps(result, default=str)

    @agent.tool
    async def tool_get_episode_summary(
        ctx: RunContext[AgentDeps],
        episode_guest: str,
    ) -> str:
        """Get a summary of a podcast episode including key topics and entities.

        Use this to get a quick overview before diving into specific content.

        Args:
            episode_guest: Guest name (e.g., "Brian Chesky")
        """
        result = await get_episode_summary(ctx, episode_guest)
        return json.dumps(result, default=str)

    # ==========================================================================
    # Memory Graph Search Tool (NEW)
    # ==========================================================================

    @agent.tool
    async def tool_memory_graph_search(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 10,
        include_related_entities: bool = True,
        max_related_per_entity: int = 5,
    ) -> str:
        """Search memory using semantic similarity, then explore the knowledge graph.

        This is the most powerful search tool - it combines:
        1. Vector search to find semantically similar podcast segments (more messages)
        2. Graph traversal to find ALL entities mentioned in those segments
        3. Relationship expansion to discover ALL connected entities for each entity found

        Use this when you want to explore a topic comprehensively, seeing both
        what was said AND the entities/concepts involved. The result is a rich graph
        visualization showing messages, their mentioned entities, and all related entities.

        Args:
            query: Natural language search (e.g., "challenges of scaling", "product-market fit")
            limit: Number of messages to find (default 10)
            include_related_entities: Whether to expand to related entities (default True)
            max_related_per_entity: Max related entities per found entity (default 5)
        """
        result = await memory_graph_search(
            ctx, query, limit, include_related_entities, max_related_per_entity
        )
        return json.dumps(result, default=str)

    return agent


# For convenience
def podcast_agent() -> Agent[AgentDeps, str]:
    """Get the podcast agent instance."""
    return get_podcast_agent()
