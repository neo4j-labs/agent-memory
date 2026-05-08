"""Smoke tests for the audit-trail example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
AUDIT_DIR = EXAMPLES_DIR / "audit-trail"


@pytest.mark.syntax
class TestAuditTrailStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "tool_calls.py", "main.py", "queries.cypher"]:
            assert (AUDIT_DIR / filename).exists(), f"Missing: {filename}"

    def test_python_files_compile(self):
        for filename in ["tool_calls.py", "main.py"]:
            ast.parse((AUDIT_DIR / filename).read_text())


@pytest.mark.imports
class TestAuditTrailImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401
        from neo4j_agent_memory.schema.models import (  # noqa: F401
            EntityRef,
            TraceOutcome,
        )

    def test_infer_touched_loads(self):
        spec = importlib.util.spec_from_file_location(
            "audit_trail_tool_calls", AUDIT_DIR / "tool_calls.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            from neo4j_agent_memory.schema.models import EntityRef

            result = module.infer_touched(
                "recommend_team",
                {"client_name": "Anthem"},
                [{"consultant": "Sara"}],
            )
            assert any(r.name == "Anthem" and r.type == "Client" for r in result)
            assert any(r.name == "Sara" and r.type == "PERSON" for r in result)
            assert all(isinstance(r, EntityRef) for r in result)
        finally:
            sys.modules.pop("audit_trail_tool_calls", None)


@pytest.mark.syntax
class TestAuditTrailContent:
    def test_main_uses_on_tool_call_recorded_hook(self):
        source = (AUDIT_DIR / "main.py").read_text()
        assert "on_tool_call_recorded" in source

    def test_main_uses_trace_outcome(self):
        source = (AUDIT_DIR / "main.py").read_text()
        assert "TraceOutcome" in source

    def test_queries_use_touched_edge(self):
        source = (AUDIT_DIR / "queries.cypher").read_text()
        assert ":TOUCHED" in source
