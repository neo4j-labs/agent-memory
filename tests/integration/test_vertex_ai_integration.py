"""Integration tests for Vertex AI embeddings.

These tests require:
1. Google Cloud credentials configured (gcloud auth application-default login)
2. A valid GOOGLE_CLOUD_PROJECT environment variable
3. The vertex-ai extra installed: pip install neo4j-agent-memory[vertex-ai]

Tests are skipped if these requirements are not met.
"""

import os

import pytest

# Check if Vertex AI is available
try:
    import vertexai  # noqa: F401
    from google.auth import default as google_auth_default

    HAS_VERTEX_AI = True
except ImportError:
    HAS_VERTEX_AI = False

# Check for GCP project
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")


def _check_gcp_credentials():
    """Check if GCP credentials are available."""
    if not HAS_VERTEX_AI:
        return False
    try:
        google_auth_default()
        return True
    except Exception:
        return False


# Skip all tests in this module if Vertex AI is not properly configured
pytestmark = [
    pytest.mark.skipif(
        not HAS_VERTEX_AI,
        reason="google-cloud-aiplatform not installed. Install with: pip install neo4j-agent-memory[vertex-ai]",
    ),
    pytest.mark.skipif(
        not GCP_PROJECT,
        reason="GOOGLE_CLOUD_PROJECT environment variable not set",
    ),
    pytest.mark.skipif(
        not _check_gcp_credentials(),
        reason="GCP credentials not configured. Run: gcloud auth application-default login",
    ),
]


@pytest.mark.integration
class TestVertexAIEmbedderIntegration:
    """Integration tests for VertexAIEmbedder with real Vertex AI API."""

    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        """Test generating embedding for a single text."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
            location="us-central1",
        )

        embedding = await embedder.embed("Hello, world!")

        assert isinstance(embedding, list)
        assert len(embedding) == 768
        assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        """Test batch embedding generation."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
        )

        texts = [
            "First test sentence",
            "Second test sentence",
            "Third test sentence",
        ]

        embeddings = await embedder.embed_batch(texts)

        assert len(embeddings) == 3
        assert all(len(e) == 768 for e in embeddings)
        assert all(isinstance(e, list) for e in embeddings)

    @pytest.mark.asyncio
    async def test_embed_empty_batch(self):
        """Test batch embedding with empty list."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
        )

        embeddings = await embedder.embed_batch([])
        assert embeddings == []

    @pytest.mark.asyncio
    async def test_embed_with_retrieval_query_task(self):
        """Test embedding with RETRIEVAL_QUERY task type."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
            task_type="RETRIEVAL_QUERY",
        )

        embedding = await embedder.embed("What is the capital of France?")

        assert len(embedding) == 768

    @pytest.mark.asyncio
    async def test_embed_with_semantic_similarity_task(self):
        """Test embedding with SEMANTIC_SIMILARITY task type."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
            task_type="SEMANTIC_SIMILARITY",
        )

        text1 = "The cat sat on the mat"
        text2 = "A feline rested on the rug"

        emb1 = await embedder.embed(text1)
        emb2 = await embedder.embed(text2)

        # Calculate cosine similarity
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        norm1 = sum(a * a for a in emb1) ** 0.5
        norm2 = sum(b * b for b in emb2) ** 0.5
        similarity = dot_product / (norm1 * norm2)

        # Similar sentences should have high similarity
        assert similarity > 0.5

    @pytest.mark.asyncio
    async def test_dimensions_property(self):
        """Test dimensions property returns correct value."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
        )

        assert embedder.dimensions == 768

    @pytest.mark.asyncio
    async def test_text_embedding_005_model(self):
        """Test with the task-specific text-embedding-005 model."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="text-embedding-005",
            project_id=GCP_PROJECT,
        )

        embedding = await embedder.embed("Test with text-embedding-005")

        assert len(embedding) == 768

    @pytest.mark.asyncio
    async def test_native_dimensionality(self):
        """gemini-embedding-001 returns 3072 dimensions when untruncated."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
            output_dimensionality=None,
        )

        embedding = await embedder.embed("Test native dimensionality")

        assert embedder.dimensions == 3072
        assert len(embedding) == 3072

    @pytest.mark.asyncio
    async def test_retired_model_rejected(self):
        """The retired text-embedding-004 id fails fast instead of 404ing."""
        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        with pytest.raises(EmbeddingError, match="retired"):
            VertexAIEmbedder(model="text-embedding-004", project_id=GCP_PROJECT)


@pytest.mark.integration
class TestVertexAIWithMemoryClientIntegration:
    """Integration tests for Vertex AI with MemoryClient."""

    @pytest.mark.asyncio
    async def test_memory_client_with_vertex_embedder(
        self, memory_settings, mock_extractor, mock_resolver
    ):
        """Test MemoryClient with Vertex AI embedder."""
        from neo4j_agent_memory import MemoryClient
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder
        from neo4j_agent_memory.memory.short_term import MessageRole

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
        )

        async with MemoryClient(
            memory_settings,
            embedder=embedder,
            extractor=mock_extractor,
            resolver=mock_resolver,
        ) as client:
            session_id = "test-vertex-session"

            # Add message with embedding
            msg = await client.short_term.add_message(
                session_id,
                MessageRole.USER,
                "I want to learn about machine learning",
                extract_entities=False,
                generate_embedding=True,
            )

            assert msg is not None
            assert msg.embedding is not None
            assert len(msg.embedding) == 768

    @pytest.mark.asyncio
    async def test_semantic_search_with_vertex_embeddings(
        self, memory_settings, mock_extractor, mock_resolver
    ):
        """Test semantic search using Vertex AI embeddings."""
        from neo4j_agent_memory import MemoryClient
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder
        from neo4j_agent_memory.memory.short_term import MessageRole

        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id=GCP_PROJECT,
        )

        async with MemoryClient(
            memory_settings,
            embedder=embedder,
            extractor=mock_extractor,
            resolver=mock_resolver,
        ) as client:
            session_id = "test-vertex-search"

            # Add several messages
            await client.short_term.add_message(
                session_id,
                MessageRole.USER,
                "I love Italian pasta and pizza",
                extract_entities=False,
                generate_embedding=True,
            )
            await client.short_term.add_message(
                session_id,
                MessageRole.USER,
                "Python is my favorite programming language",
                extract_entities=False,
                generate_embedding=True,
            )
            await client.short_term.add_message(
                session_id,
                MessageRole.USER,
                "I enjoy cooking Mediterranean food",
                extract_entities=False,
                generate_embedding=True,
            )

            # Search for food-related messages
            results = await client.short_term.search_messages(
                "Italian cuisine and cooking",
                limit=3,
            )

            # Should find food-related messages ranked higher
            assert len(results) >= 2
            # First results should be about food
            food_keywords = ["pasta", "pizza", "cooking", "food", "Mediterranean"]
            assert any(
                any(kw.lower() in r.content.lower() for kw in food_keywords) for r in results[:2]
            )
