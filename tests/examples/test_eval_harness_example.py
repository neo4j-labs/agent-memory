"""Smoke tests for the eval-harness example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EVAL_DIR = EXAMPLES_DIR / "eval-harness"


@pytest.mark.syntax
class TestEvalHarnessStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py"]:
            assert (EVAL_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((EVAL_DIR / "main.py").read_text())


@pytest.mark.imports
class TestEvalHarnessImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient  # noqa: F401
        from neo4j_agent_memory.memory.eval import (  # noqa: F401
            AuditCase,
            EvalSuite,
            PreferenceCase,
        )

    def test_main_loads(self, monkeypatch):
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location("eval_harness_main", EVAL_DIR / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            assert callable(module.build_settings)
            assert callable(module.seed)
            assert callable(module.main)
        finally:
            sys.modules.pop("eval_harness_main", None)


@pytest.mark.syntax
class TestEvalHarnessContent:
    def test_main_uses_eval_run(self):
        source = (EVAL_DIR / "main.py").read_text()
        assert "client.eval.run" in source

    def test_main_uses_audit_and_preference_cases(self):
        source = (EVAL_DIR / "main.py").read_text()
        assert "AuditCase" in source
        assert "PreferenceCase" in source
