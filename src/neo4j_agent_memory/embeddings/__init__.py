"""Embedding providers for vector representations."""

from neo4j_agent_memory.embeddings.base import BaseEmbedder, Embedder
from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder

__all__ = [
    "BaseEmbedder",
    "Embedder",
    "OpenAIEmbedder",
    "VertexAIEmbedder",
    "BedrockEmbedder",
    "SentenceTransformerEmbedder",
    "OllamaEmbedder",
]


# Lazy imports for optional providers
def __getattr__(name: str):
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
    if name == "OllamaEmbedder":
        from neo4j_agent_memory.embeddings.ollama import OllamaEmbedder

        return OllamaEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
