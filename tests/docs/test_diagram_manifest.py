"""Published diagram provenance must survive edits and new image references."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.manage_diagrams import PROJECT_ROOT, check_manifest

pytestmark = pytest.mark.docs


def fixture_manifest(root: Path) -> tuple[Path, Path]:
    image = root / "docs/modules/ROOT/images/diagrams/graph.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"reviewed export")
    source = root / "docs/assets/diagrams/excalidraw/graph.excalidraw"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"type": "excalidraw", "elements": [{"id": "node", "type": "ellipse"}]})
    )
    manifest = root / "docs/diagrams/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "diagrams": [
                    {
                        "source": str(source.relative_to(root)),
                        "output": str(image.relative_to(root)),
                        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "output_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )
    return source, image


def test_published_diagrams_are_mapped_and_fresh():
    assert not check_manifest(PROJECT_ROOT)["errors"]


def test_source_and_export_edits_require_review(tmp_path):
    source, image = fixture_manifest(tmp_path)
    assert not check_manifest(tmp_path)["errors"]
    source.write_text(source.read_text() + "\n")
    image.write_bytes(b"unreviewed replacement")
    errors = check_manifest(tmp_path)["errors"]
    assert len(errors) == 2
    assert any("Changed source" in error for error in errors)
    assert any("Changed output" in error for error in errors)


def test_unmapped_active_image_is_rejected(tmp_path):
    fixture_manifest(tmp_path)
    page = tmp_path / "docs/modules/ROOT/pages/index.adoc"
    page.parent.mkdir(parents=True)
    page.write_text('= Home\nimage::diagrams/unmapped.png["Graph"]\n')
    assert any("untracked published image" in error for error in check_manifest(tmp_path)["errors"])


def test_dangling_bindings_in_inactive_editable_scenes_are_rejected(tmp_path):
    source, _ = fixture_manifest(tmp_path)
    (source.parent / "historical.excalidraw").write_text(
        json.dumps(
            {
                "type": "excalidraw",
                "elements": [
                    {"id": "arrow", "type": "arrow", "endBinding": {"elementId": "missing"}}
                ],
            }
        )
    )
    assert any("Dangling binding" in error for error in check_manifest(tmp_path)["errors"])
