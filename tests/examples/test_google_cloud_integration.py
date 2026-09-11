"""Smoke tests for google_cloud_integration example scripts.

Tests validate that the example scripts:
- Exist and have correct structure
- Use valid Python syntax
- Can import required modules
- Follow project conventions
"""

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
INTEGRATION_DIR = EXAMPLES_DIR / "google_cloud_integration"

# Check availability of optional dependencies
try:
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False

try:
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    GOOGLE_ADK_AVAILABLE = True
except ImportError:
    GOOGLE_ADK_AVAILABLE = False

try:
    from neo4j_agent_memory.mcp.server import Neo4jMemoryMCPServer

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


class TestGoogleCloudIntegrationStructure:
    """Test that all google_cloud_integration example scripts exist and are valid."""

    EXPECTED_SCRIPTS = [
        "vertex_ai_embeddings.py",
        "adk_memory_service.py",
        "mcp_server_demo.py",
        "full_pipeline.py",
    ]

    def test_directory_exists(self):
        """Verify the example directory exists."""
        assert INTEGRATION_DIR.exists(), f"Example directory not found: {INTEGRATION_DIR}"

    def test_readme_exists(self):
        """Verify README.md exists."""
        readme = INTEGRATION_DIR / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_env_example_exists(self):
        """Verify .env.example exists."""
        env_example = INTEGRATION_DIR / ".env.example"
        assert env_example.exists(), f".env.example not found: {env_example}"

    @pytest.mark.parametrize("script_name", EXPECTED_SCRIPTS)
    def test_script_exists(self, script_name):
        """Verify each expected script file exists."""
        script = INTEGRATION_DIR / script_name
        assert script.exists(), f"Script not found: {script}"

    @pytest.mark.parametrize("script_name", EXPECTED_SCRIPTS)
    def test_script_valid_python(self, script_name):
        """Verify each script is valid Python syntax."""
        script = INTEGRATION_DIR / script_name
        source_code = script.read_text(encoding="utf-8")
        try:
            ast.parse(source_code)
        except SyntaxError as e:
            pytest.fail(f"Syntax error in {script_name}: {e}")

    @pytest.mark.parametrize("script_name", EXPECTED_SCRIPTS)
    def test_script_has_docstring(self, script_name):
        """Verify each script has a module docstring."""
        script = INTEGRATION_DIR / script_name
        content = script.read_text(encoding="utf-8")
        assert content.strip().startswith("#!/usr/bin/env python") or content.strip().startswith(
            '"""'
        ), f"{script_name} should have a module docstring or shebang"


class TestGoogleCloudIntegrationImports:
    """Test that required imports for the integration examples work."""

    def test_memory_client_importable(self):
        """Verify MemoryClient can be imported."""
        from neo4j_agent_memory import MemoryClient

        assert MemoryClient is not None

    @pytest.mark.skipif(not VERTEX_AI_AVAILABLE, reason="vertex-ai not installed")
    def test_vertex_ai_embedder_importable(self):
        """Verify VertexAIEmbedder can be imported."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        assert VertexAIEmbedder is not None

    @pytest.mark.skipif(not VERTEX_AI_AVAILABLE, reason="vertex-ai not installed")
    def test_vertex_ai_embedder_has_properties(self):
        """Verify VertexAIEmbedder has expected public properties."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder()
        assert embedder.model == "gemini-embedding-001"
        assert embedder.task_type == "RETRIEVAL_DOCUMENT"
        assert embedder.dimensions == 768

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_neo4j_memory_service_importable(self):
        """Verify Neo4jMemoryService can be imported."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        assert Neo4jMemoryService is not None

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_mcp_server_importable(self):
        """Verify Neo4jMemoryMCPServer can be imported."""
        from neo4j_agent_memory.mcp.server import Neo4jMemoryMCPServer

        assert Neo4jMemoryMCPServer is not None

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_mcp_tools_registrable(self):
        """Verify MCP tool registration function can be imported."""
        from neo4j_agent_memory.mcp._tools import register_tools

        assert register_tools is not None

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_mcp_tools_registrable_with_profile(self):
        """Verify MCP tool registration accepts profile kwarg."""
        from fastmcp import FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile="core")  # Should not raise


@pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
class TestMCPToolDefinitions:
    """Test that MCP tool definitions are complete and valid via FastMCP."""

    def test_extended_profile_tool_count(self):
        """Verify extended profile registers 16 MCP tools."""
        import asyncio

        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile="extended")

        async def _check():
            async with Client(mcp) as client:
                tools = await client.list_tools()
                assert len(tools) == 16

        asyncio.run(_check())

    def test_extended_profile_tool_names(self):
        """Verify the expected tool names for extended profile."""
        import asyncio

        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile="extended")

        async def _check():
            async with Client(mcp) as client:
                tools = await client.list_tools()
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

        asyncio.run(_check())

    def test_core_profile_tool_count(self):
        """Verify core profile registers 6 MCP tools."""
        import asyncio

        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile="core")

        async def _check():
            async with Client(mcp) as client:
                tools = await client.list_tools()
                assert len(tools) == 6

        asyncio.run(_check())

    def test_core_profile_tool_names(self):
        """Verify the expected tool names for core profile."""
        import asyncio

        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile="core")

        async def _check():
            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool_names = {t.name for t in tools}
                assert tool_names == {
                    "memory_search",
                    "memory_get_context",
                    "memory_store_message",
                    "memory_add_entity",
                    "memory_add_preference",
                    "memory_add_fact",
                }

        asyncio.run(_check())
