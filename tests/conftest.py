"""Pytest fixtures for neo4j-agent-memory tests."""

import asyncio
import hashlib
import os
import re
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.embeddings.base import BaseEmbedder
from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractionResult,
)
from neo4j_agent_memory.resolution.base import EntityResolver, ResolvedEntity

# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test requiring Neo4j")
    config.addinivalue_line("markers", "docker: mark test as requiring Docker")
    config.addinivalue_line("markers", "slow: mark test as slow running")


# Global testcontainer instance for session-scoped reuse
_neo4j_container = None


def _get_testcontainer():
    """Get or create a Neo4j testcontainer instance."""
    global _neo4j_container
    if _neo4j_container is None:
        try:
            from testcontainers.neo4j import Neo4jContainer

            # Create Neo4j container with APOC plugin
            _neo4j_container = Neo4jContainer(
                image="neo4j:5.26-community",
                password="test-password",
            )
            # Configure APOC plugin
            _neo4j_container.with_env("NEO4J_PLUGINS", '["apoc"]')
            _neo4j_container.with_env("NEO4J_dbms_security_procedures_unrestricted", "apoc.*")
            _neo4j_container.with_env("NEO4J_dbms_security_procedures_allowlist", "apoc.*")
            _neo4j_container.with_env("NEO4J_apoc_export_file_enabled", "true")
            _neo4j_container.with_env("NEO4J_apoc_import_file_enabled", "true")
        except ImportError:
            _neo4j_container = None
    return _neo4j_container


def _check_neo4j_env_available() -> dict | None:
    """Check if Neo4j is available via environment variables (e.g., GitHub Actions services)."""
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


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests if Neo4j is not available."""
    # Check environment settings
    skip_integration = os.getenv("SKIP_INTEGRATION_TESTS", "").lower() in ("1", "true", "yes")

    if skip_integration:
        skip_marker = pytest.mark.skip(reason="SKIP_INTEGRATION_TESTS=1 set")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_marker)
        return

    # Check if Neo4j is available via environment (GitHub Actions services)
    if _check_neo4j_env_available():
        return

    # Check if testcontainers is available
    try:
        from testcontainers.neo4j import Neo4jContainer  # noqa: F401

        # Testcontainers available, tests will use it
        return
    except ImportError:
        pass

    # No Neo4j available, skip integration tests
    skip_marker = pytest.mark.skip(
        reason="Neo4j not available. Install testcontainers[neo4j] or set NEO4J_URI environment variable"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Mock Components for Unit Tests
# =============================================================================


class MockEmbedder(BaseEmbedder):
    """Mock embedder for unit tests that generates deterministic embeddings.

    Produces token-bag embeddings: each whitespace-separated token contributes
    to a stable set of dimensions, so texts that share tokens have higher
    cosine similarity than disjoint texts. That's enough fidelity to exercise
    vector-search ordering without a real embedding model.
    """

    def __init__(self, dimensions: int = 1536):
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Generate deterministic token-bag embedding."""
        # Alphanumeric tokens (punctuation stripped). Each token contributes
        # both its full lowercased form and its 5-char prefix stem, so:
        #   - "Entity0" vs "Entity50" stay distinguishable (different full
        #     tokens), and
        #   - "preference" vs "preferences" still share signal (same stem).
        words = re.findall(r"[a-z0-9]+", text.lower())
        tokens: set[str] = set()
        for w in words:
            if len(w) < 3:
                continue
            tokens.add(w)
            if len(w) > 5:
                tokens.add(w[:5])

        embedding = [0.0] * self._dimensions
        if not tokens:
            # Empty / very short text: fall back to a hash so the vector
            # index still accepts the embedding (zero vectors get rejected).
            h = hashlib.sha256(text.encode()).digest()
            for i in range(self._dimensions):
                embedding[i] = h[i % len(h)] / 255.0
            norm = sum(x * x for x in embedding) ** 0.5
            return [x / norm for x in embedding] if norm > 0 else embedding

        for token in tokens:
            h = hashlib.sha256(token.encode()).digest()
            # Each token contributes weight at 8 stable indices.
            for i in range(0, 16, 2):
                idx = int.from_bytes(h[i : i + 2], "big") % self._dimensions
                embedding[idx] += 1.0
        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        return [await self.embed(t) for t in texts]


class MockExtractor(EntityExtractor):
    """Mock entity extractor for unit tests with simple rule-based extraction."""

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Simple rule-based extraction for testing."""
        entities = []
        relations = []
        preferences = []

        # Simple name detection
        words = text.split()
        for i, word in enumerate(words):
            # Detect potential names (capitalized words)
            if word and word[0].isupper() and len(word) > 1:
                # Skip common words
                if word.lower() not in [
                    "i",
                    "the",
                    "a",
                    "an",
                    "is",
                    "are",
                    "was",
                    "were",
                    "hi",
                    "hello",
                ]:
                    entities.append(
                        ExtractedEntity(
                            name=word,
                            type="PERSON"
                            if word not in ["Acme", "Google", "Apple"]
                            else "ORGANIZATION",
                            confidence=0.8,
                        )
                    )

        # Detect preferences
        if extract_preferences:
            preference_patterns = [
                ("love", "like"),
                ("prefer", "preference"),
                ("enjoy", "like"),
                ("hate", "dislike"),
                ("favorite", "like"),
            ]
            text_lower = text.lower()
            for pattern, category in preference_patterns:
                if pattern in text_lower:
                    # Find the thing being preferred
                    idx = text_lower.find(pattern)
                    after = text[idx:].split(maxsplit=2)
                    if len(after) > 1:
                        preferences.append(
                            ExtractedPreference(
                                category=category,
                                preference=text,
                                confidence=0.7,
                            )
                        )
                    break

        return ExtractionResult(
            entities=entities,
            relations=relations,
            preferences=preferences,
            source_text=text,
        )


class MockResolver(EntityResolver):
    """Mock entity resolver for unit tests."""

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> ResolvedEntity:
        """Simple exact match resolution."""
        normalized = entity_name.lower().strip()

        if existing_entities:
            for existing in existing_entities:
                if existing.lower().strip() == normalized:
                    return ResolvedEntity(
                        original_name=entity_name,
                        canonical_name=existing,
                        entity_type=entity_type,
                        confidence=1.0,
                        match_type="exact",
                    )

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
    ) -> list[ResolvedEntity]:
        """Resolve multiple entities."""
        return [await self.resolve(name, etype) for name, etype in entities]

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list:
        """Find matches from candidates."""
        return []


@pytest.fixture
def mock_embedder():
    """Provide a mock embedder for unit tests."""
    return MockEmbedder()


@pytest.fixture
def mock_extractor():
    """Provide a mock entity extractor for unit tests."""
    return MockExtractor()


@pytest.fixture
def mock_resolver():
    """Provide a mock entity resolver for unit tests."""
    return MockResolver()


# =============================================================================
# Neo4j Testcontainer Fixtures
# =============================================================================


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
def neo4j_container():
    """
    Session-scoped Neo4j testcontainer.

    This fixture provides a Neo4j database for integration tests.
    It first checks if Neo4j is available via environment variables (GitHub Actions),
    and falls back to testcontainers for local development.
    """
    # First, check if Neo4j is available via environment (GitHub Actions services)
    env_config = _check_neo4j_env_available()
    if env_config:
        print("Using Neo4j from environment variables (GitHub Actions services)")
        yield env_config
        return

    # Check if Docker is available
    if not _is_docker_available():
        pytest.skip("Docker is not running. Start Docker or set NEO4J_URI environment variable.")
        return

    # Fall back to testcontainers
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed and NEO4J_URI not set")
        return

    print("Starting Neo4j testcontainer...")
    container = Neo4jContainer(
        image="neo4j:5.26-community",
    )
    # Configure APOC plugin and settings
    container.with_env("NEO4J_PLUGINS", '["apoc"]')
    container.with_env("NEO4J_dbms_security_procedures_unrestricted", "apoc.*")
    container.with_env("NEO4J_dbms_security_procedures_allowlist", "apoc.*")
    container.with_env("NEO4J_apoc_export_file_enabled", "true")
    container.with_env("NEO4J_apoc_import_file_enabled", "true")

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


@pytest.fixture(scope="session")
def neo4j_connection_info(neo4j_container):
    """Get Neo4j connection info from the container."""
    return neo4j_container


# =============================================================================
# Integration Test Fixtures
# =============================================================================


@pytest.fixture
def memory_settings(neo4j_connection_info):
    """Test settings with connection to Neo4j testcontainer."""
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=neo4j_connection_info["uri"],
            username=neo4j_connection_info["username"],
            password=SecretStr(neo4j_connection_info["password"]),
        )
    )


@pytest.fixture
def session_id():
    """Generate unique session ID for test isolation."""
    return f"test-{uuid4()}"


@pytest.fixture
async def neo4j_client(memory_settings) -> AsyncGenerator:
    """
    Real Neo4j client for integration tests.

    Uses the Neo4j testcontainer.
    """
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.graph.schema import SchemaManager

    client = Neo4jClient(memory_settings.neo4j)

    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")

    # Set up schema
    schema_manager = SchemaManager(client)
    await schema_manager.setup_all()

    yield client

    # Cleanup test data
    try:
        await client.execute_write("MATCH (n) WHERE n.id STARTS WITH 'test-' DETACH DELETE n")
    except Exception:
        pass

    await client.close()


@pytest.fixture
async def memory_client(
    memory_settings, mock_embedder, mock_extractor, mock_resolver
) -> AsyncGenerator:
    """
    Full MemoryClient for integration tests with mock components.

    Uses mock embedder/extractor/resolver to avoid external API calls.
    Uses Neo4j testcontainer for database.
    """
    from neo4j_agent_memory import MemoryClient

    client = MemoryClient(
        memory_settings,
        embedder=mock_embedder,
        extractor=mock_extractor,
        resolver=mock_resolver,
    )

    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")

    yield client

    # Cleanup test data - delete all test entities
    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    await client.close()


@pytest.fixture
async def clean_memory_client(
    memory_settings, mock_embedder, mock_extractor, mock_resolver
) -> AsyncGenerator:
    """
    MemoryClient with clean database state for isolation.

    Cleans up ALL test data before and after each test.
    """
    from neo4j_agent_memory import MemoryClient

    client = MemoryClient(
        memory_settings,
        embedder=mock_embedder,
        extractor=mock_extractor,
        resolver=mock_resolver,
    )

    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")

    # Clean before test - delete all nodes
    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    yield client

    # Clean after test - delete all nodes
    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    await client.close()
