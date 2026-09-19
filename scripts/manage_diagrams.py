#!/usr/bin/env python3
"""Manage Excalidraw diagrams for documentation.

This script checks published image provenance and editable source/export hashes,
and retains the placeholder helpers for adding new diagrams.

Usage:
    python scripts/manage_diagrams.py list          # List all placeholders
    python scripts/manage_diagrams.py status        # Show which have diagrams
    python scripts/manage_diagrams.py missing       # Show only missing diagrams
    python scripts/manage_diagrams.py manifest      # Generate manifest JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DiagramPlaceholder:
    """Represents a diagram placeholder in an AsciiDoc file."""

    file_path: Path
    line_number: int
    title: str
    ascii_art: str
    table_content: str
    has_image_ref: bool = False
    excalidraw_file: Path | None = None

    @property
    def slug(self) -> str:
        """Generate a filename-safe slug from the title."""
        # Convert to lowercase, replace spaces with hyphens
        slug = self.title.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug

    @property
    def expected_excalidraw_path(self) -> Path:
        """Expected path for the Excalidraw JSON file."""
        return EXCALIDRAW_DIR / f"{self.slug}.excalidraw"

    @property
    def expected_image_path(self) -> Path:
        """Expected path for the exported PNG image."""
        return DIAGRAMS_DIR / f"{self.slug}.png"

    @property
    def has_excalidraw(self) -> bool:
        """Check if Excalidraw JSON file exists."""
        return self.expected_excalidraw_path.exists()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "file": str(self.file_path.relative_to(DOCS_DIR)),
            "line": self.line_number,
            "title": self.title,
            "slug": self.slug,
            "has_excalidraw": self.has_excalidraw,
            "has_image_ref": self.has_image_ref,
            "excalidraw_path": str(self.expected_excalidraw_path.relative_to(DOCS_DIR)),
            "image_path": str(self.expected_image_path.relative_to(DOCS_DIR)),
            "ascii_art": self.ascii_art[:200] + "..."
            if len(self.ascii_art) > 200
            else self.ascii_art,
        }


# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "modules" / "ROOT" / "images"
DIAGRAMS_DIR = ASSETS_DIR / "diagrams"
EXCALIDRAW_DIR = DOCS_DIR / "assets" / "diagrams" / "excalidraw"
MANIFEST = DOCS_DIR / "diagrams" / "manifest.json"


def check_manifest(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Check actual published uses; names alone never establish provenance."""
    manifest = root / "docs/diagrams/manifest.json"
    if not manifest.exists():
        return {"errors": ["Missing docs/diagrams/manifest.json"], "diagrams": []}
    data = json.loads(manifest.read_text())
    records = data["diagrams"]
    errors: list[str] = []
    outputs = {record["output"] for record in records}
    if len(outputs) != len(records):
        errors.append("Duplicate output entries in diagram manifest")
    referenced_outputs: set[str] = set()
    for page in (root / "docs/modules/ROOT/pages").rglob("*.adoc"):
        for target in re.findall(r"image::([^\[]+)\[", page.read_text()):
            if target.startswith("http"):
                continue
            output = "docs/modules/ROOT/images/" + target
            referenced_outputs.add(output)
            if output not in outputs:
                errors.append(f"{page.relative_to(root)}: untracked published image {target}")
    diagrams_dir = root / "docs/modules/ROOT/images/diagrams"
    if diagrams_dir.is_dir():
        for file in sorted(diagrams_dir.iterdir()):
            if not file.is_file():
                continue
            output = str(file.relative_to(root))
            if output not in outputs and output not in referenced_outputs:
                errors.append(
                    f"Orphaned file (in neither the manifest nor a page reference): {output}"
                )
    for record in records:
        for kind in ("source", "output"):
            name = record.get(kind)
            if not name:
                if kind == "source" and not record.get("note"):
                    errors.append(f"{record['output']}: source exception needs a reason")
                continue
            file = root / name
            if not file.exists():
                errors.append(f"Missing {kind}: {name}")
                continue
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            if digest != record.get(kind + "_sha256"):
                errors.append(f"Changed {kind}; re-export/review before recording hashes: {name}")
    sources = set((root / "docs/assets/diagrams/excalidraw").glob("*.excalidraw"))
    sources.update(root / record["source"] for record in records if record.get("source"))
    for source in sorted(sources):
        if not source.exists():
            continue
        try:
            scene = json.loads(source.read_text())
        except (ValueError, OSError) as error:
            errors.append(f"Invalid scene {source.relative_to(root)}: {error}")
            continue
        elements = scene.get("elements", [])
        ids = [element.get("id") for element in elements]
        if scene.get("type") != "excalidraw" or not ids or None in ids or len(ids) != len(set(ids)):
            errors.append(f"Invalid scene or duplicate element IDs: {source.relative_to(root)}")
        for element in elements:
            targets = [
                binding.get("elementId")
                for binding in (element.get("startBinding"), element.get("endBinding"))
                if binding
            ]
            targets.extend(bound.get("id") for bound in element.get("boundElements") or [])
            if element.get("containerId"):
                targets.append(element["containerId"])
            if any(target not in ids for target in targets):
                errors.append(f"Dangling binding in {source.relative_to(root)}: {element['id']}")
    return {"diagrams": records, "errors": errors}


# Regex patterns
PLACEHOLDER_PATTERN = re.compile(r"\[DIAGRAM PLACEHOLDER:\s*([^\]]+)\]", re.IGNORECASE)

# Pattern to find the full table containing the placeholder
TABLE_PATTERN = re.compile(
    r"(\.[^\n]+\n)?"  # Optional title like .Memory Architecture
    r"\[cols=[^\]]+\][^\n]*\n"  # [cols="1", options="header"]
    r"\|===\n"  # Table start
    r"(.*?)"  # Table content (non-greedy)
    r"\n\|===",  # Table end
    re.DOTALL,
)

# Pattern to find image references after tables
IMAGE_REF_PATTERN = re.compile(r"image::([^\[]+)\[", re.IGNORECASE)


def find_placeholders(docs_dir: Path) -> list[DiagramPlaceholder]:
    """Find all diagram placeholders in AsciiDoc files."""
    placeholders = []

    for adoc_file in docs_dir.rglob("*.adoc"):
        # Skip generated files
        if "_site" in str(adoc_file) or "node_modules" in str(adoc_file):
            continue

        content = adoc_file.read_text(encoding="utf-8")

        # Find all placeholder markers
        for match in PLACEHOLDER_PATTERN.finditer(content):
            title = match.group(1).strip()
            pos = match.start()
            line_number = content[:pos].count("\n") + 1

            # Find the containing table
            ascii_art = ""
            table_content = ""

            # Look backwards from the placeholder to find the table start
            table_match = None
            for tm in TABLE_PATTERN.finditer(content):
                if tm.start() < pos < tm.end():
                    table_match = tm
                    break

            if table_match:
                table_content = table_match.group(0)
                # Extract ASCII art from source blocks within the table
                source_match = re.search(
                    r"\[source,text\]\n----\n(.*?)\n----", table_content, re.DOTALL
                )
                if source_match:
                    ascii_art = source_match.group(1)

            # Check if there's an image reference after the table
            has_image_ref = False
            table_end_pos = table_match.end() if table_match else pos
            next_content = content[table_end_pos : table_end_pos + 200]
            if IMAGE_REF_PATTERN.search(next_content):
                has_image_ref = True

            placeholder = DiagramPlaceholder(
                file_path=adoc_file,
                line_number=line_number,
                title=title,
                ascii_art=ascii_art,
                table_content=table_content,
                has_image_ref=has_image_ref,
            )
            placeholders.append(placeholder)

    return sorted(placeholders, key=lambda p: (str(p.file_path), p.line_number))


def print_status(placeholders: list[DiagramPlaceholder]) -> None:
    """Print status of all placeholders."""
    print(f"\nFound {len(placeholders)} diagram placeholder(s):\n")

    for p in placeholders:
        status_excalidraw = "✓" if p.has_excalidraw else "✗"
        status_image = "✓" if p.has_image_ref else "✗"

        rel_path = p.file_path.relative_to(DOCS_DIR)
        print(f"  [{status_excalidraw}] {rel_path}:{p.line_number}")
        print(f"      Title: {p.title}")
        print(f"      Slug: {p.slug}")
        print(f"      Excalidraw: {status_excalidraw} {p.expected_excalidraw_path.name}")
        print(f"      Image ref: {status_image}")
        print()


def print_missing(placeholders: list[DiagramPlaceholder]) -> None:
    """Print only placeholders missing Excalidraw files."""
    missing = [p for p in placeholders if not p.has_excalidraw]

    if not missing:
        print("\nAll placeholders have Excalidraw files! ✓\n")
        return

    print(f"\nMissing {len(missing)} Excalidraw file(s):\n")

    for p in missing:
        rel_path = p.file_path.relative_to(DOCS_DIR)
        print(f"  {rel_path}:{p.line_number}")
        print(f"      Title: {p.title}")
        print(f"      Expected: {p.expected_excalidraw_path.name}")
        if p.ascii_art:
            preview = p.ascii_art.split("\n")[0][:60]
            print(f"      ASCII preview: {preview}...")
        print()


def generate_manifest(placeholders: list[DiagramPlaceholder]) -> dict[str, Any]:
    """Generate a manifest of all diagrams."""
    return {
        "generated_by": "scripts/manage_diagrams.py",
        "docs_dir": str(DOCS_DIR),
        "excalidraw_dir": str(EXCALIDRAW_DIR),
        "total_placeholders": len(placeholders),
        "missing_excalidraw": len([p for p in placeholders if not p.has_excalidraw]),
        "missing_image_refs": len([p for p in placeholders if not p.has_image_ref]),
        "placeholders": [p.to_dict() for p in placeholders],
    }


def add_image_reference(placeholder: DiagramPlaceholder) -> bool:
    """Add an image reference after the placeholder table if missing."""
    if placeholder.has_image_ref:
        return False

    content = placeholder.file_path.read_text(encoding="utf-8")

    # Find the table end
    table_match = None
    for tm in TABLE_PATTERN.finditer(content):
        if f"[DIAGRAM PLACEHOLDER: {placeholder.title}]" in tm.group(0):
            table_match = tm
            break

    if not table_match:
        print(f"  Warning: Could not find table for {placeholder.title}")
        return False

    # Insert image reference after table
    table_end = table_match.end()
    image_ref = f'\n\nimage::{placeholder.expected_image_path.relative_to(ASSETS_DIR)}["{placeholder.title}",width=100%,link=self]\n'

    new_content = content[:table_end] + image_ref + content[table_end:]
    placeholder.file_path.write_text(new_content, encoding="utf-8")

    print(f"  Added image reference for: {placeholder.title}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Manage Excalidraw diagrams for documentation")
    parser.add_argument(
        "command",
        choices=["list", "status", "missing", "manifest", "add-refs"],
        help="Command to run",
    )
    parser.add_argument("--json", action="store_true", help="Output in JSON format")

    args = parser.parse_args()

    # Find all placeholders
    placeholders = find_placeholders(DOCS_DIR / "modules" / "ROOT" / "pages")
    published = check_manifest()

    if args.command == "list":
        if args.json:
            print(json.dumps([p.to_dict() for p in placeholders], indent=2))
        else:
            for p in placeholders:
                print(f"{p.file_path.relative_to(DOCS_DIR)}:{p.line_number}: {p.title}")

    elif args.command == "status":
        if args.json:
            print(json.dumps(published, indent=2))
        else:
            print(f"{len(published['diagrams'])} tracked image exports in the provenance manifest")
            print(f"{len(list(EXCALIDRAW_DIR.glob('*.excalidraw')))} canonical editable scenes")
            for error in published["errors"]:
                print(error)
            if placeholders:
                print_status(placeholders)

    elif args.command == "missing":
        missing = [p for p in placeholders if not p.has_excalidraw]
        if args.json:
            print(
                json.dumps(
                    {
                        "errors": published["errors"],
                        "missing_placeholders": [p.to_dict() for p in missing],
                    },
                    indent=2,
                )
            )
        else:
            for error in published["errors"]:
                print(error)
            if placeholders:
                print_missing(placeholders)
            elif not published["errors"]:
                print("All published image sources and exports are current.")

    elif args.command == "manifest":
        manifest = {**published, "placeholders": generate_manifest(placeholders)}
        print(json.dumps(manifest, indent=2))

    elif args.command == "add-refs":
        print("\nAdding missing image references...\n")
        added = 0
        for p in placeholders:
            if add_image_reference(p):
                added += 1
        print(f"\nAdded {added} image reference(s)")

    # Exit with error if there are missing diagrams
    missing_count = len([p for p in placeholders if not p.has_excalidraw])
    if args.command in ["status", "missing"] and published["errors"]:
        sys.exit(1)
    if args.command in ["status", "missing"] and missing_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
