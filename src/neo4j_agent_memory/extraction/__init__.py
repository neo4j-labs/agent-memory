"""Entity and relation extraction from text.

This module provides multiple extraction approaches:
- SpacyEntityExtractor: Fast statistical NER using spaCy
- GLiNEREntityExtractor: Zero-shot NER for custom entity types
- LLMEntityExtractor: LLM-based extraction (most accurate)
- ExtractionPipeline: Multi-stage pipeline combining multiple extractors

The default configuration uses a pipeline approach:
1. spaCy for fast initial extraction of common entities
2. GLiNER for domain-specific entity types
3. LLM as fallback for complex cases and relation extraction
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
)
from neo4j_agent_memory.extraction.factory import (
    ExtractorBuilder,
    create_extraction_pipeline,
    create_extractor,
    create_gliner_extractor,
    create_llm_extractor,
    create_spacy_extractor,
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
    "ExtractedEntity",
    "ExtractedPreference",
    "ExtractedRelation",
    "ExtractionResult",
    "NoOpExtractor",
    # Entity filtering
    "ENTITY_STOPWORDS",
    "is_valid_entity_name",
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
    # GLiNER2 domain schemas
    "DomainSchema",
    "DOMAIN_SCHEMAS",
    "get_schema",
    "list_schemas",
    "is_gliner_available",
    # GLiREL relation extraction
    "is_glirel_available",
    "GLiRELExtractor",
    "GLiRELConfig",
    "GLiNERWithRelationsExtractor",
    "DEFAULT_RELATION_TYPES",
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


# Lazy imports for optional extractors and schema utilities
def __getattr__(name: str) -> Any:
    """Lazy import optional extractors and GLiNER2/GLiREL schemas."""
    if name == "SpacyEntityExtractor":
        from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor

        return SpacyEntityExtractor
    elif name == "GLiNEREntityExtractor":
        from neo4j_agent_memory.extraction.gliner_extractor import GLiNEREntityExtractor

        return GLiNEREntityExtractor
    elif name == "LLMEntityExtractor":
        from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

        return LLMEntityExtractor
    elif name == "DomainSchema":
        from neo4j_agent_memory.extraction.gliner_extractor import DomainSchema

        return DomainSchema
    elif name == "DOMAIN_SCHEMAS":
        from neo4j_agent_memory.extraction.gliner_extractor import DOMAIN_SCHEMAS

        return DOMAIN_SCHEMAS
    elif name == "get_schema":
        from neo4j_agent_memory.extraction.gliner_extractor import get_schema

        return get_schema
    elif name == "list_schemas":
        from neo4j_agent_memory.extraction.gliner_extractor import list_schemas

        return list_schemas
    elif name == "is_gliner_available":
        from neo4j_agent_memory.extraction.gliner_extractor import is_gliner_available

        return is_gliner_available
    # GLiREL relation extraction
    elif name == "is_glirel_available":
        from neo4j_agent_memory.extraction.gliner_extractor import is_glirel_available

        return is_glirel_available
    elif name == "GLiRELExtractor":
        from neo4j_agent_memory.extraction.gliner_extractor import GLiRELExtractor

        return GLiRELExtractor
    elif name == "GLiRELConfig":
        from neo4j_agent_memory.extraction.gliner_extractor import GLiRELConfig

        return GLiRELConfig
    elif name == "GLiNERWithRelationsExtractor":
        from neo4j_agent_memory.extraction.gliner_extractor import GLiNERWithRelationsExtractor

        return GLiNERWithRelationsExtractor
    elif name == "DEFAULT_RELATION_TYPES":
        from neo4j_agent_memory.extraction.gliner_extractor import DEFAULT_RELATION_TYPES

        return DEFAULT_RELATION_TYPES
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
