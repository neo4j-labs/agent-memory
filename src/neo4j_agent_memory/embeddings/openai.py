"""OpenAI embedding provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder

if TYPE_CHECKING:
    from openai import AsyncOpenAI


# Model dimensions mapping
MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedding provider."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        api_key: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 100,
    ):
        """
        Initialize OpenAI embedder.

        Args:
            model: OpenAI embedding model name
            api_key: OpenAI API key (uses OPENAI_API_KEY env var if not provided)
            dimensions: Optional dimension reduction (for text-embedding-3-* models)
            batch_size: Maximum texts per API call
        """
        self._model = model
        self._api_key = api_key
        self._requested_dimensions = dimensions
        self._batch_size = batch_size
        self._client: AsyncOpenAI | None = None

        # Determine dimensions
        if dimensions is not None:
            self._dimensions = dimensions
        else:
            self._dimensions = MODEL_DIMENSIONS.get(model, 1536)

    def _ensure_client(self) -> AsyncOpenAI:
        """Ensure the OpenAI client is initialized."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise EmbeddingError(
                    "OpenAI package not installed. Install with: pip install neo4j-agent-memory[openai]"
                )
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        client = self._ensure_client()

        try:
            kwargs: dict[str, Any] = {"input": text, "model": self._model}
            if self._requested_dimensions is not None:
                kwargs["dimensions"] = self._requested_dimensions

            response = await client.embeddings.create(**kwargs)
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
                kwargs: dict[str, Any] = {"input": batch, "model": self._model}
                if self._requested_dimensions is not None:
                    kwargs["dimensions"] = self._requested_dimensions

                response = await client.embeddings.create(**kwargs)
                # Sort by index to maintain order
                sorted_data = sorted(response.data, key=lambda x: x.index)
                all_embeddings.extend([d.embedding for d in sorted_data])

            return all_embeddings
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
