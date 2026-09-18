"""Compare GLiNER2.5 checkpoints (and optionally a legacy GLiNER v1 checkpoint)
on a POLE+O gold benchmark.

For every ``(model, relation_threshold)`` combination this builds a
:class:`~neo4j_agent_memory.extraction.gliner2_extractor.GLiNER2Extractor`,
runs it through :class:`~benchmarks.runner.BenchmarkRunner` against every
gold suite under ``--data``, and reports entity F1, relation F1 (lenient and
strict), latency, throughput, and a count of extracted relations whose
endpoint types the ontology forbids (an independent check — it does not use
the ``ExtractionResult.validate_relations`` API another PR is adding, since
that would be a moving target here).

Usage::

    uv run python benchmarks/compare_extractors.py \\
        --models fastino/gliner2.5-small-v1,fastino/gliner2.5-base-v1 \\
        --relation-thresholds 0.3,0.5 \\
        --output results.json

    # Compare against a legacy GLiNER v1 checkpoint too (needs `pip install
    # gliner` separately; the library itself no longer depends on it):
    uv run python benchmarks/compare_extractors.py \\
        --legacy-model urchade/gliner_mediumv2.1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractionResult,
)
from neo4j_agent_memory.extraction.gliner2_extractor import GLiNER2Extractor
from neo4j_agent_memory.extraction.label_mapping import map_label_to_poleo
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY, get_template
from neo4j_agent_memory.ontology.convert import load_ontology
from neo4j_agent_memory.ontology.models import OntologyDocument

# `benchmarks` is a repository-only dev package — excluded from the wheel
# (see `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages`) — so
# it is never on `sys.path` for a plain `python benchmarks/compare_extractors.py`
# invocation the way an installed package would be; only this script's own
# directory is. `neo4j_agent_memory` above needs no such fixup (it is
# installed), so this sits between the two import groups. Mirrors the same
# fixup in `tests/unit/test_benchmarks.py`; harmless and a no-op when this
# module is imported normally (e.g. `from benchmarks.compare_extractors import
# main` from a test that already has the repo root on `sys.path`).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.metrics import (  # noqa: E402
    DEFAULT_MATCH_POLICY,
    STRICT_MATCH_POLICY,
    BenchmarkResult,
)
from benchmarks.runner import BenchmarkRunner, BenchmarkSuite  # noqa: E402

DEFAULT_MODELS = "fastino/gliner2.5-small-v1,fastino/gliner2.5-base-v1"


# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------


def _parse_csv(value: str) -> list[str]:
    """Split a comma-separated string, dropping empty/whitespace entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_float_csv(value: str) -> list[float]:
    """Split a comma-separated string of floats, dropping empty entries."""
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured ``ArgumentParser``.
    """
    parser = argparse.ArgumentParser(
        prog="compare_extractors",
        description=(
            "Compare GLiNER2.5 checkpoints on a POLE+O gold benchmark: entity/"
            "relation F1, latency, throughput, and ontology endpoint-type "
            "violations."
        ),
    )
    parser.add_argument(
        "--data",
        default="benchmarks/data",
        help=(
            "A BenchmarkSuite-shaped gold JSON file, or a directory containing "
            "one or more of them (default: benchmarks/data)."
        ),
    )
    parser.add_argument(
        "--models",
        type=_parse_csv,
        default=DEFAULT_MODELS,
        help=f"Comma-separated GLiNER2.5 checkpoint ids (default: {DEFAULT_MODELS}).",
    )
    ontology_group = parser.add_mutually_exclusive_group()
    ontology_group.add_argument(
        "--ontology",
        default=None,
        help="Path to an ontology YAML/JSON file (mutually exclusive with --gliner-schema).",
    )
    ontology_group.add_argument(
        "--gliner-schema",
        default=None,
        help=(
            "Name of a built-in ontology template, e.g. 'podcast' or 'news' "
            "(mutually exclusive with --ontology). Default: the POLE+O ontology."
        ),
    )
    parser.add_argument(
        "--relation-thresholds",
        type=_parse_float_csv,
        default="0.5",
        help="Comma-separated relation-confidence thresholds to sweep (default: 0.5).",
    )
    parser.add_argument(
        "--entity-threshold",
        type=float,
        default=0.5,
        help="Entity confidence floor passed to the decoder (default: 0.5).",
    )
    parser.add_argument("--device", default="cpu", help="Inference device (default: cpu).")
    parser.add_argument(
        "--policy",
        choices=("strict", "lenient"),
        default="lenient",
        help=(
            "Relation match policy. 'lenient' allows aliases and also reports "
            "the strict numbers side by side; 'strict' reports canonical-name-"
            "only numbers only (default: lenient)."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write every BenchmarkResult (plus violation counts) to this JSON file.",
    )
    parser.add_argument(
        "--legacy-model",
        default=None,
        help=(
            "A GLiNER v1 checkpoint id to evaluate through a small in-script "
            "adapter, for a before/after comparison. Needs the 'gliner' "
            "package installed separately (the library no longer depends on "
            "it); skipped with a printed message when it is not installed."
        ),
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments excluding the program name; ``None`` reads ``sys.argv[1:]``.

    Returns:
        The parsed namespace.
    """
    return build_parser().parse_args(argv)


def _resolve_ontology(args: argparse.Namespace) -> OntologyDocument:
    """Resolve ``--ontology`` / ``--gliner-schema`` into one ontology document.

    Args:
        args: Parsed arguments.

    Returns:
        The requested ontology, or :data:`POLEO_ONTOLOGY` when neither flag
        was given.
    """
    if args.ontology:
        return load_ontology(args.ontology)
    if args.gliner_schema:
        return get_template(args.gliner_schema)
    return POLEO_ONTOLOGY


# -----------------------------------------------------------------------------
# Gold data discovery
# -----------------------------------------------------------------------------


def _looks_like_benchmark_suite(path: Path) -> bool:
    """Whether ``path`` parses as JSON with a top-level ``test_cases`` key.

    Filters out sibling files in a gold data directory that are not
    ``BenchmarkSuite`` shaped, such as ``poleo_resolution_gold.json`` (an
    entity-resolution gold file, loaded separately with
    ``load_resolution_gold``) or a stray non-JSON file.
    """
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and "test_cases" in data


def _discover_gold_files(data_path: Path) -> list[Path]:
    """Find every ``BenchmarkSuite``-shaped JSON file under ``data_path``.

    Args:
        data_path: A single gold file, or a directory containing some.

    Returns:
        Sorted list of matching file paths (empty if ``data_path`` is
        neither a matching file nor a directory).
    """
    if data_path.is_file():
        return [data_path]
    if not data_path.is_dir():
        return []
    return sorted(path for path in data_path.glob("*.json") if _looks_like_benchmark_suite(path))


def _load_suites(data_path: Path) -> list[BenchmarkSuite]:
    """Load every gold suite discovered under ``data_path``."""
    return [BenchmarkSuite.from_json_file(path) for path in _discover_gold_files(data_path)]


# -----------------------------------------------------------------------------
# Ontology endpoint-type violations
# -----------------------------------------------------------------------------


def _label_for_entity(
    ontology: OntologyDocument,
    entity_type: str,
    entity_subtype: str | None,
) -> str | None:
    """Map an extracted entity's ``(type, subtype)`` back onto an ontology label.

    Prefers an exact ``(pole_type, subtype)`` match; falls back to the first
    entity type declaring the same ``pole_type`` when no subtype lines up
    (e.g. POLE+O's built-in types all have ``subtype=None``, so most
    extracted entities match on the fallback).

    Args:
        ontology: The ontology to search.
        entity_type: The extracted entity's POLE+O type.
        entity_subtype: The extracted entity's subtype, if any.

    Returns:
        The matching label, or ``None`` if no entity type shares this
        ``pole_type`` at all.
    """
    wanted_type = entity_type.upper()
    wanted_subtype = entity_subtype.upper() if entity_subtype else None
    fallback: str | None = None
    for entity_def in ontology.entity_types:
        if entity_def.pole_type.upper() != wanted_type:
            continue
        if fallback is None:
            fallback = entity_def.label
        def_subtype = entity_def.subtype.upper() if entity_def.subtype else None
        if def_subtype == wanted_subtype:
            return entity_def.label
    return fallback


async def count_ontology_violations(
    extractor: EntityExtractor,
    ontology: OntologyDocument,
    suite: BenchmarkSuite,
) -> tuple[int, int]:
    """Count extracted relations whose endpoint types the ontology forbids.

    Runs ``extractor`` once more per test case: ``BenchmarkRunner.run_case``
    reduces a result down to ``(name, type)`` / ``(source, type, target)``
    tuples for scoring, which drops the entity ids and subtypes that
    ``OntologyDocument.permits`` needs. This does not depend on
    ``ExtractionResult.validate_relations`` (a runtime API another PR is
    adding concurrently); it re-derives endpoint labels locally instead.

    Each relation's endpoints are resolved by mention id first (JointIE
    relations carry ``source_id``/``target_id``), falling back to a
    case-insensitive name match against the same result's entities. A
    relation whose endpoints cannot be resolved at all is skipped — neither
    checked nor counted as a violation.

    Args:
        extractor: The extractor to probe (already built for this row).
        ontology: The ontology whose relationship patterns to check against.
        suite: The gold suite; only each test case's ``text`` is used.

    Returns:
        ``(violation_count, relations_checked)``.
    """
    violations = 0
    checked = 0
    for test_case in suite.test_cases:
        try:
            result = await extractor.extract(test_case.text)
        except Exception:
            continue

        by_id: dict[str, ExtractedEntity] = {
            entity.id: entity for entity in result.entities if entity.id is not None
        }
        by_name = {entity.name.lower(): entity for entity in result.entities}

        for relation in result.relations:
            source = (by_id.get(relation.source_id) if relation.source_id else None) or by_name.get(
                relation.source.lower()
            )
            target = (by_id.get(relation.target_id) if relation.target_id else None) or by_name.get(
                relation.target.lower()
            )
            if source is None or target is None:
                continue

            checked += 1
            source_label = _label_for_entity(ontology, source.type, source.subtype)
            target_label = _label_for_entity(ontology, target.type, target.subtype)
            if source_label is None or target_label is None:
                violations += 1
                continue
            if not ontology.permits(source_label, relation.relation_type, target_label):
                violations += 1

    return violations, checked


# -----------------------------------------------------------------------------
# Legacy GLiNER v1 adapter (optional, --legacy-model only)
# -----------------------------------------------------------------------------


class LegacyGLiNERAdapter:
    """Adapts a GLiNER v1 checkpoint to the ``EntityExtractor`` protocol.

    GLiNER v1 has no relation decoder, so every result's ``relations`` list
    is empty: that row's relation columns read 0/0/0 in the comparison
    table, which is the point of the comparison, not a bug. The library no
    longer depends on the ``gliner`` package (removed in 0.7); construction
    raises :class:`ImportError` with an install hint when it is absent.
    """

    #: Extractor name recorded on every entity it produces.
    name = "gliner-v1"

    def __init__(self, model_id: str, labels: list[str], threshold: float = 0.5) -> None:
        """Load a GLiNER v1 checkpoint.

        Args:
            model_id: A GLiNER v1 checkpoint id (e.g. an ``urchade/*`` or
                ``gliner-community/*`` model).
            labels: Zero-shot labels to extract, mapped back onto POLE+O
                through :func:`map_label_to_poleo`.
            threshold: Confidence floor passed to ``predict_entities``.

        Raises:
            ImportError: If the ``gliner`` package is not installed.
        """
        try:
            import gliner  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "The --legacy-model comparison needs the 'gliner' v1 package, "
                "which neo4j-agent-memory no longer depends on (removed in 0.7). "
                "Install it separately, e.g. `pip install gliner==0.2.24`, to "
                "run this comparison."
            ) from exc

        self.model_id = model_id
        self._labels = labels
        self._threshold = threshold
        self._model = gliner.GLiNER.from_pretrained(model_id)

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Extract entities only; GLiNER v1 has no relation decoder.

        Args:
            text: The text to extract from.
            entity_types: Ignored — the label set is fixed at construction.
            extract_relations: Ignored — always empty for this adapter.
            extract_preferences: Ignored — GLiNER v1 extracts no preferences.

        Returns:
            The entities found in ``text``, with no relations or preferences.
        """
        loop = asyncio.get_event_loop()
        raw: Any = await loop.run_in_executor(
            None,
            lambda: self._model.predict_entities(text, self._labels, threshold=self._threshold),
        )
        entities: list[ExtractedEntity] = []
        for item in raw:
            entity_type, subtype = map_label_to_poleo(str(item["label"]))
            entities.append(
                ExtractedEntity(
                    name=str(item["text"]),
                    type=entity_type,
                    subtype=subtype,
                    start_pos=item.get("start"),
                    end_pos=item.get("end"),
                    confidence=float(item.get("score", 1.0)),
                    extractor=self.name,
                )
            )
        return ExtractionResult(entities=entities, relations=[], preferences=[], source_text=text)


def _build_legacy_extractor(
    model_id: str,
    ontology: OntologyDocument,
    threshold: float,
) -> EntityExtractor | None:
    """Build the legacy adapter, printing a message and returning ``None`` on failure.

    Args:
        model_id: The GLiNER v1 checkpoint id.
        ontology: The ontology whose labels to extract.
        threshold: Entity confidence floor.

    Returns:
        The adapter, or ``None`` when the ``gliner`` package is not installed.
    """
    try:
        return LegacyGLiNERAdapter(model_id, ontology.labels(), threshold)
    except ImportError as exc:
        print(f"Skipping --legacy-model {model_id!r}: {exc}", file=sys.stderr)
        return None


# -----------------------------------------------------------------------------
# Table rendering
# -----------------------------------------------------------------------------


@dataclass
class CompareRow:
    """One (model, relation_threshold[, suite]) result row for the table."""

    label: str
    result: BenchmarkResult
    violations: int
    relations_checked: int


_TABLE_HEADERS = (
    "Extractor",
    "Entity P",
    "Entity R",
    "Entity F1",
    "Rel P (lenient)",
    "Rel R (lenient)",
    "Rel F1 (lenient)",
    "Rel F1 (strict)",
    "Avg latency (ms)",
    "Docs/sec",
    "Ontology violations",
)


def _row_cells(row: CompareRow) -> list[str]:
    """Render one row's cells, in ``_TABLE_HEADERS`` order."""
    metrics = row.result.metrics
    return [
        row.label,
        f"{metrics.micro_precision:.3f}",
        f"{metrics.micro_recall:.3f}",
        f"{metrics.micro_f1:.3f}",
        f"{row.result.relation_precision:.3f}",
        f"{row.result.relation_recall:.3f}",
        f"{row.result.relation_f1:.3f}",
        f"{row.result.strict_relation_f1:.3f}",
        f"{row.result.avg_latency_ms:.1f}",
        f"{row.result.throughput_docs_per_sec:.2f}",
        f"{row.violations}/{row.relations_checked}",
    ]


def _build_plain_table(rows: list[CompareRow]) -> str:
    """Render a plain tab-separated table (no ``rich`` dependency)."""
    lines = ["\t".join(_TABLE_HEADERS)]
    lines.extend("\t".join(_row_cells(row)) for row in rows)
    return "\n".join(lines)


def _build_rich_table(rows: list[CompareRow]) -> str:
    """Render a formatted table with ``rich``.

    The rendering ``Console`` writes into an in-memory buffer rather than
    the real stdout, since :func:`build_table` returns text for the caller
    to print — printing here too would duplicate the table on screen.

    Raises:
        ImportError: If ``rich`` is not installed.
    """
    import io

    from rich.console import Console
    from rich.table import Table

    table = Table(title="GLiNER2.5 extractor comparison")
    for header in _TABLE_HEADERS:
        table.add_column(header)
    for row in rows:
        table.add_row(*_row_cells(row))

    buffer = io.StringIO()
    console = Console(record=True, width=200, file=buffer)
    console.print(table)
    return console.export_text()


def build_table(rows: list[CompareRow]) -> str:
    """Render the comparison table as text.

    Uses ``rich`` for a formatted table when it is importable, otherwise
    falls back to a plain tab-separated table. Either way the rendered text
    is returned rather than only printed, so callers (and tests) can inspect
    it directly.

    Args:
        rows: One row per (model, relation_threshold[, suite]) combination.

    Returns:
        The rendered table as a string.
    """
    if not rows:
        return "No results."
    try:
        return _build_rich_table(rows)
    except ImportError:
        return _build_plain_table(rows)


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------


async def _evaluate_one(
    extractor: EntityExtractor,
    ontology: OntologyDocument,
    suite: BenchmarkSuite,
    label: str,
    *,
    extra: dict[str, Any],
    rows: list[CompareRow],
    payloads: list[dict[str, Any]],
) -> None:
    """Run one extractor against one suite, appending to ``rows``/``payloads``.

    Errors are printed and swallowed so one failing combination does not
    abort the rest of the sweep.
    """
    try:
        result = await BenchmarkRunner(extractor).run_suite(suite)
        violations, checked = await count_ontology_violations(extractor, ontology, suite)
    except Exception as exc:
        print(f"ERROR: {label} failed on suite {suite.name!r}: {exc}", file=sys.stderr)
        return

    rows.append(
        CompareRow(label=label, result=result, violations=violations, relations_checked=checked)
    )
    payloads.append(
        {
            **extra,
            "suite": suite.name,
            "label": label,
            "ontology_violations": violations,
            "relations_checked": checked,
            **result.to_dict(),
        }
    )


async def _run(
    args: argparse.Namespace,
    ontology: OntologyDocument,
    suites: list[BenchmarkSuite],
) -> tuple[list[CompareRow], list[dict[str, Any]]]:
    """Run the full (model x relation_threshold x suite) sweep, plus --legacy-model.

    Args:
        args: Parsed arguments.
        ontology: The resolved ontology, shared by every extractor.
        suites: Gold suites to run each extractor against.

    Returns:
        Table rows and their JSON-serialisable payloads, in run order.
    """
    rows: list[CompareRow] = []
    payloads: list[dict[str, Any]] = []
    multi_suite = len(suites) > 1

    for model in args.models:
        for threshold in args.relation_thresholds:
            try:
                extractor: EntityExtractor = GLiNER2Extractor(
                    model=model,
                    ontology=ontology,
                    threshold=args.entity_threshold,
                    relation_threshold=threshold,
                    device=args.device,
                )
            except Exception as exc:
                print(
                    f"ERROR: could not build GLiNER2Extractor for {model!r}: {exc}",
                    file=sys.stderr,
                )
                continue

            for suite in suites:
                label = f"{model} (rt={threshold})"
                if multi_suite:
                    label = f"{label} [{suite.name}]"
                await _evaluate_one(
                    extractor,
                    ontology,
                    suite,
                    label,
                    extra={"model": model, "relation_threshold": threshold, "legacy": False},
                    rows=rows,
                    payloads=payloads,
                )

    if args.legacy_model:
        legacy_extractor = _build_legacy_extractor(
            args.legacy_model, ontology, args.entity_threshold
        )
        if legacy_extractor is not None:
            for suite in suites:
                label = f"{args.legacy_model} (legacy gliner v1)"
                if multi_suite:
                    label = f"{label} [{suite.name}]"
                await _evaluate_one(
                    legacy_extractor,
                    ontology,
                    suite,
                    label,
                    extra={"model": args.legacy_model, "relation_threshold": None, "legacy": True},
                    rows=rows,
                    payloads=payloads,
                )

    return rows, payloads


def main(argv: list[str] | None = None) -> int:
    """Run the extractor comparison sweep, print a table, and optionally save JSON.

    Args:
        argv: Command-line arguments excluding the program name; ``None``
            reads ``sys.argv[1:]``.

    Returns:
        0 on at least one successful (model, threshold) combination, 1 if no
        gold suites were found or every combination failed, 2 for a bad
        ``--ontology``/``--gliner-schema``.
    """
    args = parse_args(argv)

    try:
        ontology = _resolve_ontology(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not resolve ontology: {exc}", file=sys.stderr)
        return 2

    data_path = Path(args.data)
    suites = _load_suites(data_path)
    if not suites:
        print(f"No benchmark suites found under {data_path}", file=sys.stderr)
        return 1

    match_policy = STRICT_MATCH_POLICY if args.policy == "strict" else DEFAULT_MATCH_POLICY
    for suite in suites:
        suite.config.match_policy = match_policy

    rows, payloads = asyncio.run(_run(args, ontology, suites))

    print(build_table(rows))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as handle:
            json.dump(payloads, handle, indent=2)
        print(f"\nWrote {len(payloads)} result(s) to {output_path}")

    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
