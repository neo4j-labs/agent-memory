"""Phase 7 tests: NAMS-flavored Pydantic AI helpers.

These tests don't require ``pydantic_ai`` to be installed —
``nams_memory_tools()`` returns plain ``Callable`` objects (async
functions) that operate on the supplied :class:`MemoryClient`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from neo4j_agent_memory.integrations.pydantic_ai.memory import nams_memory_tools


@pytest.fixture
def mock_client() -> MagicMock:
    """A mock MemoryClient with the long_term and query layers stubbed."""
    client = MagicMock()
    client.long_term = MagicMock()
    client.short_term = MagicMock()
    client.reasoning = MagicMock()
    client.query = MagicMock()
    client.long_term.set_entity_feedback = AsyncMock()
    client.long_term.get_entity_history = AsyncMock(return_value=[])
    client.long_term.get_entity_provenance = AsyncMock(return_value={})
    client.query.cypher = AsyncMock(return_value=[])
    # Base tools also need the layers stubbed to avoid attribute errors.
    client.short_term.search_messages = AsyncMock(return_value=[])
    client.long_term.search_entities = AsyncMock(return_value=[])
    client.long_term.search_preferences = AsyncMock(return_value=[])
    client.long_term.add_preference = AsyncMock()
    client.reasoning.get_similar_traces = AsyncMock(return_value=[])
    return client


class TestToolList:
    def test_returns_base_plus_platinum_tools(self, mock_client):
        tools = nams_memory_tools(mock_client)
        names = [t.__name__ for t in tools]
        # Base 3 tools from create_memory_tools:
        assert "search_memory" in names
        assert "save_preference" in names
        assert "recall_preferences" in names
        # Platinum extras:
        assert "set_entity_feedback" in names
        assert "get_entity_history" in names
        assert "get_entity_provenance" in names
        assert "cypher_query" in names
        # 3 base + 4 platinum = 7
        assert len(tools) == 7

    def test_all_tools_are_callable(self, mock_client):
        for tool in nams_memory_tools(mock_client):
            assert callable(tool)


class TestSetEntityFeedback:
    async def test_calls_long_term_method(self, mock_client):
        tools = nams_memory_tools(mock_client)
        set_feedback = next(t for t in tools if t.__name__ == "set_entity_feedback")
        result = await set_feedback(entity_id="e1", feedback="positive", user_identifier="alice")
        mock_client.long_term.set_entity_feedback.assert_awaited_once_with("e1", "positive")
        assert "positive" in result
        assert "e1" in result


class TestGetEntityHistory:
    async def test_formats_history_entries(self, mock_client):
        mock_client.long_term.get_entity_history = AsyncMock(
            return_value=[
                {"conversation_id": "c1", "mention_count": 5},
                {"conversation_id": "c2", "mention_count": 1},
            ]
        )
        tools = nams_memory_tools(mock_client)
        get_history = next(t for t in tools if t.__name__ == "get_entity_history")
        result = await get_history("e1", limit=10)
        assert "c1" in result
        assert "mentions=5" in result

    async def test_empty_history(self, mock_client):
        mock_client.long_term.get_entity_history = AsyncMock(return_value=[])
        tools = nams_memory_tools(mock_client)
        get_history = next(t for t in tools if t.__name__ == "get_entity_history")
        result = await get_history("e1")
        assert "No history" in result


class TestGetEntityProvenance:
    async def test_formats_provenance(self, mock_client):
        mock_client.long_term.get_entity_provenance = AsyncMock(
            return_value={
                "sources": [{"message_id": "m1"}, {"message_id": "m2"}],
                "extractors": [{"name": "GLiNER", "version": "1.0"}],
            }
        )
        tools = nams_memory_tools(mock_client)
        get_prov = next(t for t in tools if t.__name__ == "get_entity_provenance")
        result = await get_prov("e1")
        assert "Sources" in result
        assert "m1" in result
        assert "GLiNER" in result


class TestCypherQuery:
    async def test_returns_json_rows(self, mock_client):
        mock_client.query.cypher = AsyncMock(return_value=[{"n": 1}, {"n": 2}])
        tools = nams_memory_tools(mock_client)
        cypher = next(t for t in tools if t.__name__ == "cypher_query")
        result = await cypher("MATCH (n) RETURN n LIMIT 2")
        parsed = json.loads(result)
        assert parsed == [{"n": 1}, {"n": 2}]
        mock_client.query.cypher.assert_awaited_once_with("MATCH (n) RETURN n LIMIT 2", None)

    async def test_with_params(self, mock_client):
        tools = nams_memory_tools(mock_client)
        cypher = next(t for t in tools if t.__name__ == "cypher_query")
        await cypher("MATCH (n {id: $id}) RETURN n", {"id": "abc"})
        mock_client.query.cypher.assert_awaited_once_with(
            "MATCH (n {id: $id}) RETURN n", {"id": "abc"}
        )


class TestPropagatesNotSupportedOnBolt:
    """When the underlying MemoryClient is bolt, NotSupportedError propagates."""

    async def test_set_entity_feedback_propagates(self, mock_client):
        from neo4j_agent_memory.core.exceptions import NotSupportedError

        mock_client.long_term.set_entity_feedback = AsyncMock(
            side_effect=NotSupportedError(
                backend="bolt", method="LongTermMemory.set_entity_feedback"
            )
        )
        tools = nams_memory_tools(mock_client)
        set_feedback = next(t for t in tools if t.__name__ == "set_entity_feedback")
        with pytest.raises(NotSupportedError):
            await set_feedback("e1", "positive")
