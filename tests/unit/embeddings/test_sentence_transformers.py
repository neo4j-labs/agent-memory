"""Unit tests for the local sentence-transformers embedding provider."""

import threading
from typing import Any
from unittest.mock import patch

from neo4j_agent_memory.embeddings.sentence_transformers import (
    SentenceTransformerEmbedder,
    preload,
)


class _FakeEncoding:
    def tolist(self) -> list[float]:
        return [0.1, 0.2, 0.3]


class _FakeModel:
    def encode(
        self, text_or_texts: Any, *args: Any, **kwargs: Any
    ) -> _FakeEncoding | list[_FakeEncoding]:
        # Mirror sentence-transformers: a batch (list input) yields one encoding
        # per text; a single string yields a single encoding.
        if isinstance(text_or_texts, (list, tuple)):
            return [_FakeEncoding() for _ in text_or_texts]
        return _FakeEncoding()

    def get_sentence_embedding_dimension(self) -> int:
        return 3


class TestSentenceTransformerEmbedderThreading:
    """The heavy model load must never run on the asyncio event-loop thread."""

    async def test_embed_loads_model_off_the_event_loop_thread(self) -> None:
        """Regression guard for the Windows first-call hang.

        The first ``embed`` call lazily imports ``sentence_transformers``
        (-> scipy native libs) via ``_ensure_model``. Doing that import on the
        event-loop thread deadlocks in the Windows DLL loader lock, so
        ``_ensure_model`` must be offloaded to a worker thread.
        """
        embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
        loop_thread_id = threading.get_ident()
        seen: dict[str, int] = {}

        def fake_ensure() -> _FakeModel:
            seen["thread_id"] = threading.get_ident()
            return _FakeModel()

        with patch.object(embedder, "_ensure_model", side_effect=fake_ensure):
            result = await embedder.embed("hello")

        assert result == [0.1, 0.2, 0.3]
        assert seen["thread_id"] != loop_thread_id

    async def test_embed_batch_loads_model_off_the_event_loop_thread(self) -> None:
        """``embed_batch`` offloads ``_ensure_model`` for the same reason."""
        embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
        loop_thread_id = threading.get_ident()
        seen: dict[str, int] = {}

        def fake_ensure() -> _FakeModel:
            seen["thread_id"] = threading.get_ident()
            return _FakeModel()

        with patch.object(embedder, "_ensure_model", side_effect=fake_ensure):
            result = await embedder.embed_batch(["a", "b"])

        assert result == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
        assert seen["thread_id"] != loop_thread_id


class TestPreload:
    """``preload`` imports the native stack up-front, or no-ops if absent."""

    def test_preload_is_noop_when_not_installed(self) -> None:
        with (
            patch("importlib.util.find_spec", return_value=None) as find_spec,
            patch("importlib.import_module") as import_module,
        ):
            preload()

        find_spec.assert_called_once_with("sentence_transformers")
        import_module.assert_not_called()

    def test_preload_imports_when_installed(self) -> None:
        with (
            patch("importlib.util.find_spec", return_value=object()),
            patch("importlib.import_module") as import_module,
        ):
            preload()

        import_module.assert_called_once_with("sentence_transformers")
