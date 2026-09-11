"""Smoke tests for the eval-harness example.

The example is itself a regression harness, so these tests do more than
grep: the Neo4j-backed class runs ``main()`` end to end, asserts every
dimension scores 1.0, and proves the preference case can *fail* by
reverting the supersede the seed performs.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EVAL_DIR = EXAMPLES_DIR / "eval-harness"
MODULE_NAME = "eval_harness_main"
GATE_MODULE_NAME = "eval_harness_ci_gate"


def _load(path: Path, module_name: str) -> ModuleType:
    """Exec a script as a module (callers must pop it from sys.modules)."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.syntax
class TestEvalHarnessStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py", "ci_gate.py"]:
            assert (EVAL_DIR / filename).exists(), f"Missing: {filename}"

    def test_scripts_compile(self):
        for filename in ["main.py", "ci_gate.py"]:
            ast.parse((EVAL_DIR / filename).read_text(encoding="utf-8"))


@pytest.mark.imports
class TestEvalHarnessImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient  # noqa: F401
        from neo4j_agent_memory.memory.eval import (  # noqa: F401
            AuditCase,
            EvalSuite,
            PreferenceCase,
            RetrievalCase,
        )

    def test_build_settings_enables_multi_tenant(self, monkeypatch):
        # The example resolves a SentenceTransformersProvider at construction
        # time; skip when the extra is not installed.
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        try:
            module = _load(EVAL_DIR / "main.py", MODULE_NAME)
            assert module.build_settings().memory.multi_tenant is True
            # Parameterised so a reader can see what the flag is guarding.
            assert module.build_settings(multi_tenant=False).memory.multi_tenant is False
        finally:
            sys.modules.pop(MODULE_NAME, None)

    def test_dimension_parsing_and_payload_are_pure(self, monkeypatch):
        pytest.importorskip("sentence_transformers")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")
        try:
            module = _load(EVAL_DIR / "main.py", MODULE_NAME)

            assert module.parse_dimensions(None) is None
            assert module.parse_dimensions("retrieval, preference") == [
                "retrieval",
                "preference",
            ]
            with pytest.raises(SystemExit):
                module.parse_dimensions("retrieval,nonsense")

            from neo4j_agent_memory.memory.eval import DimensionReport, EvalReport

            report = EvalReport(audit=DimensionReport(cases=1, score=0.5, details=[]))
            assert module.report_payload(report, min_score=1.0)["passed"] is False
            assert module.report_payload(report, min_score=0.4)["passed"] is True
            # Dimensions that did not run are explicit nulls in the report.
            assert module.report_payload(report, min_score=1.0)["retrieval"] is None
        finally:
            sys.modules.pop(MODULE_NAME, None)


@pytest.mark.syntax
class TestEvalHarnessContent:
    @pytest.fixture
    def source(self) -> str:
        return (EVAL_DIR / "main.py").read_text(encoding="utf-8")

    def test_main_uses_eval_run(self, source):
        assert "client.eval.run" in source

    def test_main_covers_all_three_dimensions(self, source):
        for case in ("RetrievalCase", "AuditCase", "PreferenceCase"):
            assert case in source, case

    def test_preference_case_can_fail(self, source):
        # Two preferences are seeded and one is superseded, so the expected
        # active set is a real assertion rather than a tautology.
        assert "supersede_preference" in source

    def test_seed_is_multi_tenant(self, source):
        assert "multi_tenant" in source
        assert "user_identifier=USER_A" in source
        assert "user_identifier=USER_B" in source

    def test_seed_does_not_read_back_the_entity_id(self, source):
        # client.graph.execute_read is deprecated (removed in v0.6.0); the
        # seed knows its own ids and never queries for them.
        assert "client.graph.execute_read" not in source
        assert "EntityRef(id=" in source

    def test_main_exposes_a_ci_gate(self, source):
        assert "--min-score" in source
        assert "SystemExit" in source

    def test_ci_gate_writes_a_json_report(self):
        gate_source = (EVAL_DIR / "ci_gate.py").read_text(encoding="utf-8")
        assert "--report" in gate_source
        assert "json.dumps" in gate_source


@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestEvalHarnessEndToEnd:
    """Runs the harness against Neo4j — the example is its own regression test."""

    @pytest.fixture
    def module(self, neo4j_env):
        pytest.importorskip("sentence_transformers")
        try:
            yield _load(EVAL_DIR / "main.py", MODULE_NAME)
        finally:
            sys.modules.pop(MODULE_NAME, None)

    async def _reset(self, module) -> None:
        from neo4j_agent_memory import MemoryClient

        async with MemoryClient(module.build_settings()) as client:
            await module.reset_demo_data(client)

    async def test_every_dimension_scores_one(self, module):
        try:
            report, labels = await module.run_eval()

            assert report.retrieval is not None and report.retrieval.score == 1.0
            assert report.audit is not None and report.audit.score == 1.0
            assert report.preference is not None and report.preference.score == 1.0
            assert report.overall_score == 1.0

            # The superseded preference is a distinct node from the active one,
            # otherwise the preference case would be unfalsifiable.
            assert labels["superseded_pref_id"] != labels["active_pref_id"]
        finally:
            await self._reset(module)

    async def test_main_exits_zero_at_the_gate(self, module, tmp_path):
        trend = tmp_path / "trend.jsonl"
        try:
            # No SystemExit: a healthy graph clears --min-score 1.0.
            await module.main(["--min-score", "1.0", "--out", str(trend)])

            row = json.loads(trend.read_text(encoding="utf-8").splitlines()[-1])
            assert row["passed"] is True
            assert row["overall"] == 1.0
        finally:
            await self._reset(module)

    async def test_dimension_selection_skips_the_others(self, module):
        try:
            report, _labels = await module.run_eval(dimensions=["preference"])
            assert report.preference is not None
            assert report.retrieval is None
            assert report.audit is None
        finally:
            await self._reset(module)

    async def test_preference_case_detects_a_missed_supersede(self, module):
        """Revert the supersede and the dimension must drop below 1.0."""
        from neo4j_agent_memory import MemoryClient

        try:
            async with MemoryClient(module.build_settings()) as client:
                labels = await module.seed(client)
                await client.graph.execute_write(
                    """
                    MATCH (old:Preference {id: $old})-[r:SUPERSEDED_BY]->(:Preference)
                    DELETE r
                    SET old.valid_until = null
                    """,
                    {"old": labels["superseded_pref_id"]},
                )

                report = await client.eval.run(module.build_suite(labels))
                assert report.preference is not None
                assert report.preference.score < 1.0
                # The details carry the expected-vs-actual diff the README shows.
                detail = next(
                    d for d in report.preference.details if d["user_identifier"] == labels["user_a"]
                )
                assert labels["superseded_pref_id"] in detail["actual"]
                assert labels["superseded_pref_id"] not in detail["expected"]
        finally:
            await self._reset(module)

    async def test_multi_tenant_blocks_an_unscoped_write(self, module):
        from neo4j_agent_memory import MemoryClient

        async with MemoryClient(module.build_settings()) as client:
            with pytest.raises(ValueError, match="multi_tenant"):
                await client.long_term.add_preference("consultants", "unscoped write")

    async def test_reset_leaves_no_residue(self, module):
        from neo4j_agent_memory import MemoryClient

        async with MemoryClient(module.build_settings()) as client:
            await module.seed(client)
            await module.reset_demo_data(client)

            rows = await client.query.cypher(
                """
                MATCH (n)
                WHERE (n:User AND n.identifier IN $users)
                   OR (n:Entity AND n.name IN $names)
                   OR (n:ReasoningTrace AND n.session_id = $session)
                RETURN count(n) AS count
                """,
                {
                    "users": module.DEMO_USERS,
                    "names": module.DEMO_ENTITY_NAMES,
                    "session": module.SESSION,
                },
            )
            assert rows[0]["count"] == 0

    async def test_ci_gate_passes_and_writes_a_report(self, module, tmp_path):
        report_path = tmp_path / "eval-report.json"
        try:
            gate = _load(EVAL_DIR / "ci_gate.py", GATE_MODULE_NAME)
            try:
                exit_code = await gate.gate(["--min-score", "1.0", "--report", str(report_path)])
            finally:
                sys.modules.pop(GATE_MODULE_NAME, None)

            assert exit_code == 0
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            assert payload["passed"] is True
            assert payload["overall"] == 1.0
            # The CI report keeps the per-case details for diffing.
            assert payload["audit"]["details"][0]["recall"] == 1.0
        finally:
            await self._reset(module)
