#!/usr/bin/env python3
"""Check Antora source coverage and freshly rendered content, without live services.

Use --build for one clean temporary build, or --site-dir for an already built
artifact. External URLs and shared UI chrome are outside the content-link check.

A rendered check also confirms that every section id recorded in
scripts/docs_section_ids.json still resolves on its page, so a renamed or
removed section keeps a compatibility anchor. --record-section-ids adds the
section ids of the checked build to that baseline; it never removes one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "docs/modules/ROOT/pages"
SECTION_BASELINE = ROOT / "scripts/docs_section_ids.json"
QUADRANTS = ("tutorials", "how-to", "reference", "explanation")
XREF = re.compile(r"xref:([^\s\[]+)\[")


def page_target(target: str, source: str) -> str | None:
    """Resolve this component's page IDs, excluding other resource families."""
    target = target.split("#", 1)[0]
    if not target:
        return source
    if "$" in target or ":" in target:
        return None
    # Antora treats a page ID as module-relative, not source-directory-relative.
    if target.startswith("./") or target.startswith("../"):
        return os.path.normpath(str(Path(source).parent / target))
    return target.lstrip("/")


NAMED = re.compile(r"\s*[A-Za-z_][\w-]*=")
QUOTED = {quote: re.compile(rf"{quote}(?:\\.|[^{quote}\\])*{quote}") for quote in "\"'"}


def split_attributes(attributes: str) -> list[str]:
    """Split an AsciiDoc attribute list on commas, as Asciidoctor does.

    A double or single quote opens a quoted value at the start of a field or
    directly after ``name=``, so ``title="One, two"`` stays one field. A quote
    that is never closed is literal text.
    """
    fields: list[str] = []
    field = ""
    index = 0
    while index < len(attributes):
        char = attributes[index]
        if char == ",":
            fields.append(field)
            field = ""
            index += 1
            continue
        if char in QUOTED and (not field.strip() or NAMED.fullmatch(field)):
            quoted = QUOTED[char].match(attributes, index)
            if quoted:
                field += quoted.group()
                index = quoted.end()
                continue
        field += char
        index += 1
    fields.append(field)
    return fields


def source_report(pages: Path = PAGES) -> dict:
    content = {str(p.relative_to(pages)): p.read_text() for p in sorted(pages.rglob("*.adoc"))}
    errors: list[str] = []
    routes: dict[str, set[str]] = {}
    inventory = []
    for name, text in content.items():
        targets = {page_target(m.group(1), name) for m in XREF.finditer(text)} - {None}
        routes[name] = targets
        for target in sorted(targets):
            if target not in content:
                errors.append(f"{name}: missing page {target}")
        errors.extend(
            f"{name}: leftover scaffolding heading: {heading.strip()}"
            for heading in re.findall(r"^={1,6}[ \t]+(.*Earlier section.*)$", text, re.M)
        )
        for line, attributes in re.findall(r"^(image::?[^\[]+\[([^\n]*)\])", text, re.M):
            fields = split_attributes(attributes)
            positional = [
                field.strip().strip("\"'") for field in fields[1:] if not NAMED.match(field)
            ]
            if any(not re.fullmatch(r"\d+(?:%|px)?", field) for field in positional):
                errors.append(f"{name}: quote comma-containing image alt text: {line}")
        inventory.append(
            {
                "page": name,
                "purpose": name.split("/")[0] if "/" in name else "entry",
                "title": next(
                    (line[2:] for line in text.splitlines() if line.startswith("= ")), name
                ),
                "code_blocks": len(re.findall(r"^\[source[,\]]", text, re.M)),
            }
        )

    for quadrant in QUADRANTS:
        hub = f"{quadrant}/index.adoc"
        if hub not in content:
            errors.append(f"Missing quadrant hub: {hub}")
            continue
        # Only linked indexes extend the navigation route. A random leaf linking
        # to another leaf cannot accidentally satisfy an omitted hub entry.
        reachable: set[str] = set()
        pending = [hub]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for target in routes.get(current, set()):
                if target.startswith(quadrant + "/"):
                    reachable.add(target)
                    if target.endswith("/index.adoc"):
                        pending.append(target)
        missing = {
            name for name in content if name.startswith(quadrant + "/") and name != hub
        } - reachable
        errors.extend(f"{hub}: no index route to {name}" for name in sorted(missing))

    nav = pages.parent / "nav.adoc"
    if nav.exists():
        nav_targets = {
            page_target(m.group(1), "nav.adoc") for m in XREF.finditer(nav.read_text())
        } - {None}
        errors.extend(
            f"nav.adoc: missing page {target}" for target in sorted(nav_targets - content.keys())
        )
        # Legacy forwarding pages intentionally have no sidebar entry.
        forwards = {name for name, text in content.items() if ":page-role: legacy-redirect" in text}
        errors.extend(
            f"nav.adoc: no route to {name}"
            for name in sorted(content.keys() - nav_targets - forwards)
        )
    return {"pages": inventory, "errors": errors}


class Document(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.section_ids: list[str] = []
        self.links: list[str] = []
        self.images: list[dict[str, str | None]] = []
        self.unrendered_headings: list[str] = []
        self.in_article = False
        self.code_depth = 0
        self.paragraph: list[str] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attributes):
        attrs = dict(attributes)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "article" and "doc" in (attrs.get("class") or "").split():
            self.in_article = True
        if tag in {"pre", "code"}:
            self.code_depth += 1
            if self.paragraph is not None:
                # An inline-code example is not an accidentally rendered heading.
                self.paragraph.append("\ufffc")
        if self.in_article and tag == "p" and not self.code_depth:
            self.paragraph = []
        if self.in_article and tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if self.in_article and tag == "img":
            self.images.append(attrs)
        if self.in_article and tag in {"h2", "h3", "h4", "h5", "h6"} and attrs.get("id"):
            self.section_ids.append(attrs["id"])

    def handle_endtag(self, tag: str):
        if tag == "p" and self.paragraph is not None:
            text = "".join(self.paragraph).strip()
            if re.match(r"={1,6}[ \t]+\S", text):
                self.unrendered_headings.append(text)
            self.paragraph = None
        if tag in {"pre", "code"}:
            self.code_depth = max(0, self.code_depth - 1)
        if tag == "article":
            self.in_article = False

    def handle_data(self, data: str):
        if self.paragraph is not None and not self.code_depth:
            self.paragraph.append(data)


def rendered_report(site: Path, section_baseline: dict[str, list[str]] | None = None) -> dict:
    """Check rendered links, fragments, images and headings under ``site``.

    Pass ``section_baseline`` (usually ``load_section_baseline()``) for a full
    Antora build, so a recorded section id that stops resolving is an error too.
    Leave it out for a partial or synthetic site that does not hold every page.
    """
    site = site.resolve()
    documents = {p.resolve(): Document(p.read_text()) for p in site.rglob("*.html")}
    errors = []
    links = images = 0
    for source, document in documents.items():
        errors.extend(
            f"{source.relative_to(site)}: unrendered AsciiDoc heading: {heading!r}"
            for heading in document.unrendered_headings
        )
        for href in document.links:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                continue
            links += 1
            path = unquote(parts.path)
            target = (
                (
                    (site / path.lstrip("/")) if path.startswith("/") else source.parent / path
                ).resolve()
                if path
                else source
            )
            if target.is_dir():
                target /= "index.html"
            label = f"{source.relative_to(site)} -> {href}"
            if not target.is_relative_to(site) or not target.exists():
                errors.append(f"{label}: missing target")
            elif parts.fragment and target.suffix == ".html":
                parsed = documents.get(target)
                if parsed is not None and unquote(parts.fragment) not in parsed.ids:
                    errors.append(f"{label}: missing anchor")
        for attrs in document.images:
            images += 1
            src = attrs.get("src") or ""
            parts = urlsplit(src)
            if not parts.scheme and not parts.netloc:
                target = (
                    (site / unquote(parts.path).lstrip("/"))
                    if parts.path.startswith("/")
                    else source.parent / unquote(parts.path)
                )
                if not target.exists():
                    errors.append(f"{source.relative_to(site)}: missing image {src}")
            if not (attrs.get("alt") or "").strip():
                errors.append(f"{source.relative_to(site)}: image has no description: {src}")
            for dimension in ("width", "height"):
                value = attrs.get(dimension)
                if value is not None and not re.fullmatch(r"\d+(?:%|px)?", value):
                    errors.append(
                        f"{source.relative_to(site)}: invalid {dimension}={value!r}: {src}"
                    )
    if not documents:
        errors.append(f"No HTML files in {site}")
    if section_baseline is not None:
        errors.extend(section_baseline_errors(site, section_baseline))
    return {
        "html_files": len(documents),
        "content_links": links,
        "images": images,
        "errors": errors,
    }


def section_ids(site: Path) -> dict[str, list[str]]:
    """Map each rendered page (relative to the site root) to its section ids."""
    site = site.resolve()
    pages = {}
    for path in sorted(site.rglob("*.html")):
        ids = Document(path.read_text()).section_ids
        if ids:
            pages[path.relative_to(site).as_posix()] = sorted(set(ids))
    return pages


def load_section_baseline(path: Path = SECTION_BASELINE) -> dict[str, list[str]]:
    return json.loads(path.read_text())["pages"] if path.exists() else {}


def section_baseline_errors(site: Path, baseline: dict[str, list[str]]) -> list[str]:
    """Every recorded section id must still resolve on its page.

    Readers and other sites link to these fragments. When a section is renamed
    or merged, keep the old id with an ``anchor:<old-id>[]`` macro at the
    section that now covers the content.
    """
    site = site.resolve()
    errors = []
    for page, ids in sorted(baseline.items()):
        path = site / page
        if not path.exists():
            errors.append(f"{page}: page with recorded section ids is missing")
            continue
        present = Document(path.read_text()).ids
        errors.extend(
            f"{page}#{section}: recorded section id no longer resolves; add an anchor"
            for section in ids
            if section not in present
        )
    return errors


def record_section_ids(site: Path, path: Path = SECTION_BASELINE) -> int:
    """Add the built site's section ids to the baseline; never drop a recorded id."""
    data = (
        json.loads(path.read_text())
        if path.exists()
        else {"description": "Rendered section ids that must keep resolving.", "pages": {}}
    )
    before = sum(len(ids) for ids in data["pages"].values())
    for page, ids in section_ids(site).items():
        data["pages"][page] = sorted(set(data["pages"].get(page, [])) | set(ids))
    data["pages"] = dict(sorted(data["pages"].items()))
    path.write_text(json.dumps(data, indent=1) + "\n")
    return sum(len(ids) for ids in data["pages"].values()) - before


def build_site(destination: Path, *, root: Path = ROOT) -> tuple[str, list[str]]:
    command = [
        str(root / "docs/node_modules/.bin/antora"),
        "--to-dir",
        str(destination),
        "antora-playbook.yml",
    ]
    if os.environ.get("ANTORA_UI_BUNDLE"):
        command[1:1] = ["--ui-bundle-url", os.environ["ANTORA_UI_BUNDLE"]]
    result = subprocess.run(command, cwd=root / "docs", capture_output=True, text=True, timeout=180)
    log = result.stdout + result.stderr
    errors = []
    if result.returncode:
        errors.append(f"Antora exited {result.returncode}\n{log}")
    warnings = []
    for line in log.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {}
        level = event.get("level") if isinstance(event, dict) else None
        if level in (40, 50, 60, "warn", "warning", "error", "fatal") or re.search(
            r"\b(?:WARN|WARNING|ERROR|FATAL)\b", line
        ):
            warnings.append(line)
    errors.extend(warnings)
    return log, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true")
    group.add_argument("--site-dir", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        help="Write inventory, full build log and validation evidence as JSON",
    )
    parser.add_argument(
        "--record-section-ids",
        action="store_true",
        help=f"Add the checked build's section ids to {SECTION_BASELINE.relative_to(ROOT)}",
    )
    args = parser.parse_args()
    if args.record_section_ids and not (args.build or args.site_dir):
        parser.error("--record-section-ids needs --build or --site-dir")
    sys.path.insert(0, str(ROOT))
    from scripts.manage_diagrams import check_manifest

    report = {"source": source_report(), "diagrams": check_manifest()}
    errors = list(report["source"]["errors"]) + report["diagrams"]["errors"]
    with tempfile.TemporaryDirectory(prefix="agent-memory-docs-check-") as directory:
        site = args.site_dir
        if args.build:
            site = Path(directory) / "site"
            report["build_log"], build_errors = build_site(site)
            errors.extend(build_errors)
        if site is not None:
            report["rendered"] = rendered_report(site, load_section_baseline())
            errors.extend(report["rendered"]["errors"])
            # Record only from a clean build, so a broken page cannot seed the baseline.
            if args.record_section_ids and not errors:
                added = record_section_ids(site)
                print(f"Recorded {added} new section ids in {SECTION_BASELINE.relative_to(ROOT)}")
    report["errors"] = errors
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    for error in errors:
        print(error)
    print(f"Checked {len(report['source']['pages'])} pages; {len(errors)} errors.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
