"""Vertex AI embedding model facts (single source of truth).

Kept free of intra-package imports so both
:mod:`neo4j_agent_memory.embeddings.vertex_ai` and
:mod:`neo4j_agent_memory.config.settings` can depend on it.

Google retired ``text-embedding-004`` on 2026-01-14 and the
``textembedding-gecko*`` family on 2025-04-09; the current generation is
``gemini-embedding-001`` (3072 native dimensions, Matryoshka-truncatable to
1536 or 768), plus the task-specific ``text-embedding-005`` and
``text-multilingual-embedding-002`` (768 each).
"""

from __future__ import annotations

# Default Vertex AI embedding model.
VERTEX_DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"

# Default truncation target. 768 matches the dimensionality of the previous
# default (``text-embedding-004``) so Neo4j vector indexes -- whose
# dimensionality is fixed at creation time -- keep working unchanged.
VERTEX_DEFAULT_OUTPUT_DIMENSIONALITY = 768

# Supported models mapped to their native (untruncated) output dimensions.
VERTEX_EMBEDDING_MODELS: dict[str, int] = {
    "gemini-embedding-001": 3072,
    "text-embedding-005": 768,
    "text-multilingual-embedding-002": 768,
}

# Retired model ids mapped to the date Google stopped serving them. Used to
# fail fast with a message naming the replacement instead of surfacing a 404.
VERTEX_RETIRED_EMBEDDING_MODELS: dict[str, str] = {
    "text-embedding-004": "2026-01-14",
    "textembedding-gecko": "2025-04-09",
    "textembedding-gecko@001": "2025-04-09",
    "textembedding-gecko@002": "2025-04-09",
    "textembedding-gecko@003": "2025-04-09",
    "textembedding-gecko-multilingual@001": "2025-04-09",
}

# Per-model cap on texts per request. ``gemini-embedding-001`` accepts a
# single input per request; the other models accept up to 250.
VERTEX_MAX_BATCH_SIZE = 250
VERTEX_MODEL_MAX_BATCH: dict[str, int] = {
    "gemini-embedding-001": 1,
}


__all__ = [
    "VERTEX_DEFAULT_EMBEDDING_MODEL",
    "VERTEX_DEFAULT_OUTPUT_DIMENSIONALITY",
    "VERTEX_EMBEDDING_MODELS",
    "VERTEX_MAX_BATCH_SIZE",
    "VERTEX_MODEL_MAX_BATCH",
    "VERTEX_RETIRED_EMBEDDING_MODELS",
]
