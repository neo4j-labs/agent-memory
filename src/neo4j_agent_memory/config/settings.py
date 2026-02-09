"""Configuration settings for neo4j-agent-memory."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingProvider(str, Enum):
    """Supported embedding providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    SENTENCE_TRANSFORMERS = "sentence_transformers"
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


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    provider: EmbeddingProvider = Field(
        default=EmbeddingProvider.OPENAI, description="Embedding provider to use"
    )
    model: str = Field(default="text-embedding-3-small", description="Embedding model name")
    dimensions: int = Field(default=1536, ge=1, description="Embedding dimensions")
    api_key: SecretStr | None = Field(default=None, description="API key for embedding provider")
    batch_size: int = Field(default=100, ge=1, description="Batch size for embeddings")
    # Sentence Transformers specific
    device: str = Field(default="cpu", description="Device for sentence transformers (cpu/cuda)")


class LLMConfig(BaseModel):
    """LLM provider configuration for extraction."""

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


class SearchConfig(BaseModel):
    """Search configuration."""

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


class ServerConfig(BaseModel):
    """Configuration for the HTTP API server.

    Only used when running ``neo4j-memory serve`` or the FastAPI app directly.
    """

    host: str = Field(default="0.0.0.0", description="Host to bind to")
    port: int = Field(default=8000, description="Port to bind to")
    cors_origins: list[str] = Field(default=["*"], description="Allowed CORS origins")
    cors_origin_regex: str | None = Field(
        default=None, description="Regex pattern for allowed CORS origins"
    )
    auth_enabled: bool = Field(default=False, description="Enable API key authentication")
    api_key: SecretStr | None = Field(default=None, description="API key for authentication")
    api_prefix: str = Field(default="/api/v1", description="API route prefix")
    debug: bool = Field(default=False, description="Enable debug mode")


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
        extra="ignore",
    )

    neo4j: Neo4jConfig = Field(default_factory=lambda: Neo4jConfig(password=SecretStr("")))
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    schema_config: SchemaConfig = Field(default_factory=SchemaConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    geocoding: GeocodingConfig = Field(default_factory=GeocodingConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)

    # Server configuration (only used when running the HTTP API server)
    server: ServerConfig | None = Field(default=None, description="HTTP API server configuration")

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "MemorySettings":
        """Create settings from a dictionary."""
        return cls(**config)
