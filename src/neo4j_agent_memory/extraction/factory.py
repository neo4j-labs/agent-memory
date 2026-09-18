"""Factory for creating extraction pipelines and extractors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
    from neo4j_agent_memory.ontology.models import OntologyDocument
    from neo4j_agent_memory.schema.models import EntitySchemaConfig

logger = logging.getLogger(__name__)


def _spacy_label_mappings(
    ontology: OntologyDocument | None,
) -> tuple[dict[str, str], dict[str, str]] | None:
    """Project an ontology onto spaCy's ``(type_mapping, subtype_mapping)``.

    spaCy's NER label set is fixed, so the ontology can only influence how
    those labels are *named* downstream, not what the model finds. Two cases:

    * The ontology declares only base POLE+O labels (no subtypes) — spaCy's
      own default mapping already produces exactly those types, so ``None`` is
      returned and the extractor keeps its defaults.
    * The ontology declares subtypes — each spaCy label is remapped onto the
      first ontology label declared for its POLE+O type, so extracted entities
      carry a ``(type, subtype)`` pair the graph knows about.

    Args:
        ontology: The effective ontology, when one is configured.

    Returns:
        ``(type_mapping, subtype_mapping)``, or ``None`` to keep spaCy's
        defaults.
    """
    if ontology is None or not ontology.entity_types:
        return None

    if all(et.subtype is None for et in ontology.entity_types):
        # Nothing to add: the default mapping already emits base POLE+O types.
        return None

    from neo4j_agent_memory.ontology.compile import spacy_label_map

    mapping = spacy_label_map(ontology)
    if not mapping:
        return None

    type_mapping = {label: pole for label, (pole, _) in mapping.items()}
    subtype_mapping = {
        label: subtype for label, (_, subtype) in mapping.items() if subtype is not None
    }
    return type_mapping, subtype_mapping


def _get_type_mapping_for_schema(
    schema_config: SchemaConfig | None = None,
    *,
    ontology: OntologyDocument | None = None,
) -> dict[str, str] | None:
    """The spaCy label -> POLE+O type mapping for this configuration.

    Args:
        schema_config: Schema configuration (kept for signature compatibility;
            entity typing now comes from the ontology).
        ontology: The effective ontology, when one is configured.

    Returns:
        The type mapping, or ``None`` to keep spaCy's defaults.
    """
    mappings = _spacy_label_mappings(ontology)
    return None if mappings is None else mappings[0]


def _convert_merge_strategy(config_strategy: ConfigMergeStrategy) -> MergeStrategy:
    """Convert config merge strategy to pipeline merge strategy."""
    return MergeStrategy(config_strategy.value)


def resolve_ontology(
    extraction_config: ExtractionConfig,
    ontology: OntologyDocument | None = None,
) -> OntologyDocument:
    """The one ontology every stage of a configuration extracts against.

    Precedence: the named domain template in
    ``extraction_config.gliner_schema``, then the explicit ``ontology``
    argument, then the built-in POLE+O ontology. An unknown template name
    warns and falls through.

    Resolving this *once* is load-bearing. The GLiNER2.5 stage decodes
    relations against whatever schema it was compiled with, and
    :class:`~neo4j_agent_memory.extraction.pipeline.ExtractionPipeline`
    validates the merged result with ``mode="drop"``. Handing the two a
    different ontology drops every relation the stage just decoded.

    Args:
        extraction_config: Extraction configuration.
        ontology: The caller's ontology, when there is one.

    Returns:
        The effective ontology.
    """
    if extraction_config.gliner_schema:
        from neo4j_agent_memory.ontology.builtin import get_template

        try:
            return get_template(extraction_config.gliner_schema)
        except ValueError as e:
            logger.warning(f"Invalid GLiNER schema: {e}. Using the default ontology.")

    if ontology is not None:
        return ontology

    from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

    return POLEO_ONTOLOGY


def create_spacy_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    *,
    ontology: OntologyDocument | None = None,
) -> EntityExtractor:
    """Create a spaCy entity extractor.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration for type mapping
        ontology: Optional ontology. When it declares subtypes, spaCy's labels
            are remapped onto them; when it declares base POLE+O labels only,
            spaCy keeps its own default mapping.

    Returns:
        SpacyEntityExtractor instance

    Raises:
        ImportError: If spaCy is not installed
    """
    from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

    mappings = _spacy_label_mappings(ontology)
    type_mapping = mappings[0] if mappings else None
    subtype_mapping = mappings[1] if mappings else None

    return SpacyEntityExtractor(
        model=extraction_config.spacy_model,
        type_mapping=type_mapping,
        subtype_mapping=subtype_mapping,
        default_confidence=extraction_config.spacy_confidence,
    )


def create_gliner_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    *,
    ontology: OntologyDocument | None = None,
) -> EntityExtractor:
    """Create a GLiNER2.5 entity and relation extractor.

    The schema the model decodes against comes from, in order: the named
    domain template in ``extraction_config.gliner_schema``, the ``ontology``
    argument, and otherwise the built-in POLE+O ontology.

    Args:
        extraction_config: Extraction configuration.
        schema_config: Optional schema configuration (unused; entity typing
            now comes from the ontology).
        ontology: The ontology to extract against.

    Returns:
        A :class:`~neo4j_agent_memory.extraction.gliner2_extractor.GLiNER2Extractor`.

    Raises:
        ImportError: If ``gliner2`` is not installed.
    """
    from neo4j_agent_memory.extraction.gliner2_extractor import GLiNER2Extractor

    resolved = resolve_ontology(extraction_config, ontology)

    return GLiNER2Extractor(
        model=extraction_config.gliner_model,
        ontology=resolved,
        threshold=extraction_config.gliner_threshold,
        relation_threshold=extraction_config.gliner_relation_threshold,
        device=extraction_config.gliner_device,
        quantize=extraction_config.gliner_quantize,
        compile=extraction_config.gliner_compile,
        max_words=extraction_config.gliner_max_words,
        chunk_overlap=extraction_config.gliner_chunk_overlap,
        overlap_policy=extraction_config.gliner_overlap_policy,
        extract_relations=extraction_config.extract_relations,
        extract_attributes=extraction_config.gliner_extract_attributes,
    )


def create_llm_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
    *,
    ontology: OntologyDocument | None = None,
) -> EntityExtractor:
    """Create an LLM entity extractor.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration for entity types
        ontology: Optional ontology. When set, the extraction prompt carries
            the ontology's annotation guidelines and relationship catalogue
            instead of the hardcoded POLE+O block.
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
        "ontology": ontology,
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
        # from_provider is @overloaded: kind="llm" is typed to return LLMProvider.
        llm_provider = from_provider(model_id, kind="llm", **kwargs)
        return LLMEntityExtractor(provider=llm_provider, **common_kwargs)

    # No llm_config: let LLMEntityExtractor build its own default provider
    # from extraction_config.llm_model.
    return LLMEntityExtractor(model=extraction_config.llm_model, **common_kwargs)


def create_extraction_pipeline(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
    *,
    ontology: OntologyDocument | None = None,
) -> ExtractionPipeline:
    """Create a multi-stage extraction pipeline based on configuration.

    The pipeline combines multiple extractors (spaCy, GLiNER, LLM) according
    to the configuration settings. Stages are run in order and results are
    merged according to the specified strategy.

    Default pipeline order:
    1. spaCy - Fast statistical NER for common entities
    2. GLiNER2.5 - Joint entity and relation decoding against the ontology
    3. LLM - Fallback for complex cases and relation extraction

    Every stage — and the pipeline's own relation validation — is given the
    *same* ontology, resolved once by :func:`resolve_ontology`.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration
        llm_config: Optional LLM configuration
        ontology: Optional ontology for the GLiNER2.5 stage

    Returns:
        ExtractionPipeline instance
    """
    stages: list[EntityExtractor] = []
    resolved = resolve_ontology(extraction_config, ontology)

    # Stage 1: spaCy for fast initial extraction
    if extraction_config.enable_spacy:
        try:
            spacy_extractor = create_spacy_extractor(
                extraction_config, schema_config, ontology=resolved
            )
            stages.append(spacy_extractor)
            logger.info("Added spaCy extractor to pipeline")
        except ImportError as e:
            logger.warning(f"spaCy not available, skipping: {e}")

    # Stage 2: GLiNER2.5 for joint entity + relation decoding.
    # The availability check has to happen here: GLiNER2Extractor's constructor
    # imports nothing, so adding the stage would succeed and every later
    # extract() would fail with a raw ModuleNotFoundError instead.
    if extraction_config.enable_gliner:
        from neo4j_agent_memory.extraction.gliner2_extractor import is_gliner2_available

        if not is_gliner2_available():
            logger.warning(
                'GLiNER2.5 not available, skipping: install with pip install "neo4j-agent-memory[gliner2]"'
            )
        else:
            gliner_extractor = create_gliner_extractor(
                extraction_config, schema_config, ontology=resolved
            )
            stages.append(gliner_extractor)
            logger.info("Added GLiNER2.5 extractor to pipeline")

    # Stage 3: LLM fallback for complex cases and relations
    if extraction_config.enable_llm_fallback:
        try:
            llm_extractor = create_llm_extractor(
                extraction_config, schema_config, llm_config, ontology=resolved
            )
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
        ontology=resolved,
        min_confidence=extraction_config.confidence_threshold,
    )


def create_extractor(
    extraction_config: ExtractionConfig,
    schema_config: SchemaConfig | None = None,
    llm_config: Any = None,
    *,
    ontology: OntologyDocument | None = None,
) -> EntityExtractor:
    """Create an entity extractor based on configuration.

    This is the main factory function that creates the appropriate
    extractor based on the extractor_type setting.

    Args:
        extraction_config: Extraction configuration
        schema_config: Optional schema configuration
        llm_config: Optional LLM configuration for LLM-based extraction
        ontology: Optional ontology threaded to the GLiNER2.5 stage

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
        return create_spacy_extractor(extraction_config, schema_config, ontology=ontology)

    elif extraction_config.extractor_type == ExtractorType.GLINER:
        return create_gliner_extractor(extraction_config, schema_config, ontology=ontology)

    elif extraction_config.extractor_type == ExtractorType.LLM:
        return create_llm_extractor(extraction_config, schema_config, llm_config, ontology=ontology)

    elif extraction_config.extractor_type == ExtractorType.PIPELINE:
        return create_extraction_pipeline(
            extraction_config, schema_config, llm_config, ontology=ontology
        )

    # ExtractorType is exhausted above. This fail-fast guard means a future enum
    # value added without a branch here raises loudly instead of being silently
    # routed to one of the existing extractors.
    raise ValueError(f"Unhandled extractor type: {extraction_config.extractor_type}")


class ExtractorBuilder:
    """Builder for creating custom extraction configurations.

    Provides a fluent interface for constructing extractors with
    specific settings.

    Example:
        ```python
        # Basic pipeline with GLiNER2.5
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

        # Using your own ontology (entity labels *and* typed relationships).
        # with_ontology() configures the schema; the stages are still chosen
        # explicitly, so an LLM-only run against an ontology is expressible.
        extractor = ExtractorBuilder().with_gliner().with_ontology(doc).build()
        ```
    """

    def __init__(self) -> None:
        """Initialize builder with default settings."""
        from neo4j_agent_memory.extraction.gliner2_extractor import DEFAULT_GLINER2_5_MODEL

        self._enable_spacy = False
        self._enable_gliner = False
        self._enable_llm = False
        self._spacy_model = "en_core_web_sm"
        self._gliner_model = DEFAULT_GLINER2_5_MODEL
        self._gliner_threshold = 0.5
        self._gliner_relation_threshold: float | None = None
        self._gliner_device = "cpu"
        self._gliner_schema: str | None = None
        self._gliner_entity_labels: list[str] | None = None
        self._ontology: OntologyDocument | None = None
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
        relation_threshold: float | None = None,
    ) -> ExtractorBuilder:
        """Add the GLiNER2.5 extractor to the pipeline.

        Args:
            model: GLiNER2.5 checkpoint (default: ``fastino/gliner2.5-base-v1``).
            threshold: Entity confidence threshold.
            device: Inference device (``cpu`` / ``cuda`` / ``mps``).
            model_name: Alias for ``model`` (kept for compatibility with the CLI
                and older call sites).
            relation_threshold: Confidence floor for decoded relations.
        """
        from neo4j_agent_memory.extraction.gliner2_extractor import DEFAULT_GLINER2_5_MODEL

        self._enable_gliner = True
        self._gliner_model = model_name or model or DEFAULT_GLINER2_5_MODEL
        self._gliner_threshold = threshold
        self._gliner_relation_threshold = relation_threshold
        self._gliner_device = device
        return self

    def with_gliner_schema(
        self,
        schema_name: str,
        model: str | None = None,
        threshold: float = 0.5,
        device: str = "cpu",
        *,
        relation_threshold: float | None = None,
    ) -> ExtractorBuilder:
        """Add the GLiNER2.5 extractor with a built-in domain template.

        The templates carry entity descriptions (annotation guidelines), and
        ``poleo``/``podcast``/``news`` also carry typed relationships.

        Args:
            schema_name: Name of the schema (poleo, podcast, news, scientific,
                        business, entertainment, medical, legal)
            model: GLiNER2.5 checkpoint (default: ``fastino/gliner2.5-base-v1``)
            threshold: Entity confidence threshold
            device: Device to run on (cpu, cuda, mps)
            relation_threshold: Confidence floor for decoded relations
        """
        from neo4j_agent_memory.extraction.gliner2_extractor import DEFAULT_GLINER2_5_MODEL

        self._enable_gliner = True
        self._gliner_model = model or DEFAULT_GLINER2_5_MODEL
        self._gliner_threshold = threshold
        self._gliner_relation_threshold = relation_threshold
        self._gliner_device = device
        self._gliner_schema = schema_name
        return self

    def with_ontology(self, ontology: OntologyDocument) -> ExtractorBuilder:
        """Extract against an explicit ontology document.

        Configures the ontology every stage works from — the JointIE schema for
        GLiNER2.5, the annotation guidelines for the LLM prompt, the endpoint
        typing the pipeline validates against — and nothing else. Which stages
        run is chosen by :meth:`with_gliner`, :meth:`with_llm_fallback` and
        :meth:`with_spacy`, exactly as with :meth:`with_schema`: naming an
        ontology used to imply a GLiNER2.5 stage, which quietly turned
        ``--extractor llm --ontology x.yaml`` into a GLiNER2.5 run.

        Args:
            ontology: The ontology to compile into a JointIE schema.
        """
        self._ontology = ontology
        type_names = sorted({et.pole_type.upper() for et in ontology.entity_types})
        if type_names:
            self._entity_types = type_names
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

        Two things happen: GLiNER2.5 decodes with this as its entity threshold,
        and — when the build produces a pipeline — entities and relations below
        it are dropped from the merged result, so a stage that cannot be told a
        threshold (spaCy, an LLM) is held to the same floor.

        Args:
            threshold: Confidence floor in ``[0.0, 1.0]``.

        Raises:
            ValueError: If ``threshold`` is outside ``[0.0, 1.0]``.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"confidence_threshold must be in [0.0, 1.0], got {threshold!r}")
        self._confidence_threshold = threshold
        self._gliner_threshold = threshold
        return self

    def with_schema(self, schema_config: EntitySchemaConfig) -> ExtractorBuilder:
        """Configure extraction from an :class:`EntitySchemaConfig`.

        The schema is converted to an ontology (so subtypes reach GLiNER2.5 as
        labels, and any declared relation types reach it as typed
        relationships), and its type names still become the flat type set the
        LLM stage works from.

        Args:
            schema_config: Entity schema configuration. Each
                :class:`EntityTypeConfig` contributes its ``name`` to the
                extractor's type set.
        """
        self._ontology = schema_config.to_ontology()
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

        ontology = self._ontology
        if self._gliner_schema:
            from neo4j_agent_memory.ontology.builtin import get_template

            ontology = get_template(self._gliner_schema)

        if self._enable_spacy:
            from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

            mappings = _spacy_label_mappings(ontology)
            stages.append(
                SpacyEntityExtractor(
                    model=self._spacy_model,
                    type_mapping=mappings[0] if mappings else None,
                    subtype_mapping=mappings[1] if mappings else None,
                )
            )

        if self._enable_gliner:
            from neo4j_agent_memory.extraction.gliner2_extractor import GLiNER2Extractor

            stages.append(
                GLiNER2Extractor(
                    model=self._gliner_model,
                    ontology=ontology,
                    entity_labels=None if ontology else self._gliner_entity_labels,
                    threshold=self._gliner_threshold,
                    relation_threshold=self._gliner_relation_threshold,
                    device=self._gliner_device,
                    extract_relations=self._extract_relations,
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
                    ontology=ontology,
                )
            )

        if not stages:
            return NoOpExtractor()

        if len(stages) == 1:
            return stages[0]

        # ``ontology`` is None unless one was named: unlike the config factory
        # the builder has no implicit POLE+O default, so an un-configured build
        # keeps every relation its stages found.
        return ExtractionPipeline(
            stages=stages,
            merge_strategy=self._merge_strategy,
            ontology=ontology,
            min_confidence=self._confidence_threshold,
        )
