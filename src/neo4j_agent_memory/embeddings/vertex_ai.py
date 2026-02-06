"""Vertex AI embedding provider.

Supports Google Cloud's Vertex AI text embedding models including:
- text-embedding-004 (768 dimensions, recommended)
- textembedding-gecko@003 (768 dimensions)
- textembedding-gecko-multilingual@001 (768 dimensions)
"""

import asyncio
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder

if TYPE_CHECKING:
    from vertexai.language_models import TextEmbeddingModel


# Model dimensions mapping
VERTEX_MODEL_DIMENSIONS = {
    "text-embedding-004": 768,
    "text-embedding-005": 768,
    "textembedding-gecko@003": 768,
    "textembedding-gecko@002": 768,
    "textembedding-gecko@001": 768,
    "textembedding-gecko-multilingual@001": 768,
    "text-multilingual-embedding-002": 768,
}

# Default batch size (Vertex AI supports up to 250 texts per request)
DEFAULT_BATCH_SIZE = 250


class VertexAIEmbedder(BaseEmbedder):
    """Vertex AI embedding provider.

    This embedder uses Google Cloud's Vertex AI text embedding models to generate
    embeddings for text. It supports both single text and batch embedding operations.

    Example:
        from neo4j_agent_memory.embeddings import VertexAIEmbedder

        # Using Application Default Credentials
        embedder = VertexAIEmbedder(
            model="text-embedding-004",
            project_id="my-gcp-project",
            location="us-central1",
        )

        # Generate embedding
        embedding = await embedder.embed("Hello, world!")

        # Batch embedding
        embeddings = await embedder.embed_batch([
            "First text",
            "Second text",
            "Third text",
        ])

    Note:
        Requires the `google-cloud-aiplatform` package. Install with:
        ``pip install neo4j-agent-memory[vertex-ai]``

    Attributes:
        dimensions: The embedding vector dimensions (768 for most models).
    """

    def __init__(
        self,
        model: str = "text-embedding-004",
        *,
        project_id: str | None = None,
        location: str = "us-central1",
        credentials: Any | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ):
        """Initialize Vertex AI embedder.

        Args:
            model: Vertex AI embedding model name. Defaults to "text-embedding-004".
            project_id: GCP project ID. If not provided, uses the default project
                from Application Default Credentials (ADC).
            location: GCP region for Vertex AI. Defaults to "us-central1".
            credentials: Optional Google Cloud credentials object. If not provided,
                uses Application Default Credentials.
            batch_size: Maximum texts per API call. Defaults to 250 (Vertex AI limit).
            task_type: The type of task for which embeddings are generated.
                Options: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, SEMANTIC_SIMILARITY,
                CLASSIFICATION, CLUSTERING. Defaults to "RETRIEVAL_DOCUMENT".
        """
        self._model = model
        self._project_id = project_id
        self._location = location
        self._credentials = credentials
        self._batch_size = min(batch_size, DEFAULT_BATCH_SIZE)
        self._task_type = task_type
        self._embedding_model: TextEmbeddingModel | None = None
        self._initialized = False

        # Determine dimensions from model name
        self._dimensions = VERTEX_MODEL_DIMENSIONS.get(model, 768)

    def _ensure_initialized(self) -> "TextEmbeddingModel":
        """Ensure Vertex AI is initialized and return the embedding model."""
        if self._embedding_model is not None:
            return self._embedding_model

        try:
            import vertexai
            from vertexai.language_models import TextEmbeddingModel
        except ImportError:
            raise EmbeddingError(
                "Vertex AI package not installed. Install with: "
                "pip install neo4j-agent-memory[vertex-ai]"
            )

        try:
            # Initialize Vertex AI
            vertexai.init(
                project=self._project_id,
                location=self._location,
                credentials=self._credentials,
            )

            # Load the embedding model
            self._embedding_model = TextEmbeddingModel.from_pretrained(self._model)
            self._initialized = True

            return self._embedding_model

        except Exception as e:
            raise EmbeddingError(f"Failed to initialize Vertex AI: {e}") from e

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: The text to embed.

        Returns:
            Embedding vector as list of floats.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        model = self._ensure_initialized()

        try:
            from vertexai.language_models import TextEmbeddingInput

            # Wrap text with task_type using TextEmbeddingInput
            input_obj = TextEmbeddingInput(text, task_type=self._task_type)

            # Run synchronous Vertex AI API call in thread pool to avoid blocking event loop
            embeddings = await asyncio.to_thread(model.get_embeddings, [input_obj])
            return embeddings[0].values

        except Exception as e:
            raise EmbeddingError(f"Failed to generate embedding: {e}") from e

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts efficiently.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        if not texts:
            return []

        model = self._ensure_initialized()
        all_embeddings: list[list[float]] = []

        try:
            from vertexai.language_models import TextEmbeddingInput

            # Process in batches (Vertex AI limit is 250 per request)
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                # Wrap texts with task_type using TextEmbeddingInput
                input_objs = [
                    TextEmbeddingInput(text, task_type=self._task_type) for text in batch
                ]

                # Run synchronous Vertex AI API call in thread pool to avoid blocking event loop
                embeddings = await asyncio.to_thread(
                    model.get_embeddings, input_objs  # type: ignore[arg-type]
                )
                all_embeddings.extend([e.values for e in embeddings])

            return all_embeddings

        except Exception as e:
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
