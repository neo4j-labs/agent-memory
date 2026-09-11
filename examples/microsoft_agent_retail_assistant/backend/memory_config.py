"""Settings and memory wiring for the retail assistant.

Everything the backend needs to talk to Neo4j and OpenAI is resolved here:

* :class:`Settings` — pydantic-settings v2 config read from the environment
  and ``backend/.env`` (see ``.env.example``). ``NEO4J_PASSWORD`` is
  **required**: a missing password fails at import time instead of silently
  trying ``"password"``.
* :func:`get_memory_settings` — :class:`MemorySettings` for the one
  process-wide :class:`MemoryClient` created in ``main.py``'s lifespan.
* :func:`create_memory` — a per-session :class:`Neo4jMicrosoftMemory` wrapper
  around that **already-connected** client. It never opens a connection of its
  own, so N chat turns still use one Neo4j driver.
* :func:`create_embedder` / :func:`create_long_term_memory` — the explicit
  embedder used by the product vector search, and a
  :class:`LongTermMemory` built with this example's custom
  :class:`DeduplicationConfig` (``MemorySettings`` has no ``deduplication``
  field yet, so the config has to be passed to the constructor that accepts
  it — see the README "Known gaps" section).
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import ExtractionConfig
from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder
from neo4j_agent_memory.integrations.microsoft_agent import (
    GDSAlgorithm,
    GDSConfig,
    Neo4jContextProvider,
    Neo4jMicrosoftMemory,
)
from neo4j_agent_memory.memory.long_term import DeduplicationConfig, LongTermMemory

load_dotenv()

logger = logging.getLogger(__name__)

#: Default chat model. Override with ``OPENAI_MODEL``.
DEFAULT_CHAT_MODEL = "gpt-5-mini"
#: Default embedding model. 1536 dimensions — matches the ``product_embedding``
#: index created by ``data/load_products.py``. Override with
#: ``OPENAI_EMBEDDING_MODEL`` *and* re-run the loader if you change it.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
#: Dimensions of :data:`DEFAULT_EMBEDDING_MODEL`.
DEFAULT_EMBEDDING_DIMENSIONS = 1536


class Settings(BaseSettings):
    """Application settings.

    pydantic-settings reads the process environment and ``.env`` itself — do
    not add ``os.getenv`` defaults on top of it, or a value exported after
    import is silently ignored.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neo4j connection. No password default: misconfiguration must fail loudly.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr

    # OpenAI
    openai_api_key: SecretStr | None = None
    openai_model: str = DEFAULT_CHAT_MODEL
    openai_embedding_model: str = DEFAULT_EMBEDDING_MODEL

    # Azure OpenAI (alternative). At agent-framework 1.x GA, Azure routing goes
    # through the same OpenAIChatClient — see agent.py::get_chat_client().
    azure_openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str | None = None

    # Used for the PER_DAY session strategy when a request omits user_id.
    default_user_id: str = "guest"


settings = Settings()


def get_deduplication_config() -> DeduplicationConfig:
    """Deduplication thresholds tuned for retail product names.

    Retail catalogs are a noisy name space ("Nike Air Max" / "Nike Air Max 90"
    / "Air Max 90"), so this lowers ``flag_threshold`` from the library default
    of 0.85 to 0.80 — more pairs get flagged for human review at
    ``GET /memory/duplicates`` rather than being silently treated as distinct.
    ``auto_merge_threshold`` stays high so nothing merges without review.

    Wired in :func:`create_long_term_memory`; reviewed through the
    ``/memory/duplicates`` endpoints in ``main.py``.
    """
    return DeduplicationConfig(
        enabled=True,
        auto_merge_threshold=0.95,
        flag_threshold=0.80,  # library default is 0.85
        use_fuzzy_matching=True,  # needs the [fuzzy] extra (rapidfuzz)
        fuzzy_threshold=0.88,
        max_candidates=10,
        match_same_type_only=True,
    )


def get_extraction_config() -> ExtractionConfig:
    """Extraction pipeline for retail entity extraction.

    spaCy + GLiNER run locally (no per-message LLM cost), which is why the
    documented install uses ``neo4j-agent-memory[...,extraction]`` and the
    Quick Start downloads ``en_core_web_sm``. Without those two, extraction
    degrades to nothing — set ``enable_llm_fallback=True`` instead if you
    would rather pay per message than install the local models.
    """
    return ExtractionConfig(
        enable_spacy=True,
        enable_gliner=True,
        enable_llm_fallback=False,
    )


def get_memory_settings() -> MemorySettings:
    """Create :class:`MemorySettings` from the environment."""
    return MemorySettings(
        neo4j={
            "uri": settings.neo4j_uri,
            # Neo4jConfig forbids extra keys — the field is `username`, not `user`.
            "username": settings.neo4j_user,
            "password": settings.neo4j_password,
        },
        embedding={
            "provider": "openai",
            "model": settings.openai_embedding_model,
            "dimensions": DEFAULT_EMBEDDING_DIMENSIONS,
            "api_key": settings.openai_api_key,
        },
        extraction=get_extraction_config(),
    )


def create_embedder() -> OpenAIEmbedder | None:
    """Create the embedder used for product vector search.

    ``MemoryClient`` builds its own embedder for memory nodes but does not
    expose it (there is no ``client.embeddings`` accessor, and
    ``client._embedder`` is private and may be ``None``), so the product
    search path gets an explicit one.

    Returns ``None`` when no OpenAI key is configured — callers then skip the
    vector branch and fall back to text search.
    """
    if settings.openai_api_key is None:
        logger.warning(
            "OPENAI_API_KEY is not set — product search will use text matching "
            "instead of vector search."
        )
        return None
    return OpenAIEmbedder(
        model=settings.openai_embedding_model,
        api_key=settings.openai_api_key.get_secret_value(),
        dimensions=DEFAULT_EMBEDDING_DIMENSIONS,
    )


def create_long_term_memory(
    client: MemoryClient,
    embedder: OpenAIEmbedder | None = None,
) -> LongTermMemory:
    """Build a :class:`LongTermMemory` that actually uses this example's
    :func:`get_deduplication_config`.

    ``MemorySettings`` has no ``deduplication`` field and ``MemoryClient``
    never forwards one, so ``client.long_term`` always runs the library
    defaults. Constructing the layer directly is the only way to demonstrate
    custom thresholds today; it shares the same driver as ``client``.
    """
    return LongTermMemory(
        client.graph,
        embedder,
        deduplication=get_deduplication_config(),
    )


def get_gds_config() -> GDSConfig:
    """GDS configuration for retail recommendations.

    ``use_community_grouping`` is off: nothing in this example surfaces
    community ids, so leaving it on would advertise a feature the responses
    do not contain. The three algorithms below *are* exposed to the agent as
    tools by ``create_memory_tools(..., include_gds_tools=True)``, and fall
    back to plain Cypher when the GDS plugin is absent — ``main.py`` logs
    which mode is active at startup.
    """
    return GDSConfig(
        enabled=True,
        use_pagerank_for_ranking=True,
        pagerank_weight=0.3,
        use_community_grouping=False,
        expose_as_tools=[
            GDSAlgorithm.SHORTEST_PATH,
            GDSAlgorithm.NODE_SIMILARITY,
            GDSAlgorithm.PAGERANK,
        ],
        fallback_to_basic=True,
        warn_on_fallback=True,
    )


def create_memory(
    client: MemoryClient,
    session_id: str,
    user_id: str | None = None,
) -> Neo4jMicrosoftMemory:
    """Wrap the process-wide client in a per-session memory facade.

    ``Neo4jMicrosoftMemory`` is a thin per-session object: one connected
    :class:`MemoryClient` backs any number of sessions. Creating a client here
    (as this example used to) leaks a Neo4j driver per chat turn.

    Args:
        client: The connected client from the FastAPI lifespan.
        session_id: Session identifier.
        user_id: Optional user identifier.

    Returns:
        Configured :class:`Neo4jMicrosoftMemory` instance.
    """
    return Neo4jMicrosoftMemory(
        memory_client=client,
        session_id=session_id,
        user_id=user_id,
        include_short_term=True,
        include_long_term=True,
        include_reasoning=True,
        max_context_items=15,
        max_recent_messages=10,
        extract_entities=True,
        extract_entities_async=True,
        gds_config=get_gds_config(),
    )


def create_context_provider(
    memory_client: MemoryClient,
    session_id: str,
    user_id: str | None = None,
) -> Neo4jContextProvider:
    """Create a standalone context provider.

    ``create_memory()`` already builds one (``memory.context_provider``); this
    factory exists for agents that want the provider without the rest of the
    facade.

    Args:
        memory_client: Connected MemoryClient.
        session_id: Session identifier.
        user_id: Optional user identifier.

    Returns:
        Configured :class:`Neo4jContextProvider`.
    """
    return Neo4jContextProvider(
        memory_client=memory_client,
        session_id=session_id,
        user_id=user_id,
        include_short_term=True,
        include_long_term=True,
        include_reasoning=True,
        max_context_items=15,
        max_recent_messages=10,
        extract_entities=True,
        extract_entities_async=True,
        gds_config=get_gds_config(),
    )
