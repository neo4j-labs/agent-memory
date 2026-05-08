"""MemorySettings for the existing-graph example.

Demonstrates the first end-to-end use of ``SchemaModel.CUSTOM`` against an
existing Neo4j graph: the entity types we declare here mirror the labels
on the seed domain graph (Person, Movie, Genre).
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
    SchemaModel,
)


def build_settings() -> MemorySettings:
    """Construct settings configured for the Movies domain.

    Notes
    -----
    * ``schema_config.model = SchemaModel.CUSTOM`` opts out of the default
      POLE+O ontology and uses our domain types instead.
    * ``llm=None`` keeps the example runnable without an OpenAI key — the
      adoption helper does not need an LLM, and we set
      ``enable_llm_fallback=False`` so extraction only runs spaCy/GLiNER.
    * ``ExtractionConfig.entity_types`` mirrors the schema types so that
      anything extracted from messages is constrained to the same set
      the domain graph uses.
    """
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        schema_config=SchemaConfig(
            model=SchemaModel.CUSTOM,
            entity_types=["PERSON", "MOVIE", "GENRE"],
            strict_types=True,
        ),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=True,
            enable_gliner=True,
            enable_llm_fallback=False,
            entity_types=["PERSON", "MOVIE", "GENRE"],
        ),
    )
