"""Unit tests for benchmarks/compare_extractors.py.

No model downloads: every test either exercises pure argument-parsing /
table-building logic, or monkeypatches ``GLiNER2Extractor`` construction with
an in-process stub. ``gliner`` (the legacy v1 package) is not installed in
this environment (removed as a dependency in 0.7), so the ``--legacy-model``
tests exercise the real ``ImportError`` guard rather than a mocked one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks import compare_extractors as ce
from benchmarks.metrics import BenchmarkResult, ExtractionMetrics
from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

DATA_DIR = Path(__file__).parent.parent.parent / "benchmarks" / "data"


class _StubExtractor:
    """In-process extractor stub standing in for GLiNER2Extractor.

    Records every constructor call (so tests can assert the sweep built one
    extractor per model/threshold combination) and returns a fixed
    ExtractionResult for every ``extract`` call.
    """

    instances: list[_StubExtractor] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []
        _StubExtractor.instances.append(self)

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        self.calls.append(text)
        return ExtractionResult(
            entities=[
                ExtractedEntity(id="e1", name="Priya Raghavan", type="PERSON"),
                ExtractedEntity(id="e2", name="Acme Corp", type="ORGANIZATION"),
            ],
            relations=[
                # Permitted: PERSON -EMPLOYED_BY-> ORGANIZATION.
                ExtractedRelation(
                    source="Priya Raghavan",
                    target="Acme Corp",
                    relation_type="EMPLOYED_BY",
                    source_id="e1",
                    target_id="e2",
                ),
                # Forbidden: RESIDES_AT wants a LOCATION target, not ORGANIZATION.
                ExtractedRelation(
                    source="Priya Raghavan",
                    target="Acme Corp",
                    relation_type="RESIDES_AT",
                    source_id="e1",
                    target_id="e2",
                ),
            ],
        )


@pytest.fixture(autouse=True)
def _reset_stub_instances() -> None:
    _StubExtractor.instances = []


def _stub_benchmark_result(
    *,
    name: str = "stub_suite",
    micro_f1: float = 1.0,
    relation_f1: float = 1.0,
) -> BenchmarkResult:
    """Build a BenchmarkResult without running an extractor.

    ``ExtractionMetrics``/``RelationMetrics`` are populated with counts
    chosen to make ``micro_f1``/``relation_f1`` come out at the given value.
    """
    from benchmarks.metrics import EntityMetrics, RelationMetrics

    entity_metrics = EntityMetrics(
        entity_type="PERSON",
        true_positives=1 if micro_f1 else 0,
        false_negatives=0 if micro_f1 else 1,
    )
    relation_metrics = RelationMetrics(
        true_positives=1 if relation_f1 else 0, false_negatives=0 if relation_f1 else 1
    )
    metrics = ExtractionMetrics(
        entity_metrics={"PERSON": entity_metrics},
        total_true_positives=entity_metrics.true_positives,
        total_false_positives=entity_metrics.false_positives,
        total_false_negatives=entity_metrics.false_negatives,
        relation_metrics=relation_metrics,
    )
    return BenchmarkResult(
        name=name,
        extractor_name="StubExtractor",
        test_count=1,
        metrics=metrics,
        total_latency_ms=12.5,
        avg_latency_ms=12.5,
        throughput_docs_per_sec=80.0,
        relation_metrics=relation_metrics,
    )


# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------


def test_default_args() -> None:
    args = ce.parse_args([])

    assert args.models == ["fastino/gliner2.5-small-v1", "fastino/gliner2.5-base-v1"]
    assert args.relation_thresholds == [0.5]
    assert args.entity_threshold == 0.5
    assert args.device == "cpu"
    assert args.policy == "lenient"
    assert args.data == "benchmarks/data"
    assert args.output is None
    assert args.ontology is None
    assert args.gliner_schema is None
    assert args.legacy_model is None


def test_models_and_thresholds_parse_comma_lists() -> None:
    args = ce.parse_args(
        [
            "--models",
            "a/one,b/two, c/three ",
            "--relation-thresholds",
            "0.1,0.4,0.9",
        ]
    )

    assert args.models == ["a/one", "b/two", "c/three"]
    assert args.relation_thresholds == [0.1, 0.4, 0.9]


def test_empty_models_string_parses_to_empty_list() -> None:
    args = ce.parse_args(["--models", ""])
    assert args.models == []


def test_ontology_and_gliner_schema_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        ce.parse_args(["--ontology", "some.yaml", "--gliner-schema", "podcast"])


def test_policy_rejects_unknown_choice() -> None:
    with pytest.raises(SystemExit):
        ce.parse_args(["--policy", "loose"])


def test_resolve_ontology_defaults_to_poleo() -> None:
    args = ce.parse_args([])
    assert ce._resolve_ontology(args) is POLEO_ONTOLOGY


def test_resolve_ontology_uses_gliner_schema() -> None:
    args = ce.parse_args(["--gliner-schema", "podcast"])
    resolved = ce._resolve_ontology(args)
    assert resolved.domain.name == "podcast"


# -----------------------------------------------------------------------------
# Gold file discovery
# -----------------------------------------------------------------------------


def test_discover_gold_files_finds_a_single_file() -> None:
    path = DATA_DIR / "poleo_gold.json"
    assert ce._discover_gold_files(path) == [path]


def test_discover_gold_files_filters_non_benchmark_suite_json(tmp_path: Path) -> None:
    suite_file = tmp_path / "a_gold.json"
    suite_file.write_text(json.dumps({"name": "a", "test_cases": []}))
    resolution_file = tmp_path / "a_resolution_gold.json"
    resolution_file.write_text(json.dumps({"mentions": [], "clusters": {}}))
    not_json = tmp_path / "README.md"
    not_json.write_text("not json")

    found = ce._discover_gold_files(tmp_path)

    assert found == [suite_file]


def test_discover_gold_files_missing_path_returns_empty(tmp_path: Path) -> None:
    assert ce._discover_gold_files(tmp_path / "does-not-exist") == []


def test_load_suites_reads_real_gold_data() -> None:
    suites = ce._load_suites(DATA_DIR)
    assert [suite.name for suite in suites] == ["poleo_gold"]
    assert len(suites[0].test_cases) == 10


# -----------------------------------------------------------------------------
# Ontology endpoint-type violation counting
# -----------------------------------------------------------------------------


def test_label_for_entity_matches_on_pole_type() -> None:
    assert ce._label_for_entity(POLEO_ONTOLOGY, "PERSON", None) == "Person"
    assert ce._label_for_entity(POLEO_ONTOLOGY, "person", None) == "Person"
    assert ce._label_for_entity(POLEO_ONTOLOGY, "UNKNOWN_TYPE", None) is None


@pytest.mark.asyncio
async def test_count_ontology_violations_flags_the_forbidden_relation() -> None:
    from benchmarks.runner import BenchmarkSuite, BenchmarkTestCase

    suite = BenchmarkSuite(
        name="stub",
        description="",
        test_cases=[BenchmarkTestCase(id="tc-1", text="anything", expected_entities=[])],
    )
    extractor = _StubExtractor()

    violations, checked = await ce.count_ontology_violations(extractor, POLEO_ONTOLOGY, suite)

    # EMPLOYED_BY (PERSON->ORGANIZATION) is permitted; RESIDES_AT (PERSON->LOCATION
    # declared, but the stub targets an ORGANIZATION) is not.
    assert checked == 2
    assert violations == 1


@pytest.mark.asyncio
async def test_count_ontology_violations_skips_unresolvable_endpoints() -> None:
    from benchmarks.runner import BenchmarkSuite, BenchmarkTestCase

    class _DanglingExtractor:
        async def extract(self, text: str, **_: Any) -> ExtractionResult:
            return ExtractionResult(
                entities=[ExtractedEntity(id="e1", name="Solo", type="PERSON")],
                relations=[
                    ExtractedRelation(
                        source="Solo",
                        target="Nobody",
                        relation_type="KNOWS",
                        source_id="e1",
                        target_id="missing-id",
                    )
                ],
            )

    suite = BenchmarkSuite(
        name="stub",
        description="",
        test_cases=[BenchmarkTestCase(id="tc-1", text="anything", expected_entities=[])],
    )

    violations, checked = await ce.count_ontology_violations(
        _DanglingExtractor(), POLEO_ONTOLOGY, suite
    )

    assert checked == 0
    assert violations == 0


# -----------------------------------------------------------------------------
# Table building (stub BenchmarkResults, no extraction involved)
# -----------------------------------------------------------------------------


def test_build_table_empty_rows() -> None:
    assert ce.build_table([]) == "No results."


def test_build_table_plain_contains_expected_cells() -> None:
    row = ce.CompareRow(
        label="fake-model (rt=0.5)",
        result=_stub_benchmark_result(),
        violations=2,
        relations_checked=5,
    )

    table = ce._build_plain_table([row])

    assert "fake-model (rt=0.5)" in table
    assert "2/5" in table
    for header in ce._TABLE_HEADERS:
        assert header in table


def test_build_table_uses_rich_when_available() -> None:
    row = ce.CompareRow(
        label="fake-model (rt=0.3)",
        result=_stub_benchmark_result(micro_f1=1.0, relation_f1=0.0),
        violations=0,
        relations_checked=4,
    )

    table = ce.build_table([row])

    assert "fake-model (rt=0.3)" in table
    assert "0/4" in table


def test_build_table_falls_back_without_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_rich(rows: list[ce.CompareRow]) -> str:
        raise ImportError("rich is not installed")

    monkeypatch.setattr(ce, "_build_rich_table", _no_rich)
    row = ce.CompareRow(
        label="fake-model (rt=0.5)",
        result=_stub_benchmark_result(),
        violations=1,
        relations_checked=1,
    )

    table = ce.build_table([row])

    assert table == ce._build_plain_table([row])


# -----------------------------------------------------------------------------
# End-to-end main(), with GLiNER2Extractor construction monkeypatched
# -----------------------------------------------------------------------------


def test_main_runs_sweep_with_stubbed_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ce, "GLiNER2Extractor", _StubExtractor)
    output_path = tmp_path / "results.json"

    rc = ce.main(
        [
            "--models",
            "fake/one,fake/two",
            "--relation-thresholds",
            "0.3,0.5",
            "--data",
            str(DATA_DIR),
            "--output",
            str(output_path),
        ]
    )

    assert rc == 0
    # 2 models x 2 thresholds x 1 suite = 4 extractors built, one per combination.
    assert len(_StubExtractor.instances) == 4
    assert {inst.kwargs["relation_threshold"] for inst in _StubExtractor.instances} == {0.3, 0.5}

    payloads = json.loads(output_path.read_text())
    assert len(payloads) == 4
    assert all(p["ontology_violations"] == 10 for p in payloads)  # 1 violation x 10 gold cases
    assert all(p["relations_checked"] == 20 for p in payloads)


def test_main_reports_extractor_build_failure_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AlwaysFails:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("simulated model load failure")

    monkeypatch.setattr(ce, "GLiNER2Extractor", _AlwaysFails)

    rc = ce.main(["--models", "broken/model", "--data", str(DATA_DIR)])

    assert rc == 1  # nothing succeeded


def test_main_returns_error_for_missing_data_dir(tmp_path: Path) -> None:
    rc = ce.main(["--data", str(tmp_path / "nope")])
    assert rc == 1


def test_main_rejects_unknown_gliner_schema() -> None:
    rc = ce.main(["--gliner-schema", "not-a-real-schema", "--data", str(DATA_DIR)])
    assert rc == 2


# -----------------------------------------------------------------------------
# Legacy GLiNER v1 adapter: skip path (no download, no `gliner` install)
# -----------------------------------------------------------------------------


def test_legacy_adapter_raises_import_error_without_gliner_installed() -> None:
    with pytest.raises(ImportError, match="gliner"):
        ce.LegacyGLiNERAdapter("some/legacy-model", labels=["person"])


def test_build_legacy_extractor_prints_message_and_returns_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = ce._build_legacy_extractor("some/legacy-model", POLEO_ONTOLOGY, threshold=0.5)

    assert result is None
    captured = capsys.readouterr()
    assert "some/legacy-model" in captured.err
    assert "gliner" in captured.err.lower()


def test_main_skips_legacy_model_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce, "GLiNER2Extractor", _StubExtractor)

    rc = ce.main(
        [
            "--models",
            "fake/one",
            "--legacy-model",
            "urchade/gliner_mediumv2.1",
            "--data",
            str(DATA_DIR),
        ]
    )

    # The main sweep still produced a result even though the legacy row was skipped.
    assert rc == 0
    assert len(_StubExtractor.instances) == 1


def test_main_returns_failure_when_only_legacy_requested_and_missing() -> None:
    rc = ce.main(
        [
            "--models",
            "",
            "--legacy-model",
            "urchade/gliner_mediumv2.1",
            "--data",
            str(DATA_DIR),
        ]
    )

    assert rc == 1
