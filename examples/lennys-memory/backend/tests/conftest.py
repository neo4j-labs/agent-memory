"""Shared test fixtures for Lenny's Memory backend tests."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
import pytest_asyncio

from neo4j_agent_memory.memory.short_term import Conversation

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def neo4j_available() -> bool:
    """Check if Neo4j environment variables are configured."""
    return all(
        [
            os.environ.get("NEO4J_URI"),
            os.environ.get("NEO4J_USERNAME"),
            os.environ.get("NEO4J_PASSWORD"),
        ]
    )


@pytest.fixture
def mock_message():
    """Create a mock message with podcast metadata."""

    def _create_message(
        content: str = "Test content",
        speaker: str = "Test Speaker",
        episode_guest: str = "Test Guest",
        timestamp: str = "00:00:00",
        similarity: float = 0.85,
        source: str = "lenny_podcast",
    ):
        msg = MagicMock()
        msg.content = content
        msg.metadata = {
            "speaker": speaker,
            "episode_guest": episode_guest,
            "timestamp": timestamp,
            "source": source,
            "similarity": similarity,
        }
        return msg

    return _create_message


@pytest.fixture
def mock_memory_client():
    """Create a spec'd mock memory client for unit tests.

    Deliberately built with ``spec=``/``create_autospec`` rather than a bare
    ``MagicMock``: a bare mock auto-creates any attribute, which is how three
    broken tools (``client.search_locations_near``, ``Preference.source``,
    iterating a ``Conversation``) shipped green. With specs, a method that moves
    or changes signature fails the test instead.
    """
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.memory.long_term import LongTermMemory
    from neo4j_agent_memory.memory.reasoning import ReasoningMemory
    from neo4j_agent_memory.memory.short_term import ShortTermMemory

    client = MagicMock(spec=MemoryClient)
    client.short_term = create_autospec(ShortTermMemory, instance=True)
    client.long_term = create_autospec(LongTermMemory, instance=True)
    client.reasoning = create_autospec(ReasoningMemory, instance=True)

    client.short_term.search_messages.return_value = []
    client.short_term.get_conversation.return_value = Conversation(session_id="test-session")
    client.short_term.list_sessions.return_value = []
    client.long_term.search_entities.return_value = []
    client.long_term.get_entity_by_name.return_value = None
    client.long_term.find_potential_duplicates.return_value = []
    client.long_term.get_preferences_by_category.return_value = []
    client.long_term.search_locations_near.return_value = []
    client.reasoning.get_similar_traces.return_value = []

    # Portable read-only Cypher accessor (works on bolt and NAMS).
    client.query = MagicMock()
    client.query.cypher = AsyncMock(return_value=[])
    # Writes that have no public writer go through client.graph (bolt only).
    client.graph = MagicMock()
    client.graph.execute_write = AsyncMock(return_value=[])

    client.get_stats = AsyncMock(return_value={"conversations": 0, "messages": 0, "entities": 0})
    client.get_locations = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_agent_context(mock_memory_client):
    """Create a mock agent RunContext."""
    ctx = MagicMock()
    ctx.deps.client = mock_memory_client
    return ctx


class MockEmbedder:
    """Deterministic in-process embedder (no API key, no network).

    Implements the library's ``EmbeddingProvider`` protocol: ``embed`` takes a
    *batch* and ``embed_one`` a single text. ``dimensions`` must match the
    vector indexes of the target database -- set ``MOCK_EMBEDDING_DIMENSIONS``
    when pointing the integration tests at a shared test database that was
    created with a different embedding model.
    """

    model = "mock-embedder"

    def __init__(self, dimensions: int | None = None):
        self.dimensions = dimensions or int(os.environ.get("MOCK_EMBEDDING_DIMENSIONS", "1536"))

    async def embed_one(self, text: str) -> list[float]:
        """Generate a deterministic embedding based on a text hash."""
        import hashlib

        hash_bytes = hashlib.sha256(text.encode()).digest()
        return [(hash_bytes[i % len(hash_bytes)] - 128) / 128.0 for i in range(self.dimensions)]

    async def embed(self, texts) -> list[list[float]]:
        """Embed a batch of texts, preserving order."""
        if isinstance(texts, str):  # tolerate the legacy single-text call
            return await self.embed_one(texts)  # type: ignore[return-value]
        return [await self.embed_one(text) for text in texts]


@pytest_asyncio.fixture
async def memory_client():
    """Create a real memory client for integration tests.

    Requires NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD environment variables
    and a running Neo4j instance.
    """
    if not neo4j_available():
        pytest.skip("Neo4j not available - set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD")

    from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
    from neo4j_agent_memory.core.exceptions import ConnectionError
    from neo4j_agent_memory.llm.errors import EmbeddingDimensionMismatchError

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.environ["NEO4J_URI"],
            username=os.environ["NEO4J_USERNAME"],
            password=os.environ["NEO4J_PASSWORD"],
        ),
    )

    client = MemoryClient(settings, embedder=MockEmbedder())
    try:
        await client.__aenter__()
    except ConnectionError:
        pytest.skip("Neo4j not reachable - is the database running?")
    except EmbeddingDimensionMismatchError as exc:
        pytest.skip(
            "target database has vector indexes of another dimension "
            f"(set MOCK_EMBEDDING_DIMENSIONS to match): {exc}"
        )

    yield client

    # Cleanup test data - delete test-specific nodes only
    await client.graph.execute_write(
        """
        MATCH (c:Conversation)
        WHERE c.session_id STARTS WITH 'test-'
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        DETACH DELETE c, m
        """
    )
    await client.__aexit__(None, None, None)
