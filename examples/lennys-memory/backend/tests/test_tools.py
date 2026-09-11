"""Unit tests for agent tool implementations.

These tests verify that tools correctly filter data to only return
podcast content, excluding user chat messages and reasoning traces.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.tools import (
    # NEW: Entity management tools
    find_duplicate_entities,
    find_related_entities,
    find_similar_past_queries,
    # NEW: Conversation & summary tools
    get_conversation_context,
    get_entity_context,
    get_entity_provenance,
    get_episode_list,
    get_episode_summary,
    get_most_mentioned_entities,
    get_session_reasoning_history,
    get_speaker_list,
    get_tool_usage_patterns,
    learn_from_similar_task,
    list_podcast_sessions,
    search_by_speaker,
    search_entities,
    search_podcast_content,
    trigger_entity_enrichment,
)

from neo4j_agent_memory.memory.long_term import Entity
from neo4j_agent_memory.memory.reasoning import ToolStats
from neo4j_agent_memory.memory.short_term import Conversation, Message, MessageRole


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
        mock_agent_context.deps.client.query.cypher.assert_called_once()
        call_args = mock_agent_context.deps.client.query.cypher.call_args
        query = call_args[0][0]

        # Verify session_id prefix filter is in the query
        assert "session_id STARTS WITH 'lenny-podcast-'" in query

    @pytest.mark.asyncio
    async def test_includes_topic_filter_when_provided(self, mock_agent_context):
        """Verify topic filtering is applied when specified."""
        await search_by_speaker(mock_agent_context, "Brian Chesky", topic="growth")

        call_args = mock_agent_context.deps.client.query.cypher.call_args
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
        call_args = mock_agent_context.deps.client.query.cypher.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestFindRelatedEntities:
    """Tests for find_related_entities tool."""

    @pytest.mark.asyncio
    async def test_filters_to_podcast_sessions(self, mock_agent_context):
        """Verify entity co-occurrence query filters to podcast sessions."""
        # Mock the entity resolution to return a name
        mock_agent_context.deps.client.long_term.get_entity_by_name = AsyncMock(
            return_value=MagicMock(name="Airbnb")
        )

        await find_related_entities(mock_agent_context, "Airbnb")

        # Check that one of the execute_read calls contains the podcast filter
        calls = mock_agent_context.deps.client.query.cypher.call_args_list
        queries = [call[0][0] for call in calls]

        # The main co-occurrence query should filter to podcast sessions
        assert any("session_id STARTS WITH 'lenny-podcast-'" in q for q in queries)


class TestGetMostMentionedEntities:
    """Tests for get_most_mentioned_entities tool."""

    @pytest.mark.asyncio
    async def test_filters_to_podcast_sessions(self, mock_agent_context):
        """Verify mention count query filters to podcast sessions."""
        await get_most_mentioned_entities(mock_agent_context)

        call_args = mock_agent_context.deps.client.query.cypher.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query

    @pytest.mark.asyncio
    async def test_applies_entity_type_filter(self, mock_agent_context):
        """Verify entity type filter is applied when specified."""
        await get_most_mentioned_entities(mock_agent_context, entity_type="PERSON")

        call_args = mock_agent_context.deps.client.query.cypher.call_args
        params = call_args[0][1]

        assert params["type"] == "PERSON"


class TestSearchEntities:
    """Tests for search_entities tool."""

    @pytest.mark.asyncio
    async def test_returns_expected_fields(self, mock_agent_context):
        """Verify returned entity data structure.

        The tool ranks with the library's vector search and then reads the
        enrichment properties (which the Entity model does not surface) in one
        follow-up query -- no private embedder access.
        """
        entity = Entity(name="Product-Market Fit", type="CONCEPT", subtype="BUSINESS")
        mock_agent_context.deps.client.long_term.search_entities.return_value = [entity]
        mock_agent_context.deps.client.query.cypher = AsyncMock(
            return_value=[
                {
                    "id": str(entity.id),
                    "enriched_description": "Enriched description",
                    "wikipedia_url": "https://en.wikipedia.org/wiki/Product-market_fit",
                }
            ]
        )

        results = await search_entities(mock_agent_context, "product-market fit")

        assert len(results) == 1
        assert results[0]["name"] == "Product-Market Fit"
        assert results[0]["type"] == "CONCEPT"
        assert results[0]["description"] == "Enriched description"
        assert results[0]["enriched"] is True


class TestGetEpisodeList:
    """Tests for get_episode_list tool."""

    @pytest.mark.asyncio
    async def test_filters_by_session_prefix(self, mock_agent_context):
        """Verify query filters by lenny-podcast- session prefix."""
        await get_episode_list(mock_agent_context)

        call_args = mock_agent_context.deps.client.query.cypher.call_args
        query = call_args[0][0]

        assert "session_id STARTS WITH 'lenny-podcast-'" in query


class TestGetSpeakerList:
    """Tests for get_speaker_list tool."""

    @pytest.mark.asyncio
    async def test_filters_by_session_prefix(self, mock_agent_context):
        """Verify query filters by lenny-podcast- session prefix."""
        await get_speaker_list(mock_agent_context)

        call_args = mock_agent_context.deps.client.query.cypher.call_args
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


# =============================================================================
# Tests for NEW Enhanced Reasoning Memory Tools
# =============================================================================


class TestLearnFromSimilarTask:
    """Tests for learn_from_similar_task tool."""

    @pytest.mark.asyncio
    async def test_returns_full_trace_with_steps(self, mock_agent_context):
        """Verify the tool returns complete reasoning traces with steps."""
        # Mock trace returned by get_similar_traces
        mock_trace = MagicMock()
        mock_trace.id = "trace-123"
        mock_trace.task = "Find examples of growth strategies"
        mock_trace.metadata = {"similarity": 0.85}

        # Mock full trace with steps returned by get_trace_with_steps
        mock_full_trace = MagicMock()
        mock_full_trace.task = "Find examples of growth strategies"
        mock_full_trace.outcome = "Found 3 relevant examples"
        mock_full_trace.success = True
        mock_step = MagicMock()
        mock_step.step_number = 1
        mock_step.thought = "I should search for growth strategies"
        mock_step.action = "search_podcast_content"
        mock_step.observation = "Found 5 results"
        mock_step.tool_calls = []
        mock_full_trace.steps = [mock_step]

        mock_agent_context.deps.client.reasoning.get_similar_traces = AsyncMock(
            return_value=[mock_trace]
        )
        mock_agent_context.deps.client.reasoning.get_trace_with_steps = AsyncMock(
            return_value=mock_full_trace
        )

        results = await learn_from_similar_task(mock_agent_context, "growth strategies")

        assert len(results) == 1
        assert results[0]["task"] == "Find examples of growth strategies"
        assert results[0]["outcome"] == "Found 3 relevant examples"
        assert results[0]["success"] is True
        assert len(results[0]["steps"]) == 1
        assert results[0]["steps"][0]["thought"] == "I should search for growth strategies"

    @pytest.mark.asyncio
    async def test_uses_lower_threshold(self, mock_agent_context):
        """Verify the tool uses a lower similarity threshold for broader matching."""
        mock_agent_context.deps.client.reasoning.get_similar_traces = AsyncMock(return_value=[])

        await learn_from_similar_task(mock_agent_context, "test task")

        call_kwargs = mock_agent_context.deps.client.reasoning.get_similar_traces.call_args.kwargs
        assert call_kwargs.get("threshold") == 0.6  # Lower than default 0.7

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        results = await learn_from_similar_task(mock_agent_context, "test")

        assert len(results) == 1
        assert "error" in results[0]


class TestGetToolUsagePatterns:
    """Tests for get_tool_usage_patterns tool."""

    @pytest.mark.asyncio
    async def test_returns_tool_statistics(self, mock_agent_context):
        """Verify the tool returns formatted tool statistics."""
        # A real ToolStats instance: the tool used to read `tool_name` /
        # `success_count` / `failure_count`, which a bare MagicMock happily
        # invented. The real fields are name / successful_calls / failed_calls.
        stats = ToolStats(
            name="tool_search_podcast",
            total_calls=100,
            successful_calls=95,
            failed_calls=5,
            success_rate=0.95,
            avg_duration_ms=150.5,
        )

        mock_agent_context.deps.client.reasoning.get_tool_stats = AsyncMock(return_value=[stats])

        result = await get_tool_usage_patterns(mock_agent_context)

        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["tool_name"] == "tool_search_podcast"
        assert result["tools"][0]["total_calls"] == 100
        assert result["tools"][0]["success_rate"] == 95.0  # Converted to percentage
        assert "recommendation" in result

    @pytest.mark.asyncio
    async def test_filters_by_tool_name(self, mock_agent_context):
        """Verify filtering by specific tool name."""
        mock_agent_context.deps.client.reasoning.get_tool_stats = AsyncMock(return_value=[])

        await get_tool_usage_patterns(mock_agent_context, tool_name="tool_search_podcast")

        call_kwargs = mock_agent_context.deps.client.reasoning.get_tool_stats.call_args.kwargs
        assert call_kwargs.get("tool_name") == "tool_search_podcast"

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        result = await get_tool_usage_patterns(mock_agent_context)

        assert "error" in result


class TestGetSessionReasoningHistory:
    """Tests for get_session_reasoning_history tool."""

    @pytest.mark.asyncio
    async def test_returns_session_traces(self, mock_agent_context):
        """Verify the tool returns traces for a session."""
        from datetime import datetime

        mock_trace = MagicMock()
        mock_trace.id = "trace-123"
        mock_trace.task = "Find product examples"
        mock_trace.outcome = "Found examples"
        mock_trace.success = True
        mock_trace.started_at = datetime(2024, 1, 1, 12, 0, 0)
        mock_trace.completed_at = datetime(2024, 1, 1, 12, 1, 0)
        mock_trace.steps = [MagicMock(), MagicMock()]

        mock_agent_context.deps.client.reasoning.get_session_traces = AsyncMock(
            return_value=[mock_trace]
        )
        mock_agent_context.deps.session_id = "test-session"

        results = await get_session_reasoning_history(mock_agent_context)

        assert len(results) == 1
        assert results[0]["task"] == "Find product examples"
        assert results[0]["steps_count"] == 2

    @pytest.mark.asyncio
    async def test_uses_provided_session_id(self, mock_agent_context):
        """Verify custom session_id is used when provided."""
        mock_agent_context.deps.client.reasoning.get_session_traces = AsyncMock(return_value=[])

        await get_session_reasoning_history(mock_agent_context, session_id="custom-session")

        call_args = mock_agent_context.deps.client.reasoning.get_session_traces.call_args
        assert call_args.kwargs.get("session_id") == "custom-session"


# =============================================================================
# Tests for NEW Entity Management Tools
# =============================================================================


class TestFindDuplicateEntities:
    """Tests for find_duplicate_entities tool."""

    @pytest.mark.asyncio
    async def test_returns_duplicate_pairs(self, mock_agent_context):
        """Verify the tool returns potential duplicate entity pairs."""
        # Mock the fallback Cypher query result
        mock_agent_context.deps.client.query.cypher = AsyncMock(
            return_value=[
                {
                    "id1": "entity-1",
                    "name1": "Brian Chesky",
                    "type1": "PERSON",
                    "id2": "entity-2",
                    "name2": "Chesky",
                    "type2": "PERSON",
                    "similarity": 0.85,
                }
            ]
        )
        # No flagged SAME_AS pairs -> the tool falls back to fuzzy Cypher.
        mock_agent_context.deps.client.long_term.find_potential_duplicates.return_value = []

        results = await find_duplicate_entities(mock_agent_context)

        assert len(results) == 1
        assert results[0]["entity1"]["name"] == "Brian Chesky"
        assert results[0]["entity2"]["name"] == "Chesky"
        assert results[0]["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_filters_by_entity_type(self, mock_agent_context):
        """Verify entity type filtering."""
        mock_agent_context.deps.client.long_term.find_potential_duplicates.return_value = []
        mock_agent_context.deps.client.query.cypher = AsyncMock(return_value=[])

        await find_duplicate_entities(mock_agent_context, entity_type="PERSON")

        call_args = mock_agent_context.deps.client.query.cypher.call_args
        params = call_args[0][1]
        assert params["entity_type"] == "PERSON"

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        results = await find_duplicate_entities(mock_agent_context)

        assert len(results) == 1
        assert "error" in results[0]


class TestGetEntityProvenance:
    """Tests for get_entity_provenance tool."""

    @pytest.mark.asyncio
    async def test_returns_entity_sources(self, mock_agent_context):
        """Verify the tool returns provenance information."""
        mock_agent_context.deps.client.query.cypher = AsyncMock(
            return_value=[
                {
                    "entity_name": "Airbnb",
                    "entity_type": "ORGANIZATION",
                    "created_at": "2024-01-01T12:00:00",
                    "confidence": 0.95,
                    "enrichment_provider": "wikimedia",
                    "enriched_at": "2024-01-02T12:00:00",
                    "sources": [
                        {
                            "message_id": "msg-123",
                            "content_preview": "Airbnb was founded...",
                            "speaker": "Brian Chesky",
                            "session_id": "lenny-podcast-brian-chesky",
                            "relationship_type": "MENTIONED_IN",
                        }
                    ],
                }
            ]
        )

        result = await get_entity_provenance(mock_agent_context, "Airbnb")

        assert result["entity"]["name"] == "Airbnb"
        assert result["entity"]["type"] == "ORGANIZATION"
        assert result["enrichment"]["provider"] == "wikimedia"
        assert len(result["sources"]) == 1

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_entity(self, mock_agent_context):
        """Verify error when entity not found."""
        mock_agent_context.deps.client.query.cypher = AsyncMock(return_value=[])

        result = await get_entity_provenance(mock_agent_context, "Unknown Entity")

        assert "error" in result


class TestTriggerEntityEnrichment:
    """Tests for trigger_entity_enrichment tool."""

    @pytest.mark.asyncio
    async def test_returns_already_enriched_status(self, mock_agent_context):
        """Verify status for already enriched entities."""
        mock_entity = MagicMock()
        mock_entity.name = "Airbnb"
        mock_entity.type = "ORGANIZATION"
        # Entity model uses direct attributes and metadata dict, not properties
        mock_entity.enriched_description = "A travel company..."
        mock_entity.enrichment_provider = "wikimedia"
        mock_entity.wikipedia_url = "https://en.wikipedia.org/wiki/Airbnb"
        mock_entity.image_url = "https://example.com/airbnb.jpg"
        mock_entity.metadata = {
            "enriched_at": "2024-01-01T12:00:00",
        }

        mock_agent_context.deps.client.long_term.get_entity_by_name = AsyncMock(
            return_value=mock_entity
        )

        result = await trigger_entity_enrichment(mock_agent_context, "Airbnb")

        assert result["status"] == "already_enriched"
        assert result["entity_name"] == "Airbnb"
        assert "wikipedia_url" in result

    @pytest.mark.asyncio
    async def test_returns_enrichment_needed_status(self, mock_agent_context):
        """Verify status for entities needing enrichment."""
        mock_entity = MagicMock()
        mock_entity.name = "New Entity"
        mock_entity.type = "ORGANIZATION"
        # Entity model uses direct attributes - set to None for unenriched entity
        mock_entity.enriched_description = None
        mock_entity.enrichment_provider = None
        mock_entity.wikipedia_url = None
        mock_entity.image_url = None
        mock_entity.metadata = {}  # No enrichment data

        mock_agent_context.deps.client.long_term.get_entity_by_name = AsyncMock(
            return_value=mock_entity
        )

        result = await trigger_entity_enrichment(mock_agent_context, "New Entity")

        assert result["status"] == "enrichment_needed"
        assert "suggestion" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_entity(self, mock_agent_context):
        """Verify error when entity not found."""
        mock_agent_context.deps.client.long_term.get_entity_by_name = AsyncMock(return_value=None)

        result = await trigger_entity_enrichment(mock_agent_context, "Unknown")

        assert "error" in result


# =============================================================================
# Tests for NEW Conversation & Summary Tools
# =============================================================================


class TestGetConversationContext:
    """Tests for get_conversation_context tool."""

    @pytest.mark.asyncio
    async def test_returns_recent_messages(self, mock_agent_context):
        """Verify the tool returns recent conversation messages."""
        mock_agent_context.deps.client.short_term.get_conversation.return_value = Conversation(
            session_id="test-session",
            messages=[
                Message(
                    role=MessageRole.USER,
                    content="What did Brian Chesky say about growth?",
                )
            ],
        )
        mock_agent_context.deps.session_id = "test-session"

        results = await get_conversation_context(mock_agent_context)

        assert len(results) == 1
        assert results[0]["role"] == "user"
        assert "growth" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_truncates_long_content(self, mock_agent_context):
        """Verify long content is truncated."""
        mock_agent_context.deps.client.short_term.get_conversation.return_value = Conversation(
            session_id="test-session",
            messages=[Message(role=MessageRole.ASSISTANT, content="A" * 600)],
        )
        mock_agent_context.deps.session_id = "test-session"

        results = await get_conversation_context(mock_agent_context)

        assert len(results[0]["content"]) == 500

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        results = await get_conversation_context(mock_agent_context)

        assert len(results) == 1
        assert "error" in results[0]


class TestListPodcastSessions:
    """Tests for list_podcast_sessions tool."""

    @pytest.mark.asyncio
    async def test_filters_by_podcast_prefix(self, mock_agent_context):
        """Verify sessions are filtered by lenny-podcast- prefix."""
        mock_agent_context.deps.client.short_term.list_sessions = AsyncMock(
            side_effect=AttributeError("Method not found")
        )
        mock_agent_context.deps.client.query.cypher = AsyncMock(
            return_value=[
                {
                    "session_id": "lenny-podcast-brian-chesky",
                    "title": "Brian Chesky Episode",
                    "message_count": 150,
                    "created_at": None,
                    "updated_at": None,
                }
            ]
        )

        results = await list_podcast_sessions(mock_agent_context)

        # Verify query filters by prefix
        call_args = mock_agent_context.deps.client.query.cypher.call_args
        query = call_args[0][0]
        assert "lenny-podcast-" in query

        assert len(results) == 1
        assert results[0]["session_id"] == "lenny-podcast-brian-chesky"

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        results = await list_podcast_sessions(mock_agent_context)

        assert len(results) == 1
        assert "error" in results[0]


class TestGetEpisodeSummary:
    """Tests for get_episode_summary tool."""

    @pytest.mark.asyncio
    async def test_returns_episode_summary_fallback(self, mock_agent_context):
        """Verify the tool returns episode summary using fallback."""
        mock_agent_context.deps.client.short_term.get_conversation_summary = AsyncMock(
            side_effect=AttributeError("Method not found")
        )
        mock_agent_context.deps.client.query.cypher = AsyncMock(
            return_value=[
                {
                    "title": "Brian Chesky on Leadership",
                    "session_id": "lenny-podcast-brian-chesky",
                    "message_count": 150,
                    "entities": ["Airbnb", "Leadership", "Growth"],
                }
            ]
        )

        result = await get_episode_summary(mock_agent_context, "Brian Chesky")

        assert result["episode_guest"] == "Brian Chesky"
        assert result["session_id"] == "lenny-podcast-brian-chesky"
        assert result["message_count"] == 150
        assert "Airbnb" in result["key_entities"]

    @pytest.mark.asyncio
    async def test_converts_guest_name_to_session_id(self, mock_agent_context):
        """Verify guest name is converted to correct session_id format."""
        mock_agent_context.deps.client.short_term.get_conversation_summary = AsyncMock(
            side_effect=AttributeError("Method not found")
        )
        mock_agent_context.deps.client.query.cypher = AsyncMock(return_value=[])

        await get_episode_summary(mock_agent_context, "Brian Chesky")

        call_args = mock_agent_context.deps.client.query.cypher.call_args
        params = call_args[0][1]
        assert params["session_id"] == "lenny-podcast-brian-chesky"

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_episode(self, mock_agent_context):
        """Verify error when episode not found."""
        mock_agent_context.deps.client.short_term.get_conversation_summary = AsyncMock(
            side_effect=AttributeError("Method not found")
        )
        mock_agent_context.deps.client.query.cypher = AsyncMock(return_value=[])

        result = await get_episode_summary(mock_agent_context, "Unknown Guest")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_missing_client(self, mock_agent_context):
        """Verify error handling when client is unavailable."""
        mock_agent_context.deps.client = None

        result = await get_episode_summary(mock_agent_context, "Brian Chesky")

        assert "error" in result
