"""Unit tests for OpenAI embeddings without SDK or network dependencies."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neo4j_agent_memory.core.exceptions import EmbeddingError
from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder


@pytest.mark.parametrize("api_key", [None, "test-explicit-key"])
@pytest.mark.parametrize("batch", [False, True])
async def test_missing_sdk_explains_openai_and_local_choices(
    api_key: str | None, batch: bool
) -> None:
    """Both embedding entrypoints must help users who deliberately want a local model."""
    with patch.dict(sys.modules, {"openai": None}):
        embedder = OpenAIEmbedder(api_key=api_key)
        assert embedder._client is None

        with pytest.raises(EmbeddingError, match="OpenAI embeddings are selected") as exc_info:
            if batch:
                await embedder.embed_batch(["hello"])
            else:
                await embedder.embed("hello")

    message = str(exc_info.value)
    assert "pip install 'neo4j-agent-memory[openai]'" in message
    assert "OPENAI_API_KEY" in message
    assert "pip install 'neo4j-agent-memory[sentence-transformers]'" in message
    assert "--embedding sentence-transformers/all-MiniLM-L6-v2" in message
    assert "NAM_EMBEDDING=sentence-transformers/all-MiniLM-L6-v2" in message
    assert "https://neo4j.com/labs/agent-memory/how-to/configure-embedding-provider/" in message
    assert embedder._client is None


@pytest.mark.parametrize("batch", [False, True])
async def test_available_sdk_stays_lazy_and_reuses_client(batch: bool) -> None:
    """The diagnostic change must preserve successful requests and lazy initialization."""
    response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])])
    client = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(return_value=response)))
    constructor = MagicMock(return_value=client)
    sdk = SimpleNamespace(AsyncOpenAI=constructor)

    with patch.dict(sys.modules, {"openai": sdk}):
        embedder = OpenAIEmbedder(api_key="test-explicit-key", dimensions=2)
        constructor.assert_not_called()

        for _ in range(2):
            if batch:
                assert await embedder.embed_batch(["hello"]) == [[0.1, 0.2]]
            else:
                assert await embedder.embed("hello") == [0.1, 0.2]

    constructor.assert_called_once_with(api_key="test-explicit-key")
    client.embeddings.create.assert_called_with(
        input=["hello"] if batch else "hello",
        model="text-embedding-3-small",
        dimensions=2,
    )
    assert client.embeddings.create.await_count == 2


async def test_empty_batch_does_not_require_sdk() -> None:
    """An empty batch must keep returning immediately even when the SDK is absent."""
    with patch.dict(sys.modules, {"openai": None}):
        embedder = OpenAIEmbedder()
        assert await embedder.embed_batch([]) == []
        assert embedder._client is None
