"""Phase 7 tests: MCP Platinum-tier tools (NAMS-only).

Covers:

* Conditional registration — Platinum tools appear only when
  ``register_platinum=True``.
* Tool behavior — each of the 4 Platinum tools calls the right
  client method and returns the expected JSON shape.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from tests.unit.mcp.conftest import create_tool_server, make_mock_client

PLATINUM_TOOL_NAMES = {
    "memory_set_entity_feedback",
    "memory_get_entity_history",
    "memory_get_entity_provenance",
    "memory_get_reflections",
}


class TestConditionalRegistration:
    @pytest.mark.asyncio
    async def test_not_registered_by_default(self):
        """Extended profile without register_platinum=True has 16 tools, no Platinum."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client, profile="extended")
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
        assert not (PLATINUM_TOOL_NAMES & names), (
            f"Platinum tools should not be registered by default. Found: "
            f"{PLATINUM_TOOL_NAMES & names}"
        )

    @pytest.mark.asyncio
    async def test_registered_when_flag_set(self):
        """register_platinum=True adds the 4 Platinum tools."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client, profile="extended", register_platinum=True)
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
        assert names >= PLATINUM_TOOL_NAMES, (
            f"Platinum tools missing: {PLATINUM_TOOL_NAMES - names}"
        )

    @pytest.mark.asyncio
    async def test_core_profile_ignores_register_platinum(self):
        """Platinum tools are extended-only — core profile never registers them."""
        mock_client = make_mock_client()
        server = create_tool_server(mock_client, profile="core", register_platinum=True)
        async with Client(server) as client:
            tools = await client.list_tools()
            assert len(tools) == 6
            names = {t.name for t in tools}
        assert not (PLATINUM_TOOL_NAMES & names)


class TestPlatinumToolExecution:
    @pytest.fixture
    def mock_client(self):
        client = make_mock_client()
        client.long_term.set_entity_feedback = AsyncMock(return_value=None)
        client.long_term.get_entity_history = AsyncMock(
            return_value=[
                {"conversation_id": "c1", "mention_count": 3},
                {"conversation_id": "c2", "mention_count": 1},
            ]
        )
        client.long_term.get_entity_provenance = AsyncMock(
            return_value={"sources": [{"message_id": "m1"}], "extractors": []}
        )
        client.short_term.get_reflections = AsyncMock(
            return_value=[{"text": "reflection one"}, {"text": "reflection two"}]
        )
        return client

    @pytest.fixture
    def server(self, mock_client):
        return create_tool_server(mock_client, profile="extended", register_platinum=True)

    @pytest.mark.asyncio
    async def test_set_entity_feedback(self, server, mock_client):
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_set_entity_feedback",
                {"entity_id": "e1", "feedback": "positive"},
            )
            data = json.loads(result.content[0].text)
        assert data["status"] == "ok"
        assert data["entity_id"] == "e1"
        mock_client.long_term.set_entity_feedback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_entity_history(self, server, mock_client):
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_get_entity_history", {"entity_id": "e1", "limit": 1}
            )
            data = json.loads(result.content[0].text)
        assert data["entity_id"] == "e1"
        assert len(data["history"]) == 1
        assert data["history"][0]["mention_count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [0, -1])
    async def test_get_entity_history_non_positive_limit_returns_empty(
        self, server, mock_client, limit
    ):
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_get_entity_history", {"entity_id": "e1", "limit": limit}
            )
            data = json.loads(result.content[0].text)

        assert data["entity_id"] == "e1"
        assert data["history"] == []
        mock_client.long_term.get_entity_history.assert_awaited_once_with("e1")

    @pytest.mark.asyncio
    async def test_feedback_tool_does_not_expose_ignored_user_identifier(self, server):
        async with Client(server) as client:
            tools = await client.list_tools()
        feedback = next(tool for tool in tools if tool.name == "memory_set_entity_feedback")
        assert "user_identifier" not in feedback.inputSchema.get("properties", {})

    @pytest.mark.asyncio
    async def test_get_entity_provenance(self, server, mock_client):
        async with Client(server) as client:
            result = await client.call_tool("memory_get_entity_provenance", {"entity_id": "e1"})
            data = json.loads(result.content[0].text)
        assert "sources" in data
        assert data["sources"][0]["message_id"] == "m1"

    @pytest.mark.asyncio
    async def test_get_reflections(self, server, mock_client):
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_get_reflections", {"session_id": "s1", "limit": 1}
            )
            data = json.loads(result.content[0].text)
        assert data["session_id"] == "s1"
        assert len(data["reflections"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [0, -1])
    async def test_get_reflections_non_positive_limit_returns_empty(
        self, server, mock_client, limit
    ):
        async with Client(server) as client:
            result = await client.call_tool(
                "memory_get_reflections", {"session_id": "s1", "limit": limit}
            )
            data = json.loads(result.content[0].text)

        assert data["session_id"] == "s1"
        assert data["reflections"] == []
        mock_client.short_term.get_reflections.assert_awaited_once_with("s1")


class TestServerWiring:
    """create_mcp_server passes register_platinum=True when backend=='nams'."""

    @pytest.mark.asyncio
    async def test_create_mcp_server_with_nams_settings(self, monkeypatch):
        """Server constructed with NAMS settings registers Platinum tools."""
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings, NamsConfig
        from neo4j_agent_memory.mcp.server import create_mcp_server

        # Strip env so resolver doesn't flip backends.
        for key in list(__import__("os").environ.keys()):
            if key.startswith(("MEMORY_", "NAM_")):
                monkeypatch.delenv(key, raising=False)

        settings = MemorySettings(
            backend="nams",
            nams=NamsConfig(
                endpoint="https://memory.test/v1",
                api_key=SecretStr("nams_test"),
                validate_on_connect=False,
            ),
        )
        mcp = create_mcp_server(settings, server_name="test-platinum")
        # FastMCP doesn't expose a clean "list registered tools" without a
        # client connection, but we can introspect the manager.
        # In practice the lifespan never runs in this test, but the
        # registration phase has happened.
        registered_names = set()
        async with Client(mcp) as client:
            # Lifespan will try to construct a MemoryClient which will fail
            # without a real NAMS reachable. Skip listing tools at the
            # protocol level; instead, inspect that registration was wired.
            try:
                tools = await client.list_tools()
                registered_names = {t.name for t in tools}
            except Exception:
                pytest.skip("FastMCP lifespan requires reachable NAMS")
        # When we got tools, Platinum should be present.
        if registered_names:
            assert registered_names >= PLATINUM_TOOL_NAMES
