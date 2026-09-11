"""Pytest fixtures for example smoke tests.

These fixtures provide:
- Neo4j testcontainer (or environment-based connection)
- Mock embedders and extractors to avoid API key requirements
- Environment setup helpers
"""

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

# Add examples directory to path for imports
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(EXAMPLES_DIR))


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Configure custom markers for example tests."""
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "requires_neo4j: mark test as requiring Neo4j")
    config.addinivalue_line("markers", "requires_api_key: mark test as requiring API keys")


# =============================================================================
# Cross-example import isolation
# =============================================================================
#
# Several example directories ship a module with the same bare name --
# ``_common.py`` exists under examples/google_cloud_integration/ and under
# examples/lennys-memory/scripts/, ``main.py`` under eleven directories. Test
# modules import them by putting the example directory on ``sys.path``, so
# whichever test runs first wins and the next one silently gets the wrong
# module (symptom: ``ImportError: cannot import name 'Colors' from '_common'``
# only when the full directory is collected). Unload anything imported from
# under examples/ after each test so collection order cannot matter.


def _module_lives_under(module: Any, root: str) -> bool:
    """True when ``module`` was loaded from under ``root``.

    Namespace packages (``examples/*/backend/src``) have ``__file__ is None``,
    so fall back to ``__path__`` -- otherwise a stale ``src`` package keeps
    pointing at whichever backend was imported first.

    Reads ``__dict__`` rather than using ``getattr``: some packages
    (``transformers``) install a ``_LazyModule`` whose ``__getattr__`` imports a
    submodule on any unknown attribute, which would turn this inspection into a
    heavyweight -- and sometimes failing -- import.
    """
    namespace = getattr(module, "__dict__", {})
    origin = namespace.get("__file__")
    if origin:
        return str(origin).startswith(root)
    return any(str(entry).startswith(root) for entry in namespace.get("__path__", ()))


@pytest.fixture(autouse=True)
def _unload_example_modules():
    """Drop modules and ``sys.path`` entries that belong to ``examples/``.

    Two collisions this prevents, both order-dependent and therefore invisible
    when a test module is run on its own:

    * ``sys.modules`` -- four example directories ship a ``_common.py`` and
      eleven ship a ``main.py``; the first import of a bare name wins.
    * ``sys.path`` -- every full-stack backend has a ``src`` package, so
      ``import src.agent.tools`` resolves against whichever backend directory
      some earlier test pushed onto the path.
    """
    before_modules = set(sys.modules)
    before_path = list(sys.path)
    yield
    examples_root = str(EXAMPLES_DIR)
    for name in set(sys.modules) - before_modules:
        # ``src`` is ambiguous repo-wide: the repository root, each full-stack
        # backend and the lennys backend all have one, and the repo-root
        # namespace portion shadows the others once it is cached.
        if name == "src" or name.startswith("src."):
            del sys.modules[name]
            continue
        module = sys.modules.get(name)
        if module is not None and _module_lives_under(module, examples_root):
            del sys.modules[name]
    if sys.path != before_path:
        sys.path[:] = [
            entry
            for entry in sys.path
            if entry in before_path or not str(entry).startswith(examples_root)
        ]


# =============================================================================
# Mock Components
# =============================================================================


class MockEmbedder:
    """Mock embedder that generates deterministic embeddings without API calls."""

    def __init__(self, dimensions: int = 1536):
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Generate deterministic fake embedding based on text hash."""
        h = hashlib.sha256(text.encode()).hexdigest()
        embedding = []
        for i in range(0, min(len(h), self._dimensions * 2), 2):
            if len(embedding) >= self._dimensions:
                break
            embedding.append(float(int(h[i : i + 2], 16)) / 255.0)
        while len(embedding) < self._dimensions:
            embedding.append(0.0)
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        return [await self.embed(t) for t in texts]


class MockExtractor:
    """Mock entity extractor that returns empty results."""

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> Any:
        """Return empty extraction result."""
        from neo4j_agent_memory.extraction.base import ExtractionResult

        return ExtractionResult(
            entities=[],
            relations=[],
            preferences=[],
            source_text=text,
        )


class MockResolver:
    """Mock entity resolver that returns the original entity."""

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> Any:
        """Return entity as-is (no resolution)."""
        from neo4j_agent_memory.resolution.base import ResolvedEntity

        return ResolvedEntity(
            original_name=entity_name,
            canonical_name=entity_name,
            entity_type=entity_type,
            confidence=1.0,
            match_type="none",
        )

    async def resolve_batch(
        self,
        entities: list[tuple[str, str]],
    ) -> list[Any]:
        """Resolve multiple entities."""
        return [await self.resolve(name, etype) for name, etype in entities]


# =============================================================================
# Neo4j Fixtures
# =============================================================================


def _check_neo4j_env_available() -> dict | None:
    """Check if Neo4j is available via environment variables."""
    uri = os.getenv("NEO4J_URI")
    if not uri:
        return None

    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "test-password")

    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(username, password))
        driver.verify_connectivity()
        driver.close()
        return {
            "uri": uri,
            "username": username,
            "password": password,
        }
    except Exception:
        return None


def _is_docker_available() -> bool:
    """Check if Docker daemon is running."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def neo4j_connection():
    """
    Session-scoped Neo4j connection for example tests.

    Priority:
    1. Environment variables (NEO4J_URI, etc.)
    2. Testcontainers (if Docker available)
    3. Skip if neither available
    """
    # Check environment first
    env_config = _check_neo4j_env_available()
    if env_config:
        print("Using Neo4j from environment variables")
        yield env_config
        return

    # Check Docker availability
    if not _is_docker_available():
        pytest.skip("Neo4j not available. Set NEO4J_URI or start Docker.")
        return

    # Try testcontainers
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed and NEO4J_URI not set")
        return

    print("Starting Neo4j testcontainer for example tests...")
    container = Neo4jContainer(image="neo4j:5.26-community")
    container.with_env("NEO4J_PLUGINS", '["apoc"]')
    container.with_env("NEO4J_dbms_security_procedures_unrestricted", "apoc.*")
    container.with_env("NEO4J_dbms_security_procedures_allowlist", "apoc.*")

    try:
        container.start()
        print(f"Neo4j testcontainer started at {container.get_connection_url()}")

        yield {
            "uri": container.get_connection_url(),
            "username": "neo4j",
            "password": container.password,
        }
    finally:
        print("Stopping Neo4j testcontainer...")
        container.stop()


@pytest.fixture
def neo4j_env(neo4j_connection, monkeypatch):
    """
    Set Neo4j environment variables for example scripts.

    This allows examples to run using os.getenv() as they normally would.
    """
    monkeypatch.setenv("NEO4J_URI", neo4j_connection["uri"])
    monkeypatch.setenv("NEO4J_USERNAME", neo4j_connection["username"])
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_connection["password"])
    return neo4j_connection


@pytest.fixture
def mock_openai_env(monkeypatch):
    """
    Set a fake OpenAI API key to allow imports and initialization.

    This doesn't make real API calls - tests should use mocked components.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key-for-testing-only")


# =============================================================================
# Memory Client Fixtures
# =============================================================================


@pytest.fixture
def mock_embedder():
    """Provide mock embedder for tests."""
    return MockEmbedder()


@pytest.fixture
def mock_extractor():
    """Provide mock extractor for tests."""
    return MockExtractor()


@pytest.fixture
def mock_resolver():
    """Provide mock resolver for tests."""
    return MockResolver()


@pytest.fixture
async def memory_client(neo4j_connection, mock_embedder, mock_extractor, mock_resolver):
    """
    Configured MemoryClient for example tests.

    Uses mock components to avoid API key requirements.
    """
    from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=neo4j_connection["uri"],
            username=neo4j_connection["username"],
            password=SecretStr(neo4j_connection["password"]),
        )
    )

    client = MemoryClient(
        settings,
        embedder=mock_embedder,
        extractor=mock_extractor,
        resolver=mock_resolver,
    )

    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Could not connect to Neo4j: {e}")

    yield client

    # Cleanup
    try:
        await client.graph.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    await client.close()


@pytest.fixture
def examples_dir():
    """Return path to examples directory."""
    return EXAMPLES_DIR


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def capture_output(capsys):
    """Helper to capture and return stdout/stderr from example scripts."""

    def _capture():
        captured = capsys.readouterr()
        return captured.out, captured.err

    return _capture


@pytest.fixture
def temp_session_id():
    """Generate a unique session ID for test isolation."""
    import uuid

    return f"test-example-{uuid.uuid4()}"
