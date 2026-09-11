"""Smoke tests for the strands-memory-store example.

Structure and import checks stay cheap; the behaviour of ``search()`` /
``add()`` / ``get_tools()`` is asserted by running ``main()`` against Neo4j
rather than by grepping for the call sites.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
STRANDS_MS_DIR = EXAMPLES_DIR / "strands-memory-store"
MODULE_NAME = "strands_ms_example"


def _load(module_name: str = MODULE_NAME) -> ModuleType:
    """Exec the example as a module (callers must pop it from sys.modules)."""
    spec = importlib.util.spec_from_file_location(module_name, STRANDS_MS_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.syntax
class TestStrandsMemoryStoreStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py", ".env.example"]:
            assert (STRANDS_MS_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((STRANDS_MS_DIR / "main.py").read_text(encoding="utf-8"))

    def test_construction_is_config_based(self):
        source = (STRANDS_MS_DIR / "main.py").read_text(encoding="utf-8")
        assert "Neo4jMemoryStoreConfig" in source, "construction is dataclass-config based"


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
        try:
            module = _load()
            assert callable(module.main)
        finally:
            sys.modules.pop(MODULE_NAME, None)

    def test_build_settings_structure(self, monkeypatch):
        """No LLM, and the backend is pinned so MEMORY_API_KEY cannot hijack it."""
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")
        # A stray hosted key must not redirect the demo's writes: preference
        # and fact recall are bolt-only.
        monkeypatch.setenv("MEMORY_API_KEY", "nams_not-a-real-key")

        try:
            module = _load(f"{MODULE_NAME}_settings")
            settings = module.build_settings()
            assert settings.llm is None
            assert settings.backend == "bolt"
        finally:
            sys.modules.pop(f"{MODULE_NAME}_settings", None)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestStrandsMemoryStoreEndToEnd:
    """Runs the demo against Neo4j: recall fan-out, the default write sink, tools."""

    @pytest.fixture
    def module(self, neo4j_env) -> Iterator[ModuleType]:
        pytest.importorskip("sentence_transformers")
        name = f"{MODULE_NAME}_e2e"
        try:
            yield _load(name)
        finally:
            sys.modules.pop(name, None)

    async def test_main_recalls_writes_and_exposes_tools(self, module, capsys):
        await module.main()
        out = capsys.readouterr().out

        # search() fans out over the three long-term kinds.
        assert "search('what does the user prefer?'):" in out
        assert "preference: [preference] ui: Prefers dark mode" in out
        assert "entity: [entity] Acme Corp" in out
        assert "fact: [fact] Acme Corp HEADQUARTERED_IN Berlin" in out

        # add() default sink: a message, the one write path every backend has.
        assert "add(...): {'kind': 'message'" in out

        # Graph-native tools, prefixed by the store's name.
        assert "graph_get_entity_graph" in out
        assert "graph_get_user_preferences" in out
