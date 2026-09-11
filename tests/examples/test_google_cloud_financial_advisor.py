"""Validation tests for the google-cloud-financial-advisor example application.

These tests validate that the example app:
- Has correct directory structure
- Has required configuration files
- Has expected backend modules (agents, tools, API routes, models)

Note: These are NOT runtime tests - they validate structure and imports only.
Running the full app requires separate infrastructure (Docker, Neo4j, GCP credentials).
"""

from pathlib import Path

import pytest

from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


class TestGoogleCloudFinancialAdvisor:
    """Validation tests for the google-cloud-financial-advisor example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the google-cloud-financial-advisor example."""
        return EXAMPLES_DIR / "financial-services-advisor" / "google-cloud-financial-advisor"

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
        """Verify Makefile exists."""
        makefile = app_dir / "Makefile"
        assert makefile.exists(), f"Makefile not found: {makefile}"

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

    def test_backend_config_module_exists(self, app_dir):
        """Verify backend config.py exists."""
        config = app_dir / "backend" / "src" / "config.py"
        assert config.exists(), f"config.py not found: {config}"

    def test_backend_main_has_health_endpoint(self, app_dir):
        """Verify backend has health check endpoint."""
        main = app_dir / "backend" / "src" / "main.py"
        content = main.read_text(encoding="utf-8")
        assert "/health" in content, "Backend should have /health endpoint"
        assert "health" in content, "Backend should have health check function"

    def test_backend_has_agents_module(self, app_dir):
        """Verify backend has agents module with expected agent files."""
        agents_dir = app_dir / "backend" / "src" / "agents"
        assert agents_dir.exists(), f"Agents directory not found: {agents_dir}"

        expected_agents = [
            "supervisor.py",
            "kyc_agent.py",
            "aml_agent.py",
            "compliance_agent.py",
            "relationship_agent.py",
            "prompts.py",
        ]
        for agent_file in expected_agents:
            assert (agents_dir / agent_file).exists(), f"Agent file not found: {agent_file}"

    def test_backend_has_tools_module(self, app_dir):
        """Verify backend has tools module with expected tool files."""
        tools_dir = app_dir / "backend" / "src" / "tools"
        assert tools_dir.exists(), f"Tools directory not found: {tools_dir}"

        expected_tools = [
            "kyc_tools.py",
            "aml_tools.py",
            "compliance_tools.py",
            "relationship_tools.py",
        ]
        for tool_file in expected_tools:
            assert (tools_dir / tool_file).exists(), f"Tool file not found: {tool_file}"

    def test_backend_has_api_routes(self, app_dir):
        """Verify backend has API routes."""
        routes_dir = app_dir / "backend" / "src" / "api" / "routes"
        assert routes_dir.exists(), f"Routes directory not found: {routes_dir}"

        expected_routes = [
            "chat.py",
            "alerts.py",
            "customers.py",
            "graph.py",
            "investigations.py",
            "traces.py",
        ]
        for route in expected_routes:
            assert (routes_dir / route).exists(), f"Route file not found: {route}"

    def test_backend_has_models_module(self, app_dir):
        """Verify backend has models module."""
        models_dir = app_dir / "backend" / "src" / "models"
        assert models_dir.exists(), f"Models directory not found: {models_dir}"

        expected_models = ["chat.py", "customer.py", "alert.py", "investigation.py"]
        for model_file in expected_models:
            assert (models_dir / model_file).exists(), f"Model file not found: {model_file}"

    def test_frontend_package_json_exists(self, app_dir):
        """Verify frontend package.json exists."""
        package_json = app_dir / "frontend" / "package.json"
        assert package_json.exists(), f"package.json not found: {package_json}"

    def test_frontend_has_src_directory(self, app_dir):
        """Verify frontend has src directory with expected structure."""
        frontend_src = app_dir / "frontend" / "src"
        assert frontend_src.exists(), f"Frontend src not found: {frontend_src}"

        expected_dirs = ["components", "lib", "hooks"]
        for dir_name in expected_dirs:
            assert (frontend_src / dir_name).exists(), f"Frontend src/{dir_name} not found"

    def test_frontend_has_agent_stream_hook(self, app_dir):
        """Verify the useAgentStream hook exists."""
        hook = app_dir / "frontend" / "src" / "hooks" / "useAgentStream.ts"
        assert hook.exists(), f"useAgentStream hook not found: {hook}"

    def test_frontend_has_agent_visualization_components(self, app_dir):
        """Verify frontend has agent visualization components."""
        chat_dir = app_dir / "frontend" / "src" / "components" / "Chat"
        expected = [
            "ChatInterface.tsx",
            "AgentOrchestrationView.tsx",
            "AgentActivityTimeline.tsx",
            "ToolCallCard.tsx",
            "MemoryAccessIndicator.tsx",
        ]
        for component in expected:
            assert (chat_dir / component).exists(), f"Component not found: {component}"

    def test_frontend_package_has_an_animation_library(self, app_dir):
        """Verify the frontend depends on `motion` (or its predecessor framer-motion)."""
        package_json = app_dir / "frontend" / "package.json"
        content = package_json.read_text(encoding="utf-8")
        assert '"motion"' in content or '"framer-motion"' in content, (
            "Frontend should depend on motion"
        )

    def test_backend_chat_has_stream_endpoint(self, app_dir):
        """Verify chat.py has the SSE streaming endpoint."""
        chat = app_dir / "backend" / "src" / "api" / "routes" / "chat.py"
        content = chat.read_text(encoding="utf-8")
        assert "chat_stream" in content, "chat.py should have chat_stream function"
        assert "text/event-stream" in content, "chat.py should use SSE content type"
        assert "_sse_event" in content, "chat.py should have _sse_event helper"
        assert "_truncate_result" in content, "chat.py should have _truncate_result helper"

    def test_backend_has_shared_adk_event_consumer(self, app_dir):
        """One module owns the ADK Event shape; both chat routes use it."""
        events = app_dir / "backend" / "src" / "services" / "adk_events.py"
        assert events.exists(), f"adk_events.py not found: {events}"
        content = events.read_text(encoding="utf-8")
        assert "INTERNAL_FUNCTIONS" in content, "Should filter ADK-internal functions"
        assert "transfer_to_agent" in content, "Should reference transfer_to_agent"
        assert "async def consume_run" in content, "Should expose consume_run"
        assert "async def collect_run" in content, "Should expose collect_run"

    def test_backend_has_trace_writer(self, app_dir):
        """Reasoning traces are recorded with the audit-grade API."""
        writer = app_dir / "backend" / "src" / "services" / "trace_writer.py"
        assert writer.exists(), f"trace_writer.py not found: {writer}"
        content = writer.read_text(encoding="utf-8")
        for expected in ("triggered_by_message_id", "touched_entities", "TraceOutcome"):
            assert expected in content, f"trace_writer.py should use {expected}"

    def test_backend_has_its_own_test_suite(self, app_dir):
        """`make test` must collect something: testpaths = ["tests"]."""
        tests_dir = app_dir / "backend" / "tests"
        assert tests_dir.exists(), f"backend/tests not found: {tests_dir}"
        assert (tests_dir / "conftest.py").exists()
        assert list(tests_dir.glob("test_*.py")), "backend/tests has no test modules"

    def test_backend_readme_is_not_empty(self, app_dir):
        """pyproject declares README.md as the package readme."""
        readme = app_dir / "backend" / "README.md"
        assert readme.exists()
        assert len(readme.read_text(encoding="utf-8").strip()) > 200, (
            "backend/README.md should describe how to run and navigate the backend"
        )

    def test_image_build_does_not_depend_on_the_editable_path(self, app_dir):
        """The editable [tool.uv.sources] path lies outside the Docker context."""
        dockerfile = (app_dir / "backend" / "Dockerfile").read_text(encoding="utf-8")
        run_lines = [
            line for line in dockerfile.splitlines() if line.strip().startswith(("RUN", "CMD"))
        ]
        assert not any("uv sync" in line for line in run_lines), (
            "uv sync resolves [tool.uv.sources], whose path is outside the build context"
        )
        assert "requirements-docker.txt" in dockerfile
        assert (app_dir / "backend" / "requirements-docker.txt").exists()

    def test_cloudbuild_uses_the_current_path(self, app_dir):
        """The example was relocated under financial-services-advisor/."""
        cloudbuild = (app_dir / "infrastructure" / "cloudbuild.yaml").read_text(encoding="utf-8")
        assert "examples/financial-services-advisor/google-cloud-financial-advisor" in cloudbuild
        assert "dir: 'examples/google-cloud-financial-advisor'" not in cloudbuild

    def test_compose_mounts_the_shared_data_directory(self, app_dir):
        """The sample dataset lives one level up, shared with the AWS sibling."""
        compose = (app_dir / "docker-compose.yml").read_text(encoding="utf-8")
        assert "../data:/app" in compose, "data-loader should mount ../data"
        assert "neo4j:5.26-community" in compose, "pin the Neo4j LTS image"

    def test_no_retired_embedding_model_is_referenced(self, app_dir):
        """text-embedding-004 was shut down on 2026-01-14."""
        for relative in (
            ".env.example",
            "docker-compose.yml",
            "backend/src/config.py",
        ):
            content = (app_dir / relative).read_text(encoding="utf-8")
            occurrences = [
                line
                for line in content.splitlines()
                if "text-embedding-004" in line and "retired" not in line.lower()
            ]
            assert not occurrences, f"{relative} still uses text-embedding-004: {occurrences}"
            assert "gemini-embedding-001" in content, f"{relative} should name the current model"

    def test_no_pre_relocation_paths_in_docs(self, app_dir):
        for relative in ("README.md", "GETTING_STARTED.md"):
            content = (app_dir / relative).read_text(encoding="utf-8")
            assert "examples/google-cloud-financial-advisor" not in content, relative

    def test_backend_traces_route_exists(self, app_dir):
        """Verify traces.py has expected endpoints."""
        traces = app_dir / "backend" / "src" / "api" / "routes" / "traces.py"
        content = traces.read_text(encoding="utf-8")
        assert "get_session_traces" in content, "Should have get_session_traces"
        assert "get_trace_detail" in content, "Should have get_trace_detail"

    def test_backend_main_registers_traces_router(self, app_dir):
        """Verify main.py registers the traces router."""
        main = app_dir / "backend" / "src" / "main.py"
        content = main.read_text(encoding="utf-8")
        assert "traces" in content, "main.py should import and register traces router"

    def test_backend_neo4j_service_uses_merge_for_alerts(self, app_dir):
        """Verify create_alert uses MERGE instead of CREATE."""
        neo4j_service = app_dir / "backend" / "src" / "services" / "neo4j_service.py"
        content = neo4j_service.read_text(encoding="utf-8")
        assert "MERGE (a:Alert" in content, "create_alert should use MERGE"
        assert "ON CREATE SET" in content, "create_alert should use ON CREATE SET"

    def test_frontend_api_has_streaming_support(self, app_dir):
        """Verify api.ts has streaming functions."""
        api = app_dir / "frontend" / "src" / "lib" / "api.ts"
        content = api.read_text(encoding="utf-8")
        assert "streamChatMessage" in content, "api.ts should have streamChatMessage"
        assert "getSessionTraces" in content, "api.ts should have getSessionTraces"
        assert "AgentEvent" in content, "api.ts should define AgentEvent type"

    def test_backend_memory_service_uses_extraction_config(self, app_dir):
        """Verify memory service uses ExtractionConfig."""
        service = app_dir / "backend" / "src" / "services" / "memory_service.py"
        content = service.read_text(encoding="utf-8")
        assert "ExtractionConfig" in content, "memory_service.py should use ExtractionConfig"

    def test_backend_memory_service_references_dedup(self, app_dir):
        """Verify memory service references DeduplicationConfig."""
        service = app_dir / "backend" / "src" / "services" / "memory_service.py"
        content = service.read_text(encoding="utf-8")
        assert "DeduplicationConfig" in content, (
            "memory_service.py should reference DeduplicationConfig"
        )

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify the backend pins a current, capped neo4j-agent-memory range."""
        pin = assert_library_pin(app_dir / "backend" / "pyproject.toml")
        assert "google-adk" in pin.extras, "this example needs the [google-adk] extra"
