"""Memory client factory and lifecycle management.

One Neo4j driver for the memory graph, created in the FastAPI lifespan and
shared by every route. ``MemoryIntegration`` wraps *that same* client (it is
constructed with ``client=``, not with connection parameters) so automatic
entity extraction and preference detection run against one connection rather
than opening a second driver with a different configuration.
"""

import logging
from typing import Any

from neo4j_agent_memory import (
    BoltSettings,
    ExtractionConfig,
    ExtractorType,
    MemoryClient,
    MemoryIntegration,
    Neo4jConfig,
    SessionStrategy,
)
from neo4j_agent_memory.llm import from_provider
from src.config import ExtractionMode, get_settings

logger = logging.getLogger(__name__)

_memory_client: MemoryClient | None = None
_memory_integration: MemoryIntegration | None = None
_memory_connected: bool = False
_memory_error: str | None = None


def build_extraction_config(mode: ExtractionMode) -> ExtractionConfig:
    """Map the ``EXTRACTION_MODE`` setting onto an ``ExtractionConfig``.

    ``local`` runs the spaCy -> GLiNER pipeline (no API key, no per-message
    cost; the models download on first use). ``llm`` swaps GLiNER for the LLM
    fallback. ``none`` is explicit: it selects ``ExtractorType.NONE`` rather
    than leaving a PIPELINE with every stage disabled, which would silently
    degrade to a ``NoOpExtractor`` and make long-term memory look broken.
    """
    if mode == "none":
        return ExtractionConfig(extractor_type=ExtractorType.NONE)
    return ExtractionConfig(
        extractor_type=ExtractorType.PIPELINE,
        enable_spacy=True,
        enable_gliner=mode == "local",
        enable_llm_fallback=mode == "llm",
    )


async def init_memory_client() -> MemoryClient | None:
    """Initialize the memory client singleton.

    Returns the client if connected successfully, None otherwise.
    The app can still run without memory features if Neo4j is unavailable —
    ``/health`` then reports ``memory_connected: false`` plus the error, so a
    misconfigured password is visible instead of silently dropping writes.
    """
    global _memory_client, _memory_integration, _memory_connected, _memory_error

    if _memory_client is not None:
        return _memory_client

    settings = get_settings()

    # Build provider kwargs honouring optional LLM_MODEL / EMBEDDING_MODEL
    # env vars (see config.py). Empty strings fall through to the library's
    # OpenAI defaults so existing setups keep working unchanged.
    memory_kwargs: dict[str, Any] = {}

    if settings.embedding_model:
        emb_kwargs: dict[str, Any] = {}
        if (
            settings.embedding_model.startswith("openai/")
            and settings.openai_api_key.get_secret_value()
        ):
            emb_kwargs["api_key"] = settings.openai_api_key.get_secret_value()
        memory_kwargs["embedding"] = from_provider(
            settings.embedding_model, kind="embedding", **emb_kwargs
        )

    if settings.llm_model:
        llm_kwargs: dict[str, Any] = {}
        if settings.llm_model.startswith("openai/") and settings.openai_api_key.get_secret_value():
            llm_kwargs["api_key"] = settings.openai_api_key.get_secret_value()
        elif settings.llm_model.startswith("anthropic/") and settings.anthropic_api_key:
            llm_kwargs["api_key"] = settings.anthropic_api_key.get_secret_value()
        memory_kwargs["llm"] = from_provider(settings.llm_model, kind="llm", **llm_kwargs)

    # BoltSettings rather than MemorySettings: this backend is bolt-only (it
    # calls client.get_graph(), which NAMS does not implement), and a stray
    # MEMORY_API_KEY in the environment would otherwise retarget the hosted
    # service. See the NAMS examples for the hosted variant.
    memory_settings = BoltSettings(
        neo4j=Neo4jConfig(
            uri=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
        ),
        extraction=build_extraction_config(settings.extraction_mode),
        **memory_kwargs,
    )

    _memory_client = MemoryClient(memory_settings)

    try:
        await _memory_client.connect()
        _memory_connected = True
        _memory_error = None
        logger.info(
            "Connected to Neo4j memory graph at %s (extraction mode: %s)",
            settings.neo4j_uri,
            settings.extraction_mode,
        )

        # Wrap the *connected* client: MemoryIntegration.connect() then returns
        # immediately without opening a second driver, and store_message()
        # forwards extract_entities=auto_extract to this client's extractor.
        _memory_integration = MemoryIntegration(
            client=_memory_client,
            session_strategy=SessionStrategy.PER_CONVERSATION,
            auto_extract=settings.extraction_mode != "none",
            auto_preferences=True,
        )
        await _memory_integration.connect()
        logger.info("MemoryIntegration ready (auto_preferences=True)")

    except Exception as e:
        _memory_error = f"{type(e).__name__}: {e}"
        logger.error(
            "Failed to connect to Neo4j memory graph at %s: %s",
            settings.neo4j_uri,
            _memory_error,
        )
        logger.error(
            "Memory features are DISABLED. Check NEO4J_URI / NEO4J_USERNAME / "
            "NEO4J_PASSWORD in backend/.env — they must match docker-compose.yml."
        )
        _memory_connected = False

    return _memory_client


def get_memory_client() -> MemoryClient | None:
    """Get the memory client singleton.

    Returns:
        The memory client if initialized and connected, None otherwise.
    """
    if not _memory_connected:
        return None
    return _memory_client


def get_memory_integration() -> MemoryIntegration | None:
    """Get the MemoryIntegration singleton for high-level operations.

    Returns:
        The MemoryIntegration if initialized and connected, None otherwise.
    """
    if not _memory_connected:
        return None
    return _memory_integration


def is_memory_connected() -> bool:
    """Check if memory client is connected."""
    return _memory_connected


def get_memory_error() -> str | None:
    """The connection error, if the memory graph is unreachable."""
    return _memory_error


async def close_memory_client() -> None:
    """Close the memory client connection."""
    global _memory_client, _memory_integration, _memory_connected, _memory_error

    # The integration does not own the client (it was constructed with
    # client=...), so closing it is a no-op for the driver; close the client.
    if _memory_integration is not None:
        await _memory_integration.close()
    if _memory_client is not None and _memory_connected:
        await _memory_client.close()
    _memory_client = None
    _memory_integration = None
    _memory_connected = False
    _memory_error = None
