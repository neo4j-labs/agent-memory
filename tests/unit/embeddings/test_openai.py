"""Unit tests for OpenAI embeddings without SDK or network dependencies."""

from __future__ import annotations

import logging
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


@pytest.fixture
def force_char_fallback():
    """Force the char-estimate fallback path by making ``import tiktoken`` fail.

    Setting ``sys.modules["tiktoken"] = None`` causes a subsequent
    ``import tiktoken`` to raise ``ImportError`` inside ``_truncate_to_tokens``,
    which is the path we want to exercise since tiktoken is optional.
    """
    with patch.dict(sys.modules, {"tiktoken": None}):
        yield


class TestTruncateToTokens:
    """Tests for the module-level ``_truncate_to_tokens`` helper."""

    def test_under_budget_returns_input_unchanged(self, force_char_fallback):
        """Char-estimate path: short input passes through untouched."""
        from neo4j_agent_memory.embeddings.openai import _truncate_to_tokens

        text = "hello world"
        assert _truncate_to_tokens(text, "text-embedding-3-small") == text

    def test_over_budget_truncates_and_warns(self, force_char_fallback, caplog):
        """Char-estimate path: oversize input is truncated to char_budget and a warning is logged."""
        from neo4j_agent_memory.embeddings.openai import (
            _DEFAULT_MAX_INPUT_TOKENS,
            _TOKEN_HEADROOM,
            _truncate_to_tokens,
        )

        char_budget = (_DEFAULT_MAX_INPUT_TOKENS - _TOKEN_HEADROOM) * 4
        oversize = "x" * (char_budget + 5000)

        with caplog.at_level(logging.WARNING, logger="neo4j_agent_memory.embeddings.openai"):
            result = _truncate_to_tokens(oversize, "text-embedding-3-small")

        assert len(result) == char_budget
        assert result == "x" * char_budget
        assert any(
            "exceeds char-estimate budget" in rec.message and "truncating" in rec.message
            for rec in caplog.records
        )

    def test_unknown_model_uses_default_budget(self, force_char_fallback):
        """Unknown models fall back to the default token budget."""
        from neo4j_agent_memory.embeddings.openai import _truncate_to_tokens

        text = "some short text"
        assert _truncate_to_tokens(text, "made-up-model-name") == text


class TestOpenAIEmbedderTruncation:
    """Tests that ``OpenAIEmbedder`` truncates before hitting the API."""

    @pytest.mark.asyncio
    async def test_embed_truncates_oversize_input(self, force_char_fallback):
        """``embed()`` must truncate before calling the OpenAI client."""
        from neo4j_agent_memory.embeddings.openai import (
            _DEFAULT_MAX_INPUT_TOKENS,
            _TOKEN_HEADROOM,
            OpenAIEmbedder,
        )

        char_budget = (_DEFAULT_MAX_INPUT_TOKENS - _TOKEN_HEADROOM) * 4
        oversize = "y" * (char_budget + 10_000)

        embedder = OpenAIEmbedder(model="text-embedding-3-small", api_key="test-key")

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
        mock_client = MagicMock()
        mock_client.embeddings = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        embedder._client = mock_client

        result = await embedder.embed(oversize)

        assert result == [0.1, 0.2, 0.3]
        mock_client.embeddings.create.assert_awaited_once()
        sent_input = mock_client.embeddings.create.await_args.kwargs["input"]
        assert len(sent_input) == char_budget

    @pytest.mark.asyncio
    async def test_embed_batch_truncates_every_item(self, force_char_fallback):
        """``embed_batch()`` must truncate every text before calling the API."""
        from neo4j_agent_memory.embeddings.openai import (
            _DEFAULT_MAX_INPUT_TOKENS,
            _TOKEN_HEADROOM,
            OpenAIEmbedder,
        )

        char_budget = (_DEFAULT_MAX_INPUT_TOKENS - _TOKEN_HEADROOM) * 4
        oversize_a = "a" * (char_budget + 500)
        oversize_b = "b" * (char_budget + 1500)
        small = "c" * 10
        texts = [oversize_a, small, oversize_b]

        embedder = OpenAIEmbedder(model="text-embedding-3-small", api_key="test-key")

        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(index=0, embedding=[0.1]),
            MagicMock(index=1, embedding=[0.2]),
            MagicMock(index=2, embedding=[0.3]),
        ]
        mock_client = MagicMock()
        mock_client.embeddings = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        embedder._client = mock_client

        result = await embedder.embed_batch(texts)

        assert result == [[0.1], [0.2], [0.3]]
        mock_client.embeddings.create.assert_awaited_once()
        sent_batch = mock_client.embeddings.create.await_args.kwargs["input"]
        assert len(sent_batch) == 3
        assert len(sent_batch[0]) == char_budget
        assert sent_batch[1] == small
        assert len(sent_batch[2]) == char_budget

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list_short_circuits(self):
        """Empty list must not touch the client or the truncator."""
        from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder

        embedder = OpenAIEmbedder(model="text-embedding-3-small", api_key="test-key")
        mock_client = MagicMock()
        mock_client.embeddings = MagicMock()
        mock_client.embeddings.create = AsyncMock()
        embedder._client = mock_client

        result = await embedder.embed_batch([])

        assert result == []
        mock_client.embeddings.create.assert_not_called()
