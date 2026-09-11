"""CI gate: run the eval suite, write a JSON report, exit non-zero on a drop.

This is the ten-line script you actually put in a workflow. It reuses
``main.py`` (same directory) for the fixture, the suite and the printer, then
adds the two things CI needs: a machine-readable report on disk and a
non-zero exit code when the score regresses.

Run from the repo root::

    uv run python examples/eval-harness/ci_gate.py
    uv run python examples/eval-harness/ci_gate.py --min-score 0.9 --report eval-report.json

Exit codes: ``0`` at or above the threshold, ``1`` below it, ``2`` when the
run itself failed (no Neo4j, bad credentials, a seed error).

The report is a single JSON object — overall score, per-dimension scores and
the full per-case ``details`` — suitable for ``actions/upload-artifact`` or a
diff against the previous run's file.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load_example() -> Any:
    """Load the sibling ``main.py`` (the directory is not an import package)."""
    spec = importlib.util.spec_from_file_location("eval_harness_main", HERE / "main.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Could not load {HERE / 'main.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate CI on memory-quality scores.")
    parser.add_argument(
        "--min-score",
        type=float,
        default=1.0,
        help="Minimum acceptable overall score (default: 1.0).",
    )
    parser.add_argument(
        "--dimensions",
        default=None,
        help="Comma-separated subset of retrieval,audit,preference (default: all).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("eval-report.json"),
        help="Where to write the JSON report (default: ./eval-report.json).",
    )
    return parser.parse_args(argv)


async def gate(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    example = _load_example()

    try:
        report, _labels = await example.run_eval(
            dimensions=example.parse_dimensions(args.dimensions)
        )
    except Exception as exc:  # noqa: BLE001 - a failed run is a distinct exit code
        print(f"eval run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    example.print_report(report, min_score=args.min_score)
    payload = example.report_payload(report, min_score=args.min_score, include_details=True)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.report}")

    if payload["passed"]:
        print(f"PASS: overall {payload['overall']:.2f} >= {args.min_score:.2f}")
        return 0
    print(
        f"FAIL: overall {payload['overall']:.2f} < {args.min_score:.2f}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(gate()))
