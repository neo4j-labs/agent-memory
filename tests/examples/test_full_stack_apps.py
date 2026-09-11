"""Validation tests for full-stack example applications.

Two layers:

* **Structure / feature checks** — cheap assertions that the directory layout
  and manifests are intact. These are the historical tests.
* **Route smoke tests** (``TestFullStackChatRoutes``) — the full-stack chat
  agent's FastAPI app is actually built and driven over
  ``httpx.ASGITransport`` against a throwaway Neo4j: ``POST /threads`` ->
  ``POST /chat`` -> ``GET /threads/{id}`` -> ``GET /api/memory/*``. Substring
  assertions could not catch the 404-on-every-thread bug, the empty tool-call
  arguments, or the unreachable ``MemoryIntegration``; these can.

The chat turn uses PydanticAI's ``test`` model (``AGENT_MODEL=test``) and a
deterministic in-process embedder, so the suite needs no API key and makes no
network call.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.examples._manifests import MIN_EXAMPLE_PIN, assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
CHAT_AGENT_DIR = EXAMPLES_DIR / "full-stack-chat-agent"
CHAT_BACKEND_DIR = CHAT_AGENT_DIR / "backend"

# The library floor these examples declare is still pre-0.5.0; the bump lands
# with the phase-2 example modernisation. `strict=False` so the assertion
# reports XPASS (not a failure) the moment the manifest is bumped.
PINS_BUMPED_IN_PHASE_2 = pytest.mark.xfail(
    strict=False,
    reason=f"pins bumped in phase 2 — manifest floor is below {MIN_EXAMPLE_PIN} (xc-F02)",
)


class TestFullStackChatAgent:
    """Validation tests for the full-stack-chat-agent example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the full-stack-chat-agent example."""
        return CHAT_AGENT_DIR

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

    def test_no_duplicate_requirements_manifest(self, app_dir):
        """pyproject.toml is the single source of truth for the backend.

        A second `requirements.txt` drifted for months: it installed the
        library from `git+…@main` with no pin and without the `[openai]`
        extra, while pyproject used an editable path.
        """
        assert not (app_dir / "backend" / "requirements.txt").exists(), (
            "backend/requirements.txt is back — nixpacks now runs `uv sync --frozen`, "
            "so a second manifest can only drift out of step with pyproject.toml"
        )

    def test_quick_start_credentials_agree(self, app_dir):
        """compose, .env.example and the README must use one Neo4j password.

        Three different values used to mean that following the README verbatim
        booted the backend with memory silently disabled.
        """
        compose = (app_dir / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (app_dir / "backend" / ".env.example").read_text(encoding="utf-8")
        readme = (app_dir / "README.md").read_text(encoding="utf-8")

        auth = next(line for line in compose.splitlines() if "NEO4J_AUTH:" in line)
        password = auth.split("neo4j/", 1)[1].strip()

        assert f"NEO4J_PASSWORD={password}" in env_example, (
            f"backend/.env.example must use the compose password {password!r}"
        )
        assert f"`{password}`" in readme, f"README must document the compose password {password!r}"

    def test_compose_pins_a_current_neo4j_with_apoc(self, app_dir):
        """Neo4j 5.26 LTS (or newer) plus APOC, which the schema tool needs."""
        compose = (app_dir / "docker-compose.yml").read_text(encoding="utf-8")
        assert "neo4j:5.26-community" in compose or "neo4j:2026" in compose, (
            "docker-compose.yml should pin Neo4j 5.26 LTS or a current calendar release"
        )
        assert "apoc" in compose, "APOC is required by the get_database_schema tool"


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
    """Feature validation for the full-stack-chat-agent example.

    These are deliberately *behavioural* where the feature can be reached
    without a database: the previous versions asserted that the string
    ``"auto_preferences"`` appeared in a file, which passed while the feature
    itself was dead code.
    """

    @pytest.fixture
    def app_dir(self):
        """Path to the full-stack-chat-agent example."""
        return CHAT_AGENT_DIR

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify the backend pins a current, capped neo4j-agent-memory range."""
        pin = assert_library_pin(app_dir / "backend" / "pyproject.toml")
        assert "openai" in pin.extras, "chat agent needs the [openai] extra"

    def test_backend_pins_pydantic_ai_2x(self, app_dir):
        """The agent code is 2.x-shaped; the floor must say so."""
        content = (app_dir / "backend" / "pyproject.toml").read_text(encoding="utf-8")
        assert "pydantic-ai-slim[openai]>=2.0,<3" in content or "pydantic-ai>=2.0,<3" in content, (
            "backend must pin PydanticAI 2.x with a cap at the next major"
        )

    def test_backend_uses_pydantic_ai_2x_streaming_idioms(self, app_dir):
        """`run_stream_events` + `args_as_dict`, not the 1.x/0.x shapes."""
        chat = (app_dir / "backend" / "src" / "api" / "routes" / "chat.py").read_text(
            encoding="utf-8"
        )
        assert "run_stream_events(" in chat, (
            "chat route should stream with agent.run_stream_events() so tool events "
            "are emitted when they happen"
        )
        assert "args_as_dict()" in chat, (
            "tool-call arguments must be read with part.args_as_dict(); the "
            "`part.args.args_dict` / `part.args.model_dump` probes can never match"
        )
        for removed in ("run_stream(", ".args_dict", "all_messages()"):
            assert removed not in chat, f"chat.py still uses the legacy {removed!r} shape"

        agent = (app_dir / "backend" / "src" / "agent" / "agent.py").read_text(encoding="utf-8")
        assert "@agent.instructions" in agent, "use the 2.x instructions hook"
        assert "gpt-4o" not in agent, "gpt-4o is retired; the model comes from AGENT_MODEL"

    def test_memory_integration_wraps_the_connected_client(self, app_dir):
        """MemoryIntegration must reuse the client, not open a second driver."""
        client = (app_dir / "backend" / "src" / "memory" / "client.py").read_text(encoding="utf-8")
        construction = re.search(r"MemoryIntegration\((.*?)\)", client, re.DOTALL)
        assert construction is not None, "memory/client.py should build a MemoryIntegration"
        assert "client=_memory_client" in construction.group(1), (
            "MemoryIntegration must be constructed with client=_memory_client; "
            "constructing it from neo4j_uri/neo4j_password opens a second driver"
        )
        assert "neo4j_uri=" not in construction.group(1)
        chat = (app_dir / "backend" / "src" / "api" / "routes" / "chat.py").read_text(
            encoding="utf-8"
        )
        assert "integration.store_message(" in chat, (
            "the chat route must actually call MemoryIntegration.store_message(), "
            "otherwise auto_extract / auto_preferences never run"
        )

    def test_no_private_client_access(self, app_dir):
        """`client.query.cypher` / `client.graph`, never `client._client`.

        Parses each module rather than grepping, so the prose in a docstring
        that *names* the anti-pattern does not trip the check.
        """
        offenders = []
        for path in sorted((app_dir / "backend" / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # `<anything>._client` — but not the module-level singleton
                # `_memory_client`, which is a plain module global.
                if isinstance(node, ast.Attribute) and node.attr == "_client":
                    offenders.append(f"{path.relative_to(app_dir)}:{node.lineno}")
        assert not offenders, (
            "these lines reach into the private MemoryClient._client: "
            f"{offenders}. Use client.query.cypher() (portable) or client.graph."
        )

    def test_read_only_guard_is_not_a_substring_scan(self, app_dir):
        """The news-graph Cypher tool must not hand-roll a keyword scan."""
        tools = (app_dir / "backend" / "src" / "agent" / "tools.py").read_text(encoding="utf-8")
        assert "is_read_only_query" in tools, (
            "reuse neo4j_agent_memory.core.query.is_read_only_query for the pre-check"
        )
        assert "session.execute_read" in tools, (
            "the boundary must be a managed read transaction, so the *server* rejects writes"
        )
        assert "write_keywords" not in tools, "the hand-rolled keyword list is back"

    def test_no_provider_key_in_cypher_parameters(self, app_dir):
        """F04: the OpenAI key must never travel to the news database."""
        tools = (app_dir / "backend" / "src" / "agent" / "tools.py").read_text(encoding="utf-8")
        assert "genai.vector.encode" not in tools, "embed application-side and pass only the vector"
        assert "openai_key" not in tools, "no API key may be passed as a Cypher parameter"
        assert "text-embedding-ada-002" not in tools, "ada-002 is retired"

    def test_news_driver_is_application_scoped(self, app_dir):
        """E02: one pooled driver for the process, verified at startup."""
        main = (app_dir / "backend" / "src" / "main.py").read_text(encoding="utf-8")
        assert "verify_connectivity()" in main, (
            "verify the news driver in the lifespan so a bad URI fails loudly"
        )
        assert "app.state.news_driver" in main
        chat = (app_dir / "backend" / "src" / "api" / "routes" / "chat.py").read_text(
            encoding="utf-8"
        )
        assert "create_news_driver" not in chat, (
            "a fresh AsyncDriver per chat request throws away the connection pool"
        )


class TestLennysMemoryFeatures:
    """Feature validation for the lennys-memory example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the lennys-memory example."""
        return EXAMPLES_DIR / "lennys-memory"

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify the backend pins a current, capped neo4j-agent-memory range."""
        pin = assert_library_pin(app_dir / "backend" / "pyproject.toml")
        assert "extraction" in pin.extras, "lennys-memory needs the [extraction] extra"

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
                # Proper resource management via either the `async with
                # MemoryClient` context-manager idiom, or the backend-typed
                # `connect()` factory paired with an explicit `client.close()`.
                uses_context_manager = "async with MemoryClient" in content
                uses_connect = "connect(" in content and ".close()" in content
                assert uses_context_manager or uses_connect, (
                    f"{example.name} should manage the client via "
                    f"'async with MemoryClient' or 'connect(...)' + 'await client.close()'"
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


# =============================================================================
# Route smoke tests — the chat agent's FastAPI app, driven for real
# =============================================================================


class _DeterministicEmbedder:
    """In-process embedding provider: no API key, no network, stable vectors.

    Sized to match whatever the target database's vector indexes expect, set
    by the fixture, so the test never trips the library's dimension check.
    """

    def __init__(self, dimensions: int = 1536, model: str = "test/deterministic") -> None:
        self.dimensions = dimensions
        self.model = model

    async def embed(self, texts: Any) -> list[list[float]]:
        return [self._one(text) for text in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._one(text)

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self.dimensions)]


@contextlib.contextmanager
def _chat_backend_on_path() -> Iterator[None]:
    """Import the chat backend's ``src`` package without leaking it.

    Both full-stack backends name their package ``src``, and the repo root has
    its own ``src/`` directory, so the import has to be bracketed: drop any
    cached ``src*`` modules, put this backend first on ``sys.path``, and put
    everything back afterwards.
    """
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "src" or name.startswith("src.")
    }
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(CHAT_BACKEND_DIR))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
            del sys.modules[name]
        with contextlib.suppress(ValueError):
            sys.path.remove(str(CHAT_BACKEND_DIR))
        sys.modules.update(saved_modules)
        importlib.invalidate_caches()


def _managed_index_dimensions(uri: str, username: str, password: str) -> int:
    """Dimensions the target database's library-managed vector indexes use."""
    from neo4j import GraphDatabase

    managed = {
        "message_embedding_idx",
        "entity_embedding_idx",
        "preference_embedding_idx",
        "fact_embedding_idx",
        "task_embedding_idx",
        "step_embedding_idx",
    }
    with GraphDatabase.driver(uri, auth=(username, password)) as driver:
        records, _, _ = driver.execute_query(
            "SHOW VECTOR INDEXES YIELD name, options "
            "RETURN name, options.indexConfig['vector.dimensions'] AS dims"
        )
    for record in records:
        if record["name"] in managed and record["dims"]:
            return int(record["dims"])
    return 1536


@pytest.fixture
def chat_backend_env(monkeypatch) -> dict[str, str]:
    """Point the backend at the throwaway Neo4j and the `test` agent model.

    Every setting is pinned explicitly: the backend's own defaults point at
    ``bolt://localhost:7687``, which on a developer machine is a real database.
    """
    uri = os.getenv("NEO4J_URI")
    if not uri:
        pytest.skip("NEO4J_URI not set — run with the throwaway Neo4j (port 7688)")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "test-password")

    for key, value in {
        "NEO4J_URI": uri,
        "NEO4J_USERNAME": username,
        "NEO4J_PASSWORD": password,
        # Same instance doubles as the news graph; it simply has no :Article.
        "NEWS_GRAPH_URI": uri,
        "NEWS_GRAPH_USERNAME": username,
        "NEWS_GRAPH_PASSWORD": password,
        "NEWS_GRAPH_DATABASE": "neo4j",
        # PydanticAI's built-in stub model: calls every tool, no network.
        "AGENT_MODEL": "test",
        "EXTRACTION_MODE": "none",
        "EMBEDDING_MODEL": "test/deterministic",
        "LLM_MODEL": "",
        "OPENAI_API_KEY": "",
        "CORS_ORIGINS": "http://localhost:3000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEMORY_API_KEY", raising=False)
    return {"uri": uri, "username": username, "password": password}


@contextlib.asynccontextmanager
async def _client_for(app: Any) -> AsyncIterator[Any]:
    """An httpx client bound to ``app``, with the lifespan actually run."""
    import httpx

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
def chat_backend_modules(chat_backend_env) -> Iterator[SimpleNamespace]:
    """The chat backend's modules, imported once per test.

    Re-importing them inside a test would create a *second* copy with its own
    module globals (and therefore its own, unconnected memory singletons), so
    every test shares this one namespace.
    """
    with _chat_backend_on_path():
        modules = SimpleNamespace(
            config=importlib.import_module("src.config"),
            memory=importlib.import_module("src.memory.client"),
            main=importlib.import_module("src.main"),
            agent=importlib.import_module("src.agent.agent"),
            tools=importlib.import_module("src.agent.tools"),
            dependencies=importlib.import_module("src.agent.dependencies"),
        )
        modules.config.get_settings.cache_clear()
        modules.agent.get_news_agent.cache_clear()
        try:
            yield modules
        finally:
            modules.config.get_settings.cache_clear()
            modules.agent.get_news_agent.cache_clear()


@pytest.fixture
def chat_app(chat_backend_env, chat_backend_modules, monkeypatch) -> Any:
    """The chat backend's FastAPI app, wired to the throwaway Neo4j."""
    pytest.importorskip("httpx")
    dimensions = _managed_index_dimensions(**chat_backend_env)
    # Stub only the provider boundary; every other wire is the real one.
    monkeypatch.setattr(
        chat_backend_modules.memory,
        "from_provider",
        lambda *_args, **_kwargs: _DeterministicEmbedder(dimensions=dimensions),
    )
    return chat_backend_modules.main.create_app()


def _delete_entities_by_name(connection: dict[str, str], names: list[str]) -> None:
    """Remove named entities, before and after the extraction test.

    Needed for idempotence, not only hygiene: ``_extract_and_link_entities``
    MERGEs the ``:Entity`` by name but links ``MENTIONS`` to the *freshly
    generated* id, so re-extracting a name the database already knows writes
    neither a new entity nor a new edge. Clearing the names first makes each
    run a first sighting. (Reported upstream — see the examples review.)
    """
    from neo4j import GraphDatabase

    with GraphDatabase.driver(
        connection["uri"], auth=(connection["username"], connection["password"])
    ) as driver:
        driver.execute_query("MATCH (e:Entity) WHERE e.name IN $names DETACH DELETE e", names=names)


def _sse_events(body: str) -> list[dict[str, Any]]:
    """Parse an SSE response body into the JSON payloads it carried."""
    events = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload:
                events.append(json.loads(payload))
    return events


@pytest.mark.requires_neo4j
class TestFullStackChatRoutes:
    """Drive the real app: thread CRUD, a chat turn, and the memory reads."""

    async def test_health_reports_both_graphs(self, chat_app):
        async with _client_for(chat_app) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["memory_connected"] is True, body
        assert body["memory_error"] is None
        assert body["news_connected"] is True, body

    async def test_memory_integration_does_not_own_the_client(self, chat_app, chat_backend_modules):
        """F02: one driver for the process, shared with MemoryIntegration."""
        memory_client = chat_backend_modules.memory
        async with _client_for(chat_app):
            integration = memory_client.get_memory_integration()
            assert integration is not None
            assert integration._owns_client is False
            assert integration.client is memory_client.get_memory_client()

    async def test_thread_round_trip(self, chat_app):
        """F01/E01/F12: create, chat, reload, rename, list, delete."""
        async with _client_for(chat_app) as client:
            created = await client.post("/api/threads", json={"title": "Energy research"})
            assert created.status_code == 200, created.text
            thread_id = created.json()["id"]
            assert created.json()["title"] == "Energy research"

            # A freshly created thread loads (it used to 404 as soon as it had
            # a message, because threads.py read a non-existent field).
            empty = await client.get(f"/api/threads/{thread_id}")
            assert empty.status_code == 200, empty.text
            assert empty.json()["title"] == "Energy research"

            chat = await client.post(
                "/api/chat",
                json={"thread_id": thread_id, "message": "What is new in offshore wind?"},
            )
            assert chat.status_code == 200, chat.text
            events = _sse_events(chat.text)
            kinds = [event["type"] for event in events]
            assert "error" not in kinds, events
            assert "done" in kinds
            done = next(event for event in events if event["type"] == "done")
            assert done["trace_id"], "the chat turn must record a reasoning trace"

            # F09: tool events arrive before the final answer's last token, and
            # F08: their arguments are populated.
            assert "tool_call" in kinds, kinds
            assert kinds.index("tool_call") < len(kinds) - 1
            tool_calls = [event for event in events if event["type"] == "tool_call"]
            assert any(event["args"] for event in tool_calls), (
                f"every tool call had empty args: {tool_calls}"
            )
            results = [event for event in events if event["type"] == "tool_result"]
            assert results, "tool results must be streamed too"
            assert all(isinstance(event["duration_ms"], int) for event in results)

            # The reload path: both turns come back.
            reloaded = await client.get(f"/api/threads/{thread_id}")
            assert reloaded.status_code == 200, reloaded.text
            roles = [message["role"] for message in reloaded.json()["messages"]]
            assert roles == ["user", "assistant"], reloaded.json()
            assert reloaded.json()["messages"][0]["content"] == ("What is new in offshore wind?")

            # E01/F12: a rename persists and is visible in the listing.
            renamed = await client.patch(f"/api/threads/{thread_id}?title=Wind%20auctions")
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == "Wind auctions"
            assert renamed.json()["message_count"] == 2

            listing = await client.get("/api/threads")
            assert listing.status_code == 200
            titles = {row["id"]: row["title"] for row in listing.json()}
            assert titles.get(thread_id) == "Wind auctions", titles

            # F10: the trace has steps, and the tool stats are not empty.
            traces = await client.get(f"/api/memory/traces?session_id={thread_id}")
            assert traces.status_code == 200
            assert traces.json(), "no reasoning trace was recorded"
            assert all(trace["completed_at"] for trace in traces.json()), (
                "E03: a trace must never be left without completed_at"
            )
            assert max(trace["step_count"] for trace in traces.json()) > 0, traces.json()

            stats = await client.get("/api/memory/tool-stats")
            assert stats.status_code == 200
            assert stats.json(), "tool stats are empty — no ToolCall nodes were written"

            context = await client.get(f"/api/memory/context?thread_id={thread_id}")
            assert context.status_code == 200
            assert len(context.json()["recent_messages"]) >= 2

            # F12: deleting removes the Conversation node, not just messages.
            deleted = await client.delete(f"/api/threads/{thread_id}")
            assert deleted.status_code == 200, deleted.text
            listing_after = await client.get("/api/threads")
            assert thread_id not in {row["id"] for row in listing_after.json()}
            assert (await client.get(f"/api/threads/{thread_id}")).status_code == 404

    async def test_unknown_thread_is_404_not_500(self, chat_app):
        async with _client_for(chat_app) as client:
            response = await client.get("/api/threads/does-not-exist")
        assert response.status_code == 404

    async def test_cypher_tool_rejects_writes_at_the_server(self, chat_app, chat_backend_modules):
        """F05: the boundary is the managed read transaction, not a keyword scan.

        ``RETURN 1 AS created`` contains the substring ``CREATE``; the old guard
        rejected it. A real write is rejected even when the pre-check is skipped.
        """
        tools = chat_backend_modules.tools
        async with _client_for(chat_app):
            deps = chat_backend_modules.dependencies.AgentDeps.create(
                memory=chat_backend_modules.memory.get_memory_client(),
                session_id="cypher-guard",
                news_driver=chat_app.state.news_driver,
            )
            ctx = type("Ctx", (), {"deps": deps})()

            allowed = await tools.execute_cypher(ctx, "RETURN 1 AS created")
            assert allowed == [{"created": 1}], allowed

            blocked = await tools.execute_cypher(ctx, "CREATE (n:__Smoke) RETURN n")
            assert "error" in blocked[0]

            # Skip the friendly pre-check: the server must still refuse.
            at_server = await tools._run_query(ctx, "CREATE (n:__Smoke) RETURN n")
            assert "AccessMode" in at_server[0]["error"], at_server


@pytest.mark.requires_neo4j
class TestFullStackChatExtraction:
    """F03: long-term memory fills up when extraction is on."""

    @pytest.fixture
    def chat_backend_env(self, chat_backend_env, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODE", "local")
        return chat_backend_env

    def test_extraction_config_modes(self, chat_backend_modules):
        """The EXTRACTION_MODE setting maps onto a real extractor.

        `none` must select ExtractorType.NONE explicitly — a PIPELINE with every
        stage disabled silently degrades to a NoOpExtractor, which is how this
        example shipped with an empty long-term memory while its README
        advertised entities.
        """
        from neo4j_agent_memory import ExtractorType

        build = chat_backend_modules.memory.build_extraction_config

        assert build("none").extractor_type is ExtractorType.NONE

        local = build("local")
        assert local.extractor_type is ExtractorType.PIPELINE
        assert (local.enable_spacy, local.enable_gliner, local.enable_llm_fallback) == (
            True,
            True,
            False,
        )

        llm = build("llm")
        assert llm.enable_llm_fallback is True
        assert llm.enable_gliner is False

    @pytest.mark.slow
    async def test_entities_are_extracted_from_a_chat_turn(self, chat_app, chat_backend_env):
        """A chat turn must leave entities and MENTIONS edges behind.

        The example used to build its `MemorySettings` with every extraction
        stage disabled, so `/api/entities` was permanently empty while the
        README advertised a Person/Organization/Location legend.
        """
        expected = ["Statkraft", "European Commission", "Brussels", "Oslo"]
        _delete_entities_by_name(chat_backend_env, expected)
        async with _client_for(chat_app) as client:
            thread = await client.post("/api/threads", json={"title": "Extraction"})
            thread_id = thread.json()["id"]
            try:
                chat = await client.post(
                    "/api/chat",
                    json={
                        "thread_id": thread_id,
                        "message": (
                            "Summarise coverage of Statkraft and the European "
                            "Commission in Brussels and Oslo."
                        ),
                    },
                )
                assert chat.status_code == 200
                assert "error" not in [event["type"] for event in _sse_events(chat.text)]

                # The listing route must be a listing: `search_entities("")` is
                # a vector search no empty query can ever satisfy.
                entities = await client.get("/api/entities")
                assert entities.status_code == 200
                extracted = {entity["name"] for entity in entities.json()}
                assert extracted & set(expected), (
                    f"none of {expected} were extracted; got {sorted(extracted)[:20]}"
                )

                # Thread-scoped view: the MENTIONS edges the extractor wrote.
                context = await client.get(f"/api/memory/context?thread_id={thread_id}")
                assert context.status_code == 200
                assert context.json()["entities"], "no MENTIONS edges reach this thread's messages"
            finally:
                await client.delete(f"/api/threads/{thread_id}")
                _delete_entities_by_name(chat_backend_env, expected)
