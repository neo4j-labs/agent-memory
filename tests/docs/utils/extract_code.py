"""Utility to extract code blocks from AsciiDoc documentation files."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodeSnippet:
    """Represents an extracted code block from documentation."""

    file_path: Path
    language: str
    code: str
    line_number: int
    section: str | None = None
    title: str | None = None
    is_complete: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Determine if the snippet is a complete, runnable example."""
        self.is_complete = self._check_completeness()

    def _check_completeness(self) -> bool:
        """Check if the snippet has imports and could run standalone."""
        if self.language != "python":
            return False

        # Must have at least one import statement
        # Use regex to match actual import statements, not just "from" in comments
        import re

        has_import = bool(
            re.search(r"^\s*import\s+\w", self.code, re.MULTILINE)
            or re.search(r"^\s*from\s+\w+\s+import\s+", self.code, re.MULTILINE)
        )

        # Check for common incomplete patterns
        is_continuation = self.code.strip().startswith("...")
        is_partial = self.code.strip().startswith("#") and "..." in self.code
        has_ellipsis_placeholder = "# ..." in self.code or "..." in self.code.split("\n")[0]

        # Check if it's a class/function definition without context
        first_line = self.code.strip().split("\n")[0] if self.code.strip() else ""
        is_method_only = first_line.startswith("async def ") or first_line.startswith("def ")
        is_class_method = is_method_only and "self" in first_line

        return has_import and not is_continuation and not is_partial and not is_class_method

    @property
    def is_signature_doc(self) -> bool:
        """Check if this is an API signature documentation snippet.

        These are used in reference docs to show method parameters and are not
        meant to be runnable Python code.
        """
        if self.language != "python":
            return False

        lines = self.code.strip().split("\n")
        if not lines:
            return False

        first_line = lines[0].strip()

        # Pattern 1: Single-line signature with type annotation in params
        # Example: "entity = await long_term.get_entity(entity_id: str)"
        # This has a colon inside the parentheses which is not valid Python
        import re

        if re.search(r"\([^)]*\w+:\s*\w+[^)]*\)", first_line):
            # Has pattern like (param: type) which is signature notation
            return True

        # Pattern 2: Multi-line signature documentation
        if len(lines) >= 2 and first_line.endswith("("):
            # Check if subsequent lines look like parameter docs (name: type,)
            param_pattern_count = 0
            for line in lines[1:]:
                line = line.strip()
                if line and ":" in line and (line.endswith(",") or line.endswith(")")):
                    # Looks like "param_name: type," - this is a signature doc
                    # Must have type annotation pattern: word followed by colon then type
                    parts = line.rstrip(",)").split(":")
                    if len(parts) >= 2:
                        param_name = parts[0].strip()
                        # Param names are simple identifiers (no quotes, operators, etc.)
                        if param_name.isidentifier():
                            param_pattern_count += 1
            # If we have parameter-like lines, this is a signature doc
            if param_pattern_count >= 1:
                return True

        return False

    @property
    def is_placeholder_snippet(self) -> bool:
        """Check if this snippet uses placeholder ellipsis that isn't valid Python.

        Examples of placeholder patterns:
        - EntitySchemaConfig(name="medical", version="1.0", ...)
        - {"key": "value", ...}
        """
        if self.language != "python":
            return False

        # Check for ... used as a placeholder in function calls or dicts
        # (not as a literal Ellipsis object)
        import re

        # Pattern: ...) at end of function call - placeholder for more args
        if re.search(r",\s*\.\.\.\s*\)", self.code):
            return True

        # Pattern: ..., or ...} in dict/list - placeholder for more items
        if re.search(r"\.\.\.\s*[,}\]]", self.code):
            return True

        return False

    @property
    def id(self) -> str:
        """Generate a unique identifier for this snippet."""
        filename = self.file_path.stem
        section_slug = self.section.replace(" ", "_").lower()[:30] if self.section else "unknown"
        return f"{filename}:{self.line_number}:{section_slug}"

    @property
    def needs_async_wrapper(self) -> bool:
        """Check if this snippet contains async code that needs wrapping.

        This checks for await/async with/async for at the module level
        (not inside a function). If such code exists, it needs to be wrapped.
        """
        if self.language != "python":
            return False

        lines = self.code.split("\n")
        in_function = False
        indent_stack: list[int] = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Calculate current indentation
            indent = len(line) - len(line.lstrip())

            # Track function scope
            if stripped.startswith(("def ", "async def ")):
                in_function = True
                indent_stack.append(indent)
            elif indent_stack and indent <= indent_stack[-1]:
                # We've dedented past the function
                indent_stack.pop()
                if not indent_stack:
                    in_function = False

            # Check for async operations at module level
            if not in_function:
                if "await " in stripped:
                    return True
                if stripped.startswith("async with "):
                    return True
                if stripped.startswith("async for "):
                    return True

        return False

    def get_syntax_checkable_code(self) -> str:
        """Return code suitable for syntax validation.

        For async snippets without a wrapper function, wraps them in an async
        function to enable syntax checking.
        """
        if not self.needs_async_wrapper:
            return self.code

        # Indent all lines and wrap in async function
        indented_lines = []
        for line in self.code.split("\n"):
            if line.strip():  # Non-empty lines get indented
                indented_lines.append("    " + line)
            else:  # Preserve empty lines
                indented_lines.append("")

        wrapped = "async def __doc_snippet__():\n" + "\n".join(indented_lines)
        return wrapped

    def __repr__(self) -> str:
        preview = (
            self.code[:50].replace("\n", "\\n") + "..."
            if len(self.code) > 50
            else self.code.replace("\n", "\\n")
        )
        return (
            f"CodeSnippet({self.file_path.name}:{self.line_number}, {self.language}, {preview!r})"
        )


# Regex patterns for AsciiDoc parsing
SECTION_PATTERN = re.compile(r"^(=+)\s+(.+)$", re.MULTILINE)
CODE_BLOCK_PATTERN = re.compile(
    r"""
    (?:\.([^\n]+)\n)?                    # Optional title (e.g., .Example title)
    \[source,(\w+)\]                      # [source,language]
    \n                                    # Newline
    ----\n                                # Opening delimiter
    (.*?)                                 # Code content (non-greedy)
    \n----                                # Closing delimiter
    """,
    re.DOTALL | re.VERBOSE,
)


def expand_example_includes(code: str, file_path: Path) -> str:
    """Resolve the full Antora example resources used by maintained programs.

    Missing files and unsupported include options fail visibly instead of
    silently certifying a block that contains only an include directive.
    """
    module = next((parent for parent in file_path.parents if parent.name == "pages"), None)
    if module is None or "include::example$" not in code:
        return code
    examples = (module.parent / "examples").resolve()

    def include(match: re.Match[str]) -> str:
        target, options = match.groups()
        tags = None
        if options:
            option = re.fullmatch(r"tags?=([\w;-]+)", options)
            if not option:
                raise ValueError(f"Unsupported example include options in {file_path}: {options}")
            tags = set(option[1].split(";"))
        source = (examples / target).resolve()
        if not source.is_relative_to(examples):
            raise ValueError(f"Example include escapes module examples: {target}")
        text = source.read_text(encoding="utf-8")
        if tags is None:
            return text.rstrip()
        active: set[str] = set()
        found: set[str] = set()
        selected = []
        for line in text.splitlines():
            marker = re.search(r"(?:#|//)\s*(tag|end)::([\w-]+)\[\]", line)
            if marker:
                if marker[1] == "tag":
                    active.add(marker[2])
                    found.add(marker[2])
                else:
                    active.discard(marker[2])
                continue
            if active & tags:
                selected.append(line)
        if tags - found:
            raise ValueError(f"Missing example tags in {source}: {sorted(tags - found)}")
        return "\n".join(selected).rstrip()

    return re.sub(r"^include::example\$([^\[]+)\[([^\]]*)\]$", include, code, flags=re.MULTILINE)


def extract_snippets_from_file(file_path: Path) -> list[CodeSnippet]:
    """Extract all code blocks from an AsciiDoc file.

    Args:
        file_path: Path to the .adoc file

    Returns:
        List of CodeSnippet objects
    """
    content = file_path.read_text(encoding="utf-8")
    snippets: list[CodeSnippet] = []

    # Build a map of line numbers to section titles
    sections: dict[int, str] = {}
    for match in SECTION_PATTERN.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        sections[line_num] = match.group(2).strip()

    # Find all code blocks
    for match in CODE_BLOCK_PATTERN.finditer(content):
        title = match.group(1)
        language = match.group(2)
        code = expand_example_includes(match.group(3), file_path)

        # Calculate line number
        line_num = content[: match.start()].count("\n") + 1

        # Find the most recent section heading
        section = None
        for sec_line, sec_title in sorted(sections.items(), reverse=True):
            if sec_line < line_num:
                section = sec_title
                break

        snippet = CodeSnippet(
            file_path=file_path,
            language=language,
            code=code,
            line_number=line_num,
            section=section,
            title=title,
        )
        snippets.append(snippet)

    return snippets


def extract_python_snippets(docs_dir: Path) -> list[CodeSnippet]:
    """Extract all Python code blocks from all AsciiDoc files in a directory.

    Args:
        docs_dir: Path to the docs directory

    Returns:
        List of Python CodeSnippet objects
    """
    snippets: list[CodeSnippet] = []

    # Use authored Antora pages when passed the docs root.
    pages = docs_dir / "modules" / "ROOT" / "pages"
    if pages.is_dir():
        docs_dir = pages
    for adoc_file in docs_dir.rglob("*.adoc"):
        # Skip node_modules and _site
        if {"node_modules", "_site", "build", "attachments"}.intersection(adoc_file.parts):
            continue

        file_snippets = extract_snippets_from_file(adoc_file)
        python_snippets = [s for s in file_snippets if s.language == "python"]
        snippets.extend(python_snippets)

    return snippets


def get_docs_dir() -> Path:
    """Get the path to the docs directory."""
    # Try relative to this file
    this_file = Path(__file__)
    # tests/docs/utils/extract_code.py -> project_root/docs
    project_root = this_file.parent.parent.parent.parent
    docs_dir = project_root / "docs"

    if docs_dir.exists():
        return docs_dir

    # Fallback: try from current working directory
    cwd_docs = Path.cwd() / "docs"
    if cwd_docs.exists():
        return cwd_docs

    raise FileNotFoundError("Could not locate docs directory")


def get_all_python_snippets() -> list[CodeSnippet]:
    """Get all Python snippets from the docs directory.

    This is a convenience function for use with pytest.mark.parametrize.
    """
    docs_dir = get_docs_dir()
    return extract_python_snippets(docs_dir)


def get_complete_python_snippets() -> list[CodeSnippet]:
    """Get only complete (runnable) Python snippets.

    This is a convenience function for use with pytest.mark.parametrize.
    """
    return [s for s in get_all_python_snippets() if s.is_complete]


def local_import_errors(code: str, source_root: Path) -> list[str]:
    """Check SDK imports against source declarations without loading providers."""
    declarations: dict[str, set[str] | None] = {}

    def exports(module: str) -> set[str] | None:
        if module in declarations:
            return declarations[module]
        base = source_root.joinpath(*module.split("."))
        path = (
            base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
        )
        if not path.is_file():
            declarations[module] = None
            return None
        names: set[str] = set()
        declarations[module] = names

        def visit(statements):
            for node in statements:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                    if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
                        for branch in ast.walk(node):
                            if (
                                isinstance(branch, ast.Compare)
                                and isinstance(branch.left, ast.Name)
                                and branch.left.id == "name"
                            ):
                                names.update(
                                    value.value
                                    for value in branch.comparators
                                    if isinstance(value, ast.Constant)
                                    and isinstance(value.value, str)
                                )
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.Assign):
                    names.update(
                        target.id for target in node.targets if isinstance(target, ast.Name)
                    )
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, ast.If):
                    visit(node.body)
                    visit(node.orelse)
                elif isinstance(node, ast.Try):
                    visit(node.body)
                    visit(node.orelse)
                    visit(node.finalbody)
                    for handler in node.handlers:
                        visit(handler.body)

        visit(ast.parse(path.read_text()).body)
        return names

    errors = []
    for node in ast.walk(ast.parse(code)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (
                node.module == "neo4j_agent_memory" or node.module.startswith("neo4j_agent_memory.")
            )
        ):
            names = exports(node.module)
            if names is None:
                errors.append(f"Missing module {node.module}")
            else:
                for alias in node.names:
                    if (
                        alias.name != "*"
                        and alias.name not in names
                        and exports(f"{node.module}.{alias.name}") is None
                    ):
                        errors.append(f"{node.module} does not declare {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name == "neo4j_agent_memory"
                    or alias.name.startswith("neo4j_agent_memory.")
                ) and exports(alias.name) is None:
                    errors.append(f"Missing module {alias.name}")
    return errors
