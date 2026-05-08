"""Smoke tests for the enrichment_example.py example."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_FILE = EXAMPLES_DIR / "enrichment_example.py"


@pytest.mark.syntax
class TestEnrichmentExampleStructure:
    def test_example_file_exists(self):
        assert EXAMPLE_FILE.exists(), f"Missing: {EXAMPLE_FILE}"

    def test_example_compiles(self):
        ast.parse(EXAMPLE_FILE.read_text())


@pytest.mark.imports
class TestEnrichmentExampleImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import (  # noqa: F401
            EmbeddingConfig,
            EmbeddingProvider,
            MemoryClient,
            MemorySettings,
            Neo4jConfig,
        )
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            EnrichmentConfig,
            EnrichmentProvider,
        )
        from neo4j_agent_memory.enrichment import (  # noqa: F401
            CachedEnrichmentProvider,
            CompositeEnrichmentProvider,
            DiffbotProvider,
            EnrichmentStatus,
            WikimediaProvider,
        )


@pytest.mark.syntax
class TestEnrichmentExampleContent:
    """Guards against the kinds of API drift that broke this example before.

    The original `get_entity(entity.id)` call was a phantom — there is no
    public `get_entity()` on `LongTermMemory`. The example now uses
    `get_entity_by_name`, which is the actual public API.
    """

    def test_uses_tuple_unpacking_for_add_entity(self):
        source = EXAMPLE_FILE.read_text()
        assert "entity, dedup_result = await client.long_term.add_entity(" in source, (
            "Example must demonstrate the v0.1.1+ tuple return from add_entity"
        )

    def test_does_not_call_phantom_get_entity(self):
        source = EXAMPLE_FILE.read_text()
        assert ".long_term.get_entity(" not in source, (
            "long_term.get_entity() does not exist; use get_entity_by_name()"
        )

    def test_uses_get_entity_by_name(self):
        source = EXAMPLE_FILE.read_text()
        assert "get_entity_by_name(" in source

    def test_uses_enrichment_config(self):
        source = EXAMPLE_FILE.read_text()
        assert "EnrichmentConfig(" in source
        assert "EnrichmentProvider.WIKIMEDIA" in source
