"""Integration tests for MCP server with Neo4j.

Uses FastMCP's in-memory Client for testing tools against real Neo4j.
Tests cover both core and extended tool profiles.
"""

import json
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, FastMCP

from neo4j_agent_memory.integration import MemoryIntegration
from neo4j_agent_memory.mcp._observer import MemoryObserver
from neo4j_agent_memory.mcp._resources import register_resources
from neo4j_agent_memory.mcp._tools import register_tools
from neo4j_agent_memory.memory.long_term import EntityType
from neo4j_agent_memory.memory.short_term import MessageRole


def _create_server_with_client(memory_client, *, profile="extended"):
    """Create a FastMCP server with a real memory client in lifespan."""
    integration = MemoryIntegration(memory_client)
    observer = MemoryObserver(memory_client)
    integration.observer = observer

    @asynccontextmanager
    async def real_lifespan(server):
        yield {"client": memory_client, "integration": integration, "observer": observer}

    mcp = FastMCP("integration-test", lifespan=real_lifespan)
    register_tools(mcp, profile=profile)
    register_resources(mcp, profile=profile)
    return mcp


@pytest.mark.integration
class TestMCPToolsIntegration:
    """Integration tests for MCP tools with real Neo4j database."""

    @pytest.fixture
    def mcp_server(self, memory_client):
        return _create_server_with_client(memory_client)

    @pytest.mark.asyncio
    async def test_memory_search_messages(self, mcp_server, memory_client, session_id):
        """Test memory_search tool finds messages."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I'm looking for restaurants in San Francisco",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "I can help you find restaurants in San Francisco!",
            extract_entities=False,
            generate_embedding=True,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_search",
                {
                    "query": "restaurants San Francisco",
                    "limit": 10,
                    "memory_types": ["messages"],
                },
            )

        data = json.loads(result.content[0].text)
        assert "results" in data
        assert "messages" in data["results"]
        assert len(data["results"]["messages"]) >= 1
        assert any(
            "restaurant" in m.get("content", "").lower() for m in data["results"]["messages"]
        )

    @pytest.mark.asyncio
    async def test_memory_search_entities(self, mcp_server, memory_client, session_id):
        """Test memory_search tool finds entities."""
        await memory_client.long_term.add_entity(
            name="OpenAI",
            entity_type=EntityType.ORGANIZATION,
            description="AI research company",
            generate_embedding=True,
            resolve=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_search",
                {"query": "OpenAI organization", "limit": 10, "memory_types": ["entities"]},
            )

        data = json.loads(result.content[0].text)
        assert "results" in data
        assert "entities" in data["results"]
        assert len(data["results"]["entities"]) >= 1

    @pytest.mark.asyncio
    async def test_memory_search_preferences(self, mcp_server, memory_client, session_id):
        """Test memory_search tool finds preferences."""
        await memory_client.long_term.add_preference(
            category="food",
            preference="Loves spicy Thai food",
            context="dining preferences",
            generate_embedding=True,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_search",
                {
                    "query": "food preferences Thai",
                    "limit": 10,
                    "memory_types": ["preferences"],
                },
            )

        data = json.loads(result.content[0].text)
        assert "results" in data
        assert "preferences" in data["results"]
        assert len(data["results"]["preferences"]) >= 1

    @pytest.mark.asyncio
    async def test_memory_store_message(self, mcp_server, memory_client, session_id):
        """Test memory_store_message tool stores messages."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_store_message",
                {
                    "content": "This is a test message from MCP",
                    "session_id": session_id,
                    "role": "user",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "message"
        assert data["session_id"] == session_id

        # Verify message was stored
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 1
        assert "test message from MCP" in conversation.messages[0].content

    @pytest.mark.asyncio
    async def test_memory_add_preference(self, mcp_server, memory_client, session_id):
        """Test memory_add_preference tool stores preferences."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_add_preference",
                {
                    "category": "ui",
                    "preference": "Prefers dark mode interfaces",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "preference"
        assert data["category"] == "ui"

    @pytest.mark.asyncio
    async def test_memory_add_fact(self, mcp_server, memory_client, session_id):
        """Test memory_add_fact tool stores facts."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_add_fact",
                {
                    "subject": "Alice",
                    "predicate": "WORKS_AT",
                    "object_value": "TechCorp",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["type"] == "fact"
        assert "Alice" in data["triple"]
        assert "WORKS_AT" in data["triple"]
        assert "TechCorp" in data["triple"]

    @pytest.mark.asyncio
    async def test_memory_get_entity_found(self, mcp_server, memory_client, session_id):
        """Test memory_get_entity tool finds existing entity."""
        await memory_client.long_term.add_entity(
            name="Anthropic",
            entity_type=EntityType.ORGANIZATION,
            description="AI safety company that created Claude",
            generate_embedding=True,
            resolve=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_get_entity",
                {"name": "Anthropic", "include_neighbors": False},
            )

        data = json.loads(result.content[0].text)
        assert data["found"] is True
        assert data["entity"]["name"] == "Anthropic"
        assert data["entity"]["type"] == "ORGANIZATION"

    @pytest.mark.asyncio
    async def test_memory_get_entity_not_found(self, mcp_server, memory_client, session_id):
        """Test memory_get_entity tool handles missing entity."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_get_entity",
                {"name": "NonExistentEntity12345", "include_neighbors": False},
            )

        data = json.loads(result.content[0].text)
        assert data["found"] is False
        assert data["name"] == "NonExistentEntity12345"

    @pytest.mark.asyncio
    async def test_memory_get_conversation(self, mcp_server, memory_client, session_id):
        """Test memory_get_conversation tool retrieves messages."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "First message",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "First response",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Second message",
            extract_entities=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_get_conversation",
                {"session_id": session_id, "limit": 50},
            )

        data = json.loads(result.content[0].text)
        assert data["session_id"] == session_id
        assert data["message_count"] == 3
        assert len(data["messages"]) == 3

    @pytest.mark.asyncio
    async def test_graph_query_read_only(self, mcp_server, memory_client, session_id):
        """Test graph_query tool executes read-only queries."""
        await memory_client.long_term.add_entity(
            name="TestQueryEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "graph_query",
                {
                    "query": "MATCH (e:Entity) WHERE e.name CONTAINS 'TestQuery' RETURN e.name AS name"
                },
            )

        data = json.loads(result.content[0].text)
        assert data["success"] is True
        assert data["row_count"] >= 1
        assert any(row.get("name") == "TestQueryEntity" for row in data["rows"])

    @pytest.mark.asyncio
    async def test_graph_query_blocks_write(self, mcp_server, memory_client, session_id):
        """Test graph_query tool blocks write queries."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "graph_query",
                {"query": "CREATE (n:TestNode {name: 'Malicious'})"},
            )

        data = json.loads(result.content[0].text)
        assert "error" in data
        assert "read-only" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_graph_query_with_parameters(self, mcp_server, memory_client, session_id):
        """Test graph_query tool with parameterized queries."""
        await memory_client.long_term.add_entity(
            name="ParameterTestEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "graph_query",
                {
                    "query": "MATCH (e:Entity) WHERE e.name = $name RETURN e.name AS name",
                    "parameters": {"name": "ParameterTestEntity"},
                },
            )

        data = json.loads(result.content[0].text)
        assert data["success"] is True
        assert data["row_count"] == 1

    @pytest.mark.asyncio
    async def test_memory_start_and_complete_trace(self, mcp_server, memory_client, session_id):
        """Test reasoning trace lifecycle: start -> record step -> complete."""
        async with Client(mcp_server) as client:
            # Start trace
            result = await client.call_tool(
                "memory_start_trace",
                {"session_id": session_id, "task": "Test reasoning task"},
            )
            data = json.loads(result.content[0].text)
            assert data["started"] is True
            trace_id = data["trace_id"]

            # Record a step
            result = await client.call_tool(
                "memory_record_step",
                {
                    "trace_id": trace_id,
                    "thought": "I should search for relevant info",
                    "action": "search",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["recorded"] is True

            # Complete trace
            result = await client.call_tool(
                "memory_complete_trace",
                {
                    "trace_id": trace_id,
                    "outcome": "Task completed successfully",
                    "success": True,
                },
            )
            data = json.loads(result.content[0].text)
            assert data["completed"] is True

    @pytest.mark.asyncio
    async def test_memory_get_context(self, mcp_server, memory_client, session_id):
        """Test memory_get_context returns assembled context."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I like Italian restaurants",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.long_term.add_preference(
            category="food",
            preference="Loves Italian cuisine",
            generate_embedding=True,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_get_context",
                {"session_id": session_id},
            )

        data = json.loads(result.content[0].text)
        assert data["session_id"] == session_id
        assert "has_context" in data

    @pytest.mark.asyncio
    async def test_memory_list_sessions(self, mcp_server, memory_client, session_id):
        """Test memory_list_sessions returns available sessions."""
        # Create messages in the test session
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Hello from session listing test",
            extract_entities=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_list_sessions",
                {"limit": 20},
            )

        data = json.loads(result.content[0].text)
        assert "sessions" in data
        assert data["session_count"] >= 1

    @pytest.mark.asyncio
    async def test_memory_export_graph(self, mcp_server, memory_client, session_id):
        """Test memory_export_graph returns graph data."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Test export message",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="ExportTestEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_export_graph",
                {"session_id": session_id, "limit": 100},
            )

        data = json.loads(result.content[0].text)
        assert "node_count" in data
        assert "relationship_count" in data
        assert "nodes" in data
        assert data["node_count"] >= 1

    @pytest.mark.asyncio
    async def test_memory_create_relationship(self, mcp_server, memory_client, session_id):
        """Test memory_create_relationship links two entities."""
        await memory_client.long_term.add_entity(
            name="Alice Smith",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=True,
        )
        await memory_client.long_term.add_entity(
            name="TechCorp Inc",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=True,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_create_relationship",
                {
                    "source_name": "Alice Smith",
                    "target_name": "TechCorp Inc",
                    "relationship_type": "WORKS_AT",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["relationship_type"] == "WORKS_AT"

    @pytest.mark.asyncio
    async def test_memory_create_relationship_not_found(
        self, mcp_server, memory_client, session_id
    ):
        """Test memory_create_relationship returns error for missing entities."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_create_relationship",
                {
                    "source_name": "NonexistentPerson",
                    "target_name": "NonexistentOrg",
                    "relationship_type": "WORKS_AT",
                },
            )

        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_memory_add_entity(self, mcp_server, memory_client, session_id):
        """Test memory_add_entity creates entity in graph."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_add_entity",
                {
                    "name": "Neo4j Inc",
                    "entity_type": "ORGANIZATION",
                    "subtype": "COMPANY",
                    "description": "Graph database company",
                },
            )

        data = json.loads(result.content[0].text)
        assert data["stored"] is True
        assert data["name"] == "Neo4j Inc"
        assert data["entity_type"] == "ORGANIZATION"

    @pytest.mark.asyncio
    async def test_memory_get_observations(self, mcp_server, memory_client, session_id):
        """Test memory_get_observations returns session data."""
        # Store a message with a decision statement
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I decided to use Neo4j for the database layer.",
            extract_entities=False,
            generate_embedding=False,
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "memory_get_observations",
                {"session_id": session_id},
            )

        data = json.loads(result.content[0].text)
        assert data["session_id"] == session_id
        assert "observations" in data
        assert "reflections" in data


@pytest.mark.integration
class TestMCPServerIntegration:
    """Integration tests for the full MCP server."""

    @pytest.mark.asyncio
    async def test_server_initialization(self, memory_client):
        """Test MCP server initializes correctly with backward-compat wrapper."""
        from neo4j_agent_memory.mcp.server import Neo4jMemoryMCPServer

        server = Neo4jMemoryMCPServer(memory_client)
        assert server is not None
        assert server._client is memory_client

    @pytest.mark.asyncio
    async def test_server_has_extended_tools(self, memory_client):
        """Test server exposes all 16 tools via FastMCP Client (extended profile)."""
        server = _create_server_with_client(memory_client, profile="extended")
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 16
            tool_names = {t.name for t in tools}
            assert tool_names == {
                "memory_search",
                "memory_get_context",
                "memory_store_message",
                "memory_add_entity",
                "memory_add_preference",
                "memory_add_fact",
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

    @pytest.mark.asyncio
    async def test_server_has_core_tools(self, memory_client):
        """Test server exposes 6 tools with core profile."""
        server = _create_server_with_client(memory_client, profile="core")
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 6
            tool_names = {t.name for t in tools}
            assert tool_names == {
                "memory_search",
                "memory_get_context",
                "memory_store_message",
                "memory_add_entity",
                "memory_add_preference",
                "memory_add_fact",
            }

    @pytest.mark.asyncio
    async def test_server_has_resources(self, memory_client):
        """Test server exposes resources via FastMCP Client."""
        server = _create_server_with_client(memory_client, profile="extended")
        async with Client(server) as client:
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            assert len(resources) == 3  # entities, preferences, graph/stats
            assert len(templates) == 1  # context/{session_id}
