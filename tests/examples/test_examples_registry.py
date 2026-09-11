"""Registry meta-test: the examples tree, its tests, and its CI wiring agree.

The examples gallery has drifted the same way five separate times — a new
example lands with no index row, no test module, or no CI registration, and
nothing notices until a reader follows a broken link. Every assertion here
replaces a manual checklist step in ``examples/README.md`` ("Contributing a new
example") with something that fails a pull request.

What this enforces:

* every directory under ``examples/`` has a README with the Labs badge and a
  "verified against" footer, an index row in ``examples/README.md``, and a
  matching module under ``tests/examples/``;
* every directory under ``typescript/examples/`` has the same README shape, a
  row in ``typescript/examples/README.md``, an ``engines.node`` floor, a
  ``test`` script, and an entry in the ``type-check-examples`` CI matrix;
* every ``tests/examples/test_*.py`` module contributes at least one test to the
  quick (no-Neo4j) suite, so a new example cannot silently skip that job;
* the quick suite has exactly one definition — the marker expression — shared by
  the Makefile, ``examples/README.md`` and ``ci-python.yml``.

Everything is static except the last check, which asks pytest itself to collect
the quick suite (``--collect-only``) rather than re-deriving marker semantics.

Each exemption below carries a reason. Adding one is a deliberate, reviewable
act; forgetting a test module is not.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
TS_EXAMPLES_DIR = REPO_ROOT / "typescript" / "examples"
TESTS_DIR = Path(__file__).parent
PY_INDEX = EXAMPLES_DIR / "README.md"
TS_INDEX = TS_EXAMPLES_DIR / "README.md"
CI_PYTHON = REPO_ROOT / ".github" / "workflows" / "ci-python.yml"
CI_TYPESCRIPT = REPO_ROOT / ".github" / "workflows" / "ci-typescript.yml"

#: The single definition of the quick example suite. Must match the Makefile's
#: `test-examples-quick` target and ci-python.yml's `example-tests-quick` job.
QUICK_MARKER_EXPRESSION = "not requires_neo4j and not slow"

#: Example directories that deliberately ship no `.env.example`, with why.
NO_ENV_EXAMPLE_OK = {
    "audit-trail": "key-free bolt example; NEO4J_* come from examples/.env.example",
    "buffered-writes": "key-free bolt example; NEO4J_* come from examples/.env.example",
    "domain-schemas": "runs offline on the shipped samples/ corpora; no credentials",
    "eval-harness": "key-free bolt example; NEO4J_* come from examples/.env.example",
    "existing-graph": "key-free bolt example; NEO4J_* come from examples/.env.example",
    "no_llm": "deliberately credential-free — that is the point of the example",
}

#: Example directories with no dedicated test module, with why. Empty today;
#: keep it that way.
NO_TEST_MODULE_OK: dict[str, str] = {}


def _example_dirs() -> list[Path]:
    return sorted(
        p
        for p in EXAMPLES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "__pycache__"
    )


def _ts_example_dirs() -> list[Path]:
    return sorted(p for p in TS_EXAMPLES_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))


def _top_level_scripts() -> list[Path]:
    return sorted(p for p in EXAMPLES_DIR.glob("*.py") if not p.name.startswith("_"))


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py"))


def _test_sources() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in _test_modules()}


def covering_test_modules(name: str, sources: dict[Path, str]) -> list[str]:
    """Test modules that mention ``name``, i.e. build a path into that example."""
    return sorted(p.name for p, src in sources.items() if name in src)


# ---------------------------------------------------------------------------
# Python examples
# ---------------------------------------------------------------------------


@pytest.mark.syntax
@pytest.mark.parametrize("example", _example_dirs(), ids=lambda p: p.name)
def test_python_example_has_a_readme_with_labs_conventions(example: Path):
    readme = example / "README.md"
    assert readme.exists(), f"{example.name}/ has no README.md"
    content = readme.read_text(encoding="utf-8")
    assert "Neo4j-Labs" in content, (
        f"{example.name}/README.md is missing the Neo4j Labs badge "
        "(see examples/README.md 'Contributing a new example')"
    )
    assert re.search(r"[Vv]erified against", content), (
        f"{example.name}/README.md has no 'verified against' footer naming the "
        "library version, the framework versions tested, and the date"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("example", _example_dirs(), ids=lambda p: p.name)
def test_python_example_is_linked_from_the_index(example: Path):
    index = PY_INDEX.read_text(encoding="utf-8")
    assert example.name in index, (
        f"{example.name}/ is not mentioned in examples/README.md — add a row to "
        "the 'How to choose an example' table and a section for it"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("script", _top_level_scripts(), ids=lambda p: p.name)
def test_top_level_script_is_linked_from_the_index(script: Path):
    index = PY_INDEX.read_text(encoding="utf-8")
    assert script.name in index, f"examples/{script.name} is not mentioned in examples/README.md"


@pytest.mark.syntax
@pytest.mark.parametrize("example", _example_dirs(), ids=lambda p: p.name)
def test_python_example_has_a_test_module(example: Path):
    if example.name in NO_TEST_MODULE_OK:
        pytest.skip(f"exempt: {NO_TEST_MODULE_OK[example.name]}")
    covering = covering_test_modules(example.name, _test_sources())
    assert covering, (
        f"{example.name}/ has no module under tests/examples/ that references it. "
        "Add tests/examples/test_<example>_example.py (mirror "
        "test_buffered_writes_example.py), or add an entry with a reason to "
        "NO_TEST_MODULE_OK in this file."
    )


@pytest.mark.syntax
@pytest.mark.parametrize("script", _top_level_scripts(), ids=lambda p: p.name)
def test_top_level_script_has_a_test_module(script: Path):
    covering = covering_test_modules(script.name, _test_sources())
    assert covering, f"examples/{script.name} has no module under tests/examples/ referencing it"


@pytest.mark.syntax
@pytest.mark.parametrize("example", _example_dirs(), ids=lambda p: p.name)
def test_python_example_ships_an_env_example_or_is_exempt(example: Path):
    found = list(example.rglob(".env.example")) + list(example.rglob(".env.local.example"))
    if found:
        return
    assert example.name in NO_ENV_EXAMPLE_OK, (
        f"{example.name}/ ships no .env.example. Add one (never a real .env), or "
        "record why it needs none in NO_ENV_EXAMPLE_OK in this file."
    )


@pytest.mark.syntax
def test_shared_env_example_is_tracked():
    """The top-level scripts route through examples/_env.py, which reads this."""
    shared = EXAMPLES_DIR / ".env.example"
    assert shared.exists(), "examples/.env.example is missing — `cp` instructions point at it"
    content = shared.read_text(encoding="utf-8")
    for var in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "MEMORY_API_KEY"):
        assert var in content, f"examples/.env.example does not mention {var}"


@pytest.mark.syntax
def test_env_example_files_carry_no_real_secrets():
    """A template must never ship a usable key."""
    real_key = re.compile(r"(nams_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")
    offenders: list[str] = []
    for path in EXAMPLES_DIR.rglob(".env*.example"):
        if "node_modules" in path.parts:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "x" * 8 in line.lower():  # nams_xxxxxxxxxxxxxxxx style placeholder
                continue
            if real_key.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    assert not offenders, "placeholder-looking secrets in .env.example files:\n  " + "\n  ".join(
        offenders
    )


# ---------------------------------------------------------------------------
# TypeScript examples
# ---------------------------------------------------------------------------


@pytest.mark.syntax
@pytest.mark.parametrize("example", _ts_example_dirs(), ids=lambda p: p.name)
def test_ts_example_has_a_readme_with_labs_conventions(example: Path):
    readme = example / "README.md"
    assert readme.exists(), f"typescript/examples/{example.name}/ has no README.md"
    content = readme.read_text(encoding="utf-8")
    assert "Neo4j-Labs" in content, f"{example.name}/README.md is missing the Neo4j Labs badge"
    assert re.search(r"[Vv]erified against", content), (
        f"typescript/examples/{example.name}/README.md has no 'verified against' footer"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("example", _ts_example_dirs(), ids=lambda p: p.name)
def test_ts_example_is_linked_from_the_index(example: Path):
    index = TS_INDEX.read_text(encoding="utf-8")
    assert example.name in index, (
        f"typescript/examples/{example.name}/ is not in typescript/examples/README.md"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("example", _ts_example_dirs(), ids=lambda p: p.name)
def test_ts_example_declares_node_engine_and_a_test_script(example: Path):
    manifest = example / "package.json"
    assert manifest.exists(), f"typescript/examples/{example.name}/ has no package.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))

    engines = data.get("engines", {})
    assert "node" in engines, (
        f"{example.name}/package.json declares no engines.node; the gallery's floor is >=22"
    )
    floor = re.search(r"(\d+)", engines["node"])
    assert floor is not None and int(floor.group(1)) >= 22, (
        f"{example.name}/package.json pins Node {engines['node']}; Node 20 reached EOL 2026-04-30"
    )

    scripts = data.get("scripts", {})
    assert "test" in scripts, (
        f"{example.name}/package.json has no `test` script — CI runs "
        "`npm test --if-present` for every example and would silently skip it"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("example", _ts_example_dirs(), ids=lambda p: p.name)
def test_ts_example_is_in_the_ci_matrix(example: Path):
    workflow = CI_TYPESCRIPT.read_text(encoding="utf-8")
    assert re.search(rf"^\s*-\s*{re.escape(example.name)}\s*$", workflow, re.MULTILINE), (
        f"typescript/examples/{example.name}/ is not in the type-check-examples "
        "matrix in .github/workflows/ci-typescript.yml"
    )


@pytest.mark.syntax
@pytest.mark.parametrize("example", _ts_example_dirs(), ids=lambda p: p.name)
def test_ts_example_depends_on_the_in_tree_sdk(example: Path):
    data = json.loads((example / "package.json").read_text(encoding="utf-8"))
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    pin = deps.get("@neo4j-labs/agent-memory")
    assert pin is not None, f"{example.name}/package.json does not depend on the SDK"
    assert pin.startswith("file:"), (
        f"{example.name}/package.json pins the SDK as {pin!r}; examples use "
        '"file:../.." so contributors can iterate without an npm publish'
    )


# ---------------------------------------------------------------------------
# Test-suite / CI wiring
# ---------------------------------------------------------------------------


@pytest.mark.syntax
def test_quick_suite_has_one_definition():
    """The Makefile and the CI quick job must select the same set."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = CI_PYTHON.read_text(encoding="utf-8")
    index = PY_INDEX.read_text(encoding="utf-8")

    assert f'-m "{QUICK_MARKER_EXPRESSION}"' in makefile, (
        "make test-examples-quick no longer selects the quick suite by marker"
    )
    assert f'-m "{QUICK_MARKER_EXPRESSION}"' in workflow, (
        "ci-python.yml's example-tests-quick job no longer selects the quick "
        "suite by marker. Do not reintroduce a hand-maintained file list — it "
        "drifted out of step with tests/examples/ five times."
    )
    assert "pytest tests/examples \\\n            -m" in workflow or (
        "pytest tests/examples" in workflow
    ), "the CI quick job must run the whole tests/examples directory"
    assert "test-examples-quick" in index, (
        "examples/README.md should point readers at make test-examples-quick"
    )


@pytest.mark.syntax
def test_ci_lints_and_type_checks_the_examples_tree():
    workflow = CI_PYTHON.read_text(encoding="utf-8")
    assert "ruff check src tests examples" in workflow, "CI ruff check must cover examples/"
    assert "ruff format --check src tests examples" in workflow, (
        "CI ruff format check must cover examples/"
    )
    assert "examples/*/main.py" in workflow, (
        "CI type-checking must cover the examples/<dir>/main.py entrypoints"
    )


@pytest.mark.imports
def test_every_test_module_contributes_to_the_quick_suite():
    """Ask pytest which modules the quick marker expression selects.

    A module none of whose tests survive the marker filter is invisible to the
    no-Neo4j CI job, which is where an example's structural drift is supposed to
    be caught.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS_DIR),
            "-m",
            QUICK_MARKER_EXPRESSION,
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert result.returncode == 0, f"collection failed:\n{result.stdout}\n{result.stderr}"

    selected = set(re.findall(r"tests/examples/(test_\w+\.py)", result.stdout))
    missing = sorted({p.name for p in _test_modules()} - selected - {Path(__file__).name})

    assert not missing, (
        "these tests/examples modules contribute nothing to the quick "
        f'(-m "{QUICK_MARKER_EXPRESSION}") suite, so the no-Neo4j CI job never '
        "runs them. Give each at least one database-free test, or mark its "
        "database-bound tests with @pytest.mark.requires_neo4j so the intent is "
        "explicit:\n  " + "\n  ".join(missing)
    )
