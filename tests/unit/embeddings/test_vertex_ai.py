"""Unit tests for Vertex AI embedder."""

import sys
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

        assert embedder._model == "gemini-embedding-001"
        assert embedder._location == "us-central1"
        assert embedder._project_id is None
        # gemini-embedding-001 accepts one text per request.
        assert embedder._batch_size == 1
        assert embedder._task_type == "RETRIEVAL_DOCUMENT"
        # 768 by default so vector indexes sized for text-embedding-004 still fit.
        assert embedder.output_dimensionality == 768
        assert embedder.dimensions == 768

    def test_embedder_initialization_custom(self):
        """Test embedder initializes with custom values."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(
            model="text-multilingual-embedding-002",
            project_id="my-project",
            location="europe-west1",
            batch_size=100,
            task_type="RETRIEVAL_QUERY",
        )

        assert embedder._model == "text-multilingual-embedding-002"
        assert embedder._project_id == "my-project"
        assert embedder._location == "europe-west1"
        assert embedder._batch_size == 100
        assert embedder._task_type == "RETRIEVAL_QUERY"
        assert embedder.dimensions == 768

    def test_dimensions_property(self):
        """Test dimensions property returns correct value for known models."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # Default output_dimensionality (768) applies to every model.
        models_and_dims = [
            ("gemini-embedding-001", 768),
            ("text-embedding-005", 768),
            ("text-multilingual-embedding-002", 768),
            ("unknown-model", 768),  # Default fallback
        ]

        for model, expected_dims in models_and_dims:
            embedder = VertexAIEmbedder(model=model)
            assert embedder.dimensions == expected_dims, (
                f"Model {model} should have {expected_dims} dimensions"
            )

    def test_native_dimensions_when_output_dimensionality_disabled(self):
        """Passing output_dimensionality=None yields the model's native size."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        assert VertexAIEmbedder(output_dimensionality=None).dimensions == 3072
        assert (
            VertexAIEmbedder(model="text-embedding-005", output_dimensionality=None).dimensions
            == 768
        )

    def test_explicit_output_dimensionality(self):
        """An explicit truncation target drives the reported dimensions."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(output_dimensionality=1536)
        assert embedder.dimensions == 1536
        assert embedder.output_dimensionality == 1536

    def test_invalid_output_dimensionality_raises(self):
        """Non-positive truncation targets are rejected."""
        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        with pytest.raises(EmbeddingError, match="output_dimensionality"):
            VertexAIEmbedder(output_dimensionality=0)

    @pytest.mark.parametrize(
        "model",
        [
            "text-embedding-004",
            "textembedding-gecko@003",
            "textembedding-gecko-multilingual@001",
        ],
    )
    def test_retired_models_raise(self, model):
        """Retired Vertex AI model ids fail fast with the replacement named."""
        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        with pytest.raises(EmbeddingError, match="retired"):
            VertexAIEmbedder(model=model)

        with pytest.raises(EmbeddingError, match="gemini-embedding-001"):
            VertexAIEmbedder(model=model)

    def test_batch_size_capped_at_limit(self):
        """Test batch size is capped at the Vertex AI / per-model limit."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # text-embedding-005 allows the full 250-instance request.
        embedder = VertexAIEmbedder(model="text-embedding-005", batch_size=500)
        assert embedder._batch_size == 250  # Should be capped

        # gemini-embedding-001 allows one instance per request.
        embedder = VertexAIEmbedder(batch_size=500)
        assert embedder._batch_size == 1

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
        # Verify the truncation target is forwarded to the API
        assert mock_model.get_embeddings.call_args[1]["output_dimensionality"] == 768

    @pytest.mark.asyncio
    async def test_embed_omits_output_dimensionality_when_disabled(self):
        """output_dimensionality=None leaves the API call untruncated."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project", output_dimensionality=None)

        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 3072
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

        assert len(result) == 3072
        assert "output_dimensionality" not in mock_model.get_embeddings.call_args[1]

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        """Test batch embedding multiple texts."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        # text-embedding-005 batches all three texts into one request.
        embedder = VertexAIEmbedder(model="text-embedding-005", project_id="test-project")

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
    async def test_embed_batch_one_request_per_text_for_gemini(self):
        """gemini-embedding-001 is called once per text (API accepts one input)."""
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")

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
            result = await embedder.embed_batch(["Text 1", "Text 2", "Text 3"])

        assert len(result) == 3
        assert mock_model.get_embeddings.call_count == 3

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

        embedder = VertexAIEmbedder(
            model="text-embedding-005", project_id="test-project", batch_size=2
        )

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
            await embedder.embed_batch(["T1", "T2", "T3", "T4", "T5"])

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


class TestVertexAIGenAIFallback:
    """The google-genai path used when aiplatform 2.x drops vertexai.language_models."""

    def test_legacy_import_failure_falls_back_to_genai(self):
        """aiplatform 2.x removed vertexai.language_models; the embedder adapts."""
        pytest.importorskip("google.genai", reason="google-genai not installed")

        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")
        mock_client = MagicMock()

        with (
            patch.dict(sys.modules, {"vertexai.language_models": None}),
            patch("google.genai.Client", return_value=mock_client),
        ):
            client = embedder._ensure_initialized()

        assert client is mock_client
        assert embedder._backend == "genai"

    @pytest.mark.asyncio
    async def test_embed_uses_genai_client(self):
        """On the genai backend, embed() calls models.embed_content."""
        pytest.importorskip("google.genai", reason="google-genai not installed")

        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder(project_id="test-project")

        mock_embedding = MagicMock()
        mock_embedding.values = [0.2] * 768
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]
        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_response

        # Pretend initialization already picked the genai backend.
        embedder._embedding_model = mock_client
        embedder._backend = "genai"

        with patch("asyncio.to_thread", side_effect=lambda fn, *args: fn(*args)):
            result = await embedder.embed("Hello")

        assert result == [0.2] * 768
        kwargs = mock_client.models.embed_content.call_args[1]
        assert kwargs["model"] == "gemini-embedding-001"
        assert kwargs["config"].output_dimensionality == 768
        assert kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"

    def test_genai_missing_raises_install_hint(self):
        """Without google-genai the fallback reports the install hint."""
        import builtins

        from neo4j_agent_memory.core.exceptions import EmbeddingError
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder()
        real_import = builtins.__import__

        def no_genai(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "google" and fromlist and "genai" in fromlist:
                raise ImportError("No module named 'google.genai'")
            return real_import(name, globals, locals, fromlist, level)

        with (
            patch.object(builtins, "__import__", no_genai),
            pytest.raises(EmbeddingError, match="Vertex AI package not installed"),
        ):
            embedder._ensure_initialized_genai()


class TestVertexAIModelDimensions:
    """Tests for model dimension mappings."""

    def test_all_known_models_have_dimensions(self):
        """Test that all supported models have dimension mappings."""
        from neo4j_agent_memory.embeddings.vertex_ai import VERTEX_MODEL_DIMENSIONS

        assert VERTEX_MODEL_DIMENSIONS == {
            "gemini-embedding-001": 3072,
            "text-embedding-005": 768,
            "text-multilingual-embedding-002": 768,
        }

    def test_retired_models_are_not_supported(self):
        """Retired ids are listed as retired, never as supported."""
        from neo4j_agent_memory.embeddings.vertex_ai import (
            RETIRED_VERTEX_MODELS,
            VERTEX_MODEL_DIMENSIONS,
        )

        assert RETIRED_VERTEX_MODELS["text-embedding-004"] == "2026-01-14"
        for model in RETIRED_VERTEX_MODELS:
            assert model not in VERTEX_MODEL_DIMENSIONS
