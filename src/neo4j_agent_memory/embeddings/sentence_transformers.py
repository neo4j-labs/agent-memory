"""Sentence Transformers embedding provider for local embeddings."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


# Model dimensions mapping (common models)
MODEL_DIMENSIONS = {
    "all-MiniLM-L6-v2": 384,
    "all-MiniLM-L12-v2": 384,
    "all-mpnet-base-v2": 768,
    "paraphrase-MiniLM-L6-v2": 384,
    "multi-qa-MiniLM-L6-cos-v1": 384,
    "all-distilroberta-v1": 768,
}


class SentenceTransformerEmbedder(BaseEmbedder):
    """Local sentence-transformers embedding provider."""

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        *,
        device: str = "cpu",
    ):
        """
        Initialize SentenceTransformer embedder.

        Args:
            model_name: Name of the sentence-transformers model
            device: Device to use (cpu, cuda, mps)
        """
        self._model_name = model_name
        self._device = device
        self._model: SentenceTransformer | None = None
        self._dimensions: int | None = None

    def _ensure_model(self) -> SentenceTransformer:
        """Lazy load the model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise EmbeddingError(
                    "sentence-transformers package not installed. "
                    "Install with: pip install neo4j-agent-memory[sentence-transformers]"
                )
            self._model = SentenceTransformer(self._model_name, device=self._device)
            # Get actual dimensions from model
            self._dimensions = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        if self._dimensions is not None:
            return self._dimensions
        # Return known dimensions or default
        return MODEL_DIMENSIONS.get(self._model_name, 384)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        model = self._ensure_model()

        try:
            # Run in thread pool since sentence-transformers is sync
            loop = asyncio.get_event_loop()
            embedding = await loop.run_in_executor(
                None, lambda: model.encode(text, convert_to_numpy=True)
            )
            return cast(list[float], embedding.tolist())  # numpy tolist() is Any in stubs
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embedding: {e}") from e

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts efficiently."""
        if not texts:
            return []

        model = self._ensure_model()

        try:
            # Run in thread pool since sentence-transformers is sync
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                None, lambda: model.encode(texts, convert_to_numpy=True)
            )
            return [e.tolist() for e in embeddings]
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
