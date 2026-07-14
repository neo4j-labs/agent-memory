"""Amazon Bedrock embedding provider.

Supports Amazon Bedrock text embedding models including:
- amazon.titan-embed-text-v2:0 (1024 dimensions, recommended)
- amazon.titan-embed-text-v1 (1536 dimensions)
- cohere.embed-english-v3 (1024 dimensions)
- cohere.embed-multilingual-v3 (1024 dimensions)
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.base import BaseEmbedder

if TYPE_CHECKING:
    from types import ModuleType


# Model dimensions mapping
BEDROCK_MODEL_DIMENSIONS = {
    "amazon.titan-embed-text-v2:0": 1024,
    "amazon.titan-embed-text-v1": 1536,
    "cohere.embed-english-v3": 1024,
    "cohere.embed-multilingual-v3": 1024,
}

# Default batch size (Bedrock supports up to 25 texts per request for Titan)
DEFAULT_BATCH_SIZE = 25


class BedrockEmbedder(BaseEmbedder):
    """Amazon Bedrock embedding provider.

    This embedder uses Amazon Bedrock's text embedding models to generate
    embeddings for text. It supports both single text and batch embedding operations.

    Example:
        from neo4j_agent_memory.embeddings import BedrockEmbedder

        # Using default AWS credentials (from environment or IAM role)
        embedder = BedrockEmbedder(
            model="amazon.titan-embed-text-v2:0",
            region_name="us-east-1",
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
        Requires the `boto3` package. Install with:
        ``pip install neo4j-agent-memory[bedrock]``

    Attributes:
        dimensions: The embedding vector dimensions (1024 for Titan V2, 1536 for V1).
    """

    def __init__(
        self,
        model: str = "amazon.titan-embed-text-v2:0",
        *,
        region_name: str | None = None,
        profile_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        normalize: bool = True,
    ):
        """Initialize Amazon Bedrock embedder.

        Args:
            model: Bedrock embedding model ID. Defaults to "amazon.titan-embed-text-v2:0".
            region_name: AWS region for Bedrock. If not provided, uses the default
                region from AWS credentials or environment.
            profile_name: AWS credentials profile name. If not provided, uses the
                default profile or environment credentials.
            aws_access_key_id: AWS access key ID. If not provided, uses credentials
                from the profile or environment.
            aws_secret_access_key: AWS secret access key. If not provided, uses
                credentials from the profile or environment.
            batch_size: Maximum texts per API call. Defaults to 25 (Titan limit).
            normalize: Whether to normalize embeddings (L2 normalization).
                Defaults to True. Only applies to Titan models.
        """
        self._model = model
        self._region_name = region_name
        self._profile_name = profile_name
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._batch_size = min(batch_size, DEFAULT_BATCH_SIZE)
        self._normalize = normalize
        self._client: Any = None
        self._boto3: ModuleType | None = None

        # Determine dimensions from model name
        self._dimensions = BEDROCK_MODEL_DIMENSIONS.get(model, 1024)

    def _ensure_client(self) -> Any:
        """Ensure boto3 client is initialized and return it."""
        if self._client is not None:
            return self._client

        try:
            import boto3

            self._boto3 = boto3
        except ImportError:
            raise EmbeddingError(
                "boto3 package not installed. Install with: pip install neo4j-agent-memory[bedrock]"
            )

        try:
            # Build session kwargs
            session_kwargs: dict[str, Any] = {}
            if self._profile_name:
                session_kwargs["profile_name"] = self._profile_name

            # Create session
            session = boto3.Session(**session_kwargs)

            # Build client kwargs
            client_kwargs: dict[str, Any] = {
                "service_name": "bedrock-runtime",
            }
            if self._region_name:
                client_kwargs["region_name"] = self._region_name
            if self._aws_access_key_id and self._aws_secret_access_key:
                client_kwargs["aws_access_key_id"] = self._aws_access_key_id
                client_kwargs["aws_secret_access_key"] = self._aws_secret_access_key

            self._client = session.client(**client_kwargs)
            return self._client

        except Exception as e:
            raise EmbeddingError(f"Failed to initialize Bedrock client: {e}") from e

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self._dimensions

    def _build_request_body(self, text: str) -> str:
        """Build the request body for the embedding API.

        Args:
            text: The text to embed.

        Returns:
            JSON string for the request body.
        """
        # body values vary by model: str, bool, list[str] — use Any
        body: dict[str, Any]
        if self._model.startswith("amazon.titan"):
            # Titan embedding models
            body = {
                "inputText": text,
            }
            if self._normalize:
                body["normalize"] = True  # Titan API accepts bool for normalize
        elif self._model.startswith("cohere"):
            # Cohere embedding models
            body = {
                "texts": [text],  # Cohere API expects a list of strings
                "input_type": "search_document",
            }
        else:
            # Generic fallback
            body = {"inputText": text}

        return json.dumps(body)

    def _parse_response(self, response_body: dict[str, Any]) -> list[float]:
        """Parse the response body to extract embedding.

        Args:
            response_body: The parsed JSON response.

        Returns:
            Embedding vector as list of floats.
        """
        # response_body is dict[str, Any]; cast at this external API boundary
        if self._model.startswith("amazon.titan"):
            return cast(list[float], response_body["embedding"])
        elif self._model.startswith("cohere"):
            return cast(list[float], response_body["embeddings"][0])
        else:
            # Try common keys
            if "embedding" in response_body:
                return cast(list[float], response_body["embedding"])
            elif "embeddings" in response_body:
                return cast(list[float], response_body["embeddings"][0])
            else:
                raise EmbeddingError(f"Unknown response format: {response_body.keys()}")

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: The text to embed.

        Returns:
            Embedding vector as list of floats.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        client = self._ensure_client()

        try:
            # Bedrock SDK uses sync API, run in executor for async
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.invoke_model(
                    modelId=self._model,
                    body=self._build_request_body(text),
                    contentType="application/json",
                    accept="application/json",
                ),
            )

            response_body = json.loads(response["body"].read())
            return self._parse_response(response_body)

        except Exception as e:
            if "EmbeddingError" in type(e).__name__:
                raise
            raise EmbeddingError(f"Failed to generate embedding: {e}") from e

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts efficiently.

        Note: Bedrock's Titan models don't support native batching in a single
        API call, so this method processes texts individually but concurrently
        for better performance.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        if not texts:
            return []

        try:
            # Process texts concurrently in batches
            all_embeddings: list[list[float]] = []

            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                # Run batch concurrently
                tasks = [self.embed(text) for text in batch]
                batch_embeddings = await asyncio.gather(*tasks)
                all_embeddings.extend(batch_embeddings)

            return all_embeddings

        except Exception as e:
            if "EmbeddingError" in type(e).__name__:
                raise
            raise EmbeddingError(f"Failed to generate embeddings: {e}") from e
