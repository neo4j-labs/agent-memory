"""Unit tests for FastMCP resource registration and execution.

Tests the _resources.py module with profile-aware resource registration.
Uses FastMCP's Client for in-memory testing.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from tests.unit.mcp.conftest import create_resource_server, make_mock_client


class TestResourceRegistration:
    """Tests that resources register correctly with profiles."""

    @pytest.mark.asyncio
    async def test_core_profile_resource_templates(self):
        """Core profile should have 1 template resource (context)."""
        server = create_resource_server(make_mock_client(), profile="core")
        async with Client(server) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1
            assert templates[0].uri_template == "memory://context/{session_id}"

    @pytest.mark.asyncio
    async def test_extended_profile_resource_templates(self):
        """Extended profile should have 1 template resource."""
        server = create_resource_server(make_mock_client(), profile="extended")
        async with Client(server) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1

    @pytest.mark.asyncio
    async def test_extended_profile_static_resources(self):
        """Extended profile should have 3 static resources."""
        server = create_resource_server(make_mock_client(), profile="extended")
        async with Client(server) as client:
            resources = await client.list_resources()
            uris = {str(r.uri) for r in resources}
            assert "memory://entities" in uris
            assert "memory://preferences" in uris
            assert "memory://graph/stats" in uris

    @pytest.mark.asyncio
    async def test_core_profile_has_no_static_resources(self):
        """Core profile should have no static resources."""
        server = create_resource_server(make_mock_client(), profile="core")
        async with Client(server) as client:
            resources = await client.list_resources()
            assert len(resources) == 0

    @pytest.mark.asyncio
    async def test_resources_have_descriptions(self):
        """Every resource should have a non-empty description."""
        server = create_resource_server(make_mock_client(), profile="extended")
        async with Client(server) as client:
            templates = await client.list_resource_templates()
            for template in templates:
                assert template.description, (
                    f"Resource template {template.uri_template} has no description"
                )
            resources = await client.list_resources()
            for resource in resources:
                assert resource.description, f"Resource {resource.uri} has no description"


class TestContextResource:
    """Tests for the memory://context/{session_id} resource."""

    @pytest.mark.asyncio
    async def test_returns_context(self):
        mock_client = make_mock_client()
        mock_client.get_context = AsyncMock(return_value="Some relevant context")

        server = create_resource_server(mock_client, profile="core")
        async with Client(server) as client:
            result = await client.read_resource("memory://context/session-123")

        data = json.loads(result[0].text)
        assert data["session_id"] == "session-123"
        assert data["has_context"] is True
        assert data["context"] == "Some relevant context"

    @pytest.mark.asyncio
    async def test_empty_context(self):
        mock_client = make_mock_client()
        mock_client.get_context = AsyncMock(return_value="")

        server = create_resource_server(mock_client, profile="core")
        async with Client(server) as client:
            result = await client.read_resource("memory://context/empty-session")

        data = json.loads(result[0].text)
        assert data["has_context"] is False


class TestEntitiesCatalogResource:
    """Tests for the memory://entities resource."""

    @pytest.mark.asyncio
    async def test_returns_entities(self):
        mock_client = make_mock_client()
        mock_entity = MagicMock()
        mock_entity.id = "e-1"
        mock_entity.display_name = "Alice"
        mock_entity.type = MagicMock(value="PERSON")
        mock_entity.subtype = None
        mock_entity.description = "A person"
        mock_client.long_term.search_entities = AsyncMock(return_value=[mock_entity])

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://entities")

        data = json.loads(result[0].text)
        assert data["entity_count"] == 1
        assert data["entities"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_empty_entities(self):
        mock_client = make_mock_client()
        mock_client.long_term.search_entities = AsyncMock(return_value=[])

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://entities")

        data = json.loads(result[0].text)
        assert data["entity_count"] == 0


class TestPreferencesResource:
    """Tests for the memory://preferences resource."""

    @pytest.mark.asyncio
    async def test_returns_preferences(self):
        mock_client = make_mock_client()
        mock_pref = MagicMock()
        mock_pref.id = "p-1"
        mock_pref.category = "ui"
        mock_pref.preference = "Dark mode"
        mock_pref.context = "Always"
        mock_pref.confidence = 1.0
        mock_client.long_term.search_preferences = AsyncMock(return_value=[mock_pref])

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://preferences")

        data = json.loads(result[0].text)
        assert data["preference_count"] == 1
        assert data["preferences"][0]["preference"] == "Dark mode"


class TestGraphStatsResource:
    """Tests for the memory://graph/stats resource."""

    @pytest.mark.asyncio
    async def test_returns_stats(self):
        mock_client = make_mock_client()
        # v0.4: memory://graph/stats routes through client.query.cypher.
        mock_client.query.cypher = AsyncMock(
            return_value=[
                {"labels": ["Entity"], "count": 42},
                {"labels": ["Message"], "count": 100},
            ]
        )

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://graph/stats")

        data = json.loads(result[0].text)
        assert "stats" in data
        assert len(data["stats"]) == 2

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self):
        mock_client = make_mock_client()
        mock_client.query.cypher = AsyncMock(side_effect=Exception("Connection lost"))

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://graph/stats")

        data = json.loads(result[0].text)
        assert "error" in data


class TestResourceErrorPaths:
    """Tests that all resources handle errors gracefully."""

    @pytest.mark.asyncio
    async def test_context_resource_handles_error(self):
        mock_client = make_mock_client()
        mock_client.get_context = AsyncMock(side_effect=Exception("context error"))

        server = create_resource_server(mock_client, profile="core")
        async with Client(server) as client:
            result = await client.read_resource("memory://context/test-session")

        data = json.loads(result[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_entities_catalog_handles_error(self):
        mock_client = make_mock_client()
        mock_client.long_term.search_entities = AsyncMock(side_effect=Exception("entities error"))

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://entities")

        data = json.loads(result[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_preferences_resource_handles_error(self):
        mock_client = make_mock_client()
        mock_client.long_term.search_preferences = AsyncMock(
            side_effect=Exception("preferences error")
        )

        server = create_resource_server(mock_client, profile="extended")
        async with Client(server) as client:
            result = await client.read_resource("memory://preferences")

        data = json.loads(result[0].text)
        assert "error" in data
