"""Configuration settings for neo4j-agent-memory."""

from __future__ import annotations

import warnings
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource

# Strict config shared by every child config model. Misspelled fields raise
# at construction time instead of being silently dropped.
_STRICT_CONFIG = ConfigDict(extra="forbid")


class EmbeddingProvider(str, Enum):
    """Supported embedding providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    SENTENCE_TRANSFORMERS = "sentence_transformers"
    VERTEX_AI = "vertex_ai"
    BEDROCK = "bedrock"
    CUSTOM = "custom"


class LLMProvider(str, Enum):
    """Supported LLM providers for extraction."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


class ExtractorType(str, Enum):
    """Supported entity extractor types."""

    LLM = "llm"
    GLINER = "gliner"
    SPACY = "spacy"
    PIPELINE = "pipeline"  # Multi-stage pipeline
    NONE = "none"


class MergeStrategy(str, Enum):
    """Strategies for merging extraction results from multiple extractors."""

    UNION = "union"  # Keep all unique entities
    INTERSECTION = "intersection"  # Keep only entities found by multiple extractors
    CONFIDENCE = "confidence"  # Keep highest confidence per entity
    CASCADE = "cascade"  # Use first extractor's results, fill gaps with others


class SchemaModel(str, Enum):
    """Available schema models for entity types."""

    POLEO = "poleo"  # Person, Object, Location, Event, Organization
    LEGACY = "legacy"  # Original EntityType enum for backward compatibility
    CUSTOM = "custom"  # User-defined schema


class ResolverStrategy(str, Enum):
    """Supported entity resolution strategies."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"
    COMPOSITE = "composite"
    NONE = "none"


class Neo4jConfig(BaseModel):
    """Neo4j connection configuration."""

    model_config = _STRICT_CONFIG

    uri: str = Field(default="bolt://localhost:7687", description="Neo4j connection URI")
    username: str = Field(default="neo4j", description="Neo4j username")
    password: SecretStr = Field(description="Neo4j password")
    database: str = Field(default="neo4j", description="Neo4j database name")
    max_connection_pool_size: int = Field(
        default=50, ge=1, description="Maximum connection pool size"
    )
    connection_timeout: float = Field(
        default=30.0, gt=0, description="Connection timeout in seconds"
    )
    max_transaction_retry_time: float = Field(
        default=30.0, gt=0, description="Maximum transaction retry time in seconds"
    )
    max_connection_lifetime: int = Field(
        default=300,
        gt=0,
        description="Maximum lifetime of a pooled connection in seconds. "
        "Connections older than this are proactively closed and replaced. "
        "Should be shorter than the server's idle timeout (e.g., Neo4j Aura).",
    )
    liveness_check_timeout: int = Field(
        default=60,
        gt=0,
        description="Seconds a connection can be idle before the driver checks "
        "if it is still alive. Prevents use of stale connections.",
    )
    keep_alive: bool = Field(
        default=True,
        description="Enable TCP keep-alive on connections to prevent idle drops.",
    )


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    model_config = _STRICT_CONFIG

    provider: EmbeddingProvider = Field(
        default=EmbeddingProvider.OPENAI, description="Embedding provider to use"
    )
    model: str = Field(default="text-embedding-3-small", description="Embedding model name")
    dimensions: int = Field(default=1536, ge=1, description="Embedding dimensions")
    api_key: SecretStr | None = Field(default=None, description="API key for embedding provider")
    batch_size: int = Field(default=100, ge=1, description="Batch size for embeddings")
    # Sentence Transformers specific
    device: str = Field(default="cpu", description="Device for sentence transformers (cpu/cuda)")
    # Vertex AI specific
    project_id: str | None = Field(default=None, description="GCP project ID for Vertex AI")
    location: str = Field(default="us-central1", description="GCP region for Vertex AI")
    task_type: str = Field(
        default="RETRIEVAL_DOCUMENT",
        description="Vertex AI task type (RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, etc.)",
    )
    # AWS Bedrock specific
    aws_region: str | None = Field(default=None, description="AWS region for Bedrock")
    aws_profile: str | None = Field(default=None, description="AWS credentials profile name")


class LLMConfig(BaseModel):
    """LLM provider configuration for extraction.

    This config is optional on `MemorySettings`. Set ``MemorySettings.llm=None``
    to run without any LLM provider (e.g. spaCy/GLiNER-only extraction in
    air-gapped or no-API-key environments). See the "Running without an LLM"
    how-to and ``examples/no_llm/`` for a worked example.
    """

    model_config = _STRICT_CONFIG

    provider: LLMProvider = Field(default=LLMProvider.OPENAI, description="LLM provider to use")
    model: str = Field(default="gpt-4o-mini", description="LLM model name")
    api_key: SecretStr | None = Field(default=None, description="API key for LLM provider")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="LLM temperature")
    max_tokens: int = Field(default=4096, ge=1, description="Maximum tokens for LLM")


class SchemaConfig(BaseModel):
    """Knowledge graph schema configuration.

    Defines what entity types are valid and how the knowledge graph is structured.
    The default is the POLE+O model (Person, Object, Location, Event, Organization).
    """

    model_config = _STRICT_CONFIG

    model: SchemaModel = Field(default=SchemaModel.POLEO, description="Schema model to use")
    entity_types: list[str] | None = Field(
        default=None, description="Custom entity types (overrides model default when model=custom)"
    )
    enable_subtypes: bool = Field(default=True, description="Whether to track entity subtypes")
    strict_types: bool = Field(default=False, description="Whether to reject unknown entity types")
    custom_schema_path: str | None = Field(
        default=None, description="Path to custom schema definition file (.json or .yaml)"
    )


class ExtractionConfig(BaseModel):
    """Entity extraction configuration.

    Supports multiple extraction modes:
    - LLM: Use OpenAI/Anthropic for extraction (most accurate, highest cost)
    - GLINER: Use GLiNER zero-shot NER (good accuracy, runs locally)
    - SPACY: Use spaCy NER (fast, basic entity types)
    - PIPELINE: Multi-stage pipeline combining multiple extractors
    - NONE: Disable extraction
    """

    model_config = _STRICT_CONFIG

    extractor_type: ExtractorType = Field(
        default=ExtractorType.PIPELINE, description="Type of entity extractor"
    )

    # Pipeline settings (when extractor_type=PIPELINE)
    enable_spacy: bool = Field(default=True, description="Enable spaCy in extraction pipeline")
    enable_gliner: bool = Field(default=True, description="Enable GLiNER in extraction pipeline")
    enable_llm_fallback: bool = Field(
        default=True, description="Enable LLM as fallback in pipeline"
    )
    merge_strategy: MergeStrategy = Field(
        default=MergeStrategy.CONFIDENCE,
        description="Strategy for merging results from multiple extractors",
    )
    fallback_on_empty: bool = Field(
        default=True, description="Continue to next stage if current stage returns no results"
    )

    # spaCy settings
    spacy_model: str = Field(default="en_core_web_sm", description="spaCy model name")
    spacy_confidence: float = Field(
        default=0.85, ge=0.0, le=1.0, description="Default confidence score for spaCy extractions"
    )

    # GLiNER settings (GLiNER2 models recommended)
    gliner_model: str = Field(
        default="gliner-community/gliner_medium-v2.5",
        description="GLiNER model name (GLiNER2 v2.5 recommended for best accuracy)",
    )
    gliner_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="GLiNER confidence threshold"
    )
    gliner_device: str = Field(default="cpu", description="Device for GLiNER model (cpu/cuda/mps)")
    gliner_schema: str | None = Field(
        default=None,
        description="Domain schema for GLiNER extraction (poleo, podcast, news, scientific, business, entertainment, medical, legal)",
    )

    # LLM settings (for LLM extractor or fallback)
    llm_model: str = Field(default="gpt-4o-mini", description="LLM model for extraction")

    # General extraction settings
    entity_types: list[str] = Field(
        default=[
            "PERSON",
            "ORGANIZATION",
            "LOCATION",
            "EVENT",
            "OBJECT",
        ],
        description="Entity types to extract (POLE+O by default)",
    )
    extract_relations: bool = Field(default=True, description="Whether to extract relations")
    extract_preferences: bool = Field(default=True, description="Whether to extract preferences")
    confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for extracted entities",
    )


class ResolutionConfig(BaseModel):
    """Entity resolution configuration."""

    model_config = _STRICT_CONFIG

    strategy: ResolverStrategy = Field(
        default=ResolverStrategy.COMPOSITE, description="Resolution strategy"
    )
    exact_threshold: float = Field(default=1.0, ge=0.0, le=1.0, description="Exact match threshold")
    fuzzy_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0, description="Fuzzy match threshold"
    )
    semantic_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Semantic match threshold"
    )
    fuzzy_scorer: str = Field(default="token_sort_ratio", description="Fuzzy matching scorer")


class MemoryConfig(BaseModel):
    """Memory behavior configuration."""

    model_config = _STRICT_CONFIG

    # Short-term memory
    default_conversation_limit: int = Field(
        default=50, ge=1, description="Default conversation message limit"
    )
    message_embedding_enabled: bool = Field(default=True, description="Enable message embeddings")
    # Long-term memory
    preference_confidence_threshold: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Preference confidence threshold"
    )
    fact_deduplication_enabled: bool = Field(default=True, description="Enable fact deduplication")
    # Reasoning memory
    trace_embedding_enabled: bool = Field(
        default=True, description="Enable reasoning trace embeddings"
    )
    tool_stats_enabled: bool = Field(default=True, description="Enable tool usage statistics")
    # Multi-tenancy (v0.4)
    multi_tenant: bool = Field(
        default=False,
        description=(
            "When True, APIs that accept ``user_identifier=`` raise a "
            "ValueError if the kwarg is omitted. Use in production "
            "deployments to prevent accidental cross-tenant writes."
        ),
    )
    # Buffered writes (v0.4 P1.1)
    write_mode: Literal["sync", "buffered"] = Field(
        default="sync",
        description=(
            "How fire-and-forget writes via ``client.buffered.submit(...)`` "
            "are persisted. ``'sync'`` (default): each submit awaits the "
            "underlying execute_write — useful for tests and small loads. "
            "``'buffered'``: submits enqueue and a background task drains "
            "to Neo4j; callers use ``client.flush()`` / "
            "``client.wait_for_pending()`` at shutdown."
        ),
    )
    max_pending: int = Field(
        default=200,
        ge=1,
        description=(
            "Maximum number of buffered writes that can be in flight. "
            "When the queue is full, ``client.buffered.submit(...)`` "
            "blocks until a worker drains an item."
        ),
    )
    # Conversation hygiene (v0.5)
    conversation_ttl_days: int | None = Field(
        default=None,
        ge=1,
        description=(
            "When set, ``client.consolidation.archive_expired_conversations()``"
            " marks Conversation nodes older than this many days as archived."
        ),
    )
    # Privacy (v0.5)
    audit_read: bool = Field(
        default=False,
        description=(
            "When True, ``client.consolidation.audit_reads(...)`` records a "
            ":MemoryReadAudit node for the supplied query string. Off by "
            "default — auditing every read is opt-in."
        ),
    )


class SearchConfig(BaseModel):
    """Search configuration."""

    model_config = _STRICT_CONFIG

    default_limit: int = Field(default=10, ge=1, description="Default search limit")
    default_threshold: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Default similarity threshold"
    )
    hybrid_search_enabled: bool = Field(default=True, description="Enable hybrid search")
    graph_depth: int = Field(default=2, ge=1, description="Graph traversal depth for search")


class GeocodingProvider(str, Enum):
    """Supported geocoding providers."""

    NOMINATIM = "nominatim"
    GOOGLE = "google"


class EnrichmentProvider(str, Enum):
    """Supported enrichment providers."""

    WIKIMEDIA = "wikimedia"
    DIFFBOT = "diffbot"
    NONE = "none"


class GeocodingConfig(BaseModel):
    """Geocoding configuration for Location entities.

    Enables automatic geocoding of Location entities to add latitude/longitude
    coordinates as a Neo4j Point property, enabling geospatial queries.

    Providers:
    - NOMINATIM: Free OpenStreetMap-based geocoding (rate limited to 1 req/sec)
    - GOOGLE: Google Maps geocoding (requires API key, has usage costs)
    """

    model_config = _STRICT_CONFIG

    enabled: bool = Field(
        default=False, description="Enable automatic geocoding of Location entities"
    )
    provider: GeocodingProvider = Field(
        default=GeocodingProvider.NOMINATIM, description="Geocoding provider to use"
    )
    api_key: SecretStr | None = Field(
        default=None, description="API key for geocoding provider (required for Google)"
    )
    cache_results: bool = Field(
        default=True, description="Cache geocoding results to avoid repeated API calls"
    )
    rate_limit_per_second: float = Field(
        default=1.0,
        gt=0,
        description="Rate limit for geocoding requests (Nominatim requires <= 1 req/sec)",
    )
    user_agent: str = Field(
        default="neo4j-agent-memory",
        description="User agent string for Nominatim requests (required by their ToS)",
    )


class EnrichmentConfig(BaseModel):
    """Entity enrichment configuration.

    Configures background enrichment of entities with external data
    from Wikipedia, Diffbot, and other knowledge sources.

    Enrichment runs asynchronously and does not block entity extraction/storage.

    Providers:
    - WIKIMEDIA: Free Wikipedia/Wikidata enrichment (rate limited to ~2 req/sec)
    - DIFFBOT: Diffbot Knowledge Graph (requires API key, structured data)
    """

    model_config = _STRICT_CONFIG

    enabled: bool = Field(default=False, description="Enable automatic entity enrichment")

    # Provider selection
    providers: list[EnrichmentProvider] = Field(
        default=[EnrichmentProvider.WIKIMEDIA],
        description="Enrichment providers to use (in priority order)",
    )

    # API keys
    diffbot_api_key: SecretStr | None = Field(
        default=None, description="API key for Diffbot Knowledge Graph"
    )

    # Rate limiting
    wikimedia_rate_limit: float = Field(
        default=0.5, gt=0, description="Seconds between Wikimedia API requests"
    )
    diffbot_rate_limit: float = Field(
        default=0.2, gt=0, description="Seconds between Diffbot API requests"
    )

    # Caching
    cache_results: bool = Field(
        default=True, description="Cache enrichment results to avoid repeated API calls"
    )
    cache_ttl_hours: int = Field(
        default=24 * 7,  # 1 week
        ge=1,
        description="Hours to cache enrichment results",
    )

    # Background processing
    background_enabled: bool = Field(
        default=True, description="Run enrichment in background (non-blocking)"
    )
    queue_max_size: int = Field(default=1000, ge=1, description="Maximum enrichment queue size")
    max_retries: int = Field(
        default=3, ge=0, description="Maximum retry attempts for failed enrichments"
    )
    retry_delay_seconds: float = Field(
        default=60.0, ge=1, description="Delay between retry attempts"
    )

    # Entity type filtering
    entity_types: list[str] = Field(
        default=["PERSON", "ORGANIZATION", "LOCATION", "EVENT"],
        description="Entity types to enrich (empty = all types)",
    )
    min_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum entity confidence to trigger enrichment",
    )

    # Content options
    language: str = Field(default="en", description="Preferred language for enrichment data")
    user_agent: str = Field(
        default="neo4j-agent-memory/1.0", description="User-Agent for API requests"
    )


class NamsConfig(BaseModel):
    """Configuration for the hosted NAMS (Neo4j Agent Memory Service) backend.

    Activated when ``MemorySettings.backend = "nams"``. The hosted REST API
    lives at ``https://memory.neo4jlabs.com/v1``; on-prem or sandbox
    deployments can point ``endpoint`` elsewhere.

    Two wire protocols are supported and auto-selected from
    ``endpoint`` shape:

    * **REST** — endpoint contains ``/v\\d+`` (e.g. ``/v1``). Each method
      maps to a versioned REST path (e.g. ``POST /v1/conversations/{id}/messages``).
    * **TCK bridge** — anything else. Each method maps to a snake-case
      POST (e.g. ``POST /add_message``). Used by the conformance test
      reference implementation.

    Override the detection with ``transport_mode={"rest", "bridge"}``.
    """

    model_config = _STRICT_CONFIG

    endpoint: str = Field(
        default="https://memory.neo4jlabs.com/v1",
        description="NAMS endpoint base URL. REST shape ends in /v<N> (e.g. /v1); "
        "anything else is treated as TCK bridge unless overridden by transport_mode.",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="NAMS API key (format 'nams_...'). When constructed via "
        "MemorySettings, an unset value may be populated from MEMORY_API_KEY.",
    )
    workspace_id: str | None = Field(
        default=None,
        description="NAMS workspace id. When set, transmitted as the 'X-Workspace-Id' "
        "header on every request — required by deployments that scope by header "
        "(e.g. the development/staging service) rather than encoding the workspace "
        "in the API key (production). Optional and harmless when unset. When "
        "constructed via MemorySettings, an unset value may be populated from "
        "MEMORY_WORKSPACE_ID.",
    )
    timeout: float = Field(default=30.0, gt=0, description="HTTP request timeout in seconds.")
    max_retries: int = Field(
        default=3,
        ge=0,
        description="Max retry attempts for 429 (after Retry-After), 5xx, and network errors.",
    )
    retry_backoff_seconds: float = Field(
        default=0.5,
        gt=0,
        description="Base exponential backoff between retries (0.5 → 0.5, 1.0, 2.0...).",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers added to every request (e.g. tracing headers).",
    )
    validate_on_connect: bool = Field(
        default=True,
        description="If True, MemoryClient.connect() makes a fail-fast authenticated probe "
        "request to verify credentials and reachability. Disable for serverless/cold-start.",
    )
    transport_mode: Literal["auto", "rest", "bridge"] = Field(
        default="auto",
        description="Wire-protocol override. 'auto' (default) infers from endpoint shape.",
    )


class _FilteredDotEnvSource(DotEnvSettingsSource):
    # Wraps an existing DotEnvSettingsSource instance, preserving all runtime
    # overrides (e.g. _env_file=None or _env_file=some_path passed at
    # MemorySettings instantiation time) while dropping .env keys that are not
    # top-level model fields.  The original source is already configured with
    # the correct env_file / env_file_encoding / env_prefix when it arrives in
    # settings_customise_sources — copying its __dict__ is cheaper and safer
    # than re-constructing from settings_cls alone.
    def __init__(self, original: PydanticBaseSettingsSource) -> None:
        self.__dict__.update(original.__dict__)

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        valid = set(self.settings_cls.model_fields.keys())
        return {k: v for k, v in data.items() if k in valid}


class MemorySettings(BaseSettings):
    """
    Main configuration class for neo4j-agent-memory.

    Configuration can be loaded from:
    - Environment variables (prefixed with NAM_)
    - .env files
    - Direct instantiation

    Example:
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("password"))
        )
    """

    model_config = SettingsConfigDict(
        env_prefix="NAM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        # ``extra="forbid"`` rejects misspelled top-level fields (e.g.
        # ``schema=`` instead of ``schema_config=``) at construction time
        # rather than silently dropping them. Unrelated keys in a user's
        # ``.env`` (e.g. plain ``NEO4J_URI=…`` or ``OPENAI_API_KEY=…`` for
        # other tools) are filtered out by ``settings_customise_sources``
        # below before they reach validation.
        extra="forbid",
    )

    neo4j: Neo4jConfig = Field(default_factory=lambda: Neo4jConfig(password=SecretStr("")))
    # ``embedding`` and ``llm`` accept three shapes as of v0.3.0:
    #
    # * legacy :class:`EmbeddingConfig` / :class:`LLMConfig` — emits a
    #   :class:`DeprecationWarning`, planned removal in v0.5.0.
    # * provider string shorthand — e.g. ``"openai/text-embedding-3-small"``
    #   or ``"anthropic/claude-3-5-sonnet-latest"``; resolved via
    #   :func:`neo4j_agent_memory.llm.from_provider`.
    # * a fully-constructed :class:`EmbeddingProvider` / :class:`LLMProvider`
    #   instance (the new canonical shape).
    #
    # The declared type is :class:`Any` because Pydantic cannot natively
    # schema the runtime-checkable Protocols. Validation happens in the
    # ``_resolve_providers`` model validator below, which also catches
    # malformed inputs with a clear error message.
    embedding: Any = Field(default_factory=EmbeddingConfig)
    llm: Any = Field(default=None)
    schema_config: SchemaConfig = Field(default_factory=SchemaConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    geocoding: GeocodingConfig = Field(default_factory=GeocodingConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)

    # v0.4: storage-backend selection. ``None`` defers to ``_resolve_backend``
    # which inspects the environment (``MEMORY_API_KEY`` → NAMS, otherwise
    # bolt). Existing v0.3.x users hit the bolt default unchanged.
    backend: Literal["bolt", "nams"] | None = Field(
        default=None,
        description=(
            "Storage backend. 'bolt' uses direct Neo4j via the Python driver. "
            "'nams' uses the hosted Neo4j Agent Memory Service REST API. "
            "When unset (None), backend is resolved at construction time: "
            "NAMS if MEMORY_API_KEY is in env, otherwise bolt."
        ),
    )
    nams: NamsConfig = Field(default_factory=NamsConfig)

    @model_validator(mode="after")
    def _resolve_providers(self) -> MemorySettings:
        """Resolve string shorthand and emit deprecation warnings.

        Runs before :meth:`_validate_llm_consistency`. After this method:

        * ``self.embedding`` is :class:`EmbeddingConfig` or an
          :class:`EmbeddingProvider` instance (never a string).
        * ``self.llm`` is :class:`LLMConfig`, an :class:`LLMProvider`
          instance, or ``None`` (never a string).

        Legacy :class:`EmbeddingConfig` / :class:`LLMConfig` continue to
        work; they emit exactly one :class:`DeprecationWarning` per
        construction when the user explicitly passed them (the implicit
        default factories do not warn).
        """
        # Coerce dict input to the legacy config types — preserves the
        # v0.2 pattern ``embedding={"provider": "openai", ...}``. The
        # field is typed Any here so Pydantic does not auto-coerce; we
        # do it explicitly so the deprecation logic below still fires.
        if isinstance(self.embedding, dict):
            self.embedding = EmbeddingConfig(**self.embedding)
        if isinstance(self.llm, dict):
            self.llm = LLMConfig(**self.llm)

        # Resolve string shorthand for embedding
        if isinstance(self.embedding, str):
            from neo4j_agent_memory.llm import from_provider

            self.embedding = from_provider(self.embedding, kind="embedding")
        elif self.embedding is None:
            raise ValueError(
                "MemorySettings.embedding cannot be None — pass a config, "
                "provider string, or EmbeddingProvider instance."
            )
        elif isinstance(self.embedding, EmbeddingConfig):
            if "embedding" in self.model_fields_set:
                warnings.warn(
                    "Passing EmbeddingConfig to MemorySettings.embedding is "
                    "deprecated and will be removed in v0.5.0. Use a provider "
                    "string like 'openai/text-embedding-3-small' or pass an "
                    "EmbeddingProvider instance directly. See "
                    "https://neo4j.com/labs/agent-memory/migration/v0.3",
                    DeprecationWarning,
                    stacklevel=3,
                )
        else:
            # Must implement the EmbeddingProvider Protocol.
            from neo4j_agent_memory.llm.protocol import EmbeddingProvider as _EP

            if not isinstance(self.embedding, _EP):
                raise TypeError(
                    f"MemorySettings.embedding must be EmbeddingConfig, a "
                    f"provider string, or an EmbeddingProvider instance; got "
                    f"{type(self.embedding).__name__}."
                )

        # Resolve string shorthand for llm
        if isinstance(self.llm, str):
            from neo4j_agent_memory.llm import from_provider

            self.llm = from_provider(self.llm, kind="llm")
        elif self.llm is None:
            # Legitimate "no LLM" — handled by _validate_llm_consistency below.
            pass
        elif isinstance(self.llm, LLMConfig):
            if "llm" in self.model_fields_set:
                warnings.warn(
                    "Passing LLMConfig to MemorySettings.llm is deprecated "
                    "and will be removed in v0.5.0. Use a provider string "
                    "like 'anthropic/claude-3-5-sonnet-latest' or pass an "
                    "LLMProvider instance directly. See "
                    "https://neo4j.com/labs/agent-memory/migration/v0.3",
                    DeprecationWarning,
                    stacklevel=3,
                )
        else:
            from neo4j_agent_memory.llm.protocol import LLMProvider as _LP

            if not isinstance(self.llm, _LP):
                raise TypeError(
                    f"MemorySettings.llm must be LLMConfig, a provider string, "
                    f"an LLMProvider instance, or None; got "
                    f"{type(self.llm).__name__}."
                )

        return self

    @model_validator(mode="after")
    def _validate_llm_consistency(self) -> MemorySettings:
        ext = self.extraction
        needs_llm = ext.extractor_type == ExtractorType.LLM or (
            ext.extractor_type == ExtractorType.PIPELINE and ext.enable_llm_fallback
        )
        if needs_llm and self.llm is None:
            # Distinguish explicit `llm=None` (strict) from "user didn't mention llm".
            if "llm" in self.model_fields_set:
                raise ValueError(
                    "llm=None is incompatible with extraction settings: "
                    f"extractor_type={ext.extractor_type.value}, "
                    f"enable_llm_fallback={ext.enable_llm_fallback}. "
                    "Either provide an LLMConfig/LLMProvider, or set "
                    "extractor_type to ExtractorType.SPACY/GLINER/PIPELINE/"
                    "NONE and enable_llm_fallback=False."
                )
            # Lenient fallback preserves pre-0.2 default behavior when the user
            # didn't explicitly opt out. No deprecation warning here: the
            # user did not explicitly choose LLMConfig.
            self.llm = LLMConfig()
        return self

    @model_validator(mode="after")
    def _resolve_backend(self) -> MemorySettings:
        """Resolve the storage backend (bolt vs NAMS).

        Decision flow:

        1. ``MEMORY_API_KEY`` and ``MEMORY_ENDPOINT`` env aliases — if set
           and the corresponding ``NamsConfig`` field is unspecified, lift
           the env value into ``self.nams``. These user-friendly aliases
           complement the ``NAM_NAMS__*`` family that pydantic-settings
           already wires automatically.
        2. If the user explicitly set ``backend``, honor it.
        3. Otherwise: NAMS if ``self.nams.api_key`` is now populated
           (either from explicit config or the env alias above),
           otherwise bolt.

        Runs after :meth:`_resolve_providers` and
        :meth:`_validate_llm_consistency` so the provider config is
        already coerced into its canonical shape.
        """
        import os

        # Step 1: user-friendly env aliases (only when user hasn't overridden).
        env_api_key = os.environ.get("MEMORY_API_KEY")
        if env_api_key and self.nams.api_key is None:
            self.nams.api_key = SecretStr(env_api_key)

        env_endpoint = os.environ.get("MEMORY_ENDPOINT")
        if env_endpoint and "endpoint" not in self.nams.model_fields_set:
            self.nams.endpoint = env_endpoint

        env_workspace = os.environ.get("MEMORY_WORKSPACE_ID")
        if env_workspace and self.nams.workspace_id is None:
            self.nams.workspace_id = env_workspace

        # Step 2 & 3: resolve backend if user didn't pin it.
        if self.backend is None:
            self.backend = "nams" if self.nams.api_key is not None else "bolt"

        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003 — required by pydantic-settings hook
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _FilteredDotEnvSource(dotenv_settings),
            file_secret_settings,
        )

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> MemorySettings:
        """Create settings from a dictionary."""
        return cls(**config)


class BoltSettings(MemorySettings):
    """``MemorySettings`` pinned to the self-hosted (bolt) backend.

    Narrows ``backend`` to the ``"bolt"`` literal so backend-typed
    construction helpers (e.g. ``neo4j_agent_memory.connect()``) can return
    a precisely backend-typed ``MemoryClient`` from the argument's static
    type alone, with no runtime ``isinstance`` check.
    """

    backend: Literal["bolt"] = "bolt"


class NamsSettings(MemorySettings):
    """``MemorySettings`` pinned to the hosted NAMS backend.

    Narrows ``backend`` to the ``"nams"`` literal — see
    :class:`BoltSettings` for why this exists.
    """

    backend: Literal["nams"] = "nams"
