"""
Neo4j Agent Memory - A comprehensive memory system for AI agents.

This package provides a unified memory system for AI agents using Neo4j as the
persistence layer. It includes three types of memory:

- **Short-Term Memory**: Conversation history and experiences
- **Long-Term Memory**: Facts, preferences, and entities
- **Reasoning Memory**: Reasoning traces and tool usage patterns

Example usage:
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from pydantic import SecretStr

    settings = MemorySettings(
        neo4j={"uri": "bolt://localhost:7687", "password": SecretStr("password")}
    )

    async with MemoryClient(settings) as client:
        # Add a message
        await client.short_term.add_message(
            session_id="user-123",
            role="user",
            content="Hi, I'm looking for Italian restaurants"
        )

        # Add a preference
        await client.long_term.add_preference(
            category="food",
            preference="I love Italian cuisine"
        )

        # Search memories
        results = await client.long_term.search_preferences("food preferences")

        # Get combined context for LLM
        context = await client.get_context("restaurant recommendation")
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from neo4j_agent_memory.core.protocols import (
        CypherQueryProtocol,
        LongTermProtocol,
        ReasoningProtocol,
        ShortTermProtocol,
    )
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.enrichment.background import BackgroundEnrichmentService
    from neo4j_agent_memory.enrichment.base import EnrichmentProvider as _EnrichmentProviderProtocol
    from neo4j_agent_memory.extraction.base import EntityExtractor
    from neo4j_agent_memory.memory.buffered import BufferedWriteError, BufferedWriter
    from neo4j_agent_memory.memory.consolidation import ConsolidationMemory
    from neo4j_agent_memory.memory.eval import EvalMemory
    from neo4j_agent_memory.memory.users import UserMemory
    from neo4j_agent_memory.nams.client import NamsBackend
    from neo4j_agent_memory.resolution.base import EntityResolver
    from neo4j_agent_memory.services.geocoder import Geocoder

from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    EnrichmentConfig,
    EnrichmentProvider,
    ExtractionConfig,
    ExtractorType,
    GeocodingConfig,
    GeocodingProvider,
    LLMConfig,
    LLMProvider,
    MemoryConfig,
    MemorySettings,
    NamsConfig,
    Neo4jConfig,
    ResolutionConfig,
    ResolverStrategy,
    SearchConfig,
)
from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ConnectionError,
    EmbeddingError,
    ExtractionError,
    MemoryError,
    NotConnectedError,
    NotSupportedError,
    RateLimitError,
    ResolutionError,
    SchemaError,
    TransportError,
    ValidationError,
)
from neo4j_agent_memory.core.memory import BaseMemory, MemoryEntry
from neo4j_agent_memory.core.protocols import (
    CypherQueryProtocol,
    LongTermProtocol,
    ReasoningProtocol,
    ShortTermProtocol,
)


class GraphNode(BaseModel):
    """A node in the memory graph for visualization."""

    id: str = Field(description="Node identifier")
    labels: list[str] = Field(description="Node labels (e.g., ['Message'], ['Entity'])")
    properties: dict[str, Any] = Field(default_factory=dict, description="Node properties")


class GraphRelationship(BaseModel):
    """A relationship in the memory graph for visualization."""

    id: str = Field(description="Relationship identifier")
    type: str = Field(description="Relationship type (e.g., 'HAS_MESSAGE', 'MENTIONS')")
    from_node: str = Field(description="Source node ID")
    to_node: str = Field(description="Target node ID")
    properties: dict[str, Any] = Field(default_factory=dict, description="Relationship properties")


class MemoryGraph(BaseModel):
    """Memory graph export for visualization."""

    nodes: list[GraphNode] = Field(default_factory=list, description="Graph nodes")
    relationships: list[GraphRelationship] = Field(
        default_factory=list, description="Graph relationships"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Export metadata (filters applied, counts, etc.)",
    )


from neo4j_agent_memory.graph.client import Neo4jClient
from neo4j_agent_memory.graph.schema import SchemaManager
from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy

# Google Cloud integrations (v0.0.3+)
# These are imported conditionally to avoid requiring google dependencies.
# Stub classes provide actionable error messages when optional deps are missing.
try:
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder
except ImportError:

    class VertexAIEmbedder:  # type: ignore[no-redef]
        """Stub for VertexAIEmbedder when google-cloud-aiplatform is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "VertexAIEmbedder requires google-cloud-aiplatform. "
                "Install with: pip install neo4j-agent-memory[vertex-ai]"
            )


try:
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
except ImportError:

    class Neo4jMemoryService:  # type: ignore[no-redef]
        """Stub for Neo4jMemoryService when google-adk is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "Neo4jMemoryService requires google-adk. "
                "Install with: pip install neo4j-agent-memory[google-adk]"
            )


try:
    from neo4j_agent_memory.mcp.server import Neo4jMemoryMCPServer
except ImportError:

    class Neo4jMemoryMCPServer:  # type: ignore[no-redef]
        """Stub for Neo4jMemoryMCPServer when mcp is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "Neo4jMemoryMCPServer requires the mcp package. "
                "Install with: pip install neo4j-agent-memory[mcp]"
            )


from neo4j_agent_memory.memory.long_term import (
    Entity,
    EntityType,
    Fact,
    LongTermMemory,
    Preference,
    Relationship,
)
from neo4j_agent_memory.memory.reasoning import (
    ProceduralMemory,  # backward compatibility alias
    ReasoningMemory,
    ReasoningStep,
    ReasoningTrace,
    StreamingTraceRecorder,
    Tool,
    ToolCall,
    ToolCallStatus,
    ToolStats,
)
from neo4j_agent_memory.memory.short_term import (
    Conversation,
    ConversationSummary,
    Message,
    MessageRole,
    SessionInfo,
    ShortTermMemory,
)

__version__ = "0.5.0"

__all__ = [
    # Main client
    "MemoryClient",
    # Integration layer
    "MemoryIntegration",
    "SessionStrategy",
    # Settings
    "MemorySettings",
    "Neo4jConfig",
    "NamsConfig",
    "EmbeddingConfig",
    "LLMConfig",
    "ExtractionConfig",
    "ResolutionConfig",
    "MemoryConfig",
    "SearchConfig",
    "GeocodingConfig",
    "EnrichmentConfig",
    # Protocols (backend-agnostic contracts)
    "ShortTermProtocol",
    "LongTermProtocol",
    "ReasoningProtocol",
    "CypherQueryProtocol",
    # Enums
    "EmbeddingProvider",
    "LLMProvider",
    "ExtractorType",
    "ResolverStrategy",
    "GeocodingProvider",
    "EnrichmentProvider",
    "MessageRole",
    "EntityType",
    "ToolCallStatus",
    # Memory types
    "ShortTermMemory",
    "LongTermMemory",
    "ReasoningMemory",
    "ProceduralMemory",  # backward compatibility alias
    # Models - Short-term
    "Message",
    "Conversation",
    "ConversationSummary",
    "SessionInfo",
    # Models - Long-term
    "Entity",
    "Preference",
    "Fact",
    "Relationship",
    # Models - Reasoning
    "ReasoningTrace",
    "ReasoningStep",
    "ToolCall",
    "ToolStats",
    "Tool",
    "StreamingTraceRecorder",
    # Base classes
    "BaseMemory",
    "MemoryEntry",
    # Graph
    "Neo4jClient",
    "SchemaManager",
    # Graph Export
    "GraphNode",
    "GraphRelationship",
    "MemoryGraph",
    # Google Cloud integrations (v0.0.3+)
    "VertexAIEmbedder",
    "Neo4jMemoryService",
    "Neo4jMemoryMCPServer",
    # Exceptions
    "MemoryError",
    "ConnectionError",
    "SchemaError",
    "ExtractionError",
    "ResolutionError",
    "EmbeddingError",
    "ConfigurationError",
    "NotConnectedError",
    # NAMS exceptions (v0.4)
    "TransportError",
    "AuthenticationError",
    "NotSupportedError",
    "RateLimitError",
    "ValidationError",
]


class _DeprecatedGraphProxy:
    """Wrapper returned by ``MemoryClient.graph`` on the bolt backend.

    Forwards every attribute to the underlying :class:`Neo4jClient`, but
    intercepts ``execute_read`` to emit a one-time ``DeprecationWarning``
    pointing at the portable replacement ``client.query.cypher``. The
    warning fires once per process to avoid log spam.

    Scheduled for removal in v0.6.0 along with the underlying
    ``client.graph`` accessor itself.
    """

    _execute_read_warned: bool = False

    def __init__(self, client: Neo4jClient) -> None:
        # Use object.__setattr__ to avoid triggering __getattr__ on init.
        object.__setattr__(self, "_client", client)

    async def execute_read(self, *args: Any, **kwargs: Any) -> Any:
        if not type(self)._execute_read_warned:
            import warnings as _w

            _w.warn(
                "MemoryClient.graph.execute_read is deprecated and will be "
                "removed in v0.6.0. Use client.query.cypher(query, params) "
                "for portable read-only queries (works on both bolt and "
                "NAMS backends).",
                DeprecationWarning,
                stacklevel=2,
            )
            type(self)._execute_read_warned = True
        return await self._client.execute_read(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Delegate everything else (execute_write, vector_search, driver,
        # etc.) transparently. Not triggered for ``execute_read`` because
        # the explicit method above takes precedence.
        return getattr(self._client, name)

    def __repr__(self) -> str:
        return f"_DeprecatedGraphProxy({self._client!r})"


class MemoryClient:
    """
    Main client for interacting with the Neo4j Agent Memory system.

    Provides unified access to all three memory types:
    - short_term: Conversation history and experiences
    - long_term: Facts, preferences, and entities
    - reasoning: Reasoning traces and tool usage

    Example:
        async with MemoryClient(settings) as client:
            await client.short_term.add_message(...)
            await client.long_term.add_preference(...)
            context = await client.get_context(query)
    """

    def __init__(
        self,
        settings: MemorySettings | None = None,
        *,
        embedder: Embedder | None = None,
        extractor: EntityExtractor | None = None,
        resolver: EntityResolver | None = None,
        geocoder: Geocoder | None = None,
        enrichment_provider: _EnrichmentProviderProtocol | None = None,
    ) -> None:
        """
        Initialize the memory client.

        Args:
            settings: Memory settings (uses defaults if not provided)
            embedder: Optional embedder override (for testing)
            extractor: Optional extractor override (for testing)
            resolver: Optional resolver override (for testing)
            geocoder: Optional geocoder override (for testing)
            enrichment_provider: Optional enrichment provider override (for testing)
        """
        self._settings = settings or MemorySettings()
        # Bolt-only state. ``_client`` and ``_schema_manager`` stay None
        # when ``settings.backend == "nams"``.
        self._client: Neo4jClient | None = None
        self._schema_manager: SchemaManager | None = None
        self._embedder_override: Embedder | None = embedder
        self._extractor_override: EntityExtractor | None = extractor
        self._resolver_override: EntityResolver | None = resolver
        self._geocoder_override: Geocoder | None = geocoder
        self._enrichment_provider_override: _EnrichmentProviderProtocol | None = enrichment_provider
        self._embedder: Embedder | None = None
        self._extractor: EntityExtractor | None = None
        self._resolver: EntityResolver | None = None
        self._geocoder: Geocoder | None = None
        self._enrichment_provider: _EnrichmentProviderProtocol | None = None
        self._enrichment_service: BackgroundEnrichmentService | None = None

        # NAMS-only state. ``_nams_backend`` stays None on bolt.
        self._nams_backend: NamsBackend | None = None

        # Memory accessors (Protocol-typed so either backend's impl satisfies
        # the contract). Initialized in connect().
        self._short_term: ShortTermProtocol | None = None
        self._long_term: LongTermProtocol | None = None
        self._reasoning: ReasoningProtocol | None = None
        self._query: CypherQueryProtocol | None = None

        # Ontology accessor. Real (``NamsOntology``) on NAMS; a
        # ``_NamsUnsupported`` sentinel on bolt (ontologies are a NAMS
        # capability). Wired in connect().
        self._ontology: Any = None

        # API-key management accessor. Real (``NamsAuth``) on NAMS; a
        # ``_NamsUnsupported`` sentinel on bolt. Wired in connect().
        self._auth: Any = None

        # Bolt-only accessors. On NAMS these are replaced by ``_NamsUnsupported``
        # sentinels — but the declared type stays as the bolt class so user
        # code that type-checks against e.g. ``UserMemory`` continues to work
        # in the common (bolt) case.
        self._users: UserMemory | Any = None
        self._buffered: BufferedWriter | Any = None
        self._consolidation: ConsolidationMemory | Any = None
        self._eval: EvalMemory | None = None

    async def __aenter__(self) -> MemoryClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        await self.close()

    async def connect(self) -> None:
        """
        Connect to the configured backend and initialize memory stores.

        Dispatches on ``settings.backend``:

        * ``"bolt"`` (default) — opens the Neo4j driver, runs schema
          setup, wires the bolt memory implementations.
        * ``"nams"`` — opens the NAMS HTTP transport, runs a fail-fast
          auth probe (when ``nams.validate_on_connect=True``), wires
          the NAMS memory implementations. Client-side layers
          (extraction/embedding/etc.) are warn-and-ignored.
        """
        if self._settings.backend == "nams":
            await self._connect_nams()
        else:
            await self._connect_bolt()

    async def _connect_bolt(self) -> None:
        """Connect to Neo4j via the bolt driver (the historic path)."""
        # Create Neo4j client
        self._client = Neo4jClient(self._settings.neo4j)
        await self._client.connect()

        # Initialize embedder before schema setup so the vector index is
        # sized for the embedder's dimensions. Falls back to the legacy
        # EmbeddingConfig.dimensions when no Provider is configured.
        if self._embedder_override is not None:
            # Test/code overrides may pass either the legacy Embedder
            # shape or a new EmbeddingProvider. Adapt to the legacy shape
            # so downstream memory layers keep working unchanged.
            from neo4j_agent_memory.embeddings.base import adapt_to_legacy_embedder

            # adapt_to_legacy_embedder returns `object` because its input is
            # typed as `object`; it always produces a value satisfying `Embedder`
            # (either the original Embedder or a _ProviderToEmbedderAdapter).
            self._embedder = cast("Embedder", adapt_to_legacy_embedder(self._embedder_override))
        else:
            self._embedder = self._create_embedder()

        vector_dimensions = self._resolve_vector_dimensions()

        # Set up schema
        self._schema_manager = SchemaManager(
            self._client,
            vector_dimensions=vector_dimensions,
        )
        await self._schema_manager.setup_all()

        # Validate existing vector indexes match the configured embedder.
        # Raises EmbeddingDimensionMismatchError on mismatch with actionable
        # remediation guidance.
        if self._embedder is not None:
            await self._schema_manager.validate_vector_index_dimensions(vector_dimensions)

        # Initialize extractor (use override if provided)
        self._extractor = self._extractor_override or self._create_extractor()

        # Initialize resolver (use override if provided)
        self._resolver = self._resolver_override or self._create_resolver()

        # Initialize geocoder (use override if provided)
        self._geocoder = self._geocoder_override or self._create_geocoder()

        # Initialize enrichment (use override if provided)
        self._enrichment_provider = (
            self._enrichment_provider_override or self._create_enrichment_provider()
        )
        self._enrichment_service = await self._create_enrichment_service()

        # Create memory instances
        multi_tenant = self._settings.memory.multi_tenant
        # Surface settings.llm as the default summarizer when it's a
        # Provider instance — lets get_conversation_summary() work
        # without the caller having to wire a summarizer callable.
        from neo4j_agent_memory.llm.protocol import (
            LLMProvider as _LLMProvider,
        )

        default_llm_provider = (
            self._settings.llm if isinstance(self._settings.llm, _LLMProvider) else None
        )
        # Concrete bolt impls satisfy the Protocols structurally — mypy's
        # @runtime_checkable check is conservative, so we suppress the
        # assignment errors here. Runtime ``isinstance(..., ShortTermProtocol)``
        # checks pass (covered in tests/unit/nams/test_protocols.py).
        self._short_term = ShortTermMemory(  # type: ignore[assignment]
            self._client,
            self._embedder,
            self._extractor,
            multi_tenant=multi_tenant,
            default_llm_provider=default_llm_provider,
        )
        self._long_term = LongTermMemory(  # type: ignore[assignment]
            self._client,
            self._embedder,
            self._extractor,
            self._resolver,
            self._geocoder,
            self._enrichment_service,
            multi_tenant=multi_tenant,
        )
        self._reasoning = ReasoningMemory(  # type: ignore[assignment]
            self._client,
            self._embedder,
            multi_tenant=multi_tenant,
        )
        from neo4j_agent_memory.memory.users import UserMemory

        self._users = UserMemory(self._client)

        # Buffered writer (opt-in fire-and-forget API).
        from neo4j_agent_memory.memory.buffered import BufferedWriter

        self._buffered = BufferedWriter(
            self._client,
            write_mode=self._settings.memory.write_mode,
            max_pending=self._settings.memory.max_pending,
        )

        # Consolidation primitives (dry-runnable hygiene jobs).
        from neo4j_agent_memory.memory.consolidation import ConsolidationMemory

        self._consolidation = ConsolidationMemory(self._client)

        # Evaluation harness.
        from neo4j_agent_memory.memory.eval import EvalMemory

        self._eval = EvalMemory(self)

        # Wire the unified Cypher accessor — bolt impl forwards to
        # ``Neo4jClient.execute_read`` after read-only validation.
        from neo4j_agent_memory.core.query import BoltCypherQuery

        self._query = BoltCypherQuery(self._client)

        # Ontologies are a NAMS capability — sentinel on bolt.
        from neo4j_agent_memory.nams._unsupported import _NamsUnsupported

        self._ontology = _NamsUnsupported(
            accessor="ontology",
            message="Ontologies (typed, validated, versioned domain schemas) are a "
            "NAMS capability.",
            workaround="On bolt, define a custom schema with SchemaModel.CUSTOM "
            "(see client.schema / SchemaManager).",
        )

        self._auth = _NamsUnsupported(
            accessor="auth",
            message="API-key management is a NAMS capability — bolt authenticates "
            "to your own Neo4j with NEO4J_PASSWORD, not a NAMS API key.",
            workaround="Manage NAMS keys from the dashboard at memory.neo4jlabs.com.",
        )

    async def _connect_nams(self) -> None:
        """Connect to the hosted NAMS service via HTTP transport.

        Per plan decisions:

        * #10 — Client-side layers (``embedding``/``extraction``/
          ``resolution``/``enrichment``/``geocoding``) are warn-and-ignored
          at connect time. The ``llm`` provider stays active (decision
          #20) for any client-side LLM workflows.
        * #18 — Fail-fast auth probe (one lightweight authenticated
          request) when ``nams.validate_on_connect=True``.
        * #13 — Bolt-only accessors (``users``, ``buffered``,
          ``consolidation``, ``schema.adopt_existing_graph``) become
          ``_NamsUnsupported`` sentinels that raise ``NotSupportedError``
          on any method call.
        """
        self._warn_inactive_layers_on_nams()

        # Lazy import — httpx is only required on the NAMS path.
        from neo4j_agent_memory.nams._unsupported import _NamsUnsupported
        from neo4j_agent_memory.nams.client import NamsBackend

        self._nams_backend = NamsBackend.from_config(self._settings.nams)
        # Enter the transport's HTTP session.
        await self._nams_backend.__aenter__()

        if self._settings.nams.validate_on_connect:
            await self._nams_backend.probe()

        # Bind the protocol-typed accessors to the NAMS implementations.
        self._short_term = self._nams_backend.short_term
        self._long_term = self._nams_backend.long_term
        self._reasoning = self._nams_backend.reasoning
        self._query = self._nams_backend.query
        self._ontology = self._nams_backend.ontology
        self._auth = self._nams_backend.auth

        # Bolt-only accessors → sentinels that raise on method call.
        self._users = _NamsUnsupported(
            accessor="users",
            message="User memory is bolt-only. NAMS scopes data per-conversation "
            "via userId on requests and per-workspace via the API key.",
        )
        self._buffered = _NamsUnsupported(
            accessor="buffered",
            message="Buffered writes are bolt-only. NAMS commits writes "
            "server-side and exposes them as soon as the request returns.",
        )
        self._consolidation = _NamsUnsupported(
            accessor="consolidation",
            message="Consolidation hygiene jobs are bolt-only. NAMS handles "
            "deduplication and archival server-side.",
        )

        # Evaluation harness works on both backends — it uses the public
        # protocol surface only.
        from neo4j_agent_memory.memory.eval import EvalMemory

        self._eval = EvalMemory(self)

    def _warn_inactive_layers_on_nams(self) -> None:
        """Emit a single warning listing client-side layers ignored by NAMS.

        Surfaces silent config drift — e.g. a user who copy-pastes a bolt
        ``MemorySettings(extraction=...)`` and switches to NAMS won't
        realize their extraction config is now a no-op.
        """
        import warnings as _warnings

        s = self._settings
        inactive: list[str] = []
        if "embedding" in s.model_fields_set:
            inactive.append("embedding (server-managed)")
        if "extraction" in s.model_fields_set:
            inactive.append("extraction (server-managed)")
        if "resolution" in s.model_fields_set:
            inactive.append("resolution (server-managed)")
        if s.geocoding.enabled:
            inactive.append("geocoding (not available on NAMS)")
        if s.enrichment.enabled:
            inactive.append("enrichment (not available on NAMS)")

        if inactive:
            _warnings.warn(
                "NAMS backend ignores client-side memory layers: "
                f"{', '.join(inactive)}. NAMS provides server-managed "
                "embedding/extraction/resolution. The LLM provider config "
                "remains active for client-side summarization. "
                "See docs/how-to/use-nams.adoc.",
                UserWarning,
                stacklevel=3,
            )

    async def close(self) -> None:
        """Close the backend connection and stop background services.

        On bolt: drain buffered writes, stop enrichment background
        service, close the Neo4j driver.

        On NAMS: close the HTTP transport. No bolt-only cleanup runs.
        """
        # NAMS path — close HTTP transport and bail.
        if self._nams_backend is not None:
            await self._nams_backend.close()
            self._nams_backend = None
            # Drop accessor references so post-close attribute access
            # raises NotConnectedError consistently with the bolt path.
            self._short_term = None
            self._long_term = None
            self._reasoning = None
            self._query = None
            self._users = None
            self._buffered = None
            self._consolidation = None
            return

        # Bolt path — preserved unchanged.
        # Drain buffered writes before closing the driver — otherwise
        # in-flight writes would be lost.
        if self._buffered is not None and hasattr(self._buffered, "stop"):
            await self._buffered.stop()
            self._buffered = None

        # Stop enrichment service gracefully
        if self._enrichment_service is not None:
            await self._enrichment_service.stop()
            self._enrichment_service = None

        if self._client is not None:
            await self._client.close()
            self._client = None

    async def flush(self) -> None:
        """Drain all queued buffered writes (no-op in ``write_mode='sync'``)."""
        if self._buffered is not None:
            await self._buffered.flush()

    async def wait_for_pending(self) -> None:
        """Alias of :meth:`flush`."""
        await self.flush()

    @property
    def write_errors(self) -> list[BufferedWriteError]:
        """Background buffered-write errors recorded since startup.

        Always empty on the NAMS backend — NAMS commits writes
        synchronously server-side; there is no client-side queue.
        """
        if self._buffered is None or self._settings.backend == "nams":
            return []
        return self._buffered.errors

    @property
    def is_connected(self) -> bool:
        """Check if client is connected (either backend)."""
        if self._nams_backend is not None:
            return self._nams_backend.transport.is_open
        return self._client is not None and self._client.is_connected

    @property
    def backend(self) -> str:
        """The resolved storage backend — ``"bolt"`` or ``"nams"``.

        Useful for backend-aware integration code (e.g. the Google ADK
        memory service, which scopes searches differently on NAMS).
        """
        return self._settings.backend or "bolt"

    @property
    def is_nams(self) -> bool:
        """``True`` when the hosted NAMS backend is active."""
        return self._settings.backend == "nams"

    @property
    def short_term(self) -> ShortTermMemory:
        """
        Access short-term memory (conversations, messages).

        Returns:
            ShortTermMemory instance

        Raises:
            NotConnectedError: If client is not connected
        """
        if self._short_term is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        # Declared return type is the bolt class so bolt users get
        # type-checked access to bolt-only methods. On NAMS the runtime
        # type is NamsShortTermMemory, which structurally implements the
        # Protocol — see ShortTermProtocol in core.protocols.
        return self._short_term  # type: ignore[return-value]

    @property
    def long_term(self) -> LongTermMemory:
        """
        Access long-term memory (entities, preferences, facts).

        Returns:
            LongTermMemory instance

        Raises:
            NotConnectedError: If client is not connected
        """
        if self._long_term is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._long_term  # type: ignore[return-value]

    @property
    def reasoning(self) -> ReasoningMemory:
        """
        Access reasoning memory (reasoning traces, tool usage).

        Returns:
            ReasoningMemory instance

        Raises:
            NotConnectedError: If client is not connected
        """
        if self._reasoning is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._reasoning  # type: ignore[return-value]

    @property
    def eval(self) -> EvalMemory:
        """
        Access the evaluation harness — recall@k for retrieval, audit
        completeness, and preference fidelity.

        See ``how-to/evaluation.adoc`` for usage.
        """
        if self._eval is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._eval

    @property
    def consolidation(self) -> ConsolidationMemory:
        """
        Access consolidation primitives — dry-runnable hygiene jobs:

        * ``dedupe_entities()`` — entity SAME_AS merging.
        * ``summarize_long_traces()`` — flag traces with many steps.
        * ``detect_superseded_preferences()`` — auto-supersede near-duplicates.
        * ``archive_expired_conversations()`` — TTL-based archival.

        All default to ``dry_run=True``; pass ``dry_run=False`` to mutate.
        """
        if self._consolidation is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._consolidation

    @property
    def buffered(self) -> BufferedWriter:
        """
        Access the buffered (fire-and-forget) writer.

        In ``write_mode='sync'`` this just delegates to the underlying
        ``execute_write``. In ``write_mode='buffered'`` writes return as
        soon as they're queued; call :meth:`flush` at shutdown.

        Pair with ``MemorySettings.memory.write_mode = "buffered"``.
        """
        if self._buffered is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._buffered

    @property
    def users(self) -> UserMemory:
        """
        Access user memory for first-class :User identity in multi-tenant
        deployments.

        Pair with the ``user_identifier=`` kwarg on short-term, long-term,
        and reasoning APIs to scope reads and writes by user.
        """
        if self._users is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._users

    @property
    def schema(self) -> SchemaManager:
        """
        Access schema manager for database schema operations.

        **Bolt only.** On NAMS this returns a ``_NamsUnsupported``
        sentinel — schema operations (``adopt_existing_graph``,
        ``setup_all``, ``validate_vector_index_dimensions``, etc.) are
        server-managed by the hosted service.

        Returns:
            SchemaManager instance (bolt) or ``_NamsUnsupported`` shim (NAMS).

        Raises:
            NotConnectedError: If client is not connected (bolt path).
        """
        if self._settings.backend == "nams":
            from neo4j_agent_memory.nams._unsupported import _NamsUnsupported

            return _NamsUnsupported(  # type: ignore[return-value]
                accessor="schema",
                message="Schema operations are server-managed on NAMS.",
            )
        if self._schema_manager is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._schema_manager

    @property
    def query(self) -> CypherQueryProtocol:
        """
        Read-only Cypher accessor — works on both backends.

        On bolt, forwards to :meth:`Neo4jClient.execute_read` after a
        client-side read-only validation. On NAMS, forwards to the
        Platinum ``POST /v1/query`` endpoint.

        Example::

            async with MemoryClient(settings) as client:
                rows = await client.query.cypher(
                    "MATCH (n:Entity) RETURN n.name LIMIT 10"
                )

        Returns:
            :class:`CypherQueryProtocol` instance.

        Raises:
            NotConnectedError: If client is not connected.
        """
        if self._query is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._query

    @property
    def ontology(self) -> Any:
        """Ontology lifecycle accessor — **NAMS only**.

        Returns the :class:`~neo4j_agent_memory.nams.ontology.NamsOntology`
        accessor on NAMS, exposing ``list``, ``get``, ``get_active``,
        ``clone``, ``create``, ``update``, ``activate``, and ``delete``.

        On bolt, returns a sentinel whose method calls raise
        :class:`NotSupportedError` (define a custom schema with
        ``SchemaModel.CUSTOM`` instead).

        Example::

            async with MemoryClient(settings) as client:
                v = await client.ontology.clone("healthcare")
                await client.ontology.activate(v.id)
                active = await client.ontology.get_active()

        Raises:
            NotConnectedError: If client is not connected.
        """
        if self._ontology is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._ontology

    @property
    def auth(self) -> Any:
        """API-key management accessor — **NAMS only**.

        Returns the :class:`~neo4j_agent_memory.nams.auth_keys.NamsAuth`
        accessor on NAMS, exposing ``list_api_keys``, ``create_api_key``,
        ``reveal_api_key``, ``rotate_api_key``, ``revoke_api_key``, and
        ``refresh_access_token``.

        On bolt, returns a sentinel whose method calls raise
        :class:`NotSupportedError` (bolt authenticates to your own Neo4j; manage
        NAMS keys from the dashboard at memory.neo4jlabs.com).

        Raises:
            NotConnectedError: If client is not connected.
        """
        if self._auth is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return self._auth

    @property
    def graph(self) -> Neo4jClient:
        """
        Access the underlying Neo4j graph client for custom Cypher queries.

        **Bolt only.** On NAMS this raises :class:`NotSupportedError` —
        use :attr:`query` for portable read-only Cypher.

        On bolt, returns a thin proxy around :class:`Neo4jClient` that
        emits a one-time :class:`DeprecationWarning` for ``execute_read``
        calls. The proxy passes ``execute_write``, ``vector_search``,
        and other attributes through unchanged. Scheduled removal:
        v0.6.0.

        Example::

            async with MemoryClient(settings) as client:
                # Deprecated:
                results = await client.graph.execute_read(
                    "MATCH (c:Customer) RETURN c.name AS name LIMIT 10"
                )
                # Replacement (works on both backends):
                results = await client.query.cypher(
                    "MATCH (c:Customer) RETURN c.name AS name LIMIT 10"
                )

        Returns:
            ``_DeprecatedGraphProxy`` wrapping :class:`Neo4jClient`.

        Raises:
            NotSupportedError: When ``backend == "nams"``.
            NotConnectedError: If client is not connected.
        """
        if self._settings.backend == "nams":
            raise NotSupportedError(
                backend="nams",
                method="client.graph",
                message="Direct Neo4j driver access is bolt-only.",
                workaround="Use client.query.cypher(query, params) for portable read-only Cypher.",
            )
        if self._client is None:
            raise NotConnectedError("Client not connected. Use 'async with' or call connect().")
        return _DeprecatedGraphProxy(self._client)  # type: ignore[return-value]

    async def get_context(
        self,
        query: str,
        *,
        session_id: str | None = None,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
        max_items: int = 10,
    ) -> str:
        """
        Get combined context from all memory types for an LLM prompt.

        This method searches across all memory types and formats the results
        into a context string suitable for including in LLM prompts.

        Args:
            query: The query to search for relevant context
            session_id: Optional session ID for short-term filtering
            include_short_term: Whether to include conversation history
            include_long_term: Whether to include facts and preferences
            include_reasoning: Whether to include similar task traces
            max_items: Maximum items per memory type

        Returns:
            Formatted context string suitable for LLM prompts
        """
        parts = []

        if include_short_term:
            short_term_context = await self.short_term.get_context(
                query,
                session_id=session_id,
                max_messages=max_items,
            )
            if short_term_context:
                parts.append(f"## Conversation History\n{short_term_context}")

        if include_long_term:
            long_term_context = await self.long_term.get_context(
                query,
                max_items=max_items,
            )
            if long_term_context:
                parts.append(f"## Relevant Knowledge\n{long_term_context}")

        if include_reasoning:
            reasoning_context = await self.reasoning.get_context(
                query,
                max_traces=max_items // 2,
            )
            if reasoning_context:
                parts.append(f"## Similar Past Tasks\n{reasoning_context}")

        return "\n\n".join(parts)

    async def get_stats(self) -> dict[str, Any]:
        """
        Get memory statistics.

        Returns:
            Dictionary with counts for each memory type.

        Raises:
            NotSupportedError: When ``backend == "nams"`` — NAMS does not
                expose a stats endpoint; use ``client.query.cypher()``
                with a custom aggregation query if needed.
            NotConnectedError: If client is not connected.
        """
        if self._settings.backend == "nams":
            raise NotSupportedError(
                backend="nams",
                method="get_stats",
                workaround="Use client.query.cypher() with a counting "
                "query (e.g. 'MATCH (n) RETURN labels(n), count(n)').",
            )
        if self._client is None:
            raise NotConnectedError("Client not connected.")

        from neo4j_agent_memory.graph.queries import GET_MEMORY_STATS

        results = await self._client.execute_read(GET_MEMORY_STATS)
        if results:
            return results[0]
        return {
            "conversations": 0,
            "messages": 0,
            "entities": 0,
            "preferences": 0,
            "facts": 0,
            "traces": 0,
        }

    async def get_graph(
        self,
        *,
        memory_types: list[Literal["short_term", "long_term", "reasoning"]] | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        include_embeddings: bool = False,
        limit: int = 1000,
    ) -> MemoryGraph:
        """
        Export memory graph for visualization.

        This method retrieves nodes and relationships from the memory graph,
        formatted for visualization libraries like NVL (Neo4j Visualization Library).

        Args:
            memory_types: Which memory types to include. Defaults to all.
                         Options: 'short_term', 'long_term', 'reasoning'
            session_id: Filter by session ID (for short_term and reasoning)
            since: Only include data created/updated after this time
            until: Only include data created/updated before this time
            include_embeddings: Whether to include embedding vectors in properties.
                              Set to False (default) for smaller payloads.
            limit: Maximum number of nodes to return per memory type

        Returns:
            MemoryGraph with nodes, relationships, and metadata

        Raises:
            NotSupportedError: When ``backend == "nams"`` — the
                visualization export uses bolt-specific Cypher; NAMS
                exposes ``client.long_term.get_entity_graph()`` for a
                comparable hosted-side equivalent.
        """
        if self._settings.backend == "nams":
            raise NotSupportedError(
                backend="nams",
                method="get_graph",
                workaround="Use NAMS-specific endpoints via "
                "client.long_term.get_entity_provenance() or "
                "client.query.cypher() with custom MATCH queries.",
            )
        if self._client is None:
            raise NotConnectedError("Client not connected.")

        if memory_types is None:
            memory_types = cast(
                "list[Literal['short_term', 'long_term', 'reasoning']]",
                ["short_term", "long_term", "reasoning"],
            )

        all_nodes: list[GraphNode] = []
        all_relationships: list[GraphRelationship] = []
        node_ids_seen: set[str] = set()

        params = {
            "session_id": session_id,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "include_embeddings": include_embeddings,
            "limit": limit,
        }

        # Fetch short-term memory graph
        if "short_term" in memory_types:
            try:
                results = await self._client.execute_read(
                    """
                    MATCH (c:Conversation)-[r:HAS_MESSAGE]->(m:Message)
                    WHERE ($session_id IS NULL OR c.session_id = $session_id)
                    WITH c, r, m
                    LIMIT $limit
                    RETURN c, r, m
                    """,
                    params,
                )
                for row in results:
                    conv = dict(row["c"])
                    msg = dict(row["m"])

                    # Add conversation node
                    if conv.get("id") and conv["id"] not in node_ids_seen:
                        props = {k: v for k, v in conv.items() if v is not None}
                        all_nodes.append(
                            GraphNode(
                                id=conv["id"],
                                labels=["Conversation"],
                                properties=props,
                            )
                        )
                        node_ids_seen.add(conv["id"])

                    # Add message node
                    if msg.get("id") and msg["id"] not in node_ids_seen:
                        props = {k: v for k, v in msg.items() if v is not None}
                        if not include_embeddings:
                            props.pop("embedding", None)
                        all_nodes.append(
                            GraphNode(
                                id=msg["id"],
                                labels=["Message"],
                                properties=props,
                            )
                        )
                        node_ids_seen.add(msg["id"])

                    # Add relationship
                    if conv.get("id") and msg.get("id"):
                        all_relationships.append(
                            GraphRelationship(
                                id=f"{conv['id']}->{msg['id']}",
                                type="HAS_MESSAGE",
                                from_node=conv["id"],
                                to_node=msg["id"],
                                properties={},
                            )
                        )
            except Exception:
                pass  # Skip if query fails

        # Fetch long-term memory graph
        if "long_term" in memory_types:
            try:
                results = await self._client.execute_read(
                    """
                    MATCH (e:Entity)
                    WITH e LIMIT $limit
                    OPTIONAL MATCH (e)-[r:RELATED_TO]-(e2:Entity)
                    RETURN e, r, e2
                    """,
                    {"limit": limit},
                )
                for row in results:
                    entity = dict(row["e"])

                    if entity.get("id") and entity["id"] not in node_ids_seen:
                        props = {k: v for k, v in entity.items() if v is not None}
                        if not include_embeddings:
                            props.pop("embedding", None)
                        all_nodes.append(
                            GraphNode(
                                id=entity["id"],
                                labels=["Entity"],
                                properties=props,
                            )
                        )
                        node_ids_seen.add(entity["id"])

                    if row.get("r") and row.get("e2"):
                        e2 = dict(row["e2"])
                        if e2.get("id") and e2["id"] not in node_ids_seen:
                            props = {k: v for k, v in e2.items() if v is not None}
                            if not include_embeddings:
                                props.pop("embedding", None)
                            all_nodes.append(
                                GraphNode(
                                    id=e2["id"],
                                    labels=["Entity"],
                                    properties=props,
                                )
                            )
                            node_ids_seen.add(e2["id"])

                        rel = dict(row["r"])
                        all_relationships.append(
                            GraphRelationship(
                                id=f"{entity['id']}->{e2['id']}",
                                type=rel.get("type", "RELATED_TO"),
                                from_node=entity["id"],
                                to_node=e2["id"],
                                properties={
                                    k: v for k, v in rel.items() if k != "type" and v is not None
                                },
                            )
                        )
            except Exception:
                pass

        # Fetch reasoning memory graph
        if "reasoning" in memory_types:
            try:
                results = await self._client.execute_read(
                    """
                    MATCH (rt:ReasoningTrace)
                    WHERE ($session_id IS NULL OR rt.session_id = $session_id)
                    WITH rt LIMIT $limit
                    OPTIONAL MATCH (rt)-[r1:HAS_STEP]->(rs:ReasoningStep)
                    OPTIONAL MATCH (rs)-[r2:USES_TOOL]->(tc:ToolCall)
                    RETURN rt, r1, rs, r2, tc
                    """,
                    params,
                )
                for row in results:
                    trace = dict(row["rt"])

                    if trace.get("id") and trace["id"] not in node_ids_seen:
                        props = {k: v for k, v in trace.items() if v is not None}
                        if not include_embeddings:
                            props.pop("task_embedding", None)
                        all_nodes.append(
                            GraphNode(
                                id=trace["id"],
                                labels=["ReasoningTrace"],
                                properties=props,
                            )
                        )
                        node_ids_seen.add(trace["id"])

                    if row.get("rs"):
                        step = dict(row["rs"])
                        if step.get("id") and step["id"] not in node_ids_seen:
                            props = {k: v for k, v in step.items() if v is not None}
                            if not include_embeddings:
                                props.pop("embedding", None)
                            all_nodes.append(
                                GraphNode(
                                    id=step["id"],
                                    labels=["ReasoningStep"],
                                    properties=props,
                                )
                            )
                            node_ids_seen.add(step["id"])

                        if trace.get("id") and step.get("id"):
                            all_relationships.append(
                                GraphRelationship(
                                    id=f"{trace['id']}->{step['id']}",
                                    type="HAS_STEP",
                                    from_node=trace["id"],
                                    to_node=step["id"],
                                    properties={},
                                )
                            )

                    if row.get("tc") and row.get("rs"):
                        tc = dict(row["tc"])
                        step = dict(row["rs"])
                        if tc.get("id") and tc["id"] not in node_ids_seen:
                            props = {k: v for k, v in tc.items() if v is not None}
                            all_nodes.append(
                                GraphNode(
                                    id=tc["id"],
                                    labels=["ToolCall"],
                                    properties=props,
                                )
                            )
                            node_ids_seen.add(tc["id"])

                        if step.get("id") and tc.get("id"):
                            all_relationships.append(
                                GraphRelationship(
                                    id=f"{step['id']}->{tc['id']}",
                                    type="USES_TOOL",
                                    from_node=step["id"],
                                    to_node=tc["id"],
                                    properties={},
                                )
                            )
            except Exception:
                pass

        return MemoryGraph(
            nodes=all_nodes,
            relationships=all_relationships,
            metadata={
                "memory_types": memory_types,
                "session_id": session_id,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "include_embeddings": include_embeddings,
                "node_count": len(all_nodes),
                "relationship_count": len(all_relationships),
            },
        )

    async def get_locations(
        self,
        *,
        session_id: str | None = None,
        has_coordinates: bool = True,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Get location entities, optionally filtered by conversation session.

        This method retrieves Location entities from the knowledge graph,
        with optional filtering to only include locations mentioned in a
        specific conversation (identified by session_id).

        Args:
            session_id: Filter to locations mentioned in this conversation.
                       When provided, only returns locations that have an
                       EXTRACTED_FROM relationship to messages in this session.
            has_coordinates: Only return locations with lat/lon coordinates.
                           Defaults to True for map visualization use cases.
            limit: Maximum number of locations to return. Defaults to 500.

        Returns:
            List of location dictionaries with:
                - id: Entity UUID
                - name: Location name
                - subtype: Location subtype (city, country, landmark, etc.)
                - description: Entity description
                - enriched_description: Enhanced description from enrichment
                - wikipedia_url: Wikipedia link if available
                - latitude: Latitude coordinate
                - longitude: Longitude coordinate
                - conversations: List of conversations mentioning this location

        Raises:
            NotSupportedError: When ``backend == "nams"`` — relies on
                bolt-specific Cypher with the ``location`` Point property.
        """
        if self._settings.backend == "nams":
            raise NotSupportedError(
                backend="nams",
                method="get_locations",
                workaround="Use client.long_term.search_locations_near() "
                "is also bolt-only. NAMS does not expose Point-property "
                "geospatial queries via the REST API. File an issue if "
                "you need this on NAMS.",
            )
        if self._client is None:
            raise NotConnectedError("Client not connected.")

        # Build the query based on whether session_id filtering is needed
        if session_id:
            # Filter to locations mentioned in the specific conversation
            # EXTRACTED_FROM direction: (Entity)-[:EXTRACTED_FROM]->(Message)
            query = """
                MATCH (e:Entity {type: 'LOCATION'})-[:EXTRACTED_FROM]->(m:Message)<-[:HAS_MESSAGE]-(c:Conversation {session_id: $session_id})
                WITH DISTINCT e
                WHERE $has_coordinates = false OR (e.location.latitude IS NOT NULL AND e.location.longitude IS NOT NULL)
                WITH e LIMIT $limit
                OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(m2:Message)<-[:HAS_MESSAGE]-(c2:Conversation)
                WITH e, collect(DISTINCT {id: c2.id, title: c2.title, session_id: c2.session_id}) as conversations
                RETURN e.id as id,
                       e.name as name,
                       e.subtype as subtype,
                       e.description as description,
                       e.enriched_description as enriched_description,
                       e.wikipedia_url as wikipedia_url,
                       e.location.latitude as latitude,
                       e.location.longitude as longitude,
                       conversations
            """
        else:
            # Return all locations (no session filtering)
            query = """
                MATCH (e:Entity {type: 'LOCATION'})
                WHERE $has_coordinates = false OR (e.location.latitude IS NOT NULL AND e.location.longitude IS NOT NULL)
                WITH e LIMIT $limit
                OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(m:Message)<-[:HAS_MESSAGE]-(c:Conversation)
                WITH e, collect(DISTINCT {id: c.id, title: c.title, session_id: c.session_id}) as conversations
                RETURN e.id as id,
                       e.name as name,
                       e.subtype as subtype,
                       e.description as description,
                       e.enriched_description as enriched_description,
                       e.wikipedia_url as wikipedia_url,
                       e.location.latitude as latitude,
                       e.location.longitude as longitude,
                       conversations
            """

        params = {
            "session_id": session_id,
            "has_coordinates": has_coordinates,
            "limit": limit,
        }

        try:
            results = await self._client.execute_read(query, params)
            locations = []
            for row in results:
                # Filter out null conversation entries
                convs = [c for c in (row.get("conversations") or []) if c.get("id")]
                locations.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "subtype": row.get("subtype"),
                        "description": row.get("description"),
                        "enriched_description": row.get("enriched_description"),
                        "wikipedia_url": row.get("wikipedia_url"),
                        "latitude": row.get("latitude"),
                        "longitude": row.get("longitude"),
                        "conversations": convs,
                    }
                )
            return locations
        except Exception:
            return []

    def _create_embedder(self) -> Embedder | None:
        """Create embedder from ``self._settings.embedding``.

        Handles the union type introduced in v0.3.0:

        * :class:`EmbeddingConfig` (legacy) — translated to the matching
          concrete adapter from :mod:`neo4j_agent_memory.embeddings`.
        * :class:`EmbeddingProvider` instance — wrapped in a
          :class:`_ProviderToEmbedderAdapter` so downstream memory layers
          (which still consume the legacy ``embed(text)`` /
          ``embed_batch(texts)`` API) keep working unchanged.

        The deprecation warning for legacy config use is already emitted
        by :meth:`MemorySettings._resolve_providers` at construction
        time; this method does not re-warn.
        """
        config = self._settings.embedding

        # New shape: an EmbeddingProvider. Wrap so callers see the old
        # Embedder API. The wrapper preserves ``dimensions`` and ``model``
        # attributes used by :meth:`_resolve_vector_dimensions` and logs.
        if not isinstance(config, EmbeddingConfig):
            from neo4j_agent_memory.embeddings.base import adapt_to_legacy_embedder

            # adapt_to_legacy_embedder returns `object` (accepts both Embedder
            # and EmbeddingProvider); the result always satisfies Embedder.
            return cast("Embedder", adapt_to_legacy_embedder(config))

        # Legacy EmbeddingConfig: translate to a concrete embedder.
        if config.provider == EmbeddingProvider.OPENAI:
            from neo4j_agent_memory.embeddings.openai import OpenAIEmbedder

            return OpenAIEmbedder(
                model=config.model,
                api_key=config.api_key.get_secret_value() if config.api_key else None,
                dimensions=config.dimensions if config.dimensions != 1536 else None,
                batch_size=config.batch_size,
            )
        elif config.provider == EmbeddingProvider.SENTENCE_TRANSFORMERS:
            from neo4j_agent_memory.embeddings.sentence_transformers import (
                SentenceTransformerEmbedder,
            )

            return SentenceTransformerEmbedder(
                model_name=config.model,
                device=config.device,
            )
        else:
            return None

    def _create_extractor(self) -> EntityExtractor | None:
        """Create extractor based on settings.

        Uses the extraction factory to create the appropriate extractor
        based on configuration. Supports:
        - NONE: No extraction
        - LLM: LLM-based extraction (OpenAI)
        - SPACY: spaCy NER extraction (local)
        - GLINER: GLiNER zero-shot NER (local)
        - PIPELINE: Multi-stage pipeline combining multiple extractors

        ``self._settings.llm`` may be an :class:`LLMConfig` (legacy) or
        an :class:`LLMProvider` instance (v0.3+); the factory handles
        both shapes transparently.
        """
        from neo4j_agent_memory.extraction.factory import create_extractor

        config = self._settings.extraction

        if config.extractor_type == ExtractorType.NONE:
            return None

        return create_extractor(
            extraction_config=config,
            schema_config=self._settings.schema_config,
            llm_config=self._settings.llm,
        )

    def _resolve_vector_dimensions(self) -> int:
        """Return the vector index dimensionality for schema setup.

        Resolution order:

        1. The resolved embedder's ``dimensions`` attribute (works for
           both legacy :class:`BaseEmbedder` and new
           :class:`EmbeddingProvider` instances).
        2. The legacy :class:`EmbeddingConfig.dimensions` field when
           ``self._embedder`` is ``None`` and the settings still carry
           an :class:`EmbeddingConfig`.
        3. A default of 1536 (text-embedding-3-small) as the historical
           fallback.
        """
        if self._embedder is not None:
            dim = getattr(self._embedder, "dimensions", None)
            if isinstance(dim, int) and dim > 0:
                return dim
        cfg = self._settings.embedding
        if isinstance(cfg, EmbeddingConfig):
            return cfg.dimensions
        return 1536

    def _create_resolver(self) -> EntityResolver | None:
        """Create resolver based on settings."""
        config = self._settings.resolution

        if config.strategy == ResolverStrategy.NONE:
            return None

        if config.strategy == ResolverStrategy.EXACT:
            from neo4j_agent_memory.resolution.exact import ExactMatchResolver

            return ExactMatchResolver()

        if config.strategy == ResolverStrategy.FUZZY:
            from neo4j_agent_memory.resolution.fuzzy import FuzzyMatchResolver

            return FuzzyMatchResolver(threshold=config.fuzzy_threshold)

        if config.strategy == ResolverStrategy.SEMANTIC:
            from neo4j_agent_memory.resolution.semantic import SemanticMatchResolver

            if self._embedder is None:
                return None
            return SemanticMatchResolver(
                self._embedder,
                threshold=config.semantic_threshold,
            )

        if config.strategy == ResolverStrategy.COMPOSITE:
            from neo4j_agent_memory.resolution.composite import CompositeResolver

            return CompositeResolver(
                embedder=self._embedder,
                exact_threshold=config.exact_threshold,
                fuzzy_threshold=config.fuzzy_threshold,
                semantic_threshold=config.semantic_threshold,
            )

        return None

    def _create_geocoder(self) -> Geocoder | None:
        """Create geocoder based on settings.

        Returns a configured geocoder for Location entities, or None if
        geocoding is disabled. Supports Nominatim (free, rate-limited) and
        Google (requires API key).
        """
        config = self._settings.geocoding

        if not config.enabled:
            return None

        from neo4j_agent_memory.services.geocoder import create_geocoder

        return create_geocoder(
            provider=config.provider.value,
            api_key=config.api_key.get_secret_value() if config.api_key else None,
            cache_results=config.cache_results,
            rate_limit=config.rate_limit_per_second,
            user_agent=config.user_agent,
        )

    def _create_enrichment_provider(self) -> _EnrichmentProviderProtocol | None:
        """Create enrichment provider based on settings.

        Returns a configured enrichment provider, or None if enrichment
        is disabled. Supports Wikimedia (free) and Diffbot (requires API key).
        """
        from neo4j_agent_memory.enrichment.factory import create_enrichment_service

        return create_enrichment_service(self._settings.enrichment)

    async def _create_enrichment_service(self) -> BackgroundEnrichmentService | None:
        """Create and start the background enrichment service.

        Returns a BackgroundEnrichmentService if enrichment is enabled and
        background processing is enabled, otherwise None.
        """
        if self._enrichment_provider is None:
            return None

        if not self._settings.enrichment.background_enabled:
            return None

        if self._client is None:
            return None

        from neo4j_agent_memory.enrichment.background import BackgroundEnrichmentService

        service = BackgroundEnrichmentService(
            client=self._client,
            provider=self._enrichment_provider,
            max_queue_size=self._settings.enrichment.queue_max_size,
            max_retries=self._settings.enrichment.max_retries,
            retry_delay=self._settings.enrichment.retry_delay_seconds,
            min_confidence=self._settings.enrichment.min_confidence,
            entity_types=self._settings.enrichment.entity_types or None,
        )
        await service.start()
        return service
