"""Vertex AI embedding provider.

Supported Google Cloud Vertex AI text embedding models:

- ``gemini-embedding-001`` — default, 3072 native dimensions, truncatable
  (Matryoshka) to 1536 or 768 via ``output_dimensionality``.
- ``text-embedding-005`` — 768 dimensions, English/code.
- ``text-multilingual-embedding-002`` — 768 dimensions, multilingual.

Retired model ids (``text-embedding-004``, ``textembedding-gecko*``) raise
:class:`~neo4j_agent_memory.core.exceptions.EmbeddingError` with the
replacement named; see :data:`RETIRED_VERTEX_MODELS`.

.. note::
   ``output_dimensionality`` defaults to 768 rather than the model's native
   3072 so that Neo4j vector indexes created against the previous default
   (``text-embedding-004``, 768 dimensions) keep working unchanged. Vector
   index dimensionality is fixed at creation time, so raising it means
   dropping and recreating the indexes and re-embedding every node. Pass
   ``output_dimensionality=None`` for the model's native dimensionality (the
   highest retrieval quality) on a fresh database, or 1536 as a middle
   ground.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder
from neo4j_agent_memory.embeddings.vertex_models import (
    VERTEX_DEFAULT_EMBEDDING_MODEL,
    VERTEX_DEFAULT_OUTPUT_DIMENSIONALITY,
    VERTEX_EMBEDDING_MODELS,
    VERTEX_MAX_BATCH_SIZE,
    VERTEX_MODEL_MAX_BATCH,
    VERTEX_RETIRED_EMBEDDING_MODELS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


# Backwards-compatible aliases for the model tables, which now live in
# :mod:`neo4j_agent_memory.embeddings.vertex_models`.
DEFAULT_VERTEX_MODEL = VERTEX_DEFAULT_EMBEDDING_MODEL
DEFAULT_OUTPUT_DIMENSIONALITY = VERTEX_DEFAULT_OUTPUT_DIMENSIONALITY
VERTEX_MODEL_DIMENSIONS = VERTEX_EMBEDDING_MODELS
RETIRED_VERTEX_MODELS = VERTEX_RETIRED_EMBEDDING_MODELS
DEFAULT_BATCH_SIZE = VERTEX_MAX_BATCH_SIZE


def _reject_retired_model(model: str) -> None:
    """Raise :class:`EmbeddingError` if ``model`` is a retired Vertex AI id."""
    shutdown = RETIRED_VERTEX_MODELS.get(model)
    if shutdown is None:
        return
    supported = ", ".join(sorted(VERTEX_MODEL_DIMENSIONS))
    raise EmbeddingError(
        f"Vertex AI embedding model {model!r} was retired by Google on {shutdown} "
        f"and no longer serves requests. Use one of: {supported} "
        f"(default: {DEFAULT_VERTEX_MODEL})."
    )


class VertexAIEmbedder(BaseEmbedder):
    """Vertex AI embedding provider.

    This embedder uses Google Cloud's Vertex AI text embedding models to generate
    embeddings for text. It supports both single text and batch embedding operations.

    Example:
        from neo4j_agent_memory.embeddings import VertexAIEmbedder

        # Using Application Default Credentials
        embedder = VertexAIEmbedder(
            model="gemini-embedding-001",
            project_id="my-gcp-project",
            location="us-central1",
        )

        # Generate embedding (768 dimensions by default)
        embedding = await embedder.embed("Hello, world!")

        # Batch embedding
        embeddings = await embedder.embed_batch([
            "First text",
            "Second text",
            "Third text",
        ])

        # Full native dimensionality (3072) on a fresh database
        embedder = VertexAIEmbedder(output_dimensionality=None)

    Note:
        Requires the `google-cloud-aiplatform` package. Install with:
        ``pip install neo4j-agent-memory[vertex-ai]``

        On ``google-cloud-aiplatform`` 1.x the legacy
        ``vertexai.language_models`` path is used; on 2.x, where that module
        was removed, the embedder falls back to the ``google-genai`` client
        (``pip install google-genai``). The public surface is identical.

    Attributes:
        dimensions: The embedding vector dimensions actually produced.
    """

    def __init__(
        self,
        model: str = DEFAULT_VERTEX_MODEL,
        *,
        project_id: str | None = None,
        location: str = "us-central1",
        credentials: Any | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        task_type: str = "RETRIEVAL_DOCUMENT",
        output_dimensionality: int | None = DEFAULT_OUTPUT_DIMENSIONALITY,
    ):
        """Initialize Vertex AI embedder.

        Args:
            model: Vertex AI embedding model name. Defaults to
                ``"gemini-embedding-001"``. Retired ids raise
                :class:`EmbeddingError`.
            project_id: GCP project ID. If not provided, uses the default project
                from Application Default Credentials (ADC).
            location: GCP region for Vertex AI. Defaults to "us-central1".
            credentials: Optional Google Cloud credentials object. If not provided,
                uses Application Default Credentials.
            batch_size: Maximum texts per API call. Defaults to 250 (Vertex AI
                limit), further capped per model -- ``gemini-embedding-001``
                accepts one text per request.
            task_type: The type of task for which embeddings are generated.
                Options: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, SEMANTIC_SIMILARITY,
                CLASSIFICATION, CLUSTERING. Defaults to "RETRIEVAL_DOCUMENT".
            output_dimensionality: Truncate embeddings to this many dimensions.
                Defaults to 768 so Neo4j vector indexes sized for the previous
                ``text-embedding-004`` default keep working. Pass ``None`` for
                the model's native dimensionality.

        Raises:
            EmbeddingError: If ``model`` is retired, or
                ``output_dimensionality`` is not a positive int.
        """
        _reject_retired_model(model)
        if output_dimensionality is not None and output_dimensionality < 1:
            raise EmbeddingError(
                f"output_dimensionality must be a positive int or None, "
                f"got {output_dimensionality!r}."
            )

        self._model = model
        self._project_id = project_id
        self._location = location
        self._credentials = credentials
        self._task_type = task_type
        self._output_dimensionality = output_dimensionality
        # The client object is either a legacy ``TextEmbeddingModel`` (1.x) or a
        # ``google.genai.Client`` (2.x fallback); ``_backend`` says which.
        self._embedding_model: Any = None
        self._backend: str | None = None
        self._init_lock = asyncio.Lock()

        model_cap = VERTEX_MODEL_MAX_BATCH.get(model, DEFAULT_BATCH_SIZE)
        self._batch_size = max(1, min(batch_size, model_cap))

        # Dimensions actually produced: the truncation target when set,
        # otherwise the model's native size.
        native = VERTEX_MODEL_DIMENSIONS.get(model, DEFAULT_OUTPUT_DIMENSIONALITY)
        self._dimensions = output_dimensionality if output_dimensionality is not None else native

    def _ensure_initialized(self) -> Any:
        """Ensure Vertex AI is initialized and return the embedding client.

        Note: For thread-safe async initialization, use _ensure_initialized_async instead.
        """
        if self._embedding_model is not None:
            return self._embedding_model

        try:
            import vertexai
            from vertexai.language_models import TextEmbeddingModel
        except ImportError:
            # google-cloud-aiplatform 2.x removed ``vertexai.language_models``;
            # fall back to the google-genai client when it is available.
            return self._ensure_initialized_genai()

        try:
            # Initialize Vertex AI (note: sets global state in vertexai library)
            vertexai.init(
                project=self._project_id,
                location=self._location,
                credentials=self._credentials,
            )

            # Load the embedding model
            self._embedding_model = TextEmbeddingModel.from_pretrained(self._model)
            self._backend = "vertexai"

            return self._embedding_model

        except Exception as e:
            raise EmbeddingError(f"Failed to initialize Vertex AI: {e}") from e

    def _ensure_initialized_genai(self) -> Any:
        """Initialize the ``google-genai`` Vertex AI client (aiplatform 2.x path)."""
        try:
            from google import genai
        except ImportError:
            raise EmbeddingError(
                "Vertex AI package not installed. Install with: "
                "pip install neo4j-agent-memory[vertex-ai]"
            ) from None

        try:
            self._embedding_model = genai.Client(
                vertexai=True,
                project=self._project_id,
                location=self._location,
                credentials=self._credentials,
            )
            self._backend = "genai"
            return self._embedding_model
        except Exception as e:
            raise EmbeddingError(f"Failed to initialize Vertex AI: {e}") from e

    async def _ensure_initialized_async(self) -> Any:
        """Thread-safe async initialization."""
        if self._embedding_model is not None:
            return self._embedding_model

        async with self._init_lock:
            # Re-check under the lock: another coroutine may have initialized
            # the model while we awaited lock acquisition.
            if self._embedding_model is None:
                self._embedding_model = await asyncio.to_thread(self._ensure_initialized)
            return self._embedding_model

    @property
    def model(self) -> str:
        """Return the Vertex AI model name."""
        return self._model

    @property
    def task_type(self) -> str:
        """Return the embedding task type."""
        return self._task_type

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self._dimensions

    @property
    def output_dimensionality(self) -> int | None:
        """Return the configured truncation target, or ``None`` for native size."""
        return self._output_dimensionality

    def _embed_sync(self, client: Any, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` with a single API call (runs on a worker thread)."""
        if self._backend == "genai":
            from google.genai.types import EmbedContentConfig

            config = EmbedContentConfig(
                task_type=self._task_type,
                output_dimensionality=self._output_dimensionality,
            )
            response = client.models.embed_content(
                model=self._model,
                contents=list(texts),
                config=config,
            )
            return [list(e.values) for e in response.embeddings]

        from vertexai.language_models import TextEmbeddingInput

        inputs: list[str | TextEmbeddingInput] = [
            TextEmbeddingInput(t, task_type=self._task_type) for t in texts
        ]
        if self._output_dimensionality is not None:
            embeddings = client.get_embeddings(
                inputs, output_dimensionality=self._output_dimensionality
            )
        else:
            embeddings = client.get_embeddings(inputs)
        return [list(e.values) for e in embeddings]

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: The text to embed.

        Returns:
            Embedding vector as list of floats.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        client = await self._ensure_initialized_async()

        try:
            vectors = await asyncio.to_thread(self._embed_sync, client, [text])
            return vectors[0]

        except EmbeddingError:
            raise
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

        client = await self._ensure_initialized_async()
        all_embeddings: list[list[float]] = []

        try:
            # Process in batches (Vertex AI limit is 250 per request, 1 for
            # gemini-embedding-001).
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                all_embeddings.extend(await asyncio.to_thread(self._embed_sync, client, batch))

            return all_embeddings

        except EmbeddingError:
            raise
        except Exception as e:
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
