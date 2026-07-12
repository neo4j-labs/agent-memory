#!/usr/bin/env python3
"""Monotonic type-check error ratchet.

Runs the project type checkers (``mypy`` and ``ty``) over the checked surface
(``src``, ``benchmarks``, and the top-level ``examples/*.py`` demos — see
``_targets()``) and compares their error/diagnostic counts against a committed
budget (``scripts/typecheck-budget.txt``).

The budget may only ever *decrease*. This makes CI blocking on regressions from
day one even while the absolute count is still non-zero (see the type-safety
tracking issue #144, "Regression ratchet"):

* a checker count **above** budget  -> FAIL (regression introduced)
* a checker count **below** budget  -> FAIL (improvement not recorded; rerun
  with ``--update`` and commit the tightened budget so the ratchet stays tight)
* a checker count **equal** to budget -> PASS

Run ``python scripts/typecheck_ratchet.py --update`` (``make
typecheck-ratchet-update``) to rewrite the budget to the current counts.

The checkers must be invoked in an environment with the integration extras
installed (``uv sync --all-extras --group dev``); otherwise ``integrations/`` is
only shallowly analyzed and the counts are not comparable to the budget.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Repo-relative paths, resolved from this file so the script works from any cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BUDGET_FILE = SCRIPT_DIR / "typecheck-budget.txt"


def _targets() -> list[str]:
    """The checked surface: ``src``, ``benchmarks``, and the top-level
    single-file example demos (``examples/*.py``).

    The example subdirectories (``examples/<name>/main.py`` etc.) and the
    full-stack example applications are intentionally excluded here: their many
    identically-named ``main.py`` modules collide under a single mypy
    invocation, and the full apps are standalone projects with their own
    tooling. They are brought under the checker in a later workstream. Adding a
    new ``examples/*.py`` demo automatically extends the surface — if it does
    not type-check, the ratchet count rises and CI fails until it is annotated.
    """
    examples = sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "examples").glob("*.py"))
    return ["src", "benchmarks", *examples]


TARGETS = _targets()

BUDGET_HEADER = """\
# Type-check error budget (monotonic ratchet).
#
# The counts below may only ever decrease. CI runs scripts/typecheck_ratchet.py
# and fails any PR that raises a count (regression) or lowers one without
# recording it here. See the type-safety tracking issue #144.
#
# Measured with integration extras installed: `uv sync --all-extras --group dev`.
# Refresh after improving a count: `make typecheck-ratchet-update`.
"""


def _run(cmd: list[str]) -> str:
    """Run a checker and return its combined stdout+stderr.

    The checkers exit non-zero when they find issues, so the return code is
    ignored and the count is parsed from the output instead.
    """
    proc = subprocess.run(  # noqa: S603 - fixed, trusted argv
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.stdout + proc.stderr


def _resolve(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        sys.exit(
            f"error: `{tool}` not found on PATH. Run inside the project venv, e.g. "
            f"`uv run python scripts/typecheck_ratchet.py` (and `uv sync --all-extras --group dev`)."
        )
    return path


def count_mypy() -> int:
    out = _run([_resolve("mypy"), *TARGETS])
    m = re.search(r"Found (\d+) error", out)
    if m:
        return int(m.group(1))
    if "Success: no issues found" in out:
        return 0
    sys.exit(f"error: could not parse mypy output:\n{out}")


def count_ty() -> int:
    out = _run([_resolve("ty"), "check", *TARGETS])
    m = re.search(r"Found (\d+) diagnostic", out)
    if m:
        return int(m.group(1))
    if "All checks passed" in out:
        return 0
    sys.exit(f"error: could not parse ty output:\n{out}")


def read_budget() -> dict[str, int]:
    if not BUDGET_FILE.exists():
        sys.exit(f"error: budget file not found: {BUDGET_FILE}")
    budget: dict[str, int] = {}
    for line in BUDGET_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        budget[key.strip()] = int(value.strip())
    return budget


def write_budget(counts: dict[str, int]) -> None:
    lines = [BUDGET_HEADER, *(f"{k}={v}" for k, v in counts.items()), ""]
    BUDGET_FILE.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Rewrite the budget file to the current counts (only allowed to lower it).",
    )
    args = parser.parse_args()

    counts = {"mypy": count_mypy(), "ty": count_ty()}

    if args.update:
        budget = read_budget()
        for tool, current in counts.items():
            old = budget.get(tool)
            if old is not None and current > old:
                print(
                    f"refusing to update: {tool} count {current} is HIGHER than "
                    f"budget {old}. Fix the regression first."
                )
                return 1
        write_budget(counts)
        print(f"budget updated: {counts}")
        return 0

    budget = read_budget()
    regressed = False
    improved = False
    print(f"{'checker':<8} {'budget':>8} {'actual':>8}  status")
    print("-" * 38)
    for tool, current in counts.items():
        allowed = budget.get(tool)
        if allowed is None:
            print(f"{tool:<8} {'-':>8} {current:>8}  MISSING from budget")
            regressed = True
            continue
        if current > allowed:
            status = f"REGRESSION (+{current - allowed})"
            regressed = True
        elif current < allowed:
            status = f"improved (-{allowed - current}); record it"
            improved = True
        else:
            status = "ok"
        print(f"{tool:<8} {allowed:>8} {current:>8}  {status}")

    if regressed:
        print("\nType-check ratchet FAILED: a count rose above its budget.")
        return 1
    if improved:
        print(
            "\nType-check ratchet FAILED: a count improved but the budget was not "
            "tightened.\nRun `make typecheck-ratchet-update` and commit "
            "scripts/typecheck-budget.txt."
        )
        return 1
    print("\nType-check ratchet OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
