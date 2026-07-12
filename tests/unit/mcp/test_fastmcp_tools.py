"""Unit tests for FastMCP tool registration and execution.

Tests the _tools.py module with profile-aware tool registration.
Uses FastMCP's Client for in-memory testing.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from tests.unit.mcp.conftest import (
    create_tool_server,
    make_mock_client,
    make_mock_integration,
)

# ── Core tool names (6) ──────────────────────────────────────────────

CORE_TOOL_NAMES = {
    "memory_search",
    "memory_get_context",
    "memory_store_message",
    "memory_add_entity",
    "memory_add_preference",
    "memory_add_fact",
}

# ── Extended tool names (10 additional, 16 total) ────────────────────

EXTENDED_TOOL_NAMES = CORE_TOOL_NAMES | {
    "memory_get_conversation",
    "memory_list_sessions",
    "memory_get_entity",
    "memory_export_graph",
    "memory_create_relationship",
    "memory_start_trace",
    "memory_record_step",
    "memory_complete_trace",
    "memory_get_observations",
    "graph_query",
}


class TestToolRegistration:
    """Tests that tools register correctly with profiles."""

    @pytest.fixture
    def mock_client(self):
        return make_mock_client()

    @pytest.mark.asyncio
    async def test_core_profile_registers_6_tools(self, mock_client):
        """Core profile should register exactly 6 tools."""
        server = create_tool_server(mock_client, profile="core")
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_core_profile_tool_names(self, mock_client):
        """Core profile should have the correct tool names."""
        server = create_tool_server(mock_client, profile="core")
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == CORE_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_extended_profile_registers_16_tools(self, mock_client):
        """Extended profile should register 16 tools (6 core + 10 extended)."""
        server = create_tool_server(mock_client, profile="extended")
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 16

    @pytest.mark.asyncio
    async def test_extended_profile_tool_names(self, mock_client):
        """Extended profile should have all tool names."""
        server = create_tool_server(mock_client, profile="extended")
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == EXTENDED_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_all_tools_have_descriptions(self, mock_client):
        """Every tool should have a non-empty description."""
        server = create_tool_server(mock_client, profile="extended")
        async with Client(server) as client:
            tools = await client.list_tools()
            for tool in tools:
                assert tool.description, f"Tool {tool.name} has no description"

    @pytest.mark.asyncio
    async def test_default_profile_is_extended(self, mock_client):
        """Default profile should be 'extended'."""
        server = create_tool_server(mock_client)
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 16


class TestCoreToolParameters:
    """Tests that core tools have correct required parameters."""

    @pytest.fixture
    def mock_client(self):
        return make_mock_client()

    @pytest.fixture
    def server(self, mock_client):
        return create_tool_server(mock_client, profile="core")

    @pytest.mark.asyncio
    async def test_memory_search_requires_query(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_search")
            assert "query" in tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_memory_store_message_requires_content(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_store_message")
            assert "content" in tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_memory_add_entity_requires_name_and_type(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_add_entity")
            required = tool.inputSchema.get("required", [])
            assert "name" in required
            assert "entity_type" in required

    @pytest.mark.asyncio
    async def test_memory_add_preference_requires_category_and_preference(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_add_preference")
            required = tool.inputSchema.get("required", [])
            assert "category" in required
            assert "preference" in required

    @pytest.mark.asyncio
    async def test_memory_add_fact_requires_triple(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_add_fact")
            required = tool.inputSchema.get("required", [])
            assert "subject" in required
            assert "predicate" in required
            assert "object_value" in required


class TestCoreToolExecution:
    """Tests that core tools execute correctly with mocked backends."""

    @pytest.fixture
    def mock_client(self):
        return make_mock_client()

    @pytest.fixture
    def mock_integration(self, mock_client):
        return make_mock_integration(mock_client)

    @pytest.fixture
    def server(self, mock_client, mock_integration):
        return create_tool_server(mock_client, profile="core", mock_integration=mock_integration)

    @pytest.mark.asyncio
    async def test_memory_search_calls_integration(self, server, mock_integration):
        mock_integration.search = AsyncMock(
            return_value={"results": {"messages": []}, "query": "test"}
        )
        async with Client(server) as client:
            result = await client.call_tool("memory_search", {"query": "test"})
            data = json.loads(result.content[0].text)
            assert "results" in data
            mock_integration.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_get_context_calls_integration(self, server, mock_integration):
        mock_integration.get_context = AsyncMock(
            return_value={"session_id": "s1", "context": "ctx", "has_context": True}
        )
        async with Client(server) as client:
            result = await client.call_tool("memory_get_context", {})
            data = json.loads(result.content[0].text)
            assert data["has_context"] is True

    @pytest.mark.asyncio
    async def test_memory_store_message_calls_integration(self, server, mock_integration):
        mock_integration.store_message = AsyncMock(
            return_value={"stored": True, "type": "message", "id": "m1", "session_id": "s1"}
        )
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_store_message", {"content": "Hello", "role": "user"}
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True

    @pytest.mark.asyncio
    async def test_memory_add_entity_calls_integration(self, server, mock_integration):
        mock_integration.add_entity = AsyncMock(
            return_value={"stored": True, "type": "entity", "id": "e1", "name": "John"}
        )
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_add_entity", {"name": "John", "entity_type": "PERSON"}
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True

    @pytest.mark.asyncio
    async def test_memory_add_preference_calls_integration(self, server, mock_integration):
        mock_integration.add_preference = AsyncMock(
            return_value={"stored": True, "type": "preference", "id": "p1", "category": "food"}
        )
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_add_preference",
                {"category": "food", "preference": "Likes pasta"},
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True

    @pytest.mark.asyncio
    async def test_memory_add_fact_calls_integration(self, server, mock_integration):
        mock_integration.add_fact = AsyncMock(
            return_value={
                "stored": True,
                "type": "fact",
                "id": "f1",
                "triple": "A -> B -> C",
            }
        )
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_add_fact",
                {"subject": "A", "predicate": "B", "object_value": "C"},
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True


class TestExtendedToolExecution:
    """Tests that extended tools execute correctly."""

    @pytest.fixture
    def mock_client(self):
        return make_mock_client()

    @pytest.fixture
    def mock_integration(self, mock_client):
        return make_mock_integration(mock_client)

    @pytest.fixture
    def server(self, mock_client, mock_integration):
        return create_tool_server(
            mock_client, profile="extended", mock_integration=mock_integration
        )

    @pytest.mark.asyncio
    async def test_memory_get_conversation(self, server, mock_client):
        mock_conv = MagicMock()
        mock_conv.messages = []
        mock_client.short_term.get_conversation = AsyncMock(return_value=mock_conv)

        async with Client(server) as client:
            result = await client.call_tool(
                "memory_get_conversation", {"session_id": "test-session"}
            )
            data = json.loads(result.content[0].text)
            assert data["session_id"] == "test-session"
            assert data["message_count"] == 0

    @pytest.mark.asyncio
    async def test_memory_list_sessions(self, server, mock_client):
        mock_client.short_term.list_sessions = AsyncMock(return_value=[])

        async with Client(server) as client:
            result = await client.call_tool("memory_list_sessions", {})
            data = json.loads(result.content[0].text)
            assert data["session_count"] == 0

    @pytest.mark.asyncio
    async def test_memory_get_entity_found(self, server, mock_client):
        mock_entity = MagicMock()
        mock_entity.id = "e-1"
        mock_entity.display_name = "John"
        mock_entity.type = MagicMock(value="PERSON")
        mock_entity.subtype = None
        mock_entity.description = "A person"
        mock_entity.aliases = []

        mock_client.long_term.search_entities = AsyncMock(return_value=[mock_entity])
        # v0.4: _get_entity_neighbors uses client.query.cypher.
        mock_client.query.cypher = AsyncMock(return_value=[])

        async with Client(server) as client:
            result = await client.call_tool("memory_get_entity", {"name": "John"})
            data = json.loads(result.content[0].text)
            assert data["found"] is True
            assert data["entity"]["name"] == "John"

    @pytest.mark.asyncio
    async def test_memory_get_entity_not_found(self, server, mock_client):
        mock_client.long_term.search_entities = AsyncMock(return_value=[])

        async with Client(server) as client:
            result = await client.call_tool("memory_get_entity", {"name": "Unknown"})
            data = json.loads(result.content[0].text)
            assert data["found"] is False

    @pytest.mark.asyncio
    async def test_memory_start_trace(self, server, mock_client):
        mock_trace = MagicMock()
        mock_trace.id = "t-1"
        mock_client.reasoning.start_trace = AsyncMock(return_value=mock_trace)

        async with Client(server) as client:
            result = await client.call_tool(
                "memory_start_trace",
                {"session_id": "s1", "task": "Find restaurants"},
            )
            data = json.loads(result.content[0].text)
            assert data["started"] is True
            assert data["trace_id"] == "t-1"

    @pytest.mark.asyncio
    async def test_memory_record_step(self, server, mock_client):
        mock_step = MagicMock()
        mock_step.id = "step-1"
        mock_client.reasoning.add_step = AsyncMock(return_value=mock_step)

        async with Client(server) as client:
            result = await client.call_tool(
                "memory_record_step",
                {"trace_id": "t-1", "thought": "I should search"},
            )
            data = json.loads(result.content[0].text)
            assert data["recorded"] is True

    @pytest.mark.asyncio
    async def test_memory_record_step_with_tool_call(self, server, mock_client):
        mock_step = MagicMock()
        mock_step.id = "step-1"
        mock_tc = MagicMock()
        mock_tc.id = "tc-1"
        mock_client.reasoning.add_step = AsyncMock(return_value=mock_step)
        mock_client.reasoning.record_tool_call = AsyncMock(return_value=mock_tc)

        async with Client(server) as client:
            result = await client.call_tool(
                "memory_record_step",
                {
                    "trace_id": "t-1",
                    "thought": "Search for restaurants",
                    "tool_name": "search_api",
                    "tool_args": {"query": "Italian restaurants"},
                },
            )
            data = json.loads(result.content[0].text)
            assert data["has_tool_call"] is True
            assert data["tool_call_id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_memory_complete_trace(self, server, mock_client):
        mock_client.reasoning.complete_trace = AsyncMock(return_value=None)

        async with Client(server) as client:
            result = await client.call_tool(
                "memory_complete_trace",
                {"trace_id": "t-1", "outcome": "Found 3 restaurants", "success": True},
            )
            data = json.loads(result.content[0].text)
            assert data["completed"] is True

    @pytest.mark.asyncio
    async def test_graph_query_read_only(self, server, mock_client):
        # v0.4: the graph_query MCP tool routes through client.query.cypher.
        mock_client.query.cypher = AsyncMock(return_value=[{"name": "test"}])

        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "MATCH (n) RETURN n.name AS name LIMIT 1"},
            )
            data = json.loads(result.content[0].text)
            assert data["success"] is True
            assert data["row_count"] == 1

    @pytest.mark.asyncio
    async def test_graph_query_blocks_writes(self, server, mock_client):
        async with Client(server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "CREATE (n:Test) RETURN n"},
            )
            data = json.loads(result.content[0].text)
            assert "error" in data
            assert "read-only" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_get_observations(self, server, mock_client):
        mock_conv = MagicMock()
        mock_conv.messages = []
        mock_client.short_term.get_conversation = AsyncMock(return_value=mock_conv)

        async with Client(server) as client:
            result = await client.call_tool("memory_get_observations", {"session_id": "s1"})
            data = json.loads(result.content[0].text)
            assert data["session_id"] == "s1"


class TestReadOnlyQueryValidation:
    """Tests for the _is_read_only_query helper."""

    def test_allows_match_return(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("MATCH (n) RETURN n")

    def test_allows_call_procedures(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert _is_read_only_query("CALL db.index.vector.queryNodes(...)")
        assert _is_read_only_query("CALL apoc.meta.data()")

    def test_blocks_create(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert not _is_read_only_query("CREATE (n:Test)")

    def test_blocks_merge(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert not _is_read_only_query("MERGE (n:Test {id: 1})")

    def test_blocks_delete(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert not _is_read_only_query("MATCH (n) DELETE n")

    def test_blocks_set(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert not _is_read_only_query("MATCH (n) SET n.name = 'test'")

    def test_blocks_detach_delete(self):
        from neo4j_agent_memory.mcp._tools import _is_read_only_query

        assert not _is_read_only_query("MATCH (n) DETACH DELETE n")
