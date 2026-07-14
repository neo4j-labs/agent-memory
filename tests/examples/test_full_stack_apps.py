"""Validation tests for full-stack example applications.

These tests validate that the example apps:
- Have correct structure
- Can import without errors
- Have valid configurations

Note: These are NOT runtime tests - they validate structure and imports only.
Running the full apps requires separate infrastructure (Docker, Neo4j, API keys).
"""

from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


class TestFullStackChatAgent:
    """Validation tests for the full-stack-chat-agent example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the full-stack-chat-agent example."""
        return EXAMPLES_DIR / "full-stack-chat-agent"

    def test_app_directory_exists(self, app_dir):
        """Verify the example directory exists."""
        assert app_dir.exists(), f"Example directory not found: {app_dir}"

    def test_backend_directory_exists(self, app_dir):
        """Verify the backend directory exists."""
        backend = app_dir / "backend"
        assert backend.exists(), f"Backend directory not found: {backend}"

    def test_frontend_directory_exists(self, app_dir):
        """Verify the frontend directory exists."""
        frontend = app_dir / "frontend"
        assert frontend.exists(), f"Frontend directory not found: {frontend}"

    def test_docker_compose_exists(self, app_dir):
        """Verify docker-compose.yml exists."""
        docker_compose = app_dir / "docker-compose.yml"
        assert docker_compose.exists(), f"docker-compose.yml not found: {docker_compose}"

    def test_readme_exists(self, app_dir):
        """Verify README.md exists."""
        readme = app_dir / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_backend_pyproject_exists(self, app_dir):
        """Verify backend pyproject.toml exists."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        assert pyproject.exists(), f"pyproject.toml not found: {pyproject}"

    def test_backend_pyproject_has_neo4j_agent_memory(self, app_dir):
        """Verify backend depends on neo4j-agent-memory."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert "neo4j-agent-memory" in content, "Backend should depend on neo4j-agent-memory"

    def test_backend_main_module_exists(self, app_dir):
        """Verify backend main.py exists."""
        main = app_dir / "backend" / "src" / "main.py"
        assert main.exists(), f"main.py not found: {main}"

    def test_backend_has_api_routes(self, app_dir):
        """Verify backend has API routes."""
        api_dir = app_dir / "backend" / "src" / "api"
        assert api_dir.exists(), f"API directory not found: {api_dir}"

        routes_dir = api_dir / "routes"
        if routes_dir.exists():
            # Check for expected route files
            expected_routes = ["chat.py", "threads.py", "memory.py"]
            for route in expected_routes:
                route_file = routes_dir / route
                assert route_file.exists(), f"Route file not found: {route_file}"

    def test_backend_has_memory_module(self, app_dir):
        """Verify backend has memory module."""
        memory_dir = app_dir / "backend" / "src" / "memory"
        assert memory_dir.exists(), f"Memory directory not found: {memory_dir}"

    def test_backend_has_agent_module(self, app_dir):
        """Verify backend has agent module."""
        agent_dir = app_dir / "backend" / "src" / "agent"
        assert agent_dir.exists(), f"Agent directory not found: {agent_dir}"

    def test_backend_main_has_health_endpoint(self, app_dir):
        """Verify backend has health check endpoint."""
        main = app_dir / "backend" / "src" / "main.py"
        content = main.read_text(encoding="utf-8")
        assert "/health" in content, "Backend should have /health endpoint"
        assert "health_check" in content, "Backend should have health_check function"

    def test_frontend_package_json_exists(self, app_dir):
        """Verify frontend package.json exists."""
        package_json = app_dir / "frontend" / "package.json"
        assert package_json.exists(), f"package.json not found: {package_json}"


class TestLennysMemory:
    """Validation tests for the lennys-memory example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the lennys-memory example."""
        return EXAMPLES_DIR / "lennys-memory"

    def test_app_directory_exists(self, app_dir):
        """Verify the example directory exists."""
        assert app_dir.exists(), f"Example directory not found: {app_dir}"

    def test_backend_directory_exists(self, app_dir):
        """Verify the backend directory exists."""
        backend = app_dir / "backend"
        assert backend.exists(), f"Backend directory not found: {backend}"

    def test_frontend_directory_exists(self, app_dir):
        """Verify the frontend directory exists."""
        frontend = app_dir / "frontend"
        assert frontend.exists(), f"Frontend directory not found: {frontend}"

    def test_docker_compose_exists(self, app_dir):
        """Verify docker-compose.yml exists."""
        docker_compose = app_dir / "docker-compose.yml"
        assert docker_compose.exists(), f"docker-compose.yml not found: {docker_compose}"

    def test_readme_exists(self, app_dir):
        """Verify README.md exists."""
        readme = app_dir / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_makefile_exists(self, app_dir):
        """Verify Makefile exists (lennys-memory specific)."""
        makefile = app_dir / "Makefile"
        assert makefile.exists(), f"Makefile not found: {makefile}"

    def test_scripts_directory_exists(self, app_dir):
        """Verify scripts directory exists (lennys-memory specific)."""
        scripts_dir = app_dir / "scripts"
        assert scripts_dir.exists(), f"Scripts directory not found: {scripts_dir}"

    def test_backend_pyproject_exists(self, app_dir):
        """Verify backend pyproject.toml exists."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        assert pyproject.exists(), f"pyproject.toml not found: {pyproject}"

    def test_backend_pyproject_has_neo4j_agent_memory(self, app_dir):
        """Verify backend depends on neo4j-agent-memory."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert "neo4j-agent-memory" in content, "Backend should depend on neo4j-agent-memory"

    def test_backend_pyproject_has_extraction_extras(self, app_dir):
        """Verify backend has extraction extras (lennys-memory specific)."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        # lennys-memory should use extraction features
        assert "extraction" in content or "spacy" in content, (
            "Backend should have extraction dependencies"
        )

    def test_backend_main_module_exists(self, app_dir):
        """Verify backend main.py exists."""
        main = app_dir / "backend" / "src" / "main.py"
        assert main.exists(), f"main.py not found: {main}"

    def test_backend_has_api_routes(self, app_dir):
        """Verify backend has API routes."""
        api_dir = app_dir / "backend" / "src" / "api"
        assert api_dir.exists(), f"API directory not found: {api_dir}"

        routes_dir = api_dir / "routes"
        if routes_dir.exists():
            expected_routes = ["chat.py", "threads.py", "memory.py"]
            for route in expected_routes:
                route_file = routes_dir / route
                assert route_file.exists(), f"Route file not found: {route_file}"

    def test_backend_main_has_health_endpoint(self, app_dir):
        """Verify backend has health check endpoint."""
        main = app_dir / "backend" / "src" / "main.py"
        content = main.read_text(encoding="utf-8")
        assert "/health" in content, "Backend should have /health endpoint"

    def test_frontend_package_json_exists(self, app_dir):
        """Verify frontend package.json exists."""
        package_json = app_dir / "frontend" / "package.json"
        assert package_json.exists(), f"package.json not found: {package_json}"


class TestFullStackChatAgentFeatures:
    """Feature validation for the full-stack-chat-agent example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the full-stack-chat-agent example."""
        return EXAMPLES_DIR / "full-stack-chat-agent"

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify backend has version pin for neo4j-agent-memory."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert ">=0.1.0" in content, "Backend should pin neo4j-agent-memory>=0.1.0"

    def test_backend_uses_memory_integration(self, app_dir):
        """Verify backend uses MemoryIntegration for high-level operations."""
        memory_client = app_dir / "backend" / "src" / "memory" / "client.py"
        if not memory_client.exists():
            pytest.skip("memory/client.py not found")
        content = memory_client.read_text(encoding="utf-8")
        assert "MemoryIntegration" in content, "memory/client.py should use MemoryIntegration"

    def test_backend_uses_session_strategy(self, app_dir):
        """Verify backend uses SessionStrategy."""
        memory_client = app_dir / "backend" / "src" / "memory" / "client.py"
        if not memory_client.exists():
            pytest.skip("memory/client.py not found")
        content = memory_client.read_text(encoding="utf-8")
        assert "SessionStrategy" in content, "memory/client.py should use SessionStrategy"

    def test_backend_uses_auto_preferences(self, app_dir):
        """Verify backend uses auto_preferences for preference detection."""
        memory_client = app_dir / "backend" / "src" / "memory" / "client.py"
        if not memory_client.exists():
            pytest.skip("memory/client.py not found")
        content = memory_client.read_text(encoding="utf-8")
        assert "auto_preferences" in content, "memory/client.py should enable auto_preferences"


class TestLennysMemoryFeatures:
    """Feature validation for the lennys-memory example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the lennys-memory example."""
        return EXAMPLES_DIR / "lennys-memory"

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify backend has version pin for neo4j-agent-memory."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert ">=0.1.0" in content, "Backend should pin neo4j-agent-memory>=0.1.0"

    def test_backend_uses_extraction_config(self, app_dir):
        """Verify backend uses ExtractionConfig."""
        # Check memory service or client files
        for candidate in [
            app_dir / "backend" / "src" / "services" / "memory_service.py",
            app_dir / "backend" / "src" / "memory" / "client.py",
        ]:
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                if "ExtractionConfig" in content:
                    return
        pytest.fail("Backend should use ExtractionConfig in memory configuration")

    def test_backend_uses_dedup_config(self, app_dir):
        """Verify backend uses DeduplicationConfig."""
        for candidate in [
            app_dir / "backend" / "src" / "services" / "memory_service.py",
            app_dir / "backend" / "src" / "memory" / "client.py",
        ]:
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                if "DeduplicationConfig" in content:
                    return
        pytest.fail("Backend should use DeduplicationConfig")


class TestFullStackAppsImports:
    """Test that full-stack app modules can be imported (with mocked dependencies)."""

    def test_neo4j_agent_memory_importable(self):
        """Verify neo4j-agent-memory can be imported."""
        import neo4j_agent_memory

        assert neo4j_agent_memory is not None

    def test_memory_client_importable(self):
        """Verify MemoryClient can be imported."""
        from neo4j_agent_memory import MemoryClient

        assert MemoryClient is not None

    def test_fastapi_importable(self):
        """Verify FastAPI can be imported (required for backends)."""
        try:
            import fastapi

            assert fastapi is not None
        except ImportError:
            pytest.skip("FastAPI not installed")

    def test_pydantic_ai_importable(self):
        """Verify pydantic-ai can be imported (required for backends)."""
        try:
            import pydantic_ai

            assert pydantic_ai is not None
        except ImportError:
            pytest.skip("pydantic-ai not installed")


class TestExampleConsistency:
    """Test that all examples follow consistent patterns."""

    def test_all_examples_use_async_context_manager(self):
        """Verify all examples use async context manager pattern for MemoryClient."""
        simple_examples = [
            EXAMPLES_DIR / "basic_usage.py",
            EXAMPLES_DIR / "langchain_agent.py",
            EXAMPLES_DIR / "pydantic_ai_agent.py",
        ]

        for example in simple_examples:
            if example.exists():
                content = example.read_text(encoding="utf-8")
                # Should use async with for proper resource management
                assert "async with MemoryClient" in content, (
                    f"{example.name} should use 'async with MemoryClient'"
                )

    def test_all_examples_have_main_function(self):
        """Verify all simple examples have async main function."""
        simple_examples = [
            EXAMPLES_DIR / "basic_usage.py",
            EXAMPLES_DIR / "entity_resolution.py",
            EXAMPLES_DIR / "langchain_agent.py",
            EXAMPLES_DIR / "pydantic_ai_agent.py",
        ]

        for example in simple_examples:
            if example.exists():
                content = example.read_text(encoding="utf-8")
                # Match the definition regardless of any return-type annotation
                # (e.g. `async def main() -> None:`).
                assert "async def main(" in content, (
                    f"{example.name} should have 'async def main()'"
                )
                assert 'if __name__ == "__main__":' in content, (
                    f"{example.name} should have main entry point"
                )

    def test_all_examples_have_docstrings(self):
        """Verify all examples have module docstrings."""
        all_examples = [
            EXAMPLES_DIR / "basic_usage.py",
            EXAMPLES_DIR / "entity_resolution.py",
            EXAMPLES_DIR / "langchain_agent.py",
            EXAMPLES_DIR / "pydantic_ai_agent.py",
        ]

        for example in all_examples:
            if example.exists():
                content = example.read_text(encoding="utf-8")
                # Should start with docstring
                assert content.strip().startswith(
                    "#!/usr/bin/env python"
                ) or content.strip().startswith('"""'), (
                    f"{example.name} should have module docstring"
                )

    def test_full_stack_apps_have_consistent_structure(self):
        """Verify full-stack apps have consistent directory structure."""
        full_stack_apps = [
            EXAMPLES_DIR / "full-stack-chat-agent",
            EXAMPLES_DIR / "lennys-memory",
            EXAMPLES_DIR / "financial-services-advisor" / "google-cloud-financial-advisor",
        ]

        for app_dir in full_stack_apps:
            if app_dir.exists():
                # All should have backend with src directory
                assert (app_dir / "backend" / "src").exists(), (
                    f"{app_dir.name} should have backend/src directory"
                )

                # All should have frontend
                assert (app_dir / "frontend").exists(), (
                    f"{app_dir.name} should have frontend directory"
                )

                # All should have docker-compose
                assert (app_dir / "docker-compose.yml").exists(), (
                    f"{app_dir.name} should have docker-compose.yml"
                )
