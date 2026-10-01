"""End-to-end MCP flow tests against real Neo4j.

Simulates what Claude Desktop would do: load context, store messages,
search, manage entities, record reasoning traces.
"""

import json
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, FastMCP

from neo4j_agent_memory.integration import MemoryIntegration
from neo4j_agent_memory.mcp._observer import MemoryObserver
from neo4j_agent_memory.mcp._prompts import register_prompts
from neo4j_agent_memory.mcp._resources import register_resources
from neo4j_agent_memory.mcp._tools import register_tools


def _create_e2e_server(memory_client):
    """Create a fully configured MCP server for E2E testing."""
    integration = MemoryIntegration(memory_client)
    observer = MemoryObserver(memory_client)
    integration.observer = observer

    @asynccontextmanager
    async def lifespan(server):
        yield {"client": memory_client, "integration": integration, "observer": observer}

    mcp = FastMCP("e2e-test", lifespan=lifespan)
    register_tools(mcp, profile="extended")
    register_resources(mcp, profile="extended")
    register_prompts(mcp, profile="extended")
    return mcp


@pytest.mark.integration
class TestFullConversationFlow:
    """E2E test simulating a complete Claude Desktop conversation."""

    @pytest.mark.asyncio
    async def test_complete_memory_lifecycle(self, memory_client, session_id):
        """Full conversation flow: context -> store -> search -> entities -> graph."""
        server = _create_e2e_server(memory_client)

        async with Client(server) as client:
            # 1. Get context (empty - first conversation)
            result = await client.call_tool("memory_get_context", {"session_id": session_id})
            data = json.loads(result.content[0].text)
            assert data["session_id"] == session_id

            # 2. Store user message. Auto-extraction stores and embeds each
            # capitalized word; the mock extractor keeps trailing punctuation, so
            # "at Neo4j." would store "Neo4j." and tie with step 6's "Neo4j" in
            # the lookup in step 10.
            result = await client.call_tool(
                "memory_store_message",
                {
                    "content": "I'm a software engineer at Neo4j working on graph databases.",
                    "session_id": session_id,
                    "role": "user",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True
            msg_id = data["id"]

            # 3. Store assistant response
            result = await client.call_tool(
                "memory_store_message",
                {
                    "content": "Great! I can help with graph database topics.",
                    "session_id": session_id,
                    "role": "assistant",
                },
            )
            assert json.loads(result.content[0].text)["stored"] is True

            # 4. Search for the topic
            result = await client.call_tool(
                "memory_search",
                {
                    "query": "graph databases Neo4j",
                    "memory_types": ["messages"],
                    "session_id": session_id,
                },
            )
            data = json.loads(result.content[0].text)
            assert len(data["results"]["messages"]) >= 1

            # 5. Add a preference
            result = await client.call_tool(
                "memory_add_preference",
                {
                    "category": "technology",
                    "preference": "Works with graph databases",
                    "context": "User mentioned working on graph databases at Neo4j",
                },
            )
            assert json.loads(result.content[0].text)["stored"] is True

            # 6. Add an entity
            result = await client.call_tool(
                "memory_add_entity",
                {
                    "name": "Neo4j",
                    "entity_type": "ORGANIZATION",
                    "subtype": "COMPANY",
                    "description": "Graph database company",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["stored"] is True

            # 7. Get context again (should now have data)
            result = await client.call_tool("memory_get_context", {"session_id": session_id})
            data = json.loads(result.content[0].text)
            assert data["session_id"] == session_id

            # 8. Get conversation history
            result = await client.call_tool("memory_get_conversation", {"session_id": session_id})
            data = json.loads(result.content[0].text)
            assert data["message_count"] == 2

            # 9. List sessions
            result = await client.call_tool("memory_list_sessions", {"limit": 10})
            data = json.loads(result.content[0].text)
            assert data["session_count"] >= 1
            session_ids = [s["session_id"] for s in data["sessions"]]
            assert session_id in session_ids

            # 10. Get entity details
            result = await client.call_tool(
                "memory_get_entity",
                {"name": "Neo4j", "include_neighbors": False},
            )
            data = json.loads(result.content[0].text)
            assert data["found"] is True
            assert data["entity"]["name"] == "Neo4j"

            # 11. Export graph
            result = await client.call_tool(
                "memory_export_graph",
                {"session_id": session_id, "limit": 100},
            )
            data = json.loads(result.content[0].text)
            assert data["node_count"] >= 1


@pytest.mark.integration
class TestReasoningTraceFlow:
    """E2E test for reasoning trace recording workflow."""

    @pytest.mark.asyncio
    async def test_complete_reasoning_trace(self, memory_client, session_id):
        """Start trace -> record steps -> complete -> verify."""
        server = _create_e2e_server(memory_client)

        async with Client(server) as client:
            # 1. Start a trace
            result = await client.call_tool(
                "memory_start_trace",
                {
                    "session_id": session_id,
                    "task": "Find the best graph database for the project",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["started"] is True
            trace_id = data["trace_id"]

            # 2. Record step 1: research
            result = await client.call_tool(
                "memory_record_step",
                {
                    "trace_id": trace_id,
                    "thought": "I should compare available graph databases",
                    "action": "research",
                    "tool_name": "memory_search",
                    "tool_args": {"query": "graph database comparison"},
                    "tool_result": "Found Neo4j, FalkorDB, Memgraph",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["recorded"] is True
            assert data["has_tool_call"] is True

            # 3. Record step 2: analysis
            result = await client.call_tool(
                "memory_record_step",
                {
                    "trace_id": trace_id,
                    "thought": "Neo4j has the best ecosystem and APOC support",
                    "action": "analyze",
                },
            )
            assert json.loads(result.content[0].text)["recorded"] is True

            # 4. Record step 3: decision
            result = await client.call_tool(
                "memory_record_step",
                {
                    "trace_id": trace_id,
                    "thought": "Recommending Neo4j based on ecosystem and community",
                    "action": "decide",
                    "observation": "Neo4j is the best choice",
                },
            )
            assert json.loads(result.content[0].text)["recorded"] is True

            # 5. Complete the trace
            result = await client.call_tool(
                "memory_complete_trace",
                {
                    "trace_id": trace_id,
                    "outcome": "Recommended Neo4j for the project based on ecosystem analysis",
                    "success": True,
                },
            )
            data = json.loads(result.content[0].text)
            assert data["completed"] is True

            # 6. Get observations
            result = await client.call_tool(
                "memory_get_observations",
                {"session_id": session_id},
            )
            data = json.loads(result.content[0].text)
            assert data["session_id"] == session_id
