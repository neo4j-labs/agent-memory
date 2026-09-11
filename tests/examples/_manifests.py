"""Shared helper for asserting how examples pin ``neo4j-agent-memory``.

Why this exists
---------------
Five example smoke tests used to assert the library pin with a whole-file
substring check::

    assert ">=0.1.0" in content, "Backend should pin neo4j-agent-memory>=0.1.0"

That verifies nothing on its own terms: it passes on *any* `>=0.1.0` in the
file (an unrelated dependency will do), it says nothing about which package the
floor belongs to, and it actively blocks modernising the examples — bumping a
manifest to the current release turns the test red. See the cross-cutting
review finding xc-F02.

What this module does instead
----------------------------
It parses the manifest (``pyproject.toml`` via :mod:`tomllib`,
``requirements.txt`` line by line, ``package.json`` via :mod:`json`), finds the
requirement for the library by name, and checks:

* the requirement exists at all (not a bare name, not a floating VCS ref);
* its lower bound is at least :data:`MIN_EXAMPLE_PIN`;
* for Python manifests, an upper bound exists — the repo convention is
  ``neo4j-agent-memory[...]>=0.5.0,<0.7`` so an example cannot silently be
  resolved against a future breaking release.

Usage
-----
::

    from tests.examples._manifests import assert_library_pin

    def test_backend_pyproject_has_version_pin(self, app_dir):
        assert_library_pin(app_dir / "backend" / "pyproject.toml")

The helper raises :class:`AssertionError` with the manifest path, the
requirement as written, and what was expected, so a failure message is
actionable without opening the file.

Maintenance
-----------
When a new library release is out, bump :data:`MIN_EXAMPLE_PIN` here — one
constant, not five string literals. Manifests whose floor has not been bumped
yet are marked ``xfail(strict=False)`` at the call site, so they report as
expected-failures until the pin lands and flip to ``XPASS`` the moment it does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

#: Minimum acceptable lower bound for the library in any example manifest.
#: Keep in step with the released version the examples are verified against.
MIN_EXAMPLE_PIN = "0.5.0"

#: Upper bound the repo convention uses. Only its presence is asserted.
EXPECTED_CAP_HINT = "<0.7"

PYTHON_PACKAGE = "neo4j-agent-memory"
NPM_PACKAGE = "@neo4j-labs/agent-memory"

# ``name[extra1,extra2] >=0.5.0, <0.7`` / ``name @ git+https://...``
_REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*(?P<rest>.*)$"
)
_SPECIFIER_RE = re.compile(r"(?P<op>===|==|!=|~=|>=|<=|>|<)\s*(?P<version>[0-9][^,;\s]*)")


def _rel(path: Path) -> str:
    """Repo-relative path for messages, falling back to the full path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _normalize(name: str) -> str:
    """PEP 503 name normalisation (``Neo4J_Agent.Memory`` -> ``neo4j-agent-memory``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a release version into a comparable tuple.

    Deliberately dependency-free (no ``packaging``, which this repo does not
    declare): example pins are plain releases such as ``0.5.0`` or ``0.7``.
    Any trailing pre-release/local segment is dropped.
    """
    release = re.match(r"\d+(?:\.\d+)*", version.strip())
    if release is None:
        raise ValueError(f"unparseable version: {version!r}")
    return tuple(int(part) for part in release.group(0).split("."))


def _at_least(version: str, minimum: str) -> bool:
    """Compare two release versions, padding the shorter one with zeros."""
    left, right = _version_tuple(version), _version_tuple(minimum)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


@dataclass(frozen=True)
class LibraryPin:
    """How one manifest references the library."""

    manifest: Path
    raw: str
    extras: tuple[str, ...] = ()
    specifiers: tuple[tuple[str, str], ...] = ()
    is_direct_reference: bool = False
    is_local_path: bool = False

    @property
    def floor(self) -> str | None:
        """Lower-bound version, if the requirement declares one."""
        for op, version in self.specifiers:
            if op in (">=", "==", "===", "~="):
                return version
        return None

    @property
    def cap(self) -> str | None:
        """Upper-bound version, if the requirement declares one."""
        for op, version in self.specifiers:
            if op in ("<", "<="):
                return version
        return None

    def describe(self) -> str:
        return f"{_rel(self.manifest)}: {self.raw!r}"


def _parse_requirement(line: str, manifest: Path, package: str) -> LibraryPin | None:
    """Return a :class:`LibraryPin` if ``line`` requires ``package``."""
    requirement = line.split("#", 1)[0].strip()
    if not requirement:
        return None
    match = _REQUIREMENT_RE.match(requirement)
    if match is None or _normalize(match.group("name")) != _normalize(package):
        return None

    rest = match.group("rest").strip()
    extras = tuple(e.strip() for e in (match.group("extras") or "").split(",") if e.strip())
    # Drop an environment marker before reading specifiers.
    rest = rest.split(";", 1)[0].strip()
    return LibraryPin(
        manifest=manifest,
        raw=requirement,
        extras=extras,
        specifiers=tuple((m.group("op"), m.group("version")) for m in _SPECIFIER_RE.finditer(rest)),
        is_direct_reference=rest.startswith("@"),
        is_local_path=rest.startswith("@") and "file:" in rest,
    )


def _pyproject_requirement_strings(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    strings: list[str] = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        strings.extend(group)
    for group in (data.get("dependency-groups", {}) or {}).values():
        strings.extend(item for item in group if isinstance(item, str))
    return strings


def find_python_pin(path: Path, package: str = PYTHON_PACKAGE) -> LibraryPin | None:
    """Find how a Python manifest requires ``package``.

    Supports ``pyproject.toml`` (``project.dependencies``,
    ``project.optional-dependencies``, ``dependency-groups``) and
    ``requirements.txt``. Returns ``None`` when the manifest does not mention
    the package at all.
    """
    if path.name == "pyproject.toml":
        candidates = _pyproject_requirement_strings(path)
    else:
        candidates = path.read_text(encoding="utf-8").splitlines()

    for candidate in candidates:
        pin = _parse_requirement(candidate, path, package)
        if pin is not None:
            return pin
    return None


def find_npm_pin(path: Path, package: str = NPM_PACKAGE) -> str | None:
    """Return the version range a ``package.json`` declares for ``package``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        spec = (data.get(field) or {}).get(package)
        if isinstance(spec, str):
            return spec
    return None


def assert_library_pin(
    path: Path,
    *,
    minimum: str = MIN_EXAMPLE_PIN,
    require_cap: bool = True,
    package: str = PYTHON_PACKAGE,
) -> LibraryPin:
    """Assert a Python example manifest pins the library correctly.

    Checks, in order: the manifest exists; it requires ``package``; the
    requirement is a version range rather than a floating VCS reference; the
    lower bound is at least ``minimum``; and (when ``require_cap``) an upper
    bound is present.

    Returns the parsed :class:`LibraryPin` so callers can make extra
    assertions (for example on extras).
    """
    assert path.exists(), f"manifest not found: {path} — update the test or restore the file"

    pin = find_python_pin(path, package)
    assert pin is not None, (
        f"{_rel(path)} does not require {package}. "
        f"Expected something like {package}>={minimum},{EXPECTED_CAP_HINT}"
    )

    assert not pin.is_direct_reference, (
        f"{pin.describe()} uses a direct reference instead of a released version. "
        f"Examples must pin a release: {package}>={minimum},{EXPECTED_CAP_HINT} "
        "(keep the editable path in [tool.uv.sources] for local development)."
    )

    floor = pin.floor
    assert floor is not None, (
        f"{pin.describe()} has no lower bound. Expected >={minimum} so a fresh "
        "install cannot resolve a release that predates the APIs the example uses."
    )
    assert _at_least(floor, minimum), (
        f"{pin.describe()} pins {package}>={floor}, below the current floor "
        f"{minimum}. Bump the manifest to >={minimum},{EXPECTED_CAP_HINT} "
        "(and re-stamp the README's 'Verified against' footer)."
    )

    if require_cap:
        assert pin.cap is not None, (
            f"{pin.describe()} has no upper bound. Add one (e.g. "
            f"{EXPECTED_CAP_HINT}) so the example is not resolved against a "
            "future breaking release."
        )

    return pin


def iter_example_python_manifests() -> list[Path]:
    """Every tracked example manifest that mentions the library, sorted."""
    manifests: list[Path] = []
    for pattern in ("**/pyproject.toml", "**/requirements.txt"):
        for path in EXAMPLES_DIR.glob(pattern):
            if any(part in {".venv", "node_modules", "build", "dist"} for part in path.parts):
                continue
            if PYTHON_PACKAGE in path.read_text(encoding="utf-8"):
                manifests.append(path)
    return sorted(set(manifests))
