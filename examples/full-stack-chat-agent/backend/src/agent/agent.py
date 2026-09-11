"""PydanticAI news chat agent definition.

Targets PydanticAI 2.x. The 2.x shapes used here:

* ``@agent.instructions`` is the dynamic-prompt hook (``@agent.system_prompt``
  is legacy), and ``ctx.prompt`` is this turn's user message — so the memory
  context injected into the prompt is *relevant to the question asked* rather
  than the result of an empty query.
* The model is a provider-prefixed string from ``AGENT_MODEL``. ``openai:``
  selects the Responses API; ``openai-chat:`` selects Chat Completions.
* Streaming runs through ``agent.run_stream_events()`` (see
  ``src/api/routes/chat.py``), not ``run_stream``.
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any

from pydantic_ai import Agent, RunContext

from src.agent.dependencies import AgentDeps
from src.agent.tools import (
    execute_cypher,
    get_database_schema,
    get_news_by_topic,
    get_recent_news,
    get_topics,
    search_news,
    search_news_by_date_range,
    search_news_by_location,
    search_news_multi_topic,
    vector_search_news,
)
from src.config import get_settings

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are a helpful news research assistant with access to a comprehensive news database.

You help users find, analyze, and understand news articles. You can:
- Search for news by keywords or semantic similarity
- Filter news by topic, location, or date range
- Explore the database schema and run custom read-only Cypher queries
- Provide summaries and insights about news trends

When answering questions:
1. Use the appropriate search tools to find relevant articles
2. Summarize key findings clearly
3. Cite specific articles when relevant
4. Offer to dig deeper if the user wants more information

Be conversational and helpful. If you're unsure about something, say so and offer alternatives.
"""


def _as_json(result: Any) -> str:
    """Serialize a tool result to JSON (tools return str to avoid schema churn)."""
    return json.dumps(result, default=str)


@lru_cache
def get_news_agent() -> Agent[AgentDeps, str]:
    """Create and return the news agent (cached)."""
    settings = get_settings()
    # pydantic-ai reads OPENAI_API_KEY from the environment.
    if settings.openai_api_key.get_secret_value():
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key.get_secret_value()

    agent: Agent[AgentDeps, str] = Agent(
        settings.agent_model,
        deps_type=AgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
    )

    @agent.instructions
    async def add_memory_context(ctx: RunContext[AgentDeps]) -> str:
        """Inject memory context relevant to *this* turn's question."""
        if not (ctx.deps.memory_enabled and ctx.deps.client is not None):
            return ""
        try:
            memory_context = await ctx.deps.get_context(str(ctx.prompt or ""))
        except Exception as e:
            logger.warning("Could not load memory context: %s", e)
            return ""
        if not memory_context:
            return ""
        return f"""
## Your Memory Context

The following information is from your memory about this conversation and user:

{memory_context}

Use this context to personalize your responses and maintain continuity across conversations.
"""

    # Tools return JSON strings rather than structured objects to keep the
    # tool-result shape stable for the frontend's tool-call cards.
    @agent.tool
    async def tool_search_news(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 10,
    ) -> str:
        """Search news articles using full-text search.

        Args:
            query: The search query string.
            limit: Maximum number of results (default 10).

        Returns:
            JSON string with list of matching articles.
        """
        return _as_json(await search_news(ctx, query, limit))

    @agent.tool
    async def tool_vector_search_news(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 10,
    ) -> str:
        """Search news articles using semantic vector similarity.

        Args:
            query: The search query for semantic matching.
            limit: Maximum number of results (default 10).

        Returns:
            JSON string with list of similar articles.
        """
        return _as_json(await vector_search_news(ctx, query, limit))

    @agent.tool
    async def tool_get_recent_news(
        ctx: RunContext[AgentDeps],
        limit: int = 10,
        days: int = 7,
    ) -> str:
        """Get the most recent news articles.

        Args:
            limit: Maximum number of results (default 10).
            days: Number of days to look back (default 7).

        Returns:
            JSON string with list of recent articles.
        """
        return _as_json(await get_recent_news(ctx, limit, days))

    @agent.tool
    async def tool_get_news_by_topic(
        ctx: RunContext[AgentDeps],
        topic: str,
        limit: int = 10,
    ) -> str:
        """Get news articles by topic.

        Args:
            topic: The topic to filter by.
            limit: Maximum number of results (default 10).

        Returns:
            JSON string with list of articles matching the topic.
        """
        return _as_json(await get_news_by_topic(ctx, topic, limit))

    @agent.tool
    async def tool_get_topics(ctx: RunContext[AgentDeps]) -> str:
        """Get all available news topics with article counts.

        Returns:
            JSON string with list of topics and article counts.
        """
        return _as_json(await get_topics(ctx))

    @agent.tool
    async def tool_search_news_by_location(
        ctx: RunContext[AgentDeps],
        location: str,
        limit: int = 10,
    ) -> str:
        """Search news articles by geographic location.

        Args:
            location: The location to search for.
            limit: Maximum number of results (default 10).

        Returns:
            JSON string with list of articles related to the location.
        """
        return _as_json(await search_news_by_location(ctx, location, limit))

    @agent.tool
    async def tool_search_news_by_date_range(
        ctx: RunContext[AgentDeps],
        start_date: str,
        end_date: str,
        limit: int = 20,
    ) -> str:
        """Search news articles within a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            limit: Maximum number of results (default 20).

        Returns:
            JSON string with list of articles in the date range.
        """
        return _as_json(await search_news_by_date_range(ctx, start_date, end_date, limit))

    @agent.tool
    async def tool_search_news_multi_topic(
        ctx: RunContext[AgentDeps],
        topics: list[str],
        limit: int = 20,
    ) -> str:
        """Search news articles that match any of the given topics or keywords.

        Use this for broad research queries spanning multiple subjects, people, or events.

        Args:
            topics: List of topics/keywords to search for (e.g., ["MLK", "Obama", "civil rights"]).
            limit: Maximum number of results (default 20).

        Returns:
            JSON string with list of articles matching any of the topics.
        """
        return _as_json(await search_news_multi_topic(ctx, topics, limit))

    @agent.tool
    async def tool_get_database_schema(ctx: RunContext[AgentDeps]) -> str:
        """Get the news graph database schema.

        Returns:
            JSON string describing the database schema.
        """
        return _as_json(await get_database_schema(ctx))

    @agent.tool
    async def tool_execute_cypher(
        ctx: RunContext[AgentDeps],
        query: str,
    ) -> str:
        """Execute a read-only Cypher query against the news graph.

        Args:
            query: The Cypher query to execute. Must be read-only.

        Returns:
            JSON string with query results.
        """
        return _as_json(await execute_cypher(ctx, query))

    return agent


def news_agent() -> Agent[AgentDeps, str]:
    """Get the news agent instance (backwards-compatible alias)."""
    return get_news_agent()
