"""Smoke tests for the strands-memory-store example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
STRANDS_MS_DIR = EXAMPLES_DIR / "strands-memory-store"


@pytest.mark.syntax
class TestStrandsMemoryStoreStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py"]:
            assert (STRANDS_MS_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((STRANDS_MS_DIR / "main.py").read_text(encoding="utf-8"))


@pytest.mark.imports
class TestStrandsMemoryStoreImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemorySettings  # noqa: F401
        from neo4j_agent_memory.integrations.strands import (  # noqa: F401
            Neo4jMemoryStore,
            Neo4jMemoryStoreConfig,
        )

    def test_example_module_imports(self):
        """The module must be importable and expose a callable main()."""
        spec = importlib.util.spec_from_file_location(
            "strands_ms_example", STRANDS_MS_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # imports must succeed; main() not called
            assert callable(module.main)
        finally:
            sys.modules.pop("strands_ms_example", None)

    def test_build_settings_structure(self, monkeypatch):
        """build_settings() must produce a MemorySettings with no LLM."""
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location(
            "strands_ms_example_settings", STRANDS_MS_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            settings = module.build_settings()
            # No LLM — runs without API keys.
            assert settings.llm is None
        finally:
            sys.modules.pop("strands_ms_example_settings", None)


@pytest.mark.syntax
class TestStrandsMemoryStoreContent:
    def test_uses_the_public_store_api(self):
        source = (STRANDS_MS_DIR / "main.py").read_text(encoding="utf-8")
        assert (
            "from neo4j_agent_memory.integrations.strands import" in source
            and "Neo4jMemoryStore" in source
        )
        assert "Neo4jMemoryStoreConfig" in source, "construction is dataclass-config based"
        assert "llm=None" in source, "the example must run without an API key"

    def test_calls_search_add_and_get_tools(self):
        source = (STRANDS_MS_DIR / "main.py").read_text(encoding="utf-8")
        assert "store.search(" in source
        assert "store.add(" in source
        assert "store.get_tools(" in source
        assert "store.initialize(" in source
