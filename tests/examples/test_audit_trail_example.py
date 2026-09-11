"""Smoke tests for the audit-trail example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
AUDIT_DIR = EXAMPLES_DIR / "audit-trail"
HOWTO = REPO_ROOT / "docs/modules/ROOT/pages/how-to/audit-reasoning.adoc"


def _load(module_name: str, filename: str):
    """Import one of the example modules by file path."""
    spec = importlib.util.spec_from_file_location(module_name, AUDIT_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.syntax
class TestAuditTrailStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "tool_calls.py", "main.py", "queries.cypher"]:
            assert (AUDIT_DIR / filename).exists(), f"Missing: {filename}"

    def test_python_files_compile(self):
        for filename in ["tool_calls.py", "main.py"]:
            ast.parse((AUDIT_DIR / filename).read_text(encoding="utf-8"))

    def test_no_relative_imports(self):
        """``main.py`` must be runnable as a script, not only as a package."""
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "from .tool_calls" not in source
        assert "from tool_calls import infer_touched" in source

    def test_documented_run_command_matches_the_script_path(self):
        for filename in ["main.py", "README.md"]:
            source = (AUDIT_DIR / filename).read_text(encoding="utf-8")
            assert "uv run python examples/audit-trail/main.py" in source, filename

    def test_entity_ref_types_are_uppercase(self):
        """A mixed-case ``type`` creates an :Entity node add_entity can't match."""
        source = (AUDIT_DIR / "tool_calls.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "EntityRef":
                continue
            for kw in node.keywords:
                if kw.arg == "type" and isinstance(kw.value, ast.Constant):
                    assert kw.value.value == kw.value.value.upper(), kw.value.value
                    found += 1
        assert found >= 3, "expected the EntityRef type literals to be checked"


@pytest.mark.imports
class TestAuditTrailImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401
        from neo4j_agent_memory.memory.reasoning import (  # noqa: F401
            HookContext,
            ToolCall,
            ToolCallStatus,
        )
        from neo4j_agent_memory.schema.models import (  # noqa: F401
            EntityRef,
            TraceOutcome,
        )

    def test_infer_touched_loads(self):
        try:
            module = _load("audit_trail_tool_calls", "tool_calls.py")
            from neo4j_agent_memory.schema.models import EntityRef

            result = module.infer_touched(
                "recommend_team",
                {"client_name": "Anthem"},
                [{"consultant": "Sara"}],
            )
            assert any(r.name == "Anthem" and r.type == "CLIENT" for r in result)
            assert any(r.name == "Sara" and r.type == "PERSON" for r in result)
            assert all(isinstance(r, EntityRef) for r in result)

            # Unknown tools and missing arguments yield no edges.
            assert module.infer_touched("unknown_tool", {}, None) == []
            assert module.infer_touched("find_consultants", {}, None) == []
            assert module.infer_touched("recommend_team", {}, "not a list") == []

            industry = module.infer_touched("lookup_industry", {"industry": "Healthcare"}, None)
            assert [(r.name, r.type) for r in industry] == [("Healthcare", "INDUSTRY")]
        finally:
            sys.modules.pop("audit_trail_tool_calls", None)

    def test_main_loads(self, monkeypatch):
        """Import ``main.py`` by file path — catches the relative-import regression."""
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        try:
            module = _load("audit_trail_main", "main.py")
            assert callable(module.build_settings)
            assert callable(module.reset_demo_state)
            assert callable(module.main)
            assert module.SESSION_ID == "audit-trail-demo"
        finally:
            sys.modules.pop("audit_trail_main", None)
            sys.modules.pop("tool_calls", None)


@pytest.mark.syntax
class TestAuditTrailContent:
    def test_main_uses_on_tool_call_recorded_hook(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "on_tool_call_recorded" in source

    def test_main_uses_trace_outcome(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "TraceOutcome" in source

    def test_main_demonstrates_both_write_paths(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "touched_entities=[" in source, "Approach 1 (explicit edges) is missing"
        assert "add_touched_edge" in source, "Approach 2 (the hook) is missing"

    def test_main_records_a_failing_trace(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "success=False" in source
        assert 'error_kind="timeout"' in source

    def test_main_links_the_triggering_message(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "triggered_by_message_id=" in source
        assert "INITIATED_BY" in source

    def test_main_reads_through_the_portable_cypher_accessor(self):
        source = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        assert "client.query.cypher" in source
        assert "client.graph.execute_read" not in source

    def test_queries_use_touched_edge_and_uppercase_types(self):
        source = (AUDIT_DIR / "queries.cypher").read_text(encoding="utf-8")
        assert ":TOUCHED" in source
        assert "{type: 'CLIENT'}" in source

    def test_readme_documents_the_bolt_only_constraint(self):
        source = (AUDIT_DIR / "README.md").read_text(encoding="utf-8")
        assert "Bolt only" in source
        assert "NAMS" in source


@pytest.mark.syntax
class TestAuditReasoningHowToSnippets:
    """The how-to duplicates this example's code; keep the two in step.

    The structural fix is an Antora ``examples$`` include (see the
    follow-ups); until then these assertions catch the drift that matters.
    """

    def test_howto_exists(self):
        assert HOWTO.exists(), f"Missing: {HOWTO}"

    def test_howto_declares_the_bolt_only_constraint(self):
        source = HOWTO.read_text(encoding="utf-8")
        assert "include::partial$backend-bolt-only.adoc[]" in source
        assert "client.query.cypher" in source

    def test_howto_hook_matches_the_example(self):
        """Same hook name and body; the how-to's copy is deliberately untyped.

        ``HookContext`` is not re-exported from ``neo4j_agent_memory.memory``,
        which ``tests/docs/test_code_snippets.py`` requires of snippet imports,
        so the annotated form lives in the example only.
        """
        example = (AUDIT_DIR / "main.py").read_text(encoding="utf-8")
        howto = HOWTO.read_text(encoding="utf-8")
        assert "async def link_touched_entities(tool_call: ToolCall, ctx: HookContext)" in example
        assert "async def link_touched_entities(tool_call, ctx):" in howto
        for line in ("await ctx.add_touched_edge(ref)", "on_tool_call_recorded"):
            assert line in example, line
            assert line in howto, line

    def test_howto_guards_the_result_shape_like_the_example(self):
        """The drifted copy used to index ``result`` without a type check."""
        howto = HOWTO.read_text(encoding="utf-8")
        assert "isinstance(result, list)" in howto
        assert 'arguments.get("client_name")' in howto

    def test_howto_entity_ref_types_are_uppercase(self):
        source = HOWTO.read_text(encoding="utf-8")
        assert 'type="Client")' not in source
        assert 'type="CLIENT")' in source


@pytest.mark.requires_neo4j
@pytest.mark.asyncio
class TestAuditTrailRun:
    """Run ``main()`` end to end and assert the audit trail materialized."""

    async def test_main_writes_the_audit_trail(self, neo4j_env, capsys):
        pytest.importorskip("sentence_transformers")

        try:
            module = _load("audit_trail_main_run", "main.py")
            await module.main()
            out = capsys.readouterr().out
            assert "Audit trail for Anthem (2 step(s)):" in out
            assert "error_kind: timeout" in out
            assert "CLIENT: Anthem" in out

            from neo4j_agent_memory import MemoryClient

            settings = module.build_settings()
            async with MemoryClient(settings) as client:
                rows = await client.query.cypher(
                    """
                    MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)
                          <-[:HAS_STEP]-(rt:ReasoningTrace {session_id: $sid})
                    OPTIONAL MATCH (rt)-[:INITIATED_BY]->(m:Message)
                    RETURN rt.success AS success, rt.error_kind AS error_kind,
                           m.content AS triggered_by
                    """,
                    {"sid": module.SESSION_ID},
                )
                # One row per completed trace: the success and the timeout.
                assert len(rows) == 2
                assert {r["success"] for r in rows} == {True, False}
                assert {r["error_kind"] for r in rows} == {None, "timeout"}
                assert all(r["triggered_by"] for r in rows)

                touched = await client.query.cypher(
                    """
                    MATCH (:ReasoningTrace {session_id: $sid})-[:HAS_STEP]->(:ReasoningStep)
                          -[:TOUCHED]->(e:Entity)
                    RETURN e.name AS name, e.type AS type
                    """,
                    {"sid": module.SESSION_ID},
                )
                pairs = {(r["name"], r["type"]) for r in touched}
                # One TOUCHED edge per consultant, plus the client, industry
                # and the skill the failing trace looked for.
                assert ("Sara", "PERSON") in pairs
                assert ("Liam", "PERSON") in pairs
                assert ("Anthem", "CLIENT") in pairs
                assert ("Healthcare", "INDUSTRY") in pairs
                assert ("FedRAMP", "SKILL") in pairs

                orphans = await client.query.cypher(
                    """
                    MATCH (s:ReasoningStep)
                    WHERE NOT (s)<-[:HAS_STEP]-(:ReasoningTrace)
                    RETURN count(s) AS orphan_steps
                    """
                )
                assert orphans[0]["orphan_steps"] == 0
        finally:
            sys.modules.pop("audit_trail_main_run", None)
            sys.modules.pop("tool_calls", None)
