"""Smoke tests for google_adk_demo example.

Tests validate that the example:
- Has correct file structure
- Can import required modules
- Uses valid Python syntax
- Follows project conventions
"""

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

# Check if Google ADK is available
try:
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    GOOGLE_ADK_AVAILABLE = True
except ImportError:
    GOOGLE_ADK_AVAILABLE = False


class TestGoogleADKDemoStructure:
    """Test that the google_adk_demo example is properly structured."""

    @pytest.fixture
    def demo_dir(self):
        """Path to the google_adk_demo example."""
        return EXAMPLES_DIR / "google_adk_demo"

    def test_directory_exists(self, demo_dir):
        """Verify the example directory exists."""
        assert demo_dir.exists(), f"Example directory not found: {demo_dir}"

    def test_demo_file_exists(self, demo_dir):
        """Verify demo.py exists."""
        demo = demo_dir / "demo.py"
        assert demo.exists(), f"demo.py not found: {demo}"

    def test_readme_exists(self, demo_dir):
        """Verify README.md exists."""
        readme = demo_dir / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_env_example_exists(self, demo_dir):
        """Verify .env.example exists."""
        env_example = demo_dir / ".env.example"
        assert env_example.exists(), f".env.example not found: {env_example}"

    def test_demo_has_docstring(self, demo_dir):
        """Verify demo.py has a module docstring."""
        demo = demo_dir / "demo.py"
        content = demo.read_text()
        assert content.strip().startswith("#!/usr/bin/env python") or content.strip().startswith(
            '"""'
        ), "demo.py should have a module docstring or shebang"

    def test_demo_has_main_function(self, demo_dir):
        """Verify demo.py has async main function."""
        demo = demo_dir / "demo.py"
        content = demo.read_text()
        assert "async def main():" in content, "demo.py should have 'async def main()'"
        assert 'if __name__ == "__main__":' in content, "demo.py should have main entry point"

    def test_demo_valid_python(self, demo_dir):
        """Verify demo.py is valid Python syntax."""
        demo = demo_dir / "demo.py"
        source_code = demo.read_text()
        try:
            ast.parse(source_code)
        except SyntaxError as e:
            pytest.fail(f"Syntax error in demo.py: {e}")

    def test_demo_uses_async_context_manager(self, demo_dir):
        """Verify demo.py uses async context manager for MemoryClient."""
        demo = demo_dir / "demo.py"
        content = demo.read_text()
        assert "async with MemoryClient" in content, "demo.py should use 'async with MemoryClient'"

    def test_demo_uses_neo4j_memory_service(self, demo_dir):
        """Verify demo.py uses Neo4jMemoryService."""
        demo = demo_dir / "demo.py"
        content = demo.read_text()
        assert "Neo4jMemoryService" in content, "demo.py should use Neo4jMemoryService"


class TestGoogleADKDemoImports:
    """Test that required imports for the ADK demo work."""

    def test_memory_client_importable(self):
        """Verify MemoryClient can be imported."""
        from neo4j_agent_memory import MemoryClient

        assert MemoryClient is not None

    def test_memory_settings_importable(self):
        """Verify MemorySettings can be imported."""
        from neo4j_agent_memory import MemorySettings

        assert MemorySettings is not None

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_neo4j_memory_service_importable(self):
        """Verify Neo4jMemoryService can be imported."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        assert Neo4jMemoryService is not None

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_neo4j_memory_service_has_required_methods(self):
        """Verify Neo4jMemoryService has expected methods."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        expected_methods = [
            "add_session_to_memory",
            "search_memory",
            "get_memories_for_session",
            "add_memory",
        ]
        for method_name in expected_methods:
            assert hasattr(Neo4jMemoryService, method_name), (
                f"Neo4jMemoryService should have {method_name} method"
            )

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_neo4j_memory_service_methods_are_async(self):
        """Verify Neo4jMemoryService methods are async."""
        import asyncio

        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        async_methods = [
            "add_session_to_memory",
            "search_memory",
            "get_memories_for_session",
            "add_memory",
        ]
        for method_name in async_methods:
            method = getattr(Neo4jMemoryService, method_name)
            assert asyncio.iscoroutinefunction(method), (
                f"Neo4jMemoryService.{method_name} should be async"
            )


class TestGoogleADKDemoFeatures:
    """Feature validation for google_adk_demo example."""

    @pytest.fixture
    def demo_dir(self):
        """Path to the google_adk_demo example."""
        return EXAMPLES_DIR / "google_adk_demo"

    def test_demo_uses_session_strategy(self, demo_dir):
        """Verify demo references SessionStrategy."""
        demo = demo_dir / "demo.py"
        if not demo.exists():
            pytest.skip("demo.py not found")
        content = demo.read_text()
        assert "SessionStrategy" in content, "demo.py should reference SessionStrategy"

    def test_demo_uses_memory_integration(self, demo_dir):
        """Verify demo references MemoryIntegration."""
        demo = demo_dir / "demo.py"
        if not demo.exists():
            pytest.skip("demo.py not found")
        content = demo.read_text()
        assert "MemoryIntegration" in content, "demo.py should reference MemoryIntegration"
