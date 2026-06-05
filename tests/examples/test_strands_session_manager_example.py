"""Smoke tests for the strands-session-manager example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
STRANDS_SM_DIR = EXAMPLES_DIR / "strands-session-manager"


@pytest.mark.syntax
class TestStrandsSessionManagerStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py"]:
            assert (STRANDS_SM_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8"))


@pytest.mark.imports
class TestStrandsSessionManagerImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemorySettings  # noqa: F401
        from neo4j_agent_memory.integrations.strands import (  # noqa: F401
            Neo4jRetrievalConfig,
            Neo4jSessionManager,
        )

    def test_example_module_imports(self):
        """The module must be importable and expose a callable main()."""
        spec = importlib.util.spec_from_file_location(
            "strands_sm_example", STRANDS_SM_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # imports must succeed; main() not called
            assert callable(module.main)
        finally:
            sys.modules.pop("strands_sm_example", None)

    def test_build_settings_structure(self, monkeypatch):
        """build_settings() must produce a MemorySettings with no LLM."""
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location(
            "strands_sm_example_settings", STRANDS_SM_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            settings = module.build_settings()
            # No LLM — runs without API keys.
            assert settings.llm is None
        finally:
            sys.modules.pop("strands_sm_example_settings", None)


@pytest.mark.syntax
class TestStrandsSessionManagerContent:
    def test_main_uses_neo4j_session_manager(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "Neo4jSessionManager" in source

    def test_main_uses_retrieval_config(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "Neo4jRetrievalConfig" in source

    def test_main_calls_initialize(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "manager_a.initialize(" in source
        assert "manager_b.initialize(" in source

    def test_main_calls_append_message(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "manager_a.append_message(" in source
        assert "manager_b.append_message(" in source

    def test_main_calls_inject_context(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "manager_b._inject_context(" in source

    def test_three_sessions_present(self):
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        # Three distinct manager variables for the three phases
        assert "manager_a" in source
        assert "manager_b" in source
        assert "manager_c" in source
