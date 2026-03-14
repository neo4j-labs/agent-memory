"""Unit tests for FastMCP tool registration and execution.

Tests the _tools.py module that defines the 6 core MCP tools.
Uses FastMCP's Client for in-memory testing.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from tests.unit.mcp.conftest import create_tool_server, make_mock_client


class TestToolRegistration:
    """Tests that all 6 tools register correctly on a FastMCP server."""

    @pytest.fixture
    def server(self):
        """Create a tool server with a mock client."""
        return create_tool_server(make_mock_client())

    @pytest.mark.asyncio
    async def test_registers_6_tools(self, server):
        """All 6 memory tools should be registered."""
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_tool_names(self, server):
        """Tools should have the expected names."""
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == {
                "memory_search",
                "memory_store",
                "entity_lookup",
                "conversation_history",
                "graph_query",
                "add_reasoning_trace",
            }

    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self, server):
        """Every tool should have a non-empty description."""
        async with Client(server) as client:
            tools = await client.list_tools()
            for tool in tools:
                assert tool.description, f"Tool {tool.name} has no description"

    @pytest.mark.asyncio
    async def test_memory_search_has_required_param(self, server):
        """memory_search should require a 'query' parameter."""
        async with Client(server) as client:
            tools = await client.list_tools()
            search_tool = next(t for t in tools if t.name == "memory_search")
            assert "query" in search_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_memory_store_has_required_params(self, server):
        """memory_store should require 'memory_type' and 'content' parameters."""
        async with Client(server) as client:
            tools = await client.list_tools()
            store_tool = next(t for t in tools if t.name == "memory_store")
            required = store_tool.inputSchema.get("required", [])
            assert "memory_type" in required or "type" in required
            assert "content" in required

    @pytest.mark.asyncio
    async def test_entity_lookup_has_required_param(self, server):
        """entity_lookup should require a 'name' parameter."""
        async with Client(server) as client:
            tools = await client.list_tools()
            lookup_tool = next(t for t in tools if t.name == "entity_lookup")
            assert "name" in lookup_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_conversation_history_has_required_param(self, server):
        """conversation_history should require a 'session_id' parameter."""
        async with Client(server) as client:
            tools = await client.list_tools()
            history_tool = next(t for t in tools if t.name == "conversation_history")
            assert "session_id" in history_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_graph_query_has_required_param(self, server):
        """graph_query should require a 'query' parameter."""
        async with Client(server) as client:
            tools = await client.list_tools()
            query_tool = next(t for t in tools if t.name == "graph_query")
            assert "query" in query_tool.inputSchema.get("required", [])


class TestReadOnlyQueryValidation:
    """Tests for Cypher query read-only validation."""

    def test_allows_match(self):
        """MATCH/RETURN queries should be allowed."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MATCH (n) RETURN n") is True
        assert _is_read_only_query("MATCH (n:Person) RETURN n.name") is True

    def test_blocks_create(self):
        """CREATE queries should be blocked."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("CREATE (n:Person {name: 'Alice'})") is False
        assert _is_read_only_query("MATCH (n) CREATE (m:Copy) SET m = n") is False

    def test_blocks_merge(self):
        """MERGE queries should be blocked."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MERGE (n:Person {name: 'Alice'})") is False

    def test_blocks_delete(self):
        """DELETE and DETACH DELETE queries should be blocked."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MATCH (n) DELETE n") is False
        assert _is_read_only_query("MATCH (n) DETACH DELETE n") is False

    def test_blocks_set(self):
        """SET queries should be blocked."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MATCH (n) SET n.name = 'Bob'") is False

    def test_blocks_remove(self):
        """REMOVE queries should be blocked."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MATCH (n) REMOVE n.name") is False

    def test_case_insensitive(self):
        """Write detection should be case-insensitive."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("match (n) return n") is True
        assert _is_read_only_query("create (n)") is False
        assert _is_read_only_query("CREATE (n)") is False

    def test_allows_call_procedures(self):
        """Read-only CALL procedures should be allowed."""
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert (
            _is_read_only_query(
                "CALL db.index.vector.queryNodes('idx', 5, $vec) YIELD node RETURN node"
            )
            is True
        )
        assert _is_read_only_query("CALL apoc.meta.data() YIELD label") is True


class TestMemorySearchTool:
    """Tests for the memory_search tool behavior."""

    @pytest.mark.asyncio
    async def test_search_messages(self):
        """memory_search returns message results."""
        mock_client = make_mock_client()

        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.role = MagicMock(value="user")
        mock_msg.content = "Test message"
        mock_msg.created_at = None
        mock_msg.metadata = {"similarity": 0.9}

        mock_client.short_term.search_messages = AsyncMock(return_value=[mock_msg])
        mock_client.long_term.search_entities = AsyncMock(return_value=[])
        mock_client.long_term.search_preferences = AsyncMock(return_value=[])

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_search",
                {"query": "test", "memory_types": ["messages"]},
            )

        data = json.loads(result.content[0].text)
        assert "results" in data
        assert "messages" in data["results"]
        assert len(data["results"]["messages"]) == 1
        assert data["results"]["messages"][0]["content"] == "Test message"

    @pytest.mark.asyncio
    async def test_search_defaults_to_three_types(self):
        """memory_search defaults to messages, entities, preferences."""
        mock_client = make_mock_client()
        mock_client.short_term.search_messages = AsyncMock(return_value=[])
        mock_client.long_term.search_entities = AsyncMock(return_value=[])
        mock_client.long_term.search_preferences = AsyncMock(return_value=[])

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_search",
                {"query": "test"},
            )

        data = json.loads(result.content[0].text)
        # All three default types should be searched
        mock_client.short_term.search_messages.assert_called_once()
        mock_client.long_term.search_entities.assert_called_once()
        mock_client.long_term.search_preferences.assert_called_once()


class TestMemoryStoreTool:
    """Tests for the memory_store tool behavior."""

    @pytest.mark.asyncio
    async def test_store_message(self):
        """memory_store stores a message successfully."""
        mock_client = make_mock_client()
        mock_msg = MagicMock()
        mock_msg.id = "msg-new"
        mock_client.short_term.add_message = AsyncMock(return_value=mock_msg)

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {
                    "memory_type": "message",
                    "content": "Hello world",
                    "session_id": "session-123",
                    "role": "user",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "message"
        assert data["session_id"] == "session-123"

    @pytest.mark.asyncio
    async def test_store_message_requires_session_id(self):
        """memory_store returns error when session_id missing for message."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {"memory_type": "message", "content": "Hello"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data
        assert "session_id" in data["error"]

    @pytest.mark.asyncio
    async def test_store_preference(self):
        """memory_store stores a preference successfully."""
        mock_client = make_mock_client()
        mock_pref = MagicMock()
        mock_pref.id = "pref-new"
        mock_client.long_term.add_preference = AsyncMock(return_value=mock_pref)

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {
                    "memory_type": "preference",
                    "content": "Likes dark mode",
                    "category": "ui",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "preference"
        assert data["category"] == "ui"

    @pytest.mark.asyncio
    async def test_store_preference_requires_category(self):
        """memory_store returns error when category missing for preference."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {"memory_type": "preference", "content": "Likes dark mode"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data
        assert "category" in data["error"]

    @pytest.mark.asyncio
    async def test_store_fact(self):
        """memory_store stores a fact successfully."""
        mock_client = make_mock_client()
        mock_fact = MagicMock()
        mock_fact.id = "fact-new"
        mock_client.long_term.add_fact = AsyncMock(return_value=mock_fact)

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {
                    "memory_type": "fact",
                    "content": "",
                    "subject": "Alice",
                    "predicate": "WORKS_AT",
                    "object_value": "Acme Corp",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "fact"
        assert "Alice" in data["triple"]
        assert "WORKS_AT" in data["triple"]

    @pytest.mark.asyncio
    async def test_store_fact_requires_full_triple(self):
        """memory_store returns error when fact triple is incomplete."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store",
                {
                    "memory_type": "fact",
                    "content": "",
                    "subject": "Alice",
                },
            )

        data = json.loads(result.content[0].text)
        assert "error" in data


class TestEntityLookupTool:
    """Tests for the entity_lookup tool behavior."""

    @pytest.mark.asyncio
    async def test_entity_found(self):
        """entity_lookup returns entity data when found."""
        mock_client = make_mock_client()
        mock_entity = MagicMock()
        mock_entity.id = "entity-1"
        mock_entity.display_name = "Alice"
        mock_entity.type = MagicMock(value="PERSON")
        mock_entity.description = "A person"
        mock_entity.aliases = ["Al"]
        mock_client.long_term.search_entities = AsyncMock(return_value=[mock_entity])
        mock_client.graph = MagicMock()
        mock_client.graph.execute_read = AsyncMock(return_value=[])

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "entity_lookup",
                {"name": "Alice", "include_neighbors": False},
            )

        data = json.loads(result.content[0].text)
        assert data["found"] is True
        assert data["entity"]["name"] == "Alice"
        assert data["entity"]["type"] == "PERSON"

    @pytest.mark.asyncio
    async def test_entity_not_found(self):
        """entity_lookup returns found=False when not found."""
        mock_client = make_mock_client()
        mock_client.long_term.search_entities = AsyncMock(return_value=[])
        mock_client.graph.execute_read = AsyncMock(return_value=[])

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "entity_lookup",
                {"name": "Unknown"},
            )

        data = json.loads(result.content[0].text)
        assert data["found"] is False
        assert data["name"] == "Unknown"


class TestConversationHistoryTool:
    """Tests for the conversation_history tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_messages(self):
        """conversation_history returns messages for a session."""
        mock_client = make_mock_client()
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.role = MagicMock(value="user")
        mock_msg.content = "Hello"
        mock_msg.created_at = None
        mock_msg.metadata = None
        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_msg]
        mock_client.short_term.get_conversation = AsyncMock(return_value=mock_conversation)

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "conversation_history",
                {"session_id": "session-123"},
            )

        data = json.loads(result.content[0].text)
        assert data["session_id"] == "session-123"
        assert data["message_count"] == 1
        assert len(data["messages"]) == 1


class TestGraphQueryTool:
    """Tests for the graph_query tool behavior."""

    @pytest.mark.asyncio
    async def test_read_only_query_succeeds(self):
        """graph_query executes read-only Cypher."""
        mock_client = make_mock_client()
        mock_client.graph.execute_read = AsyncMock(return_value=[{"n.name": "Alice"}])

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "MATCH (n:Person) RETURN n.name"},
            )

        data = json.loads(result.content[0].text)
        assert data["success"] is True
        assert data["row_count"] == 1

    @pytest.mark.asyncio
    async def test_blocks_create_query(self):
        """graph_query blocks CREATE queries."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "CREATE (n:Person {name: 'Alice'})"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data
        assert "read-only" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_blocks_merge_query(self):
        """graph_query blocks MERGE queries."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "MERGE (n:Person {name: 'Alice'})"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_blocks_delete_query(self):
        """graph_query blocks DELETE queries."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "MATCH (n) DELETE n"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_blocks_set_query(self):
        """graph_query blocks SET queries."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "MATCH (n) SET n.name = 'Bob'"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_case_insensitive_blocking(self):
        """graph_query blocks write queries regardless of case."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "create (n:Person)"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data


class TestAddReasoningTraceTool:
    """Tests for the add_reasoning_trace tool behavior."""

    @pytest.mark.asyncio
    async def test_store_trace(self):
        """add_reasoning_trace stores a trace successfully."""
        mock_client = make_mock_client()
        mock_trace = MagicMock()
        mock_trace.id = "trace-1"
        mock_client.reasoning.start_trace = AsyncMock(return_value=mock_trace)
        mock_client.reasoning.complete_trace = AsyncMock()

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "add_reasoning_trace",
                {
                    "session_id": "session-123",
                    "task": "Find restaurants",
                    "outcome": "Found 3 restaurants",
                    "success": True,
                },
            )

        data = json.loads(result.content[0].text)
        assert data["success"] is True
        assert data["stored"] is True
        assert data["trace_id"] == "trace-1"
        assert data["session_id"] == "session-123"
        assert data["task"] == "Find restaurants"
        assert data["tool_call_count"] == 0

    @pytest.mark.asyncio
    async def test_store_trace_with_tool_calls(self):
        """add_reasoning_trace stores a trace with tool call steps."""
        mock_client = make_mock_client()
        mock_trace = MagicMock()
        mock_trace.id = "trace-2"
        mock_step = MagicMock()
        mock_step.id = "step-1"
        mock_client.reasoning.start_trace = AsyncMock(return_value=mock_trace)
        mock_client.reasoning.add_step = AsyncMock(return_value=mock_step)
        mock_client.reasoning.record_tool_call = AsyncMock()
        mock_client.reasoning.complete_trace = AsyncMock()

        server = create_tool_server(mock_client)
        async with Client(server) as client:
            result = await client.call_tool(
                "add_reasoning_trace",
                {
                    "session_id": "session-456",
                    "task": "Search memory",
                    "tool_calls": [
                        {
                            "tool_name": "memory_search",
                            "arguments": {"query": "test"},
                            "result": "found 2 results",
                        }
                    ],
                    "outcome": "Completed search",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["success"] is True
        assert data["tool_call_count"] == 1
        mock_client.reasoning.add_step.assert_called_once()
        mock_client.reasoning.record_tool_call.assert_called_once()
