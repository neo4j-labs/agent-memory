"""Unit tests for the sentence-transformers embedder (no model download)."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

from neo4j_agent_memory.embeddings.sentence_transformers import SentenceTransformerEmbedder


class _CountingModel:
    """Stands in for a loaded model; records how many ``encode`` calls overlap."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0

    def encode(self, sentences: str | list[str], convert_to_numpy: bool = True) -> np.ndarray:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)
        with self._guard:
            self.active -= 1
        if isinstance(sentences, str):
            return np.zeros(3)
        return np.zeros((len(sentences), 3))


async def test_concurrent_embeds_never_overlap_encode() -> None:
    """Concurrent calls must not run ``encode`` on two threads at once.

    PyTorch's MPS backend segfaults when two threads compile a Metal kernel at
    the same time, and ``embed``/``embed_batch`` run ``encode`` on the default
    executor, so the embedder serializes them.
    """
    embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
    model = _CountingModel()
    embedder._model = model  # type: ignore[assignment]

    results = await asyncio.gather(
        *(embedder.embed(f"text {i}") for i in range(4)),
        *(embedder.embed_batch([f"a {i}", f"b {i}"]) for i in range(4)),
    )

    assert model.max_active == 1
    assert results[0] == [0.0, 0.0, 0.0]
    assert results[-1] == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
