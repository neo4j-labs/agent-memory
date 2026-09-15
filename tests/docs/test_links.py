"""Tests for documentation link validation.

These tests verify that internal links, cross-references, and structure are correct.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.mark.docs
class TestXrefLinks:
    """Test AsciiDoc xref: cross-references."""

    def test_all_xref_links_have_targets(self, docs_dir: Path, all_adoc_files: list[Path]):
        """All xref: links should point to existing files."""
        xref_pattern = re.compile(r"xref:([^\[]+)\[")
        broken_links: list[str] = []

        for adoc_file in all_adoc_files:
            content = adoc_file.read_text(encoding="utf-8")
            file_dir = adoc_file.parent

            # For nav.adoc (in modules/ROOT), xrefs are relative to pages dir
            is_nav_file = adoc_file.name == "nav.adoc"

            for match in xref_pattern.finditer(content):
                target = match.group(1)

                # Strip anchor references (e.g., #section_name)
                if "#" in target:
                    target = target.split("#")[0]

                # Handle different path formats
                if target.startswith("../"):
                    # Relative path going up - resolve from file's directory
                    target_path = (file_dir / target).resolve()
                elif target.startswith("/"):
                    # Absolute path from docs root
                    target_path = docs_dir / target.lstrip("/")
                elif "/" in target or is_nav_file:
                    # Antora page ID (e.g., "explanation/memory-types.adoc")
                    # or nav.adoc xrefs - resolve from pages directory root
                    target_path = docs_dir / target
                else:
                    # Simple filename - try same directory first, then docs root
                    # (Antora resolves simple names from pages root)
                    target_path = file_dir / target
                    if not target_path.exists():
                        target_path = docs_dir / target

                # Normalize the path
                target_path = target_path.resolve()

                # Check if file exists
                if not target_path.exists():
                    try:
                        relative_source = adoc_file.relative_to(docs_dir)
                    except ValueError:
                        # nav.adoc is outside docs_dir
                        relative_source = adoc_file.parent.name + "/" + adoc_file.name
                    broken_links.append(f"{relative_source} -> {target}")

        if broken_links:
            # Group by source file for readability
            pytest.fail(
                f"Found {len(broken_links)} broken xref links:\n"
                + "\n".join(f"  {link}" for link in broken_links[:20])
                + (f"\n  ... and {len(broken_links) - 20} more" if len(broken_links) > 20 else "")
            )

    def test_no_empty_xref_links(self, all_adoc_files: list[Path], docs_dir: Path):
        """xref links should not be empty."""
        empty_pattern = re.compile(r"xref:\[\]|xref:\s*\[")
        violations = []

        for adoc_file in all_adoc_files:
            content = adoc_file.read_text(encoding="utf-8")
            if empty_pattern.search(content):
                violations.append(str(adoc_file.relative_to(docs_dir)))

        assert not violations, f"Empty xref links found in: {violations}"


@pytest.mark.docs
class TestQuadrantStructure:
    """Test Diataxis quadrant directory structure."""

    def test_all_quadrants_exist(self, quadrant_dirs: dict[str, Path]):
        """All four Diataxis quadrants should have directories."""
        for name, path in quadrant_dirs.items():
            assert path.exists(), f"Quadrant directory missing: {name}/"

    def test_all_quadrants_have_index(self, quadrant_dirs: dict[str, Path]):
        """Each quadrant should have an index.adoc file."""
        for name, path in quadrant_dirs.items():
            if path.exists():
                index = path / "index.adoc"
                assert index.exists(), f"Missing {name}/index.adoc"

    def test_tutorials_have_content(self, docs_dir: Path):
        """Tutorials quadrant should have tutorial files."""
        tutorials = docs_dir / "tutorials"
        if not tutorials.exists():
            pytest.skip("tutorials directory not found")

        tutorial_files = [f for f in tutorials.glob("*.adoc") if f.name != "index.adoc"]
        assert len(tutorial_files) >= 1, "No tutorial files found"

    def test_howto_has_content(self, docs_dir: Path):
        """How-to quadrant should have guide files."""
        howto = docs_dir / "how-to"
        if not howto.exists():
            pytest.skip("how-to directory not found")

        guide_files = [f for f in howto.glob("*.adoc") if f.name != "index.adoc"]
        assert len(guide_files) >= 1, "No how-to guide files found"

    def test_reference_has_content(self, docs_dir: Path):
        """Reference quadrant should have reference files."""
        reference = docs_dir / "reference"
        if not reference.exists():
            pytest.skip("reference directory not found")

        ref_files = list(reference.glob("*.adoc")) + list(reference.glob("**/*.adoc"))
        assert len(ref_files) >= 2, "Insufficient reference files found"

    def test_explanation_has_content(self, docs_dir: Path):
        """Explanation quadrant should have explanation files."""
        explanation = docs_dir / "explanation"
        if not explanation.exists():
            pytest.skip("explanation directory not found")

        exp_files = [f for f in explanation.glob("*.adoc") if f.name != "index.adoc"]
        assert len(exp_files) >= 1, "No explanation files found"


@pytest.mark.docs
class TestNavigationConsistency:
    """Test that navigation is consistent across files."""

    def test_index_links_to_quadrants(self, docs_dir: Path):
        """Main index should link to all quadrant indexes."""
        index = docs_dir / "index.adoc"
        if not index.exists():
            pytest.skip("index.adoc not found")

        content = index.read_text(encoding="utf-8")
        quadrants = ["tutorials", "how-to", "reference", "explanation"]

        for quadrant in quadrants:
            # Should have a link to this quadrant
            assert quadrant in content.lower(), f"No link to {quadrant} in index.adoc"

    def test_quadrant_indexes_link_to_content(self, docs_dir: Path):
        """Every leaf is reachable directly or through a linked subindex."""
        from scripts.check_docs import source_report

        errors = source_report(docs_dir)["errors"]
        assert not errors, "\n".join(errors)


@pytest.mark.docs
class TestImageReferences:
    """Test image references in documentation."""

    def test_image_references_have_files(self, docs_dir: Path, all_adoc_files: list[Path]):
        """Image references should point to existing files."""
        image_pattern = re.compile(r"image::?([^\[]+)\[")
        missing_images: list[str] = []

        # Antora resolves image::path[] against modules/ROOT/images/, which is a
        # sibling of the pages directory.
        images_dir = docs_dir.parent / "images"

        for adoc_file in all_adoc_files:
            content = adoc_file.read_text(encoding="utf-8")

            for match in image_pattern.finditer(content):
                image_path = match.group(1)

                # Skip external URLs
                if image_path.startswith("http"):
                    continue

                # Check in the Antora images directory
                full_path = images_dir / image_path
                if not full_path.exists():
                    # Also check relative to file
                    alt_path = adoc_file.parent / image_path
                    if not alt_path.exists():
                        try:
                            relative_source = adoc_file.relative_to(docs_dir)
                        except ValueError:
                            relative_source = adoc_file.name
                        missing_images.append(f"{relative_source}: {image_path}")

        assert not missing_images, "Missing images:\n" + "\n".join(missing_images)


@pytest.mark.docs
class TestOrphanedFiles:
    """Test for orphaned documentation files."""

    def test_no_orphaned_adoc_files(self, docs_dir: Path, all_adoc_files: list[Path]):
        """All .adoc files should be linked from somewhere."""
        # Build a set of all referenced files
        xref_pattern = re.compile(r"xref:([^\[]+)\[")
        include_pattern = re.compile(r"include::([^\[]+)\[")

        referenced_files: set[str] = set()

        # For nav.adoc, xrefs are relative to pages directory
        for adoc_file in all_adoc_files:
            content = adoc_file.read_text(encoding="utf-8")
            file_dir = adoc_file.parent
            is_nav_file = adoc_file.name == "nav.adoc"

            for pattern in [xref_pattern, include_pattern]:
                for match in pattern.finditer(content):
                    target = match.group(1)
                    # Strip anchor references
                    if "#" in target:
                        target = target.split("#")[0]
                    # Resolve the path
                    if target.startswith("../"):
                        target_path = (file_dir / target).resolve()
                    elif "/" in target or is_nav_file:
                        # Antora page ID or nav.adoc xrefs - resolve from pages directory
                        target_path = docs_dir / target
                    else:
                        target_path = file_dir / target

                    try:
                        relative = target_path.relative_to(docs_dir)
                        referenced_files.add(str(relative))
                    except ValueError:
                        pass

        # Find orphaned files (not referenced by any other file)
        orphaned = []
        for adoc_file in all_adoc_files:
            # Skip nav.adoc as it's outside pages directory
            if adoc_file.name == "nav.adoc":
                continue

            try:
                relative = str(adoc_file.relative_to(docs_dir))
            except ValueError:
                # File is outside docs_dir (like nav.adoc)
                continue

            # Index files and main landing pages are not orphaned
            if adoc_file.name == "index.adoc":
                continue
            if ":page-role: legacy-redirect" in adoc_file.read_text():
                continue

            if relative not in referenced_files:
                orphaned.append(relative)

        assert not orphaned, "Unlinked documentation pages:\n" + "\n".join(orphaned)


@pytest.mark.docs
@pytest.mark.slow
class TestExternalLinks:
    """Test external URL links (slow tests)."""

    def test_external_links_format(self, all_adoc_files: list[Path], docs_dir: Path):
        """External links should be properly formatted."""
        url_pattern = re.compile(r"https?://[^\s\[\]<>\"]+")
        malformed = []

        for adoc_file in all_adoc_files:
            content = adoc_file.read_text(encoding="utf-8")

            for match in url_pattern.finditer(content):
                url = match.group(0)

                # Check for common malformation issues
                if url.endswith(",") or url.endswith("."):
                    # URL ends with punctuation (likely part of sentence)
                    pass
                elif "[" in url or "]" in url:
                    relative = adoc_file.relative_to(docs_dir)
                    malformed.append(f"{relative}: {url[:50]}")

        if malformed:
            pytest.fail(f"Malformed URLs found: {malformed[:5]}")
