"""Smoke tests for the no_llm example (T7).

Validates structure, imports, and the configuration produced by
``examples/no_llm/main.py``. This test does NOT require Neo4j and runs in
CI's ``example-tests-quick`` job.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
NO_LLM_DIR = EXAMPLES_DIR / "no_llm"


class TestNoLLMExample:
    def test_example_file_exists(self):
        assert (NO_LLM_DIR / "main.py").exists()
        assert (NO_LLM_DIR / "README.md").exists()

    def test_example_compiles(self):
        """The example must be syntactically valid Python."""
        source = (NO_LLM_DIR / "main.py").read_text()
        ast.parse(source)

    def test_example_imports_work(self):
        """All imports referenced by the example must resolve."""
        from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
        from neo4j_agent_memory.config.settings import (
            EmbeddingConfig,
            EmbeddingProvider,
            ExtractionConfig,
            ExtractorType,
        )

        assert MemoryClient is not None
        assert MemorySettings is not None
        assert Neo4jConfig is not None
        assert EmbeddingConfig is not None
        assert EmbeddingProvider.SENTENCE_TRANSFORMERS is not None
        assert ExtractionConfig is not None
        assert ExtractorType.PIPELINE is not None

    def test_build_settings_produces_llm_none(self, monkeypatch):
        """The example's build_settings() must produce a settings object with llm=None."""
        # Avoid relying on a live Neo4j — `build_settings()` only constructs the
        # config object, no connection is made here.
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location("no_llm_example_main", NO_LLM_DIR / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Don't keep the module around in sys.modules across tests.
        try:
            spec.loader.exec_module(module)
            settings = module.build_settings()
            assert settings.llm is None
            assert settings.extraction.enable_llm_fallback is False
            assert settings.embedding.provider.value == "sentence_transformers"
        finally:
            sys.modules.pop("no_llm_example_main", None)

    def test_example_uses_explicit_llm_none(self):
        """Sanity check: the example demonstrates the ``llm=None`` opt-out."""
        source = (NO_LLM_DIR / "main.py").read_text()
        assert "llm=None" in source
        assert "enable_llm_fallback=False" in source

    @pytest.mark.requires_neo4j
    def test_example_runs_end_to_end(self):
        """End-to-end run requires Neo4j; gated behind the requires_neo4j marker."""
        # Intentionally a placeholder; the integration suite covers the
        # round-trip in tests/integration/test_memory_client_no_llm.py. We
        # mark this test so the example-tests CI job (which has Neo4j) can
        # opt in if desired without forcing the quick job to spin one up.
        pytest.skip("Covered by tests/integration/test_memory_client_no_llm.py")
