#!/usr/bin/env python3
"""Manage Excalidraw diagrams for documentation.

This script finds ASCII art diagrams in documentation files and tracks their
relationship to generated Excalidraw JSON files.

Supported file types:
- AsciiDoc (.adoc) files in docs/
- Markdown (.md) files in examples/ and README.md
- CLAUDE.md in project root

Usage:
    python scripts/manage_diagrams.py list          # List all diagrams
    python scripts/manage_diagrams.py status        # Show which have Excalidraw files
    python scripts/manage_diagrams.py missing       # Show only missing diagrams
    python scripts/manage_diagrams.py manifest      # Generate manifest JSON
    python scripts/manage_diagrams.py add-refs      # Add image references
    python scripts/manage_diagrams.py generate      # Generate Excalidraw conversion prompts
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DiagramInfo:
    """Represents an ASCII art diagram found in a documentation file."""

    file_path: Path
    line_number: int
    title: str
    ascii_art: str
    context: str  # Surrounding text for context
    file_type: str  # 'adoc' or 'md'
    has_image_ref: bool = False

    @property
    def slug(self) -> str:
        """Generate a filename-safe slug from the title."""
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
        try:
            rel_path = self.file_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = self.file_path

        return {
            "file": str(rel_path),
            "line": self.line_number,
            "title": self.title,
            "slug": self.slug,
            "file_type": self.file_type,
            "has_excalidraw": self.has_excalidraw,
            "has_image_ref": self.has_image_ref,
            "excalidraw_path": str(self.expected_excalidraw_path.relative_to(PROJECT_ROOT)),
            "ascii_art_preview": self.ascii_art[:300] + "..."
            if len(self.ascii_art) > 300
            else self.ascii_art,
        }


# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DOCS_DIR = PROJECT_ROOT / "docs"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
ASSETS_DIR = DOCS_DIR / "assets" / "images"
DIAGRAMS_DIR = ASSETS_DIR / "diagrams"
EXCALIDRAW_DIR = DIAGRAMS_DIR / "excalidraw"

# ASCII art detection patterns
# Unicode box-drawing: ┌ ┐ └ ┘ │ ─ ├ ┤ ┬ ┴ ┼ ╔ ╗ ╚ ╝ ║ ═
# ASCII box-drawing with + corners: +---+

# Pattern for structured box diagrams using Unicode characters
# Matches boxes with corners (┌ ┐ └ ┘) and content lines with vertical bars (│ ║)
UNICODE_BOX_PATTERN = re.compile(
    r"([ \t]*[┌╔][─═┬]+[┐╗┬].*\n"  # Top border starting with corner
    r"(?:[ \t]*[│║├┤┬┴┼].*\n)*?"  # Content lines (flexible - can have row separators)
    r"[ \t]*[└╚][─═┴]+[┘╝].*)",  # Bottom border with corner
    re.MULTILINE,
)

# Pattern for ASCII-style box diagrams using + corners and - horizontal lines
# These are commonly used in source blocks: +---+---+
ASCII_BOX_PATTERN = re.compile(
    r"([ \t]*\+[-+]+\+.*\n"  # Top border: +---+---+
    r"(?:.*\|.*\n)+"  # Content lines with | vertical bars
    r"[ \t]*\+[-+]+\+.*)",  # Bottom border: +---+---+
    re.MULTILINE,
)

# Pattern for arrow-based flow diagrams - require vertical arrows (↓ or ▼)
# to distinguish from inline text with horizontal arrows (→)
# Limit line length to avoid backtracking issues
FLOW_DIAGRAM_PATTERN = re.compile(r"((?:[^\n]{0,200}[↓▼][^\n]{0,200}\n){2,})", re.MULTILINE)

# Pattern for Cypher-style relationship diagrams
# Matches patterns like: (Node)-[:RELATIONSHIP]->(Node)
# Using a simpler pattern to avoid exponential backtracking
CYPHER_DIAGRAM_PATTERN = re.compile(
    r"(\([^)]{1,50}\)-\[:[A-Z_]+\]->\([^)]{1,50}\))",
    re.MULTILINE,
)

# Pattern for markdown code blocks containing diagrams
# Matches ```text, ```ascii, ``` followed by box-drawing or flow content
# Use atomic grouping equivalent to avoid backtracking
MD_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:text|ascii|diagram)?\n((?:[^\n`]{0,300}\n){1,100})```",
    re.MULTILINE,
)

# Pattern for explicit diagram placeholders (backwards compatibility)
PLACEHOLDER_PATTERN = re.compile(r"\[DIAGRAM PLACEHOLDER:\s*([^\]]+)\]", re.IGNORECASE)

# Heading patterns
ADOC_HEADING_PATTERN = re.compile(r"^(=+)\s+(.+)$", re.MULTILINE)
MD_HEADING_PATTERN = re.compile(r"^(#+)\s+(.+)$", re.MULTILINE)

# Image reference patterns
ADOC_IMAGE_PATTERN = re.compile(r"image::([^\[]+)\[", re.IGNORECASE)
MD_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)", re.IGNORECASE)


def find_nearest_heading(content: str, position: int, file_type: str) -> str:
    """Find the nearest heading before the given position."""
    pattern = ADOC_HEADING_PATTERN if file_type == "adoc" else MD_HEADING_PATTERN

    best_match = None
    best_pos = -1

    for match in pattern.finditer(content):
        if match.start() < position and match.start() > best_pos:
            best_match = match
            best_pos = match.start()

    if best_match:
        return best_match.group(2).strip()
    return ""


def extract_title_from_context(
    content: str, diagram_start: int, diagram_text: str, file_path: Path, file_type: str
) -> str:
    """Extract a meaningful title for the diagram from surrounding context."""
    # First, try to find the nearest heading
    heading = find_nearest_heading(content, diagram_start, file_type)

    # Look for title comments or annotations near the diagram
    # Check 5 lines before the diagram
    lines_before = content[:diagram_start].split("\n")[-5:]
    before_text = "\n".join(lines_before)

    # Look for explicit title patterns
    title_patterns = [
        r"\.([A-Z][^\n]+)\n",  # AsciiDoc title like ".Memory Architecture"
        r"//\s*Title:\s*(.+)",  # Comment title
        r"<!--\s*Title:\s*(.+?)-->",  # HTML comment title
        r"\*\*([^*]+)\*\*",  # Bold text as title
    ]

    for pattern in title_patterns:
        match = re.search(pattern, before_text)
        if match:
            return match.group(1).strip()

    # Use heading if found
    if heading:
        # Make the title more specific by analyzing diagram content
        diagram_lower = diagram_text.lower()

        # Add specificity based on diagram content
        if "pipeline" in diagram_lower or "stage" in diagram_lower:
            if "pipeline" not in heading.lower():
                return f"{heading} Pipeline"
        elif "architecture" in diagram_lower:
            if "architecture" not in heading.lower():
                return f"{heading} Architecture"

        return heading

    # Fallback: generate from file name and line number
    stem = file_path.stem.replace("-", " ").replace("_", " ").title()
    return f"{stem} Diagram"


def has_image_ref_after(content: str, diagram_end: int, file_type: str) -> bool:
    """Check if there's an image reference after the diagram."""
    # Check the next 300 characters after the diagram
    after_content = content[diagram_end : diagram_end + 300]

    if file_type == "adoc":
        return bool(ADOC_IMAGE_PATTERN.search(after_content))
    else:
        return bool(MD_IMAGE_PATTERN.search(after_content))


def find_diagrams_in_file(file_path: Path, file_type: str) -> list[DiagramInfo]:
    """Find all ASCII art diagrams in a file."""
    diagrams = []

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
        return []

    # Track positions we've already processed to avoid duplicates
    processed_positions = set()

    # Find diagrams using multiple patterns
    all_matches = []

    # Pattern 1: Explicit diagram placeholders (highest priority)
    for match in PLACEHOLDER_PATTERN.finditer(content):
        # For placeholders, extract the title directly
        title = match.group(1).strip()
        # Find the containing source block with ASCII art
        # Look for [source,text] block after the placeholder
        block_start = match.start()
        block_match = re.search(
            r"\[source,text\]\n----\n(.*?)\n----",
            content[block_start : block_start + 2000],
            re.DOTALL,
        )
        if block_match:
            ascii_art = block_match.group(1)
            all_matches.append((match.start(), block_start + block_match.end(), ascii_art, title))
        else:
            all_matches.append((match.start(), match.end(), "", title))

    # Pattern 2: Unicode box diagrams
    for match in UNICODE_BOX_PATTERN.finditer(content):
        all_matches.append((match.start(), match.end(), match.group(0), None))

    # Pattern 3: ASCII-style box diagrams (+---+)
    for match in ASCII_BOX_PATTERN.finditer(content):
        all_matches.append((match.start(), match.end(), match.group(0), None))

    # Pattern 4: Flow diagrams with Unicode arrows
    for match in FLOW_DIAGRAM_PATTERN.finditer(content):
        diagram_text = match.group(0)
        # Only if it has substantial content
        if len(diagram_text.strip()) > 50:
            all_matches.append((match.start(), match.end(), diagram_text, None))

    # Pattern 5: Cypher-style relationship diagrams
    # Find all Cypher patterns and group consecutive ones
    cypher_matches = list(CYPHER_DIAGRAM_PATTERN.finditer(content))
    if len(cypher_matches) >= 2:
        # Group consecutive matches (within 200 chars of each other)
        groups = []
        current_group = [cypher_matches[0]]
        for m in cypher_matches[1:]:
            if m.start() - current_group[-1].end() < 200:
                current_group.append(m)
            else:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = [m]
        if len(current_group) >= 2:
            groups.append(current_group)

        for group in groups:
            start = group[0].start()
            end = group[-1].end()
            diagram_text = content[start:end]
            all_matches.append((start, end, diagram_text, None))

    # Pattern 6: Markdown code blocks with diagrams
    # Only check if the block contains diagram-like characters
    for match in MD_CODE_BLOCK_PATTERN.finditer(content):
        diagram_text = match.group(1)
        # Check if it looks like a diagram (has box-drawing or flow chars)
        if len(diagram_text.strip()) > 50 and any(c in diagram_text for c in "┌┐└┘│─╔╗╚╝║═▼↓"):
            all_matches.append((match.start(), match.end(), diagram_text, None))

    # Deduplicate and process matches
    for match_tuple in all_matches:
        start, end, diagram_text, explicit_title = match_tuple

        # Skip if we've already processed something at this position (within 50 chars)
        if any(abs(start - p) < 50 for p in processed_positions):
            continue

        # Skip very short diagrams (likely not real diagrams) unless they have an explicit title
        if not explicit_title and len(diagram_text.strip()) < 80:
            continue

        # Skip if it's inside a code block showing code (not a diagram)
        # Check for common code indicators
        if any(
            indicator in diagram_text
            for indicator in ["def ", "class ", "import ", "function ", "const ", "let ", "var "]
        ):
            continue

        processed_positions.add(start)

        # Calculate line number
        line_number = content[:start].count("\n") + 1

        # Use explicit title if provided, otherwise extract from context
        if explicit_title:
            title = explicit_title
        else:
            title = extract_title_from_context(content, start, diagram_text, file_path, file_type)

        # Check for image reference
        has_image = has_image_ref_after(content, end, file_type)

        # Get context (a few lines before and after)
        context_start = max(0, content.rfind("\n", 0, max(0, start - 200)))
        context_end = min(len(content), content.find("\n", min(len(content), end + 200)))
        context = content[context_start:context_end]

        diagram = DiagramInfo(
            file_path=file_path,
            line_number=line_number,
            title=title,
            ascii_art=diagram_text.strip(),
            context=context,
            file_type=file_type,
            has_image_ref=has_image,
        )
        diagrams.append(diagram)

    return diagrams


def find_all_diagrams() -> list[DiagramInfo]:
    """Find all ASCII art diagrams in docs and examples."""
    diagrams = []

    # Search docs directory for .adoc files
    if DOCS_DIR.exists():
        for adoc_file in DOCS_DIR.rglob("*.adoc"):
            # Skip generated/build directories
            if "_site" in str(adoc_file) or "node_modules" in str(adoc_file):
                continue
            diagrams.extend(find_diagrams_in_file(adoc_file, "adoc"))

    # Search examples directory for .md files
    if EXAMPLES_DIR.exists():
        for md_file in EXAMPLES_DIR.rglob("*.md"):
            # Skip generated/dependency directories
            if any(
                skip in str(md_file)
                for skip in ["node_modules", ".venv", "venv", "site-packages", "__pycache__"]
            ):
                continue
            diagrams.extend(find_diagrams_in_file(md_file, "md"))

    # Search for main README.md
    readme_path = PROJECT_ROOT / "README.md"
    if readme_path.exists():
        diagrams.extend(find_diagrams_in_file(readme_path, "md"))

    # Search for CLAUDE.md
    claude_path = PROJECT_ROOT / "CLAUDE.md"
    if claude_path.exists():
        diagrams.extend(find_diagrams_in_file(claude_path, "md"))

    # Sort by file path and line number
    return sorted(diagrams, key=lambda d: (str(d.file_path), d.line_number))


def deduplicate_diagrams(diagrams: list[DiagramInfo]) -> list[DiagramInfo]:
    """Remove duplicate diagrams based on slug (same title)."""
    seen_slugs: dict[str, DiagramInfo] = {}

    for diagram in diagrams:
        slug = diagram.slug
        if slug not in seen_slugs:
            seen_slugs[slug] = diagram
        else:
            # Keep the one with more context or from a more specific file
            existing = seen_slugs[slug]
            if len(diagram.ascii_art) > len(existing.ascii_art):
                seen_slugs[slug] = diagram

    return list(seen_slugs.values())


def print_list(diagrams: list[DiagramInfo]) -> None:
    """Print a simple list of all diagrams."""
    print(f"\nFound {len(diagrams)} ASCII art diagram(s):\n")

    for d in diagrams:
        try:
            rel_path = d.file_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = d.file_path
        print(f"  {rel_path}:{d.line_number}")
        print(f"      Title: {d.title}")
        print(f"      Slug: {d.slug}")
        print()


def print_status(diagrams: list[DiagramInfo]) -> None:
    """Print status of all diagrams."""
    print(f"\nFound {len(diagrams)} ASCII art diagram(s):\n")

    for d in diagrams:
        status_excalidraw = "✓" if d.has_excalidraw else "✗"
        status_image = "✓" if d.has_image_ref else "✗"

        try:
            rel_path = d.file_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = d.file_path

        print(f"  [{status_excalidraw}] {rel_path}:{d.line_number}")
        print(f"      Title: {d.title}")
        print(f"      Slug: {d.slug}")
        print(f"      Excalidraw: {status_excalidraw} {d.expected_excalidraw_path.name}")
        print(f"      Image ref: {status_image}")
        print()


def print_missing(diagrams: list[DiagramInfo]) -> None:
    """Print only diagrams missing Excalidraw files."""
    missing = [d for d in diagrams if not d.has_excalidraw]

    if not missing:
        print("\nAll diagrams have Excalidraw files! ✓\n")
        return

    print(f"\nMissing {len(missing)} Excalidraw file(s):\n")

    for d in missing:
        try:
            rel_path = d.file_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = d.file_path

        print(f"  {rel_path}:{d.line_number}")
        print(f"      Title: {d.title}")
        print(f"      Slug: {d.slug}")
        print(f"      Expected: {d.expected_excalidraw_path.name}")
        preview = d.ascii_art.split("\n")[0][:70]
        print(f"      Preview: {preview}...")
        print()


def generate_manifest(diagrams: list[DiagramInfo]) -> dict[str, Any]:
    """Generate a manifest of all diagrams."""
    return {
        "generated_by": "scripts/manage_diagrams.py",
        "project_root": str(PROJECT_ROOT),
        "excalidraw_dir": str(EXCALIDRAW_DIR),
        "total_diagrams": len(diagrams),
        "with_excalidraw": len([d for d in diagrams if d.has_excalidraw]),
        "missing_excalidraw": len([d for d in diagrams if not d.has_excalidraw]),
        "missing_image_refs": len([d for d in diagrams if not d.has_image_ref]),
        "diagrams": [d.to_dict() for d in diagrams],
    }


def classify_diagram_type(ascii_art: str) -> str:
    """Classify the type of diagram based on its content."""
    lower = ascii_art.lower()

    # Check for Cypher patterns
    if re.search(r"\([^)]+\)\s*-\[:[A-Z_]+\]->\s*\([^)]+\)", ascii_art):
        return "cypher"

    # Check for sequence diagram patterns
    if "│" in ascii_art and ("─>" in ascii_art or "→" in ascii_art):
        lines_with_arrows = sum(1 for line in ascii_art.split("\n") if "→" in line or "->" in line)
        if lines_with_arrows >= 3:
            return "sequence"

    # Check for table patterns
    if ascii_art.count("┼") >= 2 or (ascii_art.count("+") >= 4 and ascii_art.count("-") >= 10):
        return "table"

    # Check for flowchart patterns
    if "▼" in ascii_art or "↓" in ascii_art:
        return "flowchart"

    # Check for architecture patterns
    if "│" in ascii_art and ("┌" in ascii_art or "╔" in ascii_art or "+" in ascii_art):
        return "architecture"

    return "diagram"


def generate_prompts(diagrams: list[DiagramInfo]) -> None:
    """Generate structured prompts for Excalidraw conversion."""
    missing = [d for d in diagrams if not d.has_excalidraw]

    if not missing:
        print("\nAll diagrams have Excalidraw files! ✓\n")
        return

    print(f"\n# Excalidraw Conversion Prompts")
    print(f"# Generated for {len(missing)} missing diagram(s)\n")
    print("# Use the docs-excalidraw skill to convert each diagram.\n")
    print("=" * 60)

    for i, d in enumerate(missing, 1):
        try:
            rel_path = d.file_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = d.file_path

        diagram_type = classify_diagram_type(d.ascii_art)

        print(f"\n## Diagram {i}: {d.title}")
        print(f"Source: {rel_path}:{d.line_number}")
        print(f"Output: {d.expected_excalidraw_path.name}")
        print(f"Type: {diagram_type}")
        print(f"\n### ASCII Art:\n```")
        print(d.ascii_art)
        print("```")
        print(f"\n### Instructions:")
        print(f"Convert this {diagram_type} diagram to Excalidraw JSON following the")
        print(f"docs-excalidraw skill guidelines. Use Neo4j color palette:")
        print(f"  - Short-Term Memory: Green (#b2f2bb / #2f9e44)")
        print(f"  - Long-Term Memory: Yellow (#ffec99 / #f08c00)")
        print(f"  - Reasoning Memory: Purple (#d0bfff / #9c36b5)")
        print(f"  - Neo4j/Storage: Blue (#a5d8ff / #1971c2)")
        print("\n" + "=" * 60)


def add_image_reference(diagram: DiagramInfo) -> bool:
    """Add an image reference after the diagram if missing."""
    if diagram.has_image_ref:
        return False

    content = diagram.file_path.read_text(encoding="utf-8")

    # Find the diagram in the content
    diagram_pos = content.find(diagram.ascii_art[:50])  # Use first 50 chars to find
    if diagram_pos == -1:
        print(f"  Warning: Could not locate diagram for {diagram.title}")
        return False

    # Find the end of the diagram block
    diagram_end = diagram_pos + len(diagram.ascii_art)

    # Find the next line break after the diagram
    next_newline = content.find("\n", diagram_end)
    if next_newline == -1:
        next_newline = len(content)

    # Generate appropriate image reference based on file type
    if diagram.file_type == "adoc":
        # For AsciiDoc files
        try:
            image_path = diagram.expected_image_path.relative_to(ASSETS_DIR)
        except ValueError:
            image_path = diagram.expected_image_path.name
        image_ref = f"\n\nimage::{image_path}[{diagram.title}]\n"
    else:
        # For Markdown files
        try:
            image_path = diagram.expected_image_path.relative_to(PROJECT_ROOT)
        except ValueError:
            image_path = diagram.expected_image_path
        image_ref = f"\n\n![{diagram.title}]({image_path})\n"

    new_content = content[:next_newline] + image_ref + content[next_newline:]
    diagram.file_path.write_text(new_content, encoding="utf-8")

    print(f"  Added image reference for: {diagram.title}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Manage Excalidraw diagrams for documentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/manage_diagrams.py list          # List all ASCII art diagrams
    python scripts/manage_diagrams.py status        # Show Excalidraw file status
    python scripts/manage_diagrams.py missing       # Show missing Excalidraw files
    python scripts/manage_diagrams.py manifest      # Generate JSON manifest
    python scripts/manage_diagrams.py add-refs      # Add image references
        """,
    )
    parser.add_argument(
        "command",
        choices=["list", "status", "missing", "manifest", "add-refs", "generate"],
        help="Command to run",
    )
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--no-dedup", action="store_true", help="Don't deduplicate by slug")

    args = parser.parse_args()

    # Ensure directories exist
    EXCALIDRAW_DIR.mkdir(parents=True, exist_ok=True)

    # Find all diagrams
    diagrams = find_all_diagrams()

    # Optionally deduplicate
    if not args.no_dedup:
        diagrams = deduplicate_diagrams(diagrams)

    if args.command == "list":
        if args.json:
            print(json.dumps([d.to_dict() for d in diagrams], indent=2))
        else:
            print_list(diagrams)

    elif args.command == "status":
        if args.json:
            print(json.dumps(generate_manifest(diagrams), indent=2))
        else:
            print_status(diagrams)

    elif args.command == "missing":
        missing = [d for d in diagrams if not d.has_excalidraw]
        if args.json:
            print(json.dumps([d.to_dict() for d in missing], indent=2))
        else:
            print_missing(diagrams)

    elif args.command == "manifest":
        manifest = generate_manifest(diagrams)
        print(json.dumps(manifest, indent=2))

    elif args.command == "add-refs":
        print("\nAdding missing image references...\n")
        added = 0
        for d in diagrams:
            if add_image_reference(d):
                added += 1
        print(f"\nAdded {added} image reference(s)")

    elif args.command == "generate":
        generate_prompts(diagrams)


if __name__ == "__main__":
    main()
