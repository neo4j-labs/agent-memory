"""Shared configuration for the existing-graph example.

Holds the `MemorySettings` factory plus the constants that describe the
pre-existing domain graph (which labels map to which library entity types,
which property holds each label's display name, and the names the demo
writes about). Every script in this directory imports from here so the
adoption mapping is declared exactly once.

Demonstrates the first end-to-end use of ``SchemaModel.CUSTOM`` against an
existing Neo4j graph: the entity types declared here mirror the labels on
the seed domain graph (``:Person`` / ``:Movie`` / ``:Genre``).

**Bolt only.** ``client.schema.adopt_existing_graph()`` raises
``NotSupportedError`` on the hosted NAMS backend, where the schema is
server-managed.
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
    SchemaModel,
)

# The ontology of the pre-existing graph, expressed the way
# ``adopt_existing_graph`` wants it.
LABEL_TO_TYPE: dict[str, str] = {
    "Person": "PERSON",
    "Movie": "MOVIE",
    "Genre": "GENRE",
}

# :Movie nodes use ``title`` for their display name rather than ``name``.
# This single line is the gotcha real graphs hit most often.
NAME_PROPERTY_PER_LABEL: dict[str, str] = {"Movie": "title"}

# The domain types this example declares to the library (the values of
# LABEL_TO_TYPE, de-duplicated, declaration order preserved). Not POLE+O —
# that is the point.
ENTITY_TYPES: list[str] = list(dict.fromkeys(LABEL_TO_TYPE.values()))

# Labels the seed graph owns. ``seed.py --reset`` scopes its delete to these.
SEED_LABELS: list[str] = sorted(LABEL_TO_TYPE)

# Names the demo messages talk about; the verification step asserts that
# exactly one node exists per name across the whole graph.
DEMO_NAMES: list[str] = ["Arrival", "Bob Singh", "Carol Reyes", "Inception"]

# Every display name the seed graph owns. Tests use this to clean up by name
# rather than by label, so a shared database keeps its other nodes.
SEED_NAMES: list[str] = [
    "Alice Carter",
    "Arrival",
    "Bob Singh",
    "Carol Reyes",
    "Drama",
    "Inception",
    "Science Fiction",
    "The Matrix",
]

SESSION_ID = "existing-graph-demo"


def neo4j_config() -> Neo4jConfig:
    """Neo4j connection settings, defaulting to the repo's test container.

    ``make neo4j-start`` from the repo root brings up Neo4j with the
    credentials used as defaults here (see ``docker-compose.test.yml``).
    """
    return Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
    )


def build_settings() -> MemorySettings:
    """Construct settings configured for the Movies domain.

    Notes
    -----
    * ``schema_config.model = SchemaModel.CUSTOM`` opts out of the default
      POLE+O ontology and declares the domain types instead.
    * ``llm=None`` keeps the example runnable without an OpenAI key, and
      ``enable_llm_fallback=False`` says so explicitly rather than relying
      on a default.
    * The embedding is set via the v0.3 provider-string shorthand; it
      resolves to a local sentence-transformers model — no network calls
      beyond the one-time model download.
    * **Extraction is off.** Automatic extraction cannot link a message
      to an adopted node in v0.5.0: with the extractors' POLE+O mapping a
      ``MOVIE`` mention arrives typed ``OBJECT`` and MERGEs a *second*
      ``:Entity:Object`` node next to the adopted ``:Movie``; and with a
      ``label_mapping`` that preserves ``MOVIE`` the MERGE does find the
      adopted node but no ``MENTIONS`` edge is written, because the link
      step looks up the id it generated rather than the id the MERGE
      returned. ``memory_io.py`` therefore links mentions explicitly
      (``extraction_mode="explicit"``), which lands on the adopted node by
      construction. See the README "Known gaps" section.
    * ``schema_config.strict_types`` governs ``long_term.add_entity()``
      type validation only — extractor output is never validated against
      it. As of v0.5.0 ``MemoryClient`` does not forward
      ``schema_config`` to ``LongTermMemory`` at all, so the flag is
      declarative here; see the README "Known gaps" section.
    """
    extraction = ExtractionConfig(
        extractor_type=ExtractorType.NONE,
        enable_spacy=False,
        enable_gliner=False,
        enable_llm_fallback=False,
        entity_types=ENTITY_TYPES,
    )

    return MemorySettings(
        neo4j=neo4j_config(),
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        schema_config=SchemaConfig(
            model=SchemaModel.CUSTOM,
            entity_types=ENTITY_TYPES,
            strict_types=True,
        ),
        extraction=extraction,
    )


def describe_target() -> str:
    """One-line description of where this run will write (no secrets)."""
    config = neo4j_config()
    return f"Neo4j at {config.uri} as user {config.username!r}"
