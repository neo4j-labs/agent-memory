"""Memory client factory and lifecycle management."""

import logging

from neo4j_agent_memory import (
    ExtractionConfig,
    MemoryClient,
    MemorySettings,
    Neo4jConfig,
)
from neo4j_agent_memory.config.settings import EnrichmentConfig, EnrichmentProvider
from neo4j_agent_memory.llm import from_provider
from neo4j_agent_memory.memory.long_term import DeduplicationConfig
from src.config import get_settings

logger = logging.getLogger(__name__)

_memory_client: MemoryClient | None = None
_memory_connected: bool = False


async def init_memory_client() -> MemoryClient | None:
    """Initialize the memory client singleton.

    Returns the client if connected successfully, None otherwise.
    The app can still run without memory features if Neo4j is unavailable.
    """
    global _memory_client, _memory_connected

    if _memory_client is not None:
        return _memory_client

    settings = get_settings()

    # Debug: verify API key is loaded
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    logger.info(
        f"OpenAI API key loaded: {bool(api_key)} (length: {len(api_key) if api_key else 0})"
    )

    # Configure enrichment providers
    providers = [EnrichmentProvider.WIKIMEDIA]  # Free, no API key needed
    diffbot_key = settings.diffbot_api_key.get_secret_value() if settings.diffbot_api_key else None
    if diffbot_key:
        providers.insert(0, EnrichmentProvider.DIFFBOT)  # Diffbot first if available
        logger.info("Diffbot enrichment enabled")

    enrichment_config = EnrichmentConfig(
        enabled=settings.enrichment_enabled,
        providers=providers,
        diffbot_api_key=settings.diffbot_api_key,
        background_enabled=True,  # Async processing
        cache_results=True,  # Cache to avoid repeated API calls
        entity_types=["PERSON", "ORGANIZATION", "LOCATION", "EVENT"],
        min_confidence=0.7,
    )

    if settings.enrichment_enabled:
        logger.info(f"Entity enrichment enabled with providers: {[p.value for p in providers]}")

    # Configure extraction with podcast-optimized GLiNER schema
    extraction_config = ExtractionConfig(
        gliner_schema="podcast",
    )

    # Resolve providers from the user-configurable strings. Defaults are
    # OpenAI for both; set LLM_MODEL=anthropic/claude-3-5-sonnet-latest
    # and EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 in the .env to run the
    # demo entirely on Anthropic + local embeddings (PRD acceptance #2).
    embed_kwargs: dict = {}
    if (
        settings.embedding_model.startswith("openai/")
        and settings.openai_api_key.get_secret_value()
    ):
        embed_kwargs["api_key"] = settings.openai_api_key.get_secret_value()
    embedding_provider = from_provider(
        settings.embedding_model,
        kind="embedding",
        **embed_kwargs,
    )

    llm_kwargs: dict = {}
    if settings.llm_model.startswith("openai/") and settings.openai_api_key.get_secret_value():
        llm_kwargs["api_key"] = settings.openai_api_key.get_secret_value()
    elif settings.llm_model.startswith("anthropic/") and settings.anthropic_api_key:
        llm_kwargs["api_key"] = settings.anthropic_api_key.get_secret_value()
    llm_provider = from_provider(settings.llm_model, kind="llm", **llm_kwargs)

    logger.info(
        "Memory providers: llm=%s embedding=%s",
        type(llm_provider).__name__,
        type(embedding_provider).__name__,
    )

    memory_settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
        ),
        embedding=embedding_provider,
        llm=llm_provider,
        enrichment=enrichment_config,
        extraction=extraction_config,
    )

    _memory_client = MemoryClient(memory_settings)

    try:
        await _memory_client.connect()
        _memory_connected = True

        # Configure entity deduplication on the long-term memory layer.
        # Auto-merge near-exact duplicates (>=0.95 similarity), flag potential
        # duplicates (>=0.85) for review, and use fuzzy string matching to
        # catch typos and name variations across podcast episodes.
        #
        # NOTE: this reaches a private attribute because ``MemorySettings`` has
        # no deduplication field yet (``LongTermMemory(deduplication=...)`` is
        # only reachable when you construct the layer yourself). Tracked as a
        # library follow-up; replace with the settings field once it lands.
        # Fuzzy matching needs the ``fuzzy`` extra (rapidfuzz) -- without it the
        # library silently falls back to embedding-only matching.
        _memory_client.long_term._deduplication = DeduplicationConfig(
            auto_merge_threshold=0.95,
            flag_threshold=0.85,
            use_fuzzy_matching=True,
        )
        logger.info(
            "Successfully connected to Neo4j memory graph "
            "(deduplication enabled, podcast extraction schema)"
        )
    except Exception as e:
        logger.warning(f"Failed to connect to Neo4j memory graph: {e}")
        logger.warning("Memory features will be disabled. Check your Neo4j configuration.")
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


def is_memory_connected() -> bool:
    """Check if memory client is connected."""
    return _memory_connected


async def close_memory_client() -> None:
    """Close the memory client connection."""
    global _memory_client, _memory_connected

    if _memory_client is not None and _memory_connected:
        await _memory_client.close()
    _memory_client = None
    _memory_connected = False
