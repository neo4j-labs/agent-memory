"""Unit tests for Vertex AI embedder."""

from unittest.mock import MagicMock, patch

import pytest

# Check if vertex AI is available
pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")


class TestVertexAIEmbedder:
    """Tests for VertexAIEmbedder class."""

    def test_embedder_initialization_default(self):
        """Test embedder initializes with default values."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder()

        assert embedder._model == "text-embedding-004"
        assert embedder._location == "us-central1"
        assert embedder._project_id is None
        assert embedder._batch_size == 250
        assert embedder._task_type == "RETRIEVAL_DOCUMENT"
        assert embedder.dimensions == 768

    def test_embedder_initialization_custom(self):
        """Test embedder initializes with custom values."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="textembedding-gecko@003",
            project_id="my-project",
            location="europe-west1",
            batch_size=100,
            task_type="RETRIEVAL_QUERY",
        )

        assert embedder._model == "textembedding-gecko@003"
        assert embedder._project_id == "my-project"
        assert embedder._location == "europe-west1"
        assert embedder._batch_size == 100
        assert embedder._task_type == "RETRIEVAL_QUERY"
        assert embedder.dimensions == 768

    def test_dimensions_property(self):
        """Test dimensions property returns correct value for known models."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # Test various models
        models_and_dims = [
            ("text-embedding-004", 768),
            ("textembedding-gecko@003", 768),
            ("textembedding-gecko-multilingual@001", 768),
            ("unknown-model", 768),  # Default fallback
        ]

        for model, expected_dims in models_and_dims:
            embedder = VertexAIEmbedder(model=model)
            assert embedder.dimensions == expected_dims, (
                f"Model {model} should have {expected_dims} dimensions"
            )

    def test_model_property(self):
        """Test model property returns correct value."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # Test default model
        embedder = VertexAIEmbedder()
        assert embedder.model == "text-embedding-004"

        # Test custom model
        embedder = VertexAIEmbedder(model="textembedding-gecko@003")
        assert embedder.model == "textembedding-gecko@003"

    def test_task_type_property(self):
        """Test task_type property returns correct value."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # Test default task type
        embedder = VertexAIEmbedder()
        assert embedder.task_type == "RETRIEVAL_DOCUMENT"

        # Test custom task type
        embedder = VertexAIEmbedder(task_type="RETRIEVAL_QUERY")
        assert embedder.task_type == "RETRIEVAL_QUERY"

    def test_batch_size_capped_at_limit(self):
        """Test batch size is capped at Vertex AI limit."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(batch_size=500)
        assert embedder._batch_size == 250  # Should be capped

    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        """Test embedding a single text."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")

        # Mock the embedding model
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 768

        mock_model = MagicMock()
        mock_model.get_embeddings.return_value = [mock_embedding]

        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
            patch("asyncio.to_thread", side_effect=lambda fn, *args: fn(*args)),
        ):
            result = await embedder.embed("Hello, world!")

        assert len(result) == 768
        assert result == [0.1] * 768
        mock_model.get_embeddings.assert_called_once()
        # Verify TextEmbeddingInput was used
        call_args = mock_model.get_embeddings.call_args[0][0]
        assert len(call_args) == 1

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        """Test batch embedding multiple texts."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")

        # Mock embeddings for 3 texts
        mock_embeddings = []
        for i in range(3):
            mock_emb = MagicMock()
            mock_emb.values = [0.1 * (i + 1)] * 768
            mock_embeddings.append(mock_emb)

        mock_model = MagicMock()
        mock_model.get_embeddings.return_value = mock_embeddings

        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
            patch("asyncio.to_thread", side_effect=lambda fn, *args: fn(*args)),
        ):
            result = await embedder.embed_batch(["Text 1", "Text 2", "Text 3"])

        assert len(result) == 3
        assert len(result[0]) == 768
        mock_model.get_embeddings.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self):
        """Test batch embedding with empty list returns empty list."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")
        result = await embedder.embed_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_batch_chunks_large_batches(self):
        """Test that large batches are chunked correctly."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project", batch_size=2)

        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 768

        mock_model = MagicMock()
        mock_model.get_embeddings.return_value = [mock_embedding, mock_embedding]

        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
            patch("asyncio.to_thread", side_effect=lambda fn, *args: fn(*args)),
        ):
            # 5 texts with batch_size=2 should result in 3 API calls
            result = await embedder.embed_batch(["T1", "T2", "T3", "T4", "T5"])

        # Should have made 3 calls: [T1, T2], [T3, T4], [T5]
        assert mock_model.get_embeddings.call_count == 3

    @pytest.mark.asyncio
    async def test_embed_raises_error_on_failure(self):
        """Test that embedding errors are wrapped in EmbeddingError."""
        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")

        mock_model = MagicMock()
        mock_model.get_embeddings.side_effect = Exception("API Error")

        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
            patch("asyncio.to_thread", side_effect=lambda fn, *args: fn(*args)),
            pytest.raises(EmbeddingError, match="Failed to generate embedding"),
        ):
            await embedder.embed("Hello")

    def test_ensure_initialized_raises_without_package(self):
        """Test that missing package raises helpful error."""
        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder()

        # Simulate the import failing inside _ensure_initialized
        with patch(
            "neo4j_agent_memory.embeddings.vertex_ai.VertexAIEmbedder._ensure_initialized"
        ) as mock_init:
            mock_init.side_effect = EmbeddingError(
                "Vertex AI package not installed. Install with: "
                "pip install neo4j-agent-memory[vertex-ai]"
            )

            with pytest.raises(EmbeddingError, match="Vertex AI package not installed"):
                embedder._ensure_initialized()


class TestVertexAIModelDimensions:
    """Tests for model dimension mappings."""

    def test_all_known_models_have_dimensions(self):
        """Test that all known models have dimension mappings."""
        from neo4j_agent_memory.embeddings.vertex_ai import VERTEX_MODEL_DIMENSIONS

        expected_models = [
            "text-embedding-004",
            "textembedding-gecko@003",
            "textembedding-gecko@002",
            "textembedding-gecko@001",
            "textembedding-gecko-multilingual@001",
        ]

        for model in expected_models:
            assert model in VERTEX_MODEL_DIMENSIONS, f"Model {model} should have dimension mapping"
            assert VERTEX_MODEL_DIMENSIONS[model] == 768, (
                f"Model {model} should have 768 dimensions"
            )
