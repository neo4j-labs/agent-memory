"""Smoke tests for the no_llm example (T7).

Validates structure, imports, and the configuration produced by
``examples/no_llm/main.py``. Everything except
``test_example_runs_end_to_end`` runs without Neo4j, in CI's
``example-tests-quick`` job; the end-to-end case runs ``main()`` against the
Neo4j-backed ``example-tests`` job and asserts the local pipeline actually
wrote entities (the cheapest guard against silent extraction degradation).
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
NO_LLM_DIR = EXAMPLES_DIR / "no_llm"


def _load_example() -> ModuleType:
    """Import ``examples/no_llm/main.py`` under a throwaway module name."""
    spec = importlib.util.spec_from_file_location("no_llm_example_main", NO_LLM_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNoLLMExample:
    def test_example_file_exists(self):
        assert (NO_LLM_DIR / "main.py").exists()
        assert (NO_LLM_DIR / "README.md").exists()

    def test_example_compiles(self):
        """The example must be syntactically valid Python."""
        source = (NO_LLM_DIR / "main.py").read_text(encoding="utf-8")
        ast.parse(source)

    def test_example_imports_work(self):
        """All imports referenced by the example must resolve."""
        from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
        from neo4j_agent_memory.config.settings import (
            ExtractionConfig,
            ExtractorType,
        )
        from neo4j_agent_memory.extraction import create_extractor, is_gliner_available
        from neo4j_agent_memory.schema import TraceOutcome

        assert MemoryClient is not None
        assert MemorySettings is not None
        assert Neo4jConfig is not None
        assert ExtractionConfig is not None
        assert ExtractorType.PIPELINE is not None
        assert callable(create_extractor)
        assert callable(is_gliner_available)
        assert TraceOutcome is not None

    def test_build_settings_produces_llm_none(self, monkeypatch):
        """The example's build_settings() must produce a settings object with llm=None."""
        # v0.3+: the example uses ``from_provider("sentence-transformers/...")``
        # which resolves the adapter eagerly. Skip when the extra is not
        # installed in the test env.
        pytest.importorskip("sentence_transformers")

        # Avoid relying on a live Neo4j — `build_settings()` only constructs the
        # config object, no connection is made here.
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
        # A stray MEMORY_API_KEY must not redirect this example to NAMS.
        monkeypatch.setenv("MEMORY_API_KEY", "nams_not_a_real_key")

        try:
            module = _load_example()
            settings = module.build_settings()
            assert settings.llm is None
            assert settings.extraction.enable_llm_fallback is False
            assert settings.backend == "bolt"
            # v0.3+: embedding is an EmbeddingProvider instance (the
            # resolved adapter), not the legacy EmbeddingConfig enum.
            from neo4j_agent_memory.llm.protocol import EmbeddingProvider

            assert isinstance(settings.embedding, EmbeddingProvider)
            assert "MiniLM" in settings.embedding.model
        finally:
            sys.modules.pop("no_llm_example_main", None)

    def test_example_uses_explicit_llm_none(self):
        """Sanity check: the example demonstrates the ``llm=None`` opt-out."""
        source = (NO_LLM_DIR / "main.py").read_text(encoding="utf-8")
        assert "llm=None" in source
        assert "enable_llm_fallback=False" in source
        assert 'backend="bolt"' in source

    def test_example_exercises_all_three_memory_layers(self):
        """The example must show that every layer works with no LLM."""
        source = (NO_LLM_DIR / "main.py").read_text(encoding="utf-8")
        for call in (
            "short_term.add_message",
            "short_term.clear_session",
            "long_term.add_preference",
            "long_term.add_fact",
            "long_term.add_entity",
            "long_term.search_entities",
            "reasoning.start_trace",
            "reasoning.add_step",
            "reasoning.complete_trace",
            "consolidation.dedupe_entities",
        ):
            assert call in source, f"example no longer calls {call}"

    def test_local_stack_guard_fails_loudly(self, monkeypatch):
        """A missing local model must exit with the install command, not degrade."""
        pytest.importorskip("spacy")
        try:
            module = _load_example()
            monkeypatch.setattr(module, "SPACY_MODEL", "en_core_web_sm_not_installed")
            with pytest.raises(SystemExit) as excinfo:
                module.check_local_stack()
            assert "python -m spacy download" in str(excinfo.value)
        finally:
            sys.modules.pop("no_llm_example_main", None)

    @pytest.mark.requires_neo4j
    def test_example_runs_end_to_end(self, monkeypatch):
        """Run main() against Neo4j and assert the local pipeline wrote entities.

        Runs in the Neo4j-backed ``example-tests`` job (which exports
        ``NEO4J_URI``); skips in ``example-tests-quick``, which has no database.
        """
        pytest.importorskip("sentence_transformers")
        pytest.importorskip("gliner")
        spacy = pytest.importorskip("spacy")
        if not spacy.util.is_package("en_core_web_sm"):
            pytest.skip("en_core_web_sm not installed")

        uri = os.environ.get("NEO4J_URI")
        if not uri:
            pytest.skip("NEO4J_URI not set — the end-to-end run needs a database")
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "test-password")

        from neo4j import GraphDatabase

        from neo4j_agent_memory import MemoryClient

        try:
            driver = GraphDatabase.driver(uri, auth=(username, password))
            driver.verify_connectivity()
            driver.close()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Neo4j not reachable at {uri}: {exc}")

        monkeypatch.setenv("NEO4J_URI", uri)
        monkeypatch.setenv("NEO4J_USERNAME", username)
        monkeypatch.setenv("NEO4J_PASSWORD", password)

        try:
            module = _load_example()
            asyncio.run(module.main())

            async def count_entities() -> int:
                async with MemoryClient(module.build_settings()) as client:
                    rows = await client.query.cypher(
                        "MATCH (e:Entity) WHERE e.name IN $names RETURN count(e) AS n",
                        {"names": ["John Smith", "Acme Corp"]},
                    )
                    return int(rows[0]["n"])

            assert asyncio.run(count_entities()) >= 1
        finally:
            sys.modules.pop("no_llm_example_main", None)
