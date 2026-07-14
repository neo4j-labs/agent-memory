"""Factory for creating extraction pipelines and extractors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
)
from neo4j_agent_memory.config.settings import (
    MergeStrategy as ConfigMergeStrategy,
)
from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    NoOpExtractor,
)
from neo4j_agent_memory.extraction.pipeline import (
    ExtractionPipeline,
    MergeStrategy,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import LLMProvider
    from neo4j_agent_memory.schema.models import EntitySchemaConfig

logger = logging.getLogger(__name__)


def _get_type_mapping_for_schema(schema_config: SchemaConfig) -> dict[str, str] | None:
    """Get spaCy type mapping based on schema configuration."""
    # If custom entity types specified, we may need custom mapping
    # For now, return None to use default mappings
    return None


def _get_entity_labels_for_schema(schema_config: SchemaConfig) -> list[str]:
    """Get GLiNER labels based on schema configuration."""
    if schema_config.entity_types:
        # Use custom entity types as labels (lowercase for GLiNER)
        return [t.lower() for t in schema_config.entity_types]

    # Default POLE+O labels
    return [
        "person",
        "organization",
        "location",
        "event",
        "object",
        # Common subtypes
        "vehicle",
        "phone number",
        "email address",
        "document",
        "device",
        "address",
        "city",
        "country",
        "company",
        "meeting",
    ]


def _convert_merge_strategy(config_strategy: ConfigMergeStrategy) -> MergeStrategy:
    """Convert config merge strategy to pipeline merge strategy."""
    return MergeStrategy(config_strategy.value)


def create_spacy_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
) -> EntityExtractor:
    """Create a spaCy entity extractor.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration for type mapping

    Returns:
        SpacyEntityExtractor instance

    Raises:
        ImportError: If spaCy is not installed
    """
    from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

    type_mapping = None
    if schema_config:
        type_mapping = _get_type_mapping_for_schema(schema_config)

    return SpacyEntityExtractor(
        model=extraction_config.spacy_model,
        type_mapping=type_mapping,
        default_confidence=extraction_config.spacy_confidence,
    )


def create_gliner_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
) -> EntityExtractor:
    """Create a GLiNER entity extractor.

    GLiNER2 supports domain schemas with entity type descriptions for
    improved extraction accuracy. If a schema is specified in the config,
    it will be used for extraction.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration for entity labels

    Returns:
        GLiNEREntityExtractor instance

    Raises:
        ImportError: If GLiNER is not installed
    """
    from neo4j_agent_memory.extraction.gliner_extractor import (
        GLiNEREntityExtractor,
        get_schema,
    )

    # Check if a domain schema is specified
    if extraction_config.gliner_schema:
        # Use the pre-defined domain schema with descriptions
        try:
            schema = get_schema(extraction_config.gliner_schema)
            return GLiNEREntityExtractor(
                model=extraction_config.gliner_model,
                schema=schema,
                threshold=extraction_config.gliner_threshold,
                device=extraction_config.gliner_device,
            )
        except ValueError as e:
            logger.warning(f"Invalid GLiNER schema: {e}. Using default labels.")

    # Fall back to simple labels from schema config or defaults
    entity_labels = _get_entity_labels_for_schema(schema_config) if schema_config else None

    return GLiNEREntityExtractor(
        model=extraction_config.gliner_model,
        entity_labels=entity_labels,
        threshold=extraction_config.gliner_threshold,
        device=extraction_config.gliner_device,
    )


def create_llm_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
) -> EntityExtractor:
    """Create an LLM entity extractor.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration for entity types
        llm_config: Optional LLM source. Accepts:

            * an :class:`LLMConfig` (legacy) — translated to a provider
              via :func:`neo4j_agent_memory.llm.from_provider`,
            * a fully-constructed :class:`LLMProvider` instance,
            * ``None`` — falls back to the legacy ``extraction_config.llm_model``
              and lets :class:`LLMEntityExtractor` build its own default.

    Returns:
        LLMEntityExtractor instance
    """
    from neo4j_agent_memory.config.settings import LLMConfig
    from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

    entity_types = extraction_config.entity_types
    if schema_config and schema_config.entity_types:
        entity_types = schema_config.entity_types

    common_kwargs: dict[str, Any] = {
        "entity_types": entity_types,
        "extract_relations": extraction_config.extract_relations,
        "extract_preferences": extraction_config.extract_preferences,
    }

    # Provider instance: pass through directly.
    if llm_config is not None and not isinstance(llm_config, LLMConfig):
        return LLMEntityExtractor(provider=llm_config, **common_kwargs)

    # Legacy LLMConfig: build provider via from_provider() so the extractor
    # is wired with the user-selected provider/api_key, not the hardcoded
    # extraction_config.llm_model default.
    if isinstance(llm_config, LLMConfig):
        from neo4j_agent_memory.llm import from_provider

        # Map legacy LLMConfig.provider enum to provider-prefixed model id.
        provider_prefix = llm_config.provider.value
        # CUSTOM provider lacks a registry entry; treat the configured model
        # string as authoritative and let from_provider() infer the prefix.
        if provider_prefix == "custom":
            model_id = llm_config.model
        else:
            model_id = (
                llm_config.model
                if "/" in llm_config.model
                else f"{provider_prefix}/{llm_config.model}"
            )
        kwargs: dict[str, Any] = {}
        if llm_config.api_key is not None:
            kwargs["api_key"] = llm_config.api_key.get_secret_value()
        llm_provider = cast(
            "LLMProvider", from_provider(model_id, kind="llm", **kwargs)
        )  # kind="llm" guarantees LLMProvider
        return LLMEntityExtractor(provider=llm_provider, **common_kwargs)

    # No llm_config: let LLMEntityExtractor build its own default provider
    # from extraction_config.llm_model.
    return LLMEntityExtractor(model=extraction_config.llm_model, **common_kwargs)


def create_extraction_pipeline(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
) -> ExtractionPipeline:
    """Create a multi-stage extraction pipeline based on configuration.

    The pipeline combines multiple extractors (spaCy, GLiNER, LLM) according
    to the configuration settings. Stages are run in order and results are
    merged according to the specified strategy.

    Default pipeline order:
    1. spaCy - Fast statistical NER for common entities
    2. GLiNER - Zero-shot NER for domain-specific entities
    3. LLM - Fallback for complex cases and relation extraction

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration
        llm_config: Optional LLM configuration

    Returns:
        ExtractionPipeline instance
    """
    stages: list[EntityExtractor] = []

    # Stage 1: spaCy for fast initial extraction
    if extraction_config.enable_spacy:
        try:
            spacy_extractor = create_spacy_extractor(extraction_config, schema_config)
            stages.append(spacy_extractor)
            logger.info("Added spaCy extractor to pipeline")
        except ImportError as e:
            logger.warning(f"spaCy not available, skipping: {e}")

    # Stage 2: GLiNER for zero-shot NER with custom types
    if extraction_config.enable_gliner:
        try:
            gliner_extractor = create_gliner_extractor(extraction_config, schema_config)
            stages.append(gliner_extractor)
            logger.info("Added GLiNER extractor to pipeline")
        except ImportError as e:
            logger.warning(f"GLiNER not available, skipping: {e}")

    # Stage 3: LLM fallback for complex cases and relations
    if extraction_config.enable_llm_fallback:
        try:
            llm_extractor = create_llm_extractor(extraction_config, schema_config, llm_config)
            stages.append(llm_extractor)
            logger.info("Added LLM extractor to pipeline")
        except Exception as e:
            logger.warning(f"LLM extractor not available, skipping: {e}")

    if not stages:
        logger.warning("No extraction stages available, using NoOpExtractor")
        stages.append(NoOpExtractor())

    # Convert merge strategy
    merge_strategy = _convert_merge_strategy(extraction_config.merge_strategy)

    return ExtractionPipeline(
        stages=stages,
        merge_strategy=merge_strategy,
        stop_on_success=not extraction_config.fallback_on_empty,
    )


def create_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
) -> EntityExtractor:
    """Create an entity extractor based on configuration.

    This is the main factory function that creates the appropriate
    extractor based on the extractor_type setting.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration
        llm_config: Optional LLM configuration for LLM-based extraction

    Returns:
        EntityExtractor instance

    Examples:
        ```python
        # Create pipeline extractor (default)
        config = ExtractionConfig(extractor_type=ExtractorType.PIPELINE)
        extractor = create_extractor(config)

        # Create spaCy-only extractor
        config = ExtractionConfig(extractor_type=ExtractorType.SPACY)
        extractor = create_extractor(config)

        # Create GLiNER extractor
        config = ExtractionConfig(extractor_type=ExtractorType.GLINER)
        extractor = create_extractor(config)

        # Create LLM extractor
        config = ExtractionConfig(extractor_type=ExtractorType.LLM)
        extractor = create_extractor(config)
        ```
    """
    if extraction_config.extractor_type == ExtractorType.NONE:
        return NoOpExtractor()

    elif extraction_config.extractor_type == ExtractorType.SPACY:
        return create_spacy_extractor(extraction_config, schema_config)

    elif extraction_config.extractor_type == ExtractorType.GLINER:
        return create_gliner_extractor(extraction_config, schema_config)

    elif extraction_config.extractor_type == ExtractorType.LLM:
        return create_llm_extractor(extraction_config, schema_config, llm_config)

    elif extraction_config.extractor_type == ExtractorType.PIPELINE:
        return create_extraction_pipeline(extraction_config, schema_config, llm_config)

    else:
        logger.warning(f"Unknown extractor type: {extraction_config.extractor_type}, using NoOp")
        return NoOpExtractor()


class ExtractorBuilder:
    """Builder for creating custom extraction configurations.

    Provides a fluent interface for constructing extractors with
    specific settings.

    Example:
        ```python
        # Basic pipeline with GLiNER2
        extractor = (
            ExtractorBuilder()
            .with_spacy("en_core_web_lg")
            .with_gliner(threshold=0.6)
            .with_llm_fallback()
            .merge_by_confidence()
            .build()
        )

        # Using a domain schema for better accuracy
        extractor = (
            ExtractorBuilder()
            .with_spacy()
            .with_gliner_schema("podcast")  # Use podcast domain schema
            .with_llm_fallback()
            .build()
        )
        ```
    """

    def __init__(self) -> None:
        """Initialize builder with default settings."""
        self._enable_spacy = False
        self._enable_gliner = False
        self._enable_llm = False
        self._spacy_model = "en_core_web_sm"
        self._gliner_model = "gliner-community/gliner_medium-v2.5"
        self._gliner_threshold = 0.5
        self._gliner_device = "cpu"
        self._gliner_schema: str | None = None
        self._gliner_entity_labels: list[str] | None = None
        self._llm_model = "gpt-4o-mini"
        self._merge_strategy = MergeStrategy.CONFIDENCE
        self._entity_types: list[str] = [
            "PERSON",
            "ORGANIZATION",
            "LOCATION",
            "EVENT",
            "OBJECT",
        ]
        self._extract_relations = True
        self._extract_preferences = True
        self._confidence_threshold: float | None = None

    def with_spacy(self, model: str = "en_core_web_sm") -> ExtractorBuilder:
        """Add spaCy extractor to pipeline."""
        self._enable_spacy = True
        self._spacy_model = model
        return self

    def with_gliner(
        self,
        model: str | None = None,
        threshold: float = 0.5,
        device: str = "cpu",
        *,
        model_name: str | None = None,
    ) -> ExtractorBuilder:
        """Add GLiNER2 extractor to pipeline with simple labels.

        Args:
            model: GLiNER model identifier (e.g. ``gliner-community/gliner_medium-v2.5``).
            threshold: Confidence threshold.
            device: Inference device (``cpu`` / ``cuda`` / ``mps``).
            model_name: Alias for ``model`` (kept for compatibility with the CLI
                and older call sites).
        """
        self._enable_gliner = True
        resolved = model_name or model or "gliner-community/gliner_medium-v2.5"
        self._gliner_model = resolved
        self._gliner_threshold = threshold
        self._gliner_device = device
        return self

    def with_gliner_schema(
        self,
        schema_name: str,
        model: str = "gliner-community/gliner_medium-v2.5",
        threshold: float = 0.5,
        device: str = "cpu",
    ) -> ExtractorBuilder:
        """Add GLiNER2 extractor with a domain schema for better accuracy.

        Using domain schemas with entity type descriptions significantly
        improves extraction accuracy.

        Args:
            schema_name: Name of the schema (poleo, podcast, news, scientific,
                        business, entertainment, medical, legal)
            model: GLiNER2 model to use
            threshold: Confidence threshold
            device: Device to run on (cpu, cuda, mps)
        """
        self._enable_gliner = True
        self._gliner_model = model
        self._gliner_threshold = threshold
        self._gliner_device = device
        self._gliner_schema = schema_name
        return self

    def with_llm_fallback(self, model: str = "gpt-4o-mini") -> ExtractorBuilder:
        """Add LLM extractor as fallback."""
        self._enable_llm = True
        self._llm_model = model
        return self

    def with_llm(self, model: str = "gpt-4o-mini") -> ExtractorBuilder:
        """Add an LLM extractor (alias of :meth:`with_llm_fallback`)."""
        return self.with_llm_fallback(model)

    def with_entity_types(self, types: list[str]) -> ExtractorBuilder:
        """Set entity types to extract."""
        self._entity_types = types
        # GLiNER labels mirror the configured entity types when no domain
        # schema is provided.
        self._gliner_entity_labels = [t.lower() for t in types]
        return self

    def with_confidence_threshold(self, threshold: float) -> ExtractorBuilder:
        """Set the confidence threshold for entity extraction.

        Applies to GLiNER (the threshold passed to the model) and is recorded
        for downstream filtering by other stages.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"confidence_threshold must be in [0.0, 1.0], got {threshold!r}")
        self._confidence_threshold = threshold
        self._gliner_threshold = threshold
        return self

    def with_schema(self, schema_config: EntitySchemaConfig) -> ExtractorBuilder:
        """Configure entity types from an :class:`EntitySchemaConfig`.

        Derives ``entity_types`` from the schema's
        :attr:`EntitySchemaConfig.entity_types` and uses them as labels for
        GLiNER and as the type set for the LLM extractor. Equivalent to
        calling :meth:`with_entity_types` with the names from the schema.

        Args:
            schema_config: Entity schema configuration. Each
                :class:`EntityTypeConfig` contributes its ``name`` to the
                extractor's type set.
        """
        type_names = [t.name for t in schema_config.entity_types]
        if type_names:
            self._entity_types = type_names
            self._gliner_entity_labels = [t.lower() for t in type_names]
        return self

    def merge_by_union(self) -> ExtractorBuilder:
        """Use union strategy for merging."""
        self._merge_strategy = MergeStrategy.UNION
        return self

    def merge_by_intersection(self) -> ExtractorBuilder:
        """Use intersection strategy for merging."""
        self._merge_strategy = MergeStrategy.INTERSECTION
        return self

    def merge_by_confidence(self) -> ExtractorBuilder:
        """Use confidence strategy for merging."""
        self._merge_strategy = MergeStrategy.CONFIDENCE
        return self

    def merge_by_cascade(self) -> ExtractorBuilder:
        """Use cascade strategy for merging."""
        self._merge_strategy = MergeStrategy.CASCADE
        return self

    def extract_relations(self, enabled: bool = True) -> ExtractorBuilder:
        """Enable/disable relation extraction."""
        self._extract_relations = enabled
        return self

    def extract_preferences(self, enabled: bool = True) -> ExtractorBuilder:
        """Enable/disable preference extraction."""
        self._extract_preferences = enabled
        return self

    def build(self) -> EntityExtractor:
        """Build the extractor based on configuration."""
        stages: list[EntityExtractor] = []

        if self._enable_spacy:
            from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

            stages.append(SpacyEntityExtractor(model=self._spacy_model))

        if self._enable_gliner:
            from neo4j_agent_memory.extraction.gliner_extractor import (
                GLiNEREntityExtractor,
                get_schema,
            )

            if self._gliner_schema:
                # Use domain schema for better accuracy
                schema = get_schema(self._gliner_schema)
                stages.append(
                    GLiNEREntityExtractor(
                        model=self._gliner_model,
                        schema=schema,
                        threshold=self._gliner_threshold,
                        device=self._gliner_device,
                    )
                )
            else:
                stages.append(
                    GLiNEREntityExtractor(
                        model=self._gliner_model,
                        entity_labels=self._gliner_entity_labels,
                        threshold=self._gliner_threshold,
                        device=self._gliner_device,
                    )
                )

        if self._enable_llm:
            from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

            stages.append(
                LLMEntityExtractor(
                    model=self._llm_model,
                    entity_types=self._entity_types,
                    extract_relations=self._extract_relations,
                    extract_preferences=self._extract_preferences,
                )
            )

        if not stages:
            return NoOpExtractor()

        if len(stages) == 1:
            return stages[0]

        return ExtractionPipeline(stages=stages, merge_strategy=self._merge_strategy)
