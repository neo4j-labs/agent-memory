"""Unit tests for agent tool implementations.

These tests verify that tools correctly filter data to only return
podcast content, excluding user chat messages and reasoning traces.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.tools import (
    find_related_entities,
    find_similar_past_queries,
    get_entity_context,
    get_episode_list,
    get_most_mentioned_entities,
    get_speaker_list,
    search_by_speaker,
    search_entities,
    search_podcast_content,
)


class TestSearchPodcastContent:
    """Tests for search_podcast_content tool."""

    @pytest.mark.asyncio
    async def test_uses_metadata_filter_for_podcast_source(self, mock_agent_context):
        """Verify search_podcast_content filters by source=lenny_podcast."""
        await search_podcast_content(mock_agent_context, "test query", limit=5)

        # Verify metadata_filters was passed
        mock_agent_context.deps.client.short_term.search_messages.assert_called_once()
        call_kwargs = mock_agent_context.deps.client.short_term.search_messages.call_args.kwargs
        assert call_kwargs.get("metadata_filters") == {"source": "lenny_podcast"}

    @pytest.mark.asyncio
    async def test_returns_expected_fields(self, mock_agent_context, mock_message):
        """Verify returned data structure contains expected fields."""
        # Create mock message with podcast metadata
        msg = mock_message(
            content="Test content about product-market fit",
            speaker="Brian Chesky",
            episode_guest="Brian Chesky",
            timestamp="00:15:30",
            similarity=0.85,
        )

        mock_agent_context.deps.client.short_term.search_messages = AsyncMock(return_value=[msg])

        results = await search_podcast_content(mock_agent_context, "product-market fit")

        assert len(results) == 1
        assert results[0]["speaker"] == "Brian Chesky"
        assert results[0]["episode_guest"] == "Brian Chesky"
        assert results[0]["relevance"] == 0.85
        assert results[0]["timestamp"] == "00:15:30"
        assert "content" in results[0]

    @pytest.mark.asyncio
    async def test_truncates_long_content(self, mock_agent_context, mock_message):
        """Verify long content is truncated to 500 characters."""
        long_content = "A" * 600
        msg = mock_message(content=long_content)

        mock_agent_context.deps.client.short_term.search_messages = AsyncMock(return_value=[msg])

        results = await search_podcast_content(mock_agent_context, "test")

        assert len(results[0]["content"]) == 503  # 500 + "..."
        assert results[0]["content"].endswith("...")

    @pytest.mark.asyncio
    async def test_handles_missing_metadata(self, mock_agent_context):
        """Verify graceful handling of missing metadata fields."""
        msg = MagicMock()
        msg.content = "Test content"
        msg.metadata = {}  # No metadata

        mock_agent_context.deps.client.short_term.search_messages = AsyncMock(return_value=[msg])

        results = await search_podcast_content(mock_agent_context, "test")

        assert len(results) == 1
        assert results[0]["speaker"] == "Unknown"
        assert results[0]["episode_guest"] == "Unknown"
        assert results[0]["timestamp"] == ""
        assert results[0]["relevance"] == 0

    @pytest.mark.asyncio
    async def test_returns_error_when_client_unavailable(self, mock_agent_context):
        """Verify error response when memory client is not available."""
        mock_agent_context.deps.client = None

        results = await search_podcast_content(mock_agent_context, "test")

        assert len(results) == 1
        assert "error" in results[0]

    @pytest.mark.asyncio
    async def test_handles_search_exception(self, mock_agent_context):
        """Verify graceful handling of search exceptions."""
        mock_agent_context.deps.client.short_term.search_messages = AsyncMock(
            side_effect=Exception("Database error")
        )

        results = await search_podcast_content(mock_agent_context, "test")

        assert len(results) == 1
        assert "error" in results[0]
        assert "Search failed" in results[0]["error"]


class TestSearchBySpeaker:
    """Tests for search_by_speaker tool."""

    @pytest.mark.asyncio
    async def test_filters_by_session_prefix(self, mock_agent_context):
        """Verify query filters by lenny-podcast- session prefix."""
        await search_by_speaker(mock_agent_context, "Brian Chesky")

        # Check the Cypher query was called
        mock_agent_context.deps.client._client.execute_read.assert_called_once()
        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        # Verify session_id prefix filter is in the query
        assert "session_id STARTS WITH 'lenny-podcast-'" in query

    @pytest.mark.asyncio
    async def test_includes_topic_filter_when_provided(self, mock_agent_context):
        """Verify topic filtering is applied when specified."""
        await search_by_speaker(mock_agent_context, "Brian Chesky", topic="growth")

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        assert "toLower(m.content) CONTAINS toLower($topic)" in query
        assert params["topic"] == "growth"


class TestGetEntityContext:
    """Tests for get_entity_context tool."""

    @pytest.mark.asyncio
    async def test_filters_mentions_to_podcast_sessions(self, mock_agent_context):
        """Verify entity mentions are filtered to podcast sessions only."""
        # Mock entity lookup
        mock_entity = MagicMock()
        mock_entity.name = "Airbnb"
        mock_entity.type = "ORGANIZATION"
        mock_entity.subtype = None
        mock_entity.description = "Travel company"
        mock_entity.enriched_description = None
        mock_entity.wikipedia_url = None
        mock_agent_context.deps.client.long_term.get_entity_by_name = AsyncMock(
            return_value=mock_entity
        )

        await get_entity_context(mock_agent_context, "Airbnb")

        # Check the mentions query filters by session prefix
        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestFindRelatedEntities:
    """Tests for find_related_entities tool."""

    @pytest.mark.asyncio
    async def test_filters_to_podcast_sessions(self, mock_agent_context):
        """Verify entity co-occurrence query filters to podcast sessions."""
        await find_related_entities(mock_agent_context, "Airbnb")

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestGetMostMentionedEntities:
    """Tests for get_most_mentioned_entities tool."""

    @pytest.mark.asyncio
    async def test_filters_to_podcast_sessions(self, mock_agent_context):
        """Verify mention count query filters to podcast sessions."""
        await get_most_mentioned_entities(mock_agent_context)

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query

    @pytest.mark.asyncio
    async def test_applies_entity_type_filter(self, mock_agent_context):
        """Verify entity type filter is applied when specified."""
        await get_most_mentioned_entities(mock_agent_context, entity_type="PERSON")

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        params = call_args[0][1]

        assert params["type"] == "PERSON"


class TestSearchEntities:
    """Tests for search_entities tool."""

    @pytest.mark.asyncio
    async def test_returns_expected_fields(self, mock_agent_context):
        """Verify returned entity data structure."""
        mock_entity = MagicMock()
        mock_entity.name = "Product-Market Fit"
        mock_entity.type = "CONCEPT"
        mock_entity.subtype = "business"
        mock_entity.description = "A business concept"
        mock_entity.wikipedia_url = "https://en.wikipedia.org/wiki/Product-market_fit"
        mock_entity.enriched_description = "Enriched description"

        mock_agent_context.deps.client.long_term.search_entities = AsyncMock(
            return_value=[mock_entity]
        )

        results = await search_entities(mock_agent_context, "product-market fit")

        assert len(results) == 1
        assert results[0]["name"] == "Product-Market Fit"
        assert results[0]["type"] == "CONCEPT"
        assert results[0]["enriched"] is True


class TestGetEpisodeList:
    """Tests for get_episode_list tool."""

    @pytest.mark.asyncio
    async def test_filters_by_session_prefix(self, mock_agent_context):
        """Verify query filters by lenny-podcast- session prefix."""
        await get_episode_list(mock_agent_context)

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestGetSpeakerList:
    """Tests for get_speaker_list tool."""

    @pytest.mark.asyncio
    async def test_filters_by_session_prefix(self, mock_agent_context):
        """Verify query filters by lenny-podcast- session prefix."""
        await get_speaker_list(mock_agent_context)

        call_args = mock_agent_context.deps.client._client.execute_read.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestFindSimilarPastQueries:
    """Tests for find_similar_past_queries tool.

    Note: This tool intentionally returns reasoning traces as its purpose
    is to find similar past queries and their resolutions.
    """

    @pytest.mark.asyncio
    async def test_returns_traces_intentionally(self, mock_agent_context):
        """Verify the tool returns reasoning traces (expected behavior)."""
        mock_trace = MagicMock()
        mock_trace.task = "Find product-market fit examples"
        mock_trace.outcome = "Found 5 relevant examples"
        mock_trace.success = True
        mock_trace.steps = [MagicMock(), MagicMock()]

        mock_agent_context.deps.client.reasoning.get_similar_traces = AsyncMock(
            return_value=[mock_trace]
        )

        results = await find_similar_past_queries(mock_agent_context, "product-market fit")

        assert len(results) == 1
        assert results[0]["task"] == "Find product-market fit examples"
        assert results[0]["outcome"] == "Found 5 relevant examples"
        assert results[0]["success"] is True
        assert results[0]["steps_count"] == 2

    @pytest.mark.asyncio
    async def test_filters_to_successful_traces_only(self, mock_agent_context):
        """Verify only successful traces are returned."""
        await find_similar_past_queries(mock_agent_context, "test query")

        call_kwargs = mock_agent_context.deps.client.reasoning.get_similar_traces.call_args.kwargs
        assert call_kwargs.get("success_only") is True
