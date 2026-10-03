"""Entity and relation extraction from text.

This module provides multiple extraction approaches:

- ``SpacyEntityExtractor``: fast statistical NER for common entity types
- ``GLiNER2Extractor``: GLiNER2.5 joint entity + relation decoding against an
  ontology, locally and without an LLM call
- ``LLMEntityExtractor``: LLM-based extraction (most flexible)
- ``ExtractionPipeline``: multi-stage pipeline combining several extractors

The default configuration uses a pipeline:

1. spaCy for fast initial extraction of common entities
2. GLiNER2.5 for the ontology's labels and typed relationships
3. LLM as a fallback for complex cases and free-form relations

GLiNER v1 and GLiREL were removed in 0.7; see :class:`GLiNER2Extractor`.
"""

from typing import Any

from neo4j_agent_memory.extraction.base import (
    ENTITY_STOPWORDS,
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
    NoOpExtractor,
    is_valid_entity_name,
    normalize_relation_type,
)
from neo4j_agent_memory.extraction.domain_schemas import (
    DEFAULT_POLEO_LABELS,
    DOMAIN_SCHEMAS,
    DomainSchema,
    get_schema,
    list_schemas,
)
from neo4j_agent_memory.extraction.factory import (
    ExtractorBuilder,
    create_extraction_pipeline,
    create_extractor,
    create_gliner_extractor,
    create_llm_extractor,
    create_spacy_extractor,
    resolve_ontology,
)
from neo4j_agent_memory.extraction.label_mapping import (
    DEFAULT_LABEL_MAPPING,
    map_label_to_poleo,
)
from neo4j_agent_memory.extraction.pipeline import (
    BatchExtractionResult,
    BatchItemResult,
    ConditionalPipeline,
    ExtractionPipeline,
    ExtractorStage,
    MergeStrategy,
    PipelineResult,
    ProgressCallback,
    StageResult,
    merge_extraction_results,
)
from neo4j_agent_memory.extraction.streaming import (
    ChunkInfo,
    StreamingChunkResult,
    StreamingExtractionResult,
    StreamingExtractionStats,
    StreamingExtractor,
    chunk_text_by_chars,
    chunk_text_by_tokens,
    create_streaming_extractor,
)

__all__ = [
    # Base classes
    "EntityExtractor",
    "RemovedExtractorError",
    "ExtractedEntity",
    "ExtractedPreference",
    "ExtractedRelation",
    "ExtractionResult",
    "NoOpExtractor",
    # Entity filtering
    "ENTITY_STOPWORDS",
    "is_valid_entity_name",
    "normalize_relation_type",
    # Pipeline
    "ExtractionPipeline",
    "ConditionalPipeline",
    "ExtractorStage",
    "MergeStrategy",
    "PipelineResult",
    "StageResult",
    "BatchExtractionResult",
    "BatchItemResult",
    "ProgressCallback",
    "merge_extraction_results",
    # Factory
    "ExtractorBuilder",
    "create_extractor",
    "create_extraction_pipeline",
    "create_gliner_extractor",
    "create_llm_extractor",
    "create_spacy_extractor",
    "resolve_ontology",
    # Label mapping (extractor label -> POLE+O)
    "DEFAULT_LABEL_MAPPING",
    "map_label_to_poleo",
    # Domain schemas (entity labels with descriptions)
    "DomainSchema",
    "DOMAIN_SCHEMAS",
    "DEFAULT_POLEO_LABELS",
    "get_schema",
    "list_schemas",
    # GLiNER2.5
    "GLiNER2Extractor",
    "DEFAULT_GLINER2_5_MODEL",
    "is_gliner2_available",
    # Streaming extraction
    "StreamingExtractor",
    "StreamingExtractionResult",
    "StreamingExtractionStats",
    "StreamingChunkResult",
    "ChunkInfo",
    "chunk_text_by_chars",
    "chunk_text_by_tokens",
    "create_streaming_extractor",
]

# Names removed in 0.7 with the GLiNER v1 / GLiREL stack, mapped to the hint
# each one gets when something still imports it.
_REMOVED: dict[str, str] = {
    "GLiNEREntityExtractor": "use GLiNER2Extractor",
    "GLiNERConfig": "GLiNER2Extractor takes its settings as constructor arguments",
    "GLiNERWithRelationsExtractor": (
        "use GLiNER2Extractor — it decodes entities and relations in one pass"
    ),
    "GLiRELExtractor": ("use GLiNER2Extractor — relations come from the same pass as the entities"),
    "GLiRELConfig": "use GLiNER2Extractor",
    "DEFAULT_RELATION_TYPES": (
        "declare relationships on an OntologyDocument "
        "(neo4j_agent_memory.ontology.POLEO_ONTOLOGY is the default)"
    ),
    "is_gliner_available": "use is_gliner2_available",
    "is_glirel_available": "use is_gliner2_available",
}


class RemovedExtractorError(ImportError):
    """A name removed in 0.7, raised with the migration hint.

    An :class:`ImportError` subclass on purpose. The obvious alternative — also
    subclassing :class:`AttributeError`, so that
    ``hasattr(module, "is_gliner_available")`` answered ``False`` — is not
    available: CPython 3.10+ gave ``AttributeError`` its own instance layout
    (for the "did you mean" hints), so ``class E(ImportError, AttributeError)``
    fails at class-creation time with "multiple bases have instance lay-out
    conflict". And raising a plain ``AttributeError`` instead would not help
    either: on the ``from ... import name`` path the interpreter discards it
    and substitutes its own bare ``ImportError: cannot import name``, losing
    the hint that is the whole point of this class.
    """


# Lazy imports for optional extractors, plus clear errors for removed names.
def __getattr__(name: str) -> Any:
    """Lazily import optional extractors; explain the names removed in 0.7.

    A removed name raises :class:`RemovedExtractorError`, an
    :class:`ImportError`. That means ``hasattr(extraction, "<removed name>")``
    *raises* rather than returning ``False`` — by design, and unavoidable here
    (see :class:`RemovedExtractorError`). Probe with
    :func:`~neo4j_agent_memory.extraction.is_gliner2_available` or a
    ``try``/``except ImportError`` around the import, not with ``hasattr``.
    """
    if name == "SpacyEntityExtractor":
        from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

        return SpacyEntityExtractor
    elif name == "LLMEntityExtractor":
        from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

        return LLMEntityExtractor
    elif name == "GLiNER2Extractor":
        from neo4j_agent_memory.extraction.gliner2_extractor import GLiNER2Extractor

        return GLiNER2Extractor
    elif name == "is_gliner2_available":
        from neo4j_agent_memory.extraction.gliner2_extractor import is_gliner2_available

        return is_gliner2_available
    elif name == "DEFAULT_GLINER2_5_MODEL":
        from neo4j_agent_memory.extraction.gliner2_extractor import DEFAULT_GLINER2_5_MODEL

        return DEFAULT_GLINER2_5_MODEL
    elif name in _REMOVED:
        raise RemovedExtractorError(
            f"{name} was removed in 0.7 along with the GLiNER v1 and GLiREL stack; "
            f'{_REMOVED[name]}. Install it with: pip install "neo4j-agent-memory[gliner2]"'
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
