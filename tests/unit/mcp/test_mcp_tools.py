"""Unit tests for FastMCP tool registration (simple registration-only tests).

Tests that tools register correctly on a FastMCP server with proper
names, descriptions, and schemas. Tests tool counts for both profiles.
"""

import pytest
from fastmcp import Client, FastMCP


class TestFastMCPToolRegistration:
    """Tests that all tools register correctly with profiles."""

    @pytest.fixture
    def core_server(self):
        """Create a FastMCP server with core tools registered."""
        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test-server")
        register_tools(mcp, profile="core")
        return mcp

    @pytest.fixture
    def extended_server(self):
        """Create a FastMCP server with extended tools registered."""
        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test-server")
        register_tools(mcp, profile="extended")
        return mcp

    @pytest.mark.asyncio
    async def test_core_profile_registers_6_tools(self, core_server):
        async with Client(core_server) as client:
            tools = await client.list_tools()
            assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_extended_profile_registers_16_tools(self, extended_server):
        async with Client(extended_server) as client:
            tools = await client.list_tools()
            assert len(tools) == 16

    @pytest.mark.asyncio
    async def test_core_tool_names(self, core_server):
        async with Client(core_server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == {
                "memory_search",
                "memory_get_context",
                "memory_store_message",
                "memory_add_entity",
                "memory_add_preference",
                "memory_add_fact",
            }

    @pytest.mark.asyncio
    async def test_extended_includes_core_tools(self, extended_server):
        async with Client(extended_server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            core_names = {
                "memory_search",
                "memory_get_context",
                "memory_store_message",
                "memory_add_entity",
                "memory_add_preference",
                "memory_add_fact",
            }
            assert core_names.issubset(names)

    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self, extended_server):
        async with Client(extended_server) as client:
            tools = await client.list_tools()
            for tool in tools:
                assert tool.description, f"Tool {tool.name} has no description"

    @pytest.mark.asyncio
    async def test_memory_search_schema(self, core_server):
        async with Client(core_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_search")
            assert "query" in tool.input_schema.get("required", [])

    @pytest.mark.asyncio
    async def test_memory_store_message_schema(self, core_server):
        async with Client(core_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_store_message")
            assert "content" in tool.input_schema.get("required", [])

    @pytest.mark.asyncio
    async def test_memory_add_entity_schema(self, core_server):
        async with Client(core_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "memory_add_entity")
            required = tool.input_schema.get("required", [])
            assert "name" in required
            assert "entity_type" in required

    @pytest.mark.asyncio
    async def test_graph_query_schema(self, extended_server):
        async with Client(extended_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "graph_query")
            assert "query" in tool.input_schema.get("required", [])

    @pytest.mark.asyncio
    async def test_graph_query_description_mentions_read_only(self, extended_server):
        async with Client(extended_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "graph_query")
            assert "read-only" in tool.description.lower()
