"""Regression fixtures for the failures missed by the old docs checks."""

import json
from pathlib import Path

import pytest

from scripts.check_docs import build_site, rendered_report, source_report

pytestmark = pytest.mark.docs


@pytest.mark.parametrize("level", [40, 50, 60, "warn", "warning", "error", "fatal"])
def test_build_rejects_antora_json_diagnostics(monkeypatch, tmp_path, level):
    import json
    import subprocess

    log = json.dumps({"level": level, "msg": "missing attribute: id"}) + "\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, log, ""),
    )
    full_log, errors = build_site(tmp_path / "site")
    assert full_log == log
    assert errors == [log.rstrip()]


def test_rendered_links_check_fragments_and_raw_adoc(tmp_path):
    (tmp_path / "index.html").write_text(
        '<article class="doc"><a href="other.html#missing">x</a><a href="other.adoc">y</a></article>'
    )
    (tmp_path / "other.html").write_text(
        '<article class="doc"><h2 id="present">Title</h2></article>'
    )
    errors = rendered_report(tmp_path)["errors"]
    assert len(errors) == 2
    assert any("missing anchor" in error for error in errors)
    assert any("other.adoc" in error for error in errors)


def test_rendered_shared_ui_is_separate_and_encoded_fragments_work(tmp_path):
    (tmp_path / "index.html").write_text(
        '<nav><a href="/outside">Shared navigation</a></nav><article class="doc"><a href="index.html#caf%C3%A9">x</a><h2 id="café">Title</h2></article>'
    )
    assert not rendered_report(tmp_path)["errors"]


def test_rendered_image_dimensions_and_description(tmp_path):
    (tmp_path / "image.png").write_bytes(b"fixture")
    (tmp_path / "index.html").write_text(
        '<article class="doc"><img src="image.png" width="long-term" height="400" alt=""></article>'
    )
    errors = rendered_report(tmp_path)["errors"]
    assert any("no description" in error for error in errors)
    assert any("invalid width" in error for error in errors)


@pytest.mark.parametrize(
    "paragraph",
    [
        '<p><a id="legacy"></a>\n== Setup</p>',
        "<p>=== Read <em>and verify</em></p>",
    ],
)
def test_rendered_heading_markers_in_paragraphs_are_rejected(tmp_path, paragraph):
    (tmp_path / "index.html").write_text(f'<article class="doc">{paragraph}</article>')
    errors = rendered_report(tmp_path)["errors"]
    assert len(errors) == 1
    assert "unrendered AsciiDoc heading" in errors[0]


def test_rendered_heading_examples_and_shared_ui_are_ignored(tmp_path):
    (tmp_path / "index.html").write_text(
        '<nav><p>== Shared UI</p></nav><article class="doc">'
        '<h2 id="setup">Setup</h2><pre><code>== Code example</code></pre>'
        "<pre><p>== Preformatted example</p></pre>"
        "<p><code>== </code> begins a heading.</p>"
        "<p>Use <code>== Title</code> for a section.</p></article>"
    )
    assert not rendered_report(tmp_path)["errors"]


def test_every_leaf_needs_a_hub_route_but_subindexes_are_supported(tmp_path):
    for quadrant in ("tutorials", "how-to", "reference", "explanation"):
        folder = tmp_path / quadrant
        folder.mkdir()
        (folder / "index.adoc").write_text(f"= {quadrant}\n")
    nested = tmp_path / "how-to/integrations"
    nested.mkdir()
    (nested / "index.adoc").write_text("= Integrations\nxref:how-to/integrations/demo.adoc[Demo]\n")
    (nested / "demo.adoc").write_text("= Demo\n")
    assert any("no index route" in error for error in source_report(tmp_path)["errors"])
    (tmp_path / "how-to/index.adoc").write_text(
        "= Tasks\nxref:how-to/integrations/index.adoc[Integrations]\n"
    )
    assert not source_report(tmp_path)["errors"]


def test_image_alt_with_commas_must_be_quoted(tmp_path):
    for quadrant in ("tutorials", "how-to", "reference", "explanation"):
        folder = tmp_path / quadrant
        folder.mkdir()
        (folder / "index.adoc").write_text(f"= {quadrant}\n")
    entry = tmp_path / "index.adoc"
    entry.write_text("= Home\nimage::graph.png[Messages, entities, and traces,width=700]\n")
    assert any("quote comma" in error for error in source_report(tmp_path)["errors"])
    entry.write_text('= Home\nimage::graph.png["Messages, entities, and traces",width=700]\n')
    assert not source_report(tmp_path)["errors"]


def test_leftover_scaffolding_heading_is_rejected(tmp_path):
    for quadrant in ("tutorials", "how-to", "reference", "explanation"):
        folder = tmp_path / quadrant
        folder.mkdir()
        (folder / "index.adoc").write_text(f"= {quadrant}\n")
    entry = tmp_path / "index.adoc"
    entry.write_text("= Home\n\n== Earlier section links\n\nanchor:_x[]\nStale note.\n")
    errors = source_report(tmp_path)["errors"]
    assert any("leftover scaffolding heading" in error for error in errors)
    entry.write_text("= Home\n\nanchor:_x[]\n== See also\n")
    assert not any("leftover scaffolding heading" in error for error in source_report(tmp_path)["errors"])


def test_syntax_checker_reads_the_actual_example_include(tmp_path):
    from tests.docs.utils.extract_code import extract_snippets_from_file

    pages = tmp_path / "modules/ROOT/pages/tutorials"
    pages.mkdir(parents=True)
    examples = pages.parent.parent / "examples"
    examples.mkdir()
    program = examples / "program.py"
    program.write_text("print('actual program')\n")
    page = pages / "lesson.adoc"
    page.write_text("= Lesson\n[source,python]\n----\ninclude::example$program.py[]\n----\n")
    snippets = extract_snippets_from_file(page)
    assert snippets[0].code == "print('actual program')"
    program.unlink()
    with pytest.raises(FileNotFoundError):
        extract_snippets_from_file(page)


def test_import_checker_uses_the_module_and_rejects_missing_names(tmp_path):
    from tests.docs.utils.extract_code import local_import_errors

    package = tmp_path / "neo4j_agent_memory"
    package.mkdir()
    (package / "__init__.py").write_text("class RootOnly: pass\n")
    (package / "adapter.py").write_text("class SpecialAdapter: pass\n")
    assert not local_import_errors(
        "from neo4j_agent_memory.adapter import (\n SpecialAdapter as Store,\n)", tmp_path
    )
    assert local_import_errors("from neo4j_agent_memory import SpecialAdapter", tmp_path) == [
        "neo4j_agent_memory does not declare SpecialAdapter"
    ]
    assert local_import_errors("from neo4j_agent_memory.adapter import Missing", tmp_path)


def test_complete_async_helper_keeps_future_import_at_module_level():
    import ast

    program = (
        '"""Helper."""\nfrom __future__ import annotations\nasync def work():\n    await action()\n'
    )
    compile(program, "helper.py", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_tagged_example_includes_select_real_regions(tmp_path):
    from tests.docs.utils.extract_code import expand_example_includes

    pages = tmp_path / "modules/ROOT/pages"
    pages.mkdir(parents=True)
    examples = pages.parent / "examples"
    examples.mkdir()
    (examples / "recipes.py").write_text(
        "import unused\n# tag::first[]\nx = 1\n# end::first[]\n"
        "# tag::second[]\ny = 2\n# end::second[]\n"
    )
    assert (
        expand_example_includes("include::example$recipes.py[tags=first;second]", pages / "x.adoc")
        == "x = 1\ny = 2"
    )
    with pytest.raises(ValueError, match="Missing example tags"):
        expand_example_includes("include::example$recipes.py[tag=missing]", pages / "x.adoc")


@pytest.mark.parametrize(
    "target,source",
    json.loads(
        (Path(__file__).resolve().parents[2] / "docs/extensions/example-files.json").read_text()
    ).items(),
)
def test_syntax_checker_reads_every_registered_example(target, source):
    """The snippet checker and Antora must read identical Python and JSON resources."""
    from tests.docs.utils.extract_code import expand_example_includes

    root = Path(__file__).resolve().parents[2]
    page = root / "docs/modules/ROOT/pages/how-to/team-memory-in-your-editor.adoc"
    assert (
        expand_example_includes(f"include::example${target}[]", page)
        == (root / source).read_text(encoding="utf-8").rstrip()
    )


@pytest.fixture
def registered_example(tmp_path):
    """A virtual resource exists only at its registered repository source path."""
    root = tmp_path / "repository"
    docs = root / "docs"
    page = docs / "modules/ROOT/pages/how-to/lesson.adoc"
    page.parent.mkdir(parents=True)
    manifest = docs / "extensions/example-files.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"team-memory/program.py": "examples/program.py"}))
    source = root / "examples/program.py"
    source.parent.mkdir()
    source.write_text("import os\n# tag::run[]\nprint('actual source')\n# end::run[]\n")
    page.write_text(
        "= Lesson\n[source,python]\n----\ninclude::example$team-memory/program.py[]\n----\n"
    )
    return page, manifest, source


def test_registered_example_is_expanded_during_snippet_collection(registered_example):
    from tests.docs.utils.extract_code import extract_snippets_from_file

    page, _, source = registered_example
    assert extract_snippets_from_file(page)[0].code == source.read_text().rstrip()
    source.unlink()
    with pytest.raises(FileNotFoundError):
        extract_snippets_from_file(page)


def test_registered_example_supports_tags_and_rejects_invalid_includes(registered_example):
    from tests.docs.utils.extract_code import expand_example_includes

    page, _, _ = registered_example
    assert (
        expand_example_includes("include::example$team-memory/program.py[tag=run]", page)
        == "print('actual source')"
    )
    with pytest.raises(ValueError, match="Missing example tags"):
        expand_example_includes("include::example$team-memory/program.py[tag=absent]", page)
    with pytest.raises(ValueError, match="Unsupported example include options"):
        expand_example_includes("include::example$team-memory/program.py[lines=1]", page)
    with pytest.raises(FileNotFoundError):
        expand_example_includes("include::example$team-memory/unknown.py[]", page)


def test_registered_examples_preserve_include_path_boundaries(registered_example):
    from tests.docs.utils.extract_code import expand_example_includes

    page, manifest, _ = registered_example
    with pytest.raises(ValueError, match="escapes module examples"):
        expand_example_includes("include::example$../outside.py[]", page)
    manifest.write_text(json.dumps({"team-memory/program.py": "../outside.py"}))
    with pytest.raises(ValueError, match="Registered example escapes repository"):
        expand_example_includes("include::example$team-memory/program.py[]", page)
