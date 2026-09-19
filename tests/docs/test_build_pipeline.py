"""Validate one fresh Antora build; no implicit installs or stale build output."""

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts.check_docs import build_site, rendered_report


@pytest.fixture(scope="session")
def built_docs(tmp_path_factory, project_root):
    executable = project_root / "docs/node_modules/.bin/antora"
    assert executable.exists(), "Install docs dependencies first: cd docs && npm ci"
    destination = tmp_path_factory.mktemp("antora") / "site"
    log, errors = build_site(destination, root=project_root)
    (destination.parent / "build.log").write_text(log)
    assert not errors, "\n".join(errors) + "\nFull build output:\n" + log
    return destination


@pytest.mark.docs
def test_all_pages_render(built_docs: Path, docs_dir: Path):
    component = built_docs / "agent-memory"
    missing = [
        str(p.relative_to(docs_dir))
        for p in docs_dir.rglob("*.adoc")
        if not (component / p.relative_to(docs_dir).with_suffix(".html")).exists()
    ]
    assert not missing, f"Missing rendered pages: {missing}"


@pytest.mark.docs
def test_rendered_content_links_fragments_and_images(built_docs: Path):
    report = rendered_report(built_docs)
    assert report["content_links"] > 0
    assert not report["errors"], "\n".join(report["errors"])


@pytest.mark.docs
def test_entry_has_navigation_and_content(built_docs: Path):
    html = (built_docs / "agent-memory/index.html").read_text()
    assert '<article class="doc"' in html
    assert "<nav" in html
    assert "tutorials/" in html and "reference/" in html


class _NamedFileListings(HTMLParser):
    """Read complete listings titled 'Save as filename' from rendered Antora HTML."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.files: dict[str, str] = {}
        self.depth = 0
        self.title_depth = 0
        self.in_pre = False
        self.title: list[str] = []
        self.source: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        classes = (dict(attributes).get("class") or "").split()
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "listingblock" in classes:
                self.depth = 1
                self.title = []
                self.source = []
            if self.depth and "title" in classes:
                self.title_depth = self.depth
        if self.depth and tag == "pre":
            self.in_pre = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = False
        if tag == "div" and self.depth:
            if self.depth == self.title_depth:
                self.title_depth = 0
            self.depth -= 1
            if not self.depth:
                title = "".join(self.title).strip()
                if title.startswith("Save as "):
                    name = title.removeprefix("Save as ").strip()
                    if name in self.files:
                        raise ValueError(f"Duplicate complete file listing: {name}")
                    self.files[name] = "".join(self.source)

    def handle_data(self, data):
        if self.title_depth:
            self.title.append(data)
        elif self.in_pre:
            self.source.append(data)


def named_file_listings(html: str) -> dict[str, str]:
    """Return filename-to-source mappings for complete on-page programs/templates."""
    return _NamedFileListings(html).files


@pytest.mark.docs
def test_named_file_listings_preserve_source():
    html = (
        '<details><summary>Complete file</summary><div class="listingblock">'
        '<div class="title">Save as <code>nested/example.py</code></div>'
        '<div class="content"><pre><code class="language-python">'
        '<span class="hljs-keyword">if</span> x &lt; 2:\n    print("a &amp; b")\n'
        "</code></pre></div></div></details>"
        '<div class="listingblock"><div class="title">Partial snippet</div>'
        '<div class="content"><pre><code>ignored</code></pre></div></div>'
    )
    assert named_file_listings(html) == {"nested/example.py": 'if x < 2:\n    print("a & b")\n'}
    with pytest.raises(ValueError, match="Duplicate complete file listing"):
        named_file_listings(html + html)


@pytest.mark.docs
def test_external_example_files_render_in_full(built_docs: Path, project_root: Path):
    """Readers can copy every maintained external file directly from its guide."""
    sources = json.loads(
        subprocess.check_output(
            [
                "node",
                "-e",
                "process.stdout.write(JSON.stringify(require('./docs/extensions/example-files').EXAMPLE_FILES))",
            ],
            cwd=project_root,
            text=True,
        )
    )
    pages = {
        "no_llm": "how-to/running-without-an-llm.html",
        "eval-harness": "how-to/evaluation.html",
        "team-memory": "how-to/team-memory-in-your-editor.html",
    }
    listings = {
        folder: named_file_listings((built_docs / "agent-memory" / page).read_text())
        for folder, page in pages.items()
    }
    for name, source in sources.items():
        folder = name.split("/", 1)[0]
        assert name in listings[folder], f"Missing full {name} listing in {pages[folder]}"
        # Asciidoctor omits trailing line endings, but all source content,
        # indentation and internal blank lines must remain exact.
        assert listings[folder][name].rstrip("\n") == (project_root / source).read_text().rstrip(
            "\n"
        ), name
    assert not (built_docs / "agent-memory/_attachments/python-tutorials.zip").exists()


PYTHON_TUTORIAL_PROGRAMS = {
    "first-agent-memory": "first_agent_memory.py",
    "conversation-memory": "conversation_memory.py",
    "knowledge-graph": "knowledge_graph.py",
    "anthropic-and-local-embeddings": "anthropic_local_memory.py",
    "microsoft-agent-memory": "microsoft_shopping_tutorial.py",
    "strands-agent-quickstart": "strands_memory_tutorial.py",
    "mcp-server": "mcp_local_tutorial.py",
    "nams-quickstart": "nams_quickstart.py",
    "ontology-quickstart": "ontology_quickstart.py",
    "skills-quickstart": "skills_quickstart.py",
}


@pytest.mark.docs
@pytest.mark.parametrize("page,program", PYTHON_TUTORIAL_PROGRAMS.items())
def test_python_tutorial_supplies_complete_local_files(
    built_docs: Path, project_root: Path, page: str, program: str
):
    """A copied lesson must not depend on unpublished or separately downloaded helpers."""
    import ast

    examples = project_root / "docs/modules/ROOT/examples"
    html = (built_docs / "agent-memory/tutorials" / f"{page}.html").read_text()
    files = named_file_listings(html)
    assert program in files, f"{page} must show the complete {program} with a Save as title"
    assert "python-tutorials.zip" not in html
    for name, displayed in files.items():
        if not name.endswith(".py"):
            continue
        canonical = examples / name
        assert canonical.is_file(), f"Unexpected tutorial file: {page}: {name}"
        assert displayed.rstrip("\n") == canonical.read_text().rstrip("\n"), (
            f"{page}: {name} must display the full maintained file"
        )
        # Compile what the reader copies, not the original source file.
        tree = ast.parse(displayed, filename=f"{page}/{name}")
        compile(tree, f"{page}/{name}", "exec")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                local = Path(name).parent / (module.replace(".", "/") + ".py")
                if (examples / local).is_file():
                    assert str(local) in files, (
                        f"{page}: {name} imports {local}, but the page does not supply it"
                    )


@pytest.mark.docs
def test_how_to_named_programs_include_their_local_imports(built_docs: Path, project_root: Path):
    """Task excerpts can be partial; every file offered for execution must be complete."""
    import ast

    examples = project_root / "docs/modules/ROOT/examples"
    sources = {str(p.relative_to(examples)): p for p in examples.rglob("*.py")}
    external = json.loads(
        subprocess.check_output(
            [
                "node",
                "-e",
                "process.stdout.write(JSON.stringify(require('./docs/extensions/example-files').EXAMPLE_FILES))",
            ],
            cwd=project_root,
            text=True,
        )
    )
    sources.update({name: project_root / source for name, source in external.items()})
    checked = 0
    for page in (built_docs / "agent-memory/how-to").rglob("*.html"):
        files = named_file_listings(page.read_text())
        for name, displayed in files.items():
            if not name.endswith(".py"):
                continue
            checked += 1
            tree = ast.parse(displayed, filename=f"{page.name}/{name}")
            compile(tree, f"{page.name}/{name}", "exec")
            if name in sources:
                assert displayed.rstrip("\n") == sources[name].read_text().rstrip("\n"), (
                    page.name,
                    name,
                )
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    local = str(Path(name).parent / (module.replace(".", "/") + ".py"))
                    if local in sources:
                        assert local in files, f"{page.name}: {name} needs the full {local}"
    assert checked > 0
