"""Smoke tests for the existing-graph example.

Validates structure, imports, and the configuration produced by
``examples/existing-graph/memory_settings.py``. This test does NOT require
Neo4j and runs in CI's ``example-tests-quick`` job.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXISTING_GRAPH_DIR = EXAMPLES_DIR / "existing-graph"


@pytest.mark.syntax
class TestExistingGraphExampleStructure:
    def test_required_files_exist(self):
        for filename in [
            "README.md",
            "seed_domain_graph.cypher",
            "memory_settings.py",
            "adopt.py",
            "memory_io.py",
            "run.sh",
        ]:
            assert (EXISTING_GRAPH_DIR / filename).exists(), f"Missing example file: {filename}"

    def test_python_files_compile(self):
        for filename in ["memory_settings.py", "adopt.py", "memory_io.py"]:
            source = (EXISTING_GRAPH_DIR / filename).read_text()
            ast.parse(source)


@pytest.mark.imports
class TestExistingGraphExampleImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig  # noqa: F401
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            EmbeddingConfig,
            EmbeddingProvider,
            ExtractionConfig,
            ExtractorType,
            SchemaConfig,
            SchemaModel,
        )
        from neo4j_agent_memory.schema.models import (  # noqa: F401
            AdoptionLabelReport,
            AdoptionReport,
        )

    def test_build_settings_uses_custom_schema(self, monkeypatch):
        """The example's build_settings() must produce a CUSTOM-schema config."""
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location(
            "existing_graph_memory_settings",
            EXISTING_GRAPH_DIR / "memory_settings.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            settings = module.build_settings()

            from neo4j_agent_memory.config.settings import SchemaModel

            assert settings.schema_config.model is SchemaModel.CUSTOM
            assert settings.schema_config.entity_types == [
                "PERSON",
                "MOVIE",
                "GENRE",
            ]
            assert settings.schema_config.strict_types is True
            # No LLM — runs without API keys.
            assert settings.llm is None
            assert settings.extraction.enable_llm_fallback is False
        finally:
            sys.modules.pop("existing_graph_memory_settings", None)


@pytest.mark.syntax
class TestExistingGraphExampleContent:
    """Sanity-check that the example demonstrates what its README claims."""

    def test_adopt_calls_adopt_existing_graph(self):
        source = (EXISTING_GRAPH_DIR / "adopt.py").read_text()
        assert "adopt_existing_graph" in source
        assert "label_to_type" in source

    def test_seed_creates_pre_library_nodes(self):
        """Seed graph should NOT use :Entity — that's the whole point of adopt."""
        source = (EXISTING_GRAPH_DIR / "seed_domain_graph.cypher").read_text()
        # Strip comment lines before checking for :Entity in actual Cypher.
        cypher = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("//")
        )
        assert ":Person" in cypher and ":Movie" in cypher
        # No ``:Entity`` super-label in the seed — adoption attaches it.
        assert ":Entity" not in cypher
