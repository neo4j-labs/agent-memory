"""Embedding providers for vector representations.

.. note::
    The canonical Provider Protocol now lives in
    :mod:`neo4j_agent_memory.llm`. This module retains the legacy
    :class:`Embedder` Protocol and concrete implementations
    (:class:`OpenAIEmbedder`, :class:`SentenceTransformerEmbedder`, etc.)
    for backward compatibility. New code should prefer the providers in
    :mod:`neo4j_agent_memory.llm.adapters`, accessible through
    :func:`neo4j_agent_memory.llm.from_provider`.
"""

from __future__ import annotations

from neo4j_agent_memory.embeddings.base import (
    BaseEmbedder,
    Embedder,
    adapt_to_embedding_provider,
)
from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder

# Re-export the new Protocol so users who reach for it from the old
# location continue to get something sensible.
from neo4j_agent_memory.llm.protocol import EmbeddingProvider

__all__ = [
    # Old protocol + legacy classes (kept for backward compat)
    "BaseEmbedder",
    "Embedder",
    "OpenAIEmbedder",
    "VertexAIEmbedder",
    "BedrockEmbedder",
    "SentenceTransformerEmbedder",
    # New protocol + bridge helper
    "EmbeddingProvider",
    "adapt_to_embedding_provider",
]


# Lazy imports for optional providers
def __getattr__(name: str) -> type:
    if name == "VertexAIEmbedder":
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        return VertexAIEmbedder
    if name == "BedrockEmbedder":
        from neo4j_agent_memory.embeddings.bedrock import BedrockEmbedder

        return BedrockEmbedder
    if name == "SentenceTransformerEmbedder":
        from neo4j_agent_memory.embeddings.sentence_transformers import (
            SentenceTransformerEmbedder,
        )

        return SentenceTransformerEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
