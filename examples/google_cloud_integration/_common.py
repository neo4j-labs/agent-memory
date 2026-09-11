"""Shared setup for the Google Cloud example scripts.

Every script in this directory needs the same three things:

* ``load_env()`` — pick up ``.env`` next to the scripts (guarded, so
  ``python-dotenv`` stays optional).
* ``build_settings()`` — one settings builder that resolves the backend
  (hosted NAMS when ``MEMORY_API_KEY`` is set, otherwise bolt) and the
  embedding/extraction path (OpenAI when ``OPENAI_API_KEY`` is set, otherwise a
  local sentence-transformers embedder with a local spaCy/GLiNER pipeline, so
  the scripts run with no API key at all).
* ``describe_settings()`` — print the resolved backend, embedder, LLM and
  extractor before any work happens, so a degraded install is visible instead
  of silently producing an empty graph.

Footgun handled here once: ``MemorySettings(neo4j=Neo4jConfig(...))`` with
``MEMORY_API_KEY`` present in the environment resolves to ``backend="nams"``
and ignores the bolt config. ``build_settings()`` is explicit about which
backend it is asking for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
from neo4j_agent_memory.extraction import create_extractor, is_gliner_available

#: Default embedding ids. Vertex: ``gemini-embedding-001`` (``text-embedding-004``
#: was shut down 2026-01-14). OpenAI: ``text-embedding-3-small``.
VERTEX_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
OPENAI_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_LLM_MODEL = os.getenv("MEMORY_LLM", "openai/gpt-5-mini")
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def load_env() -> None:
    """Load ``.env`` from this directory (then ``examples/.env``) if present."""
    here = Path(__file__).resolve().parent
    for env_file in (here / ".env", here.parent / ".env"):
        if not env_file.exists():
            continue
        try:
            from dotenv import load_dotenv
        except ImportError:  # python-dotenv is optional
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        else:
            load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")


def use_vertex_embeddings() -> bool:
    """True when the environment asks for (and can reach) Vertex AI embeddings."""
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT")) and os.getenv(
        "EMBEDDING_PROVIDER", ""
    ).lower() in ("vertex_ai", "vertex", "google")


def build_embedding_provider() -> Any:
    """Resolve an embedding provider from the environment (v0.3+ provider strings)."""
    from neo4j_agent_memory.llm import from_provider

    if use_vertex_embeddings():
        return from_provider(
            f"vertex_ai/{VERTEX_EMBEDDING_MODEL}",
            kind="embedding",
            project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("VERTEX_AI_LOCATION", "us-central1"),
        )
    if os.getenv("OPENAI_API_KEY"):
        return from_provider(f"openai/{OPENAI_EMBEDDING_MODEL}", kind="embedding")
    # No cloud credentials: keep embeddings local so the scripts still run.
    return LOCAL_EMBEDDING_MODEL


def local_extraction() -> ExtractionConfig:
    """A local (no-LLM) extraction pipeline, downgraded to what is installed."""
    try:
        import spacy

        has_spacy = bool(spacy.util.is_package("en_core_web_sm"))
    except ImportError:
        has_spacy = False

    if not (has_spacy or is_gliner_available()):
        # Explicit rather than letting the pipeline fall back to NoOpExtractor.
        return ExtractionConfig(extractor_type=ExtractorType.NONE, enable_llm_fallback=False)

    return ExtractionConfig(
        extractor_type=ExtractorType.PIPELINE,
        enable_spacy=has_spacy,
        enable_gliner=is_gliner_available(),
        enable_llm_fallback=False,
    )


def build_settings(embedding: Any = None, *, multi_tenant: bool = False) -> MemorySettings:
    """Build ``MemorySettings`` for whichever backend the environment selects.

    Args:
        embedding: Optional embedding provider (or provider string) to use
            instead of the environment-resolved default.
        multi_tenant: When True, sets ``memory.multi_tenant`` so every write
            must carry ``user_identifier=``. Only enable it for scripts that
            actually pass one.
    """
    memory: dict[str, Any] = {"multi_tenant": True} if multi_tenant else {}

    if os.getenv("MEMORY_API_KEY"):
        # Hosted NAMS: embeddings and entity extraction run server-side.
        # MemorySettings() reads MEMORY_API_KEY / MEMORY_ENDPOINT itself.
        return MemorySettings(memory=memory) if memory else MemorySettings()

    embedding = embedding if embedding is not None else build_embedding_provider()

    neo4j = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", os.getenv("NEO4J_USER", "neo4j")),
        password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )

    if os.getenv("OPENAI_API_KEY"):
        return MemorySettings(
            backend="bolt",
            neo4j=neo4j,
            llm=OPENAI_LLM_MODEL,
            embedding=embedding,
            memory=memory,
        )

    return MemorySettings(
        backend="bolt",
        neo4j=neo4j,
        llm=None,
        embedding=embedding,
        extraction=local_extraction(),
        memory=memory,
    )


def describe_extractor(extractor: Any) -> str:
    """Name the extractor and its pipeline stages."""
    name = type(extractor).__name__
    stages = getattr(extractor, "stages", None)
    if stages:
        labels = [getattr(stage, "name", None) or type(stage).__name__ for stage in stages]
        name += " (" + ", ".join(labels) + ")"
    return name


def build_extractor(settings: MemorySettings) -> Any:
    """Build the extractor the client will use, or ``None`` on NAMS."""
    if settings.backend == "nams":
        return None
    return create_extractor(settings.extraction, None, settings.llm)


def describe_settings(settings: MemorySettings, extractor: Any = None) -> None:
    """Print the resolved configuration before any memory work happens."""
    embedding = settings.embedding
    label = getattr(embedding, "model", None) or type(embedding).__name__
    print(f"  backend (requested): {settings.backend}")
    print(f"  embedding: {type(embedding).__name__} — {label}")
    print(f"  llm: {settings.llm}")
    if extractor is not None:
        print(f"  extractor: {describe_extractor(extractor)}")
    elif settings.backend == "nams":
        print("  extractor: server-side (hosted NAMS)")
