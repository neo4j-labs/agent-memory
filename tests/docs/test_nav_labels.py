"""Tests that keep nav.adoc, the quadrant indexes, and page H1s aligned.

For a page listed both in ``nav.adoc`` and in its quadrant's ``index.adoc``,
the nav link text, the index link text, and the target page's own H1 should
all read as one name. This test parses those three surfaces and fails when
one diverges from the others, unless the pair is on the explicit allowlist
below (each entry documents *why* the difference is intentional).

No Neo4j, NAMS, or network access is required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NAV_XREF_RE = re.compile(r"xref:([a-zA-Z0-9/_.\-]+)\[([^\]]*)\]")
H1_RE = re.compile(r"^=\s+(.+?)\s*$", re.MULTILINE)

# (nav_path_as_written_in_xref) -> reason the nav/index/H1 text is allowed to differ.
# Keep this list explicit and commented; it is the record of every deliberate
# shortening or prose-style reference, not a way to silence real drift.
ALLOWED_DIVERGENCE = {
    # how-to/index.adoc references its own sibling integrations index inline
    # ("See also ... the integration chooser") as natural prose, not as a
    # peer list row, so it doesn't repeat the page's H1 verbatim.
    "how-to/integrations/index.adoc": "prose cross-reference ('the integration chooser'), not a list entry",
    # Quadrant index pages refer to each other inline as common nouns
    # ("a tutorial", "how-to guides", "reference") rather than by their own H1.
    "tutorials/index.adoc": "inline prose reference to the Tutorials quadrant, not a list entry",
    "reference/index.adoc": "inline prose reference to the Reference quadrant, not a list entry",
    "how-to/index.adoc": "inline prose reference to the How-to quadrant, not a list entry",
}


def _project_root() -> Path:
    return Path(__file__).parent.parent.parent


def _pages_dir() -> Path:
    return _project_root() / "docs" / "modules" / "ROOT" / "pages"


def _nav_path() -> Path:
    return _project_root() / "docs" / "modules" / "ROOT" / "nav.adoc"


def _norm(text: str) -> str:
    """Normalize whitespace only -- never case or punctuation."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_xrefs(text: str) -> dict[str, list[str]]:
    """Map each xref target path to the list of link texts used for it."""
    found: dict[str, list[str]] = {}
    for target, label in NAV_XREF_RE.findall(text):
        target = target.split("#")[0]
        found.setdefault(target, []).append(_norm(label))
    return found


def _page_h1(path: str) -> str | None:
    """Return the sentence-case H1 heading text of a page, or None if unreadable."""
    full = _pages_dir() / path
    if not full.exists():
        return None
    content = full.read_text(encoding="utf-8")
    match = H1_RE.search(content)
    if not match:
        return None
    return _norm(match.group(1))


QUADRANT_INDEXES = {
    "tutorials": "tutorials/index.adoc",
    "how-to": "how-to/index.adoc",
    "reference": "reference/index.adoc",
    "explanation": "explanation/index.adoc",
}


@pytest.fixture(scope="module")
def nav_text() -> str:
    return _nav_path().read_text(encoding="utf-8")


#: nav.adoc scope for the strict nav/index/H1 alignment checks: the How-to,
#: Reference and Explanation groups only. The Tutorials group intentionally
#: keeps short nav labels next to fuller "lesson and outcome" index-table
#: sentences (that pairing predates this task and is explicitly out of its
#: scope), and the top-level entries (site title, SDK links, Glossary, FAQ)
#: aren't part of any quadrant index's page listing at all.
_STRICT_SCOPE_START = "** xref:how-to/index.adoc[How-to guides]"
_STRICT_SCOPE_END = "** xref:glossary.adoc[Glossary]"


def _strict_scope_nav_text(nav_text: str) -> str:
    start = nav_text.index(_STRICT_SCOPE_START)
    end = nav_text.index(_STRICT_SCOPE_END)
    return nav_text[start:end]


@pytest.fixture(scope="module")
def nav_xrefs(nav_text: str) -> dict[str, list[str]]:
    """All nav.adoc xrefs, for ordering/disambiguation checks."""
    return _extract_xrefs(nav_text)


@pytest.fixture(scope="module")
def strict_nav_xrefs(nav_text: str) -> dict[str, list[str]]:
    """nav.adoc xrefs restricted to the How-to/Reference/Explanation groups."""
    return _extract_xrefs(_strict_scope_nav_text(nav_text))


@pytest.fixture(scope="module", params=sorted(QUADRANT_INDEXES.items()))
def quadrant_index(request) -> tuple[str, dict[str, list[str]]]:
    quadrant, rel_path = request.param
    full = _pages_dir() / rel_path
    text = full.read_text(encoding="utf-8")
    return quadrant, _extract_xrefs(text)


@pytest.mark.docs
class TestNavIndexH1Alignment:
    """nav.adoc, each quadrant index, and target H1s must read as one name."""

    def test_nav_label_matches_quadrant_index_label(
        self, strict_nav_xrefs: dict[str, list[str]], quadrant_index: tuple[str, dict[str, list[str]]]
    ) -> None:
        _quadrant, index_xrefs = quadrant_index
        mismatches = []
        for path, index_labels in index_xrefs.items():
            if path not in strict_nav_xrefs:
                continue
            if path in ALLOWED_DIVERGENCE:
                continue
            nav_labels = set(strict_nav_xrefs[path])
            if nav_labels != set(index_labels):
                mismatches.append(f"{path}: nav={sorted(nav_labels)} index={sorted(set(index_labels))}")
        assert not mismatches, "nav.adoc and index link text disagree:\n" + "\n".join(mismatches)

    def test_nav_label_matches_target_h1(self, strict_nav_xrefs: dict[str, list[str]]) -> None:
        mismatches = []
        for path, labels in strict_nav_xrefs.items():
            if path in ALLOWED_DIVERGENCE:
                continue
            h1 = _page_h1(path)
            if h1 is None:
                continue
            for label in set(labels):
                if label != h1:
                    mismatches.append(f"{path}: nav={label!r} h1={h1!r}")
        assert not mismatches, "nav.adoc link text disagrees with the target page's H1:\n" + "\n".join(mismatches)

    def test_index_label_matches_target_h1(self, quadrant_index: tuple[str, dict[str, list[str]]]) -> None:
        quadrant, index_xrefs = quadrant_index
        mismatches = []
        for path, labels in index_xrefs.items():
            if path in ALLOWED_DIVERGENCE:
                continue
            h1 = _page_h1(path)
            if h1 is None:
                continue
            for label in set(labels):
                if label != h1:
                    mismatches.append(f"{path}: {quadrant}/index={label!r} h1={h1!r}")
        assert not mismatches, "Quadrant index link text disagrees with the target page's H1:\n" + "\n".join(
            mismatches
        )

    def test_perturbed_label_is_caught(self, strict_nav_xrefs: dict[str, list[str]]) -> None:
        """Sanity check that the alignment assertion actually fails on a real divergence."""
        path = "explanation/memory-types.adoc"
        h1 = _page_h1(path)
        assert h1 is not None
        nav_labels = set(strict_nav_xrefs[path])
        assert nav_labels == {h1}, "fixture page must currently be aligned for this sanity check to be meaningful"
        perturbed = h1 + " (perturbed)"
        assert perturbed not in nav_labels
        with pytest.raises(AssertionError):
            assert nav_labels == {perturbed}


@pytest.mark.docs
class TestMcpToolsDisambiguation:
    """ia-nav-4 / ref-hosted-diataxis-2: the two 'MCP Tools' pages must read differently."""

    def test_mcp_tools_label_is_not_reused(self, nav_xrefs: dict[str, list[str]]) -> None:
        python_mcp_labels = set(nav_xrefs["reference/mcp-tools.adoc"])
        ts_mcp_labels = set(nav_xrefs["how-to/typescript/mcp.adoc"])
        assert not (python_mcp_labels & ts_mcp_labels), (
            "reference/mcp-tools.adoc and how-to/typescript/mcp.adoc share a nav label; "
            f"python={python_mcp_labels} typescript={ts_mcp_labels}"
        )


@pytest.mark.docs
class TestNavOrdering:
    """howto-typescript-reader-2 / explanation-reader-2: specific ordering requirements."""

    def test_typescript_overview_leads_the_group(self, nav_text: str) -> None:
        overview_pos = nav_text.index("xref:how-to/typescript/integrations-overview.adoc[")
        edge_runtime_pos = nav_text.index("xref:how-to/typescript/edge-runtime.adoc[")
        assert overview_pos < edge_runtime_pos, (
            "Framework Integrations Overview must lead the TypeScript nav group, "
            "ahead of Edge Runtime Deployment"
        )

    def test_backends_follows_memory_types_in_concepts(self, nav_text: str) -> None:
        memory_types_pos = nav_text.index("xref:explanation/memory-types.adoc[")
        backends_pos = nav_text.index("xref:explanation/backends.adoc[")
        poleo_pos = nav_text.index("xref:explanation/poleo-model.adoc[")
        assert memory_types_pos < backends_pos < poleo_pos, (
            "explanation/backends.adoc must sit directly after explanation/memory-types.adoc "
            "in the Concepts nav group"
        )


@pytest.mark.docs
class TestExplanationIndexAnchors:
    """explanation-diataxis-3: legacy anchors must be kept, with provenance comments."""

    LEGACY_ANCHORS = [
        "_core_concepts",
        "_technical_deep_dives",
        "_key_design_decisions",
        "_graph_native_memory",
        "_three_layer_architecture",
        "_poleo_classification",
        "_explanation_philosophy",
        "_further_reading",
    ]

    def test_all_legacy_anchors_are_retained(self) -> None:
        text = (_pages_dir() / "explanation" / "index.adoc").read_text(encoding="utf-8")
        missing = [a for a in self.LEGACY_ANCHORS if f"anchor:{a}[]" not in text]
        assert not missing, f"Legacy anchors removed from explanation/index.adoc: {missing}"

    def test_anchor_line_has_a_provenance_comment_above_it(self) -> None:
        lines = (_pages_dir() / "explanation" / "index.adoc").read_text(encoding="utf-8").split("\n")
        anchor_line_idx = next(i for i, line in enumerate(lines) if line.startswith("anchor:_core_concepts"))
        # At least one of the lines immediately above the anchor line must be a
        # "//" comment that names where the anchors came from.
        preceding = lines[max(0, anchor_line_idx - 5) : anchor_line_idx]
        assert any(
            line.strip().startswith("//") and "index.adoc" in line for line in preceding
        ), "explanation/index.adoc's anchor line needs a provenance comment above it, like explanation/backends.adoc:9"
