"""Ollama embedding provider (OpenAI-compatible API)."""

from typing import TYPE_CHECKING

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder

if TYPE_CHECKING:
    from openai import AsyncOpenAI


# Ollama model dimensions
MODEL_DIMENSIONS = {
    "nomic-embed-text": 768,
    "nomic-embed-text:latest": 768,
    "nomic-embed-text-v2-moe": 768,
    "nomic-embed-text-v2-moe:latest": 768,
}


class OllamaEmbedder(BaseEmbedder):
    """Ollama embedding provider using OpenAI-compatible API."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        *,
        base_url: str = "http://localhost:11434",
        api_key: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 100,
    ):
        """
        Initialize Ollama embedder.

        Args:
            model: Ollama embedding model name
            base_url: Ollama server URL (default: http://localhost:11434)
            api_key: Optional API key (Ollama typically doesn't require one)
            dimensions: Optional dimension override
            batch_size: Maximum texts per API call
        """
        self._model = model
        self._base_url = base_url
        self._api_key = api_key or "ollama"  # Ollama accepts any key
        self._requested_dimensions = dimensions
        self._batch_size = batch_size
        self._client: AsyncOpenAI | None = None

        # Determine dimensions
        if dimensions is not None:
            self._dimensions = dimensions
        else:
            self._dimensions = MODEL_DIMENSIONS.get(model, 768)

    def _ensure_client(self) -> "AsyncOpenAI":
        """Ensure the OpenAI client is initialized with Ollama endpoint."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise EmbeddingError(
                    "OpenAI package not installed. Install with: pip install openai"
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=f"{self._base_url}/v1",
            )
        return self._client

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        client = self._ensure_client()

        try:
            response = await client.embeddings.create(
                input=text,
                model=self._model,
            )
            return response.data[0].embedding
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embedding: {e}") from e

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts efficiently."""
        if not texts:
            return []

        client = self._ensure_client()
        all_embeddings: list[list[float]] = []

        try:
            # Process in batches
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                response = await client.embeddings.create(
                    input=batch,
                    model=self._model,
                )
                # Sort by index to maintain order
                sorted_data = sorted(response.data, key=lambda x: x.index)
                all_embeddings.extend([d.embedding for d in sorted_data])

            return all_embeddings
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
