"""Unit tests for the MCP CLI subcommand.

Tests the `neo4j-agent-memory mcp serve` subcommand added to cli/main.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from neo4j_agent_memory.cli.main import cli


@pytest.fixture
def runner(monkeypatch):
    """CliRunner with NEO4J_* env vars cleared.

    The `mcp serve --password` option is wired to `envvar="NEO4J_PASSWORD"`,
    so without clearing the var, `test_serve_requires_password` picks up the
    developer's local password and gets past the validation guard.
    """
    for var in ("NEO4J_PASSWORD", "NEO4J_USER", "NEO4J_USERNAME", "NEO4J_URI"):
        monkeypatch.delenv(var, raising=False)
    return CliRunner()


class TestMCPGroupExists:
    """Tests that the mcp command group is registered."""

    def test_mcp_group_in_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "mcp" in result.output

    def test_mcp_help(self, runner):
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "serve" in result.output

    def test_mcp_serve_help(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert result.exit_code == 0
        assert "--profile" in result.output
        assert "--transport" in result.output
        assert "--session-strategy" in result.output


class TestMCPServeOptions:
    """Tests that serve options are parsed correctly."""

    def test_serve_requires_password(self, runner):
        result = runner.invoke(cli, ["mcp", "serve"])
        assert result.exit_code != 0
        assert "password" in result.output.lower()

    def test_profile_choices(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert "core" in result.output
        assert "extended" in result.output

    def test_transport_choices(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert "stdio" in result.output
        assert "sse" in result.output
        assert "http" in result.output

    def test_session_strategy_choices(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert "per_conversation" in result.output
        assert "per_day" in result.output
        assert "persistent" in result.output

    def test_observation_threshold_in_help(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert "--observation-threshold" in result.output

    def test_no_auto_preferences_in_help(self, runner):
        result = runner.invoke(cli, ["mcp", "serve", "--help"])
        assert "--no-auto-preferences" in result.output


class TestMCPServeExecution:
    """Tests that serve delegates to run_server correctly."""

    @patch("neo4j_agent_memory.cli.main.asyncio")
    @patch("neo4j_agent_memory.mcp.server.run_server", new_callable=AsyncMock)
    def test_serve_calls_run_server(self, mock_run_server, mock_asyncio, runner):
        # Make asyncio.run just call the coroutine
        mock_asyncio.run = lambda _coro: None

        result = runner.invoke(
            cli,
            ["mcp", "serve", "--password", "test-pw"],
        )
        assert result.exit_code == 0

    @patch("neo4j_agent_memory.cli.main.asyncio")
    @patch("neo4j_agent_memory.mcp.server.run_server", new_callable=AsyncMock)
    def test_serve_passes_profile(self, mock_run_server, mock_asyncio, runner):
        mock_asyncio.run = lambda _coro: None

        result = runner.invoke(
            cli,
            ["mcp", "serve", "--password", "pw", "--profile", "core"],
        )
        assert result.exit_code == 0

    @patch("neo4j_agent_memory.cli.main.asyncio")
    @patch("neo4j_agent_memory.mcp.server.run_server", new_callable=AsyncMock)
    def test_serve_passes_session_strategy(self, mock_run_server, mock_asyncio, runner):
        mock_asyncio.run = lambda _coro: None

        result = runner.invoke(
            cli,
            ["mcp", "serve", "--password", "pw", "--session-strategy", "per_day"],
        )
        assert result.exit_code == 0
