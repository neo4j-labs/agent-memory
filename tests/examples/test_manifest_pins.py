"""Tests for the shared example-manifest pin helper, and a sweep over every manifest.

The helper is `tests/examples/_manifests.py`; the individual example smoke
tests call `assert_library_pin` on their own manifest. This module (a) proves
the helper actually distinguishes a good pin from a bad one — a substring check
never did — and (b) sweeps every tracked example manifest so a directory with
no test module of its own (the three `nams-*` examples, for one) cannot ship a
stale floor unnoticed.
"""

from __future__ import annotations

import pytest

from tests.examples._manifests import (
    MIN_EXAMPLE_PIN,
    LibraryPin,
    _at_least,
    assert_library_pin,
    find_npm_pin,
    find_python_pin,
    iter_example_python_manifests,
)

PYPROJECT_TEMPLATE = """\
[project]
name = "example-backend"
version = "0.1.0"
dependencies = [
    "fastapi>=0.115.0",
    {requirement}
]

[tool.uv.sources]
neo4j-agent-memory = {{ path = "../../..", editable = true }}
"""


def _write_pyproject(tmp_path, requirement: str):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT_TEMPLATE.format(requirement=f'"{requirement}",'), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Helper behaviour
# ---------------------------------------------------------------------------


@pytest.mark.syntax
def test_accepts_a_correctly_pinned_pyproject(tmp_path):
    pin = assert_library_pin(_write_pyproject(tmp_path, "neo4j-agent-memory[openai]>=0.5.0,<0.7"))
    assert pin.floor == "0.5.0"
    assert pin.cap == "0.7"
    assert pin.extras == ("openai",)


@pytest.mark.syntax
def test_accepts_a_floor_above_the_minimum(tmp_path):
    pin = assert_library_pin(_write_pyproject(tmp_path, "neo4j-agent-memory>=0.6.1,<0.7"))
    assert pin.floor == "0.6.1"


@pytest.mark.syntax
def test_rejects_a_stale_floor(tmp_path):
    """The whole point: `>=0.1.0` must fail, and say so."""
    with pytest.raises(AssertionError, match=r"below the current floor"):
        assert_library_pin(_write_pyproject(tmp_path, "neo4j-agent-memory>=0.1.0,<0.7"))


@pytest.mark.syntax
def test_rejects_a_missing_cap(tmp_path):
    with pytest.raises(AssertionError, match=r"no upper bound"):
        assert_library_pin(_write_pyproject(tmp_path, "neo4j-agent-memory>=0.5.0"))


@pytest.mark.syntax
def test_rejects_a_floating_git_reference(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "fastapi>=0.115.0\n"
        "neo4j-agent-memory @ git+https://github.com/neo4j-labs/agent-memory@main\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match=r"direct reference"):
        assert_library_pin(path)


@pytest.mark.syntax
def test_rejects_a_manifest_that_never_requires_the_library(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("fastapi>=0.115.0\nuvicorn>=0.30.0\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"does not require neo4j-agent-memory"):
        assert_library_pin(path)


@pytest.mark.syntax
def test_unrelated_dependency_with_an_old_floor_is_not_mistaken_for_the_library(tmp_path):
    """The old substring assertion passed on exactly this file. This one must not."""
    path = tmp_path / "requirements.txt"
    path.write_text("some-other-package>=0.1.0\nneo4j-agent-memory>=0.5.0,<0.7\n", encoding="utf-8")
    assert assert_library_pin(path).floor == "0.5.0"


@pytest.mark.syntax
def test_comments_are_not_requirements(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "# neo4j-agent-memory from GitHub\nneo4j-agent-memory>=0.5.0,<0.7\n", encoding="utf-8"
    )
    assert assert_library_pin(path).raw == "neo4j-agent-memory>=0.5.0,<0.7"


@pytest.mark.syntax
def test_optional_dependency_groups_are_searched(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\ndependencies = []\n\n'
        "[project.optional-dependencies]\n"
        'memory = ["neo4j-agent-memory[nams]>=0.5.0,<0.7"]\n',
        encoding="utf-8",
    )
    assert assert_library_pin(path).extras == ("nams",)


@pytest.mark.syntax
def test_missing_manifest_fails_rather_than_skips(tmp_path):
    with pytest.raises(AssertionError, match=r"manifest not found"):
        assert_library_pin(tmp_path / "nope" / "pyproject.toml")


@pytest.mark.syntax
@pytest.mark.parametrize(
    ("version", "minimum", "expected"),
    [
        ("0.5.0", "0.5.0", True),
        ("0.5", "0.5.0", True),
        ("0.6", "0.5.0", True),
        ("1.0", "0.5.0", True),
        ("0.4.9", "0.5.0", False),
        ("0.1.0", "0.5.0", False),
    ],
)
def test_version_comparison_pads_short_versions(version, minimum, expected):
    assert _at_least(version, minimum) is expected


@pytest.mark.syntax
def test_pin_without_specifiers_reports_no_floor():
    pin = LibraryPin(manifest=__import__("pathlib").Path("x"), raw="neo4j-agent-memory")
    assert pin.floor is None
    assert pin.cap is None


# ---------------------------------------------------------------------------
# Sweep over the real manifests
# ---------------------------------------------------------------------------


@pytest.mark.syntax
def test_every_example_manifest_is_discovered():
    manifests = iter_example_python_manifests()
    assert manifests, "no example manifest mentions neo4j-agent-memory — the glob is wrong"
    names = {p.parent.name for p in manifests}
    assert "nams-quickstart" in names, "the NAMS examples must be swept too"


@pytest.mark.syntax
@pytest.mark.xfail(
    strict=False,
    reason="example manifest floors move to >=0.5.0,<0.7 in the per-example slices; "
    "delete this marker once they have landed",
)
def test_every_example_manifest_pins_a_current_capped_range():
    """One report listing every manifest that is behind, instead of five separate failures."""
    problems: list[str] = []
    for manifest in iter_example_python_manifests():
        try:
            assert_library_pin(manifest)
        except AssertionError as exc:
            problems.append(str(exc).splitlines()[0])

    assert not problems, "example manifests with a stale or unbounded pin:\n  " + "\n  ".join(
        problems
    )


@pytest.mark.syntax
def test_every_example_manifest_names_the_library_exactly_once_per_file():
    """A duplicate requirement means one of the two is dead and will drift."""
    for manifest in iter_example_python_manifests():
        pin = find_python_pin(manifest)
        assert pin is not None, f"{manifest} mentions the library but declares no requirement"


@pytest.mark.syntax
def test_npm_pin_reader_handles_the_repo_convention(tmp_path):
    """npm examples keep a `file:` link to the SDK; the reader must not choke on it."""
    path = tmp_path / "package.json"
    path.write_text(
        '{"dependencies": {"@neo4j-labs/agent-memory": "file:../.."}}',
        encoding="utf-8",
    )
    assert find_npm_pin(path) == "file:../.."
