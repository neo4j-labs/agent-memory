"""Check the rendered site's layout for defect classes a source-only check
cannot see: inline TOC duplication, images shrunk far below their natural
size, mobile horizontal overflow, leftover scaffolding headings and
inconsistent closing sections. See docs/audit-evidence/2026-09-17/
rendered-site-review-plan.md (finding F1) and scripts/render_docs.mjs.

Runs against a fresh `docs/build/site`; `make docs-render-check` builds it
first. Skips cleanly (with a clear reason) when the build or Playwright is
absent, rather than building or installing anything itself.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.rendered

PRODUCT_ALLOWLIST = {
    "neo4j", "nams", "langchain", "llamaindex", "crewai", "pydanticai",
    "pydantic", "openai", "mcp", "typescript", "javascript", "python",
    "aws", "gcp", "azure", "api", "apis", "sdk", "sdks", "cli", "json",
    "yaml", "rest", "llm", "llms", "gliner", "glirel", "agent", "skills",
    "bedrock", "vertex", "adk", "faq", "docker", "graphql", "cypher",
    "tck", "poleo", "pole+o", "strands", "google", "microsoft", "framework",
    "next.js", "nextjs", "react", "vercel", "fastmcp", "diffbot",
    "wikimedia", "wikipedia", "opentelemetry", "opik", "gds", "ollama",
    "litellm", "aura", "agents",
}
CONNECTOR_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "or", "over", "per", "the", "to", "via", "with",
    "vs", "is", "are", "your", "you",
}
CLOSING_HEADING_CANDIDATES = {
    "see also", "next steps", "related", "related tasks",
    "related documentation", "where to go next", "related links",
    "further reading",
}
ALLOWED_CLOSING_HEADINGS = {"See also", "Next steps"}


def looks_title_case(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'.+-]*", text)
    content_words = [w for w in words if len(w) > 2 and w.lower() not in CONNECTOR_WORDS]
    if len(content_words) < 3:
        return False
    capitalized = [w for w in content_words if w[0].isupper()]
    if len(capitalized) != len(content_words):
        return False
    non_product = [w for w in capitalized if w.lower() not in PRODUCT_ALLOWLIST]
    return len(non_product) >= 2


@pytest.fixture(scope="session")
def render_report(project_root: Path, tmp_path_factory) -> dict:
    site = project_root / "docs" / "build" / "site" / "agent-memory"
    if not site.is_dir():
        pytest.skip("docs/build/site/agent-memory not found; run `make docs` first")
    if not (project_root / "docs/diagrams/node_modules/playwright").is_dir():
        pytest.skip("Playwright not installed under docs/diagrams; run `make docs-install`")

    out_dir = tmp_path_factory.mktemp("render-report")
    result = subprocess.run(
        ["node", str(project_root / "scripts/render_docs.mjs"), str(site), str(out_dir)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"render_docs.mjs failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    report_path = out_dir / "report.json"
    assert report_path.exists(), f"render_docs.mjs did not write {report_path}"
    return json.loads(report_path.read_text())


def test_no_inline_toc(render_report: dict):
    offenders = [
        page
        for page, widths in render_report["pages"].items()
        for width, facts in widths.items()
        if facts.get("hasInlineToc")
        for page in [f"{page} ({width})"]
    ]
    assert not offenders, f"Pages with an inline #toc element: {offenders}"


def test_no_undersized_images(render_report: dict):
    offenders = []
    for page, widths in render_report["pages"].items():
        facts = widths.get("desktop") or {}
        for img in facts.get("imgs", []):
            natural_w = img.get("natural", [0, 0])[0]
            rendered_w = img.get("rendered", [0, 0])[0]
            if not natural_w or natural_w < 40 or img.get("broken"):
                continue
            ratio = rendered_w / natural_w
            if ratio < 0.45:
                offenders.append(f"{page}: {img.get('src')} rendered at {ratio:.2f}x natural width")
    assert not offenders, "Images rendered below 0.45x natural width:\n" + "\n".join(offenders)


def test_no_mobile_horizontal_overflow(render_report: dict):
    offenders = [
        page
        for page, widths in render_report["pages"].items()
        if (widths.get("mobile") or {}).get("docScrollX")
    ]
    assert not offenders, f"Pages that scroll horizontally at 390px: {offenders}"


def test_diagrams_have_captions(render_report: dict):
    offenders = []
    for page, widths in render_report["pages"].items():
        facts = widths.get("desktop") or {}
        for img in facts.get("imgs", []):
            src = img.get("src") or ""
            if "diagrams" not in src:
                continue
            if not img.get("caption"):
                offenders.append(f"{page}: {src}")
    assert not offenders, "Diagrams without a caption:\n" + "\n".join(offenders)


def test_headings_are_not_title_case(render_report: dict):
    offenders = []
    seen: set[tuple[str, str]] = set()
    for page, widths in render_report["pages"].items():
        facts = widths.get("desktop") or {}
        for heading in facts.get("headings", []):
            text = heading.get("text", "")
            key = (page, text)
            if key in seen:
                continue
            seen.add(key)
            if looks_title_case(text):
                offenders.append(f"{page}: {text!r}")
    assert not offenders, "Title Case headings (sentence case expected):\n" + "\n".join(offenders)


def test_no_leftover_scaffolding_headings(render_report: dict):
    offenders = []
    for page, widths in render_report["pages"].items():
        facts = widths.get("desktop") or {}
        for heading in facts.get("headings", []):
            if "Earlier section" in heading.get("text", ""):
                offenders.append(f"{page}: {heading['text']!r}")
    assert not offenders, "Leftover scaffolding headings:\n" + "\n".join(offenders)


def test_closing_sections_are_standardised(render_report: dict):
    offenders = []
    for page, widths in render_report["pages"].items():
        facts = widths.get("desktop") or {}
        headings = [h for h in facts.get("headings", []) if h.get("level") != "H1"]
        if not headings:
            continue
        last = headings[-1]["text"]
        if last.lower() in CLOSING_HEADING_CANDIDATES and last not in ALLOWED_CLOSING_HEADINGS:
            offenders.append(f"{page}: {last!r}")
    assert not offenders, (
        "Closing sections must be exactly 'See also' or 'Next steps':\n" + "\n".join(offenders)
    )
