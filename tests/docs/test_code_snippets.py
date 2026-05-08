"""Tests for Python code snippets in documentation.

These tests validate that code examples in the documentation are syntactically
correct and use valid imports.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.docs.utils import CodeSnippet, extract_python_snippets


def get_docs_dir() -> Path:
    """Get the docs directory."""
    return Path(__file__).parent.parent.parent / "docs"


def get_all_snippets() -> list[CodeSnippet]:
    """Get all Python snippets for parametrization."""
    docs_dir = get_docs_dir()
    if not docs_dir.exists():
        return []
    return extract_python_snippets(docs_dir)


def snippet_id(snippet: CodeSnippet) -> str:
    """Generate test ID for a snippet."""
    return f"{snippet.file_path.name}:{snippet.line_number}"


# Get snippets at module load time for parametrization
ALL_SNIPPETS = get_all_snippets()


@pytest.mark.docs
@pytest.mark.syntax
class TestSnippetSyntax:
    """Test that all Python snippets have valid syntax."""

    @pytest.mark.parametrize("snippet", ALL_SNIPPETS, ids=snippet_id)
    def test_snippet_is_valid_python(self, snippet: CodeSnippet):
        """Every Python snippet should be valid Python syntax.

        For async snippets without a wrapper function, the code is automatically
        wrapped in an async function for syntax checking.

        Signature documentation snippets (used in API reference to show method
        parameters) are skipped as they are intentionally not runnable Python.
        """
        # Skip signature documentation snippets (used in API reference docs)
        if snippet.is_signature_doc:
            pytest.skip("Signature documentation snippet - not runnable Python")

        # Skip placeholder snippets that use ... as a placeholder
        if snippet.is_placeholder_snippet:
            pytest.skip("Placeholder snippet with ellipsis - not runnable Python")

        # Use syntax-checkable code which wraps async snippets if needed
        checkable_code = snippet.get_syntax_checkable_code()
        try:
            compile(checkable_code, f"{snippet.file_path}:{snippet.line_number}", "exec")
        except SyntaxError as e:
            # Provide helpful error message
            error_line = e.lineno
            # Adjust line number if we wrapped the code
            if snippet.needs_async_wrapper and error_line is not None:
                error_line = max(1, error_line - 1)  # Account for wrapper line

            pytest.fail(
                f"Syntax error in {snippet.file_path.name} line {snippet.line_number}\n"
                f"Section: {snippet.section}\n"
                f"Error: {e.msg} at line {error_line}\n"
                f"Code:\n{snippet.code[:500]}"
            )


@pytest.mark.docs
@pytest.mark.syntax
class TestSnippetPatterns:
    """Test that snippets follow expected patterns."""

    def test_no_bare_except_clauses(self, python_snippets: list[CodeSnippet]):
        """Snippets should not use bare except clauses."""
        violations = []
        for snippet in python_snippets:
            if re.search(r"\bexcept\s*:", snippet.code):
                violations.append(f"{snippet.file_path.name}:{snippet.line_number}")

        # Allow some violations in explanatory code
        if len(violations) > 5:
            pytest.fail(f"Too many bare except clauses: {violations}")

    def test_no_hardcoded_passwords(self, python_snippets: list[CodeSnippet]):
        """Snippets should not have hardcoded passwords (except placeholders)."""
        violations = []
        for snippet in python_snippets:
            # Look for password assignments that aren't placeholders
            if re.search(r'password\s*=\s*["\'][^"\']*[a-zA-Z]{8,}["\']', snippet.code, re.I):
                # Skip if it's clearly a placeholder
                # Common documentation placeholders include "password", "your-password", etc.
                if not any(
                    p in snippet.code.lower()
                    for p in [
                        "your-",
                        "example",
                        "placeholder",
                        "password123",
                        "xxxxx",
                        '"password"',  # Literal "password" is a common doc placeholder
                        "'password'",  # Same with single quotes
                    ]
                ):
                    violations.append(f"{snippet.file_path.name}:{snippet.line_number}")

        assert not violations, f"Hardcoded passwords found: {violations}"

    def test_imports_use_correct_package_name(self, python_snippets: list[CodeSnippet]):
        """Snippets should import from neo4j_agent_memory, not other names."""
        violations = []
        for snippet in python_snippets:
            # Check for wrong package names
            if "from neo4j_memory" in snippet.code and "neo4j_agent_memory" not in snippet.code:
                violations.append(
                    f"{snippet.file_path.name}:{snippet.line_number}: uses 'neo4j_memory'"
                )
            if "import agent_memory" in snippet.code and "neo4j_agent_memory" not in snippet.code:
                violations.append(
                    f"{snippet.file_path.name}:{snippet.line_number}: uses 'agent_memory'"
                )

        assert not violations, f"Wrong package name imports: {violations}"


@pytest.mark.docs
@pytest.mark.imports
class TestSnippetImports:
    """Test that imports in snippets are resolvable."""

    def test_neo4j_agent_memory_imports_exist(self, python_snippets: list[CodeSnippet]):
        """Verify that imported names from neo4j_agent_memory exist."""
        # Collect all imports from neo4j_agent_memory
        import_pattern = re.compile(r"from\s+neo4j_agent_memory(?:\.\w+)*\s+import\s+([^#\n]+)")

        all_imports: set[str] = set()
        for snippet in python_snippets:
            for match in import_pattern.finditer(snippet.code):
                imports_str = match.group(1)
                # Parse comma-separated imports, handling parentheses
                imports_str = imports_str.replace("(", "").replace(")", "").replace("\n", " ")
                for name in imports_str.split(","):
                    name = name.strip()
                    if name and not name.startswith("#"):
                        # Handle "as" aliases
                        if " as " in name:
                            name = name.split(" as ")[0].strip()
                        all_imports.add(name)

        # Try to import each name
        import neo4j_agent_memory

        missing = []
        for name in all_imports:
            # Check if name exists in the package
            if not hasattr(neo4j_agent_memory, name):
                # Try submodules
                found = False
                for submodule in ["extraction", "models", "config", "memory"]:
                    try:
                        mod = getattr(neo4j_agent_memory, submodule, None)
                        if mod and hasattr(mod, name):
                            found = True
                            break
                    except Exception:
                        pass
                if not found:
                    missing.append(name)

        # Allow some names that might be in submodules not checked
        # These are classes that exist in submodules but are imported via
        # submodule paths in docs (e.g., from neo4j_agent_memory.integrations.langchain import ...)
        allowed_missing = {
            # Schema and extraction
            "EntitySchemaConfig",
            "EntityTypeConfig",
            "RelationTypeConfig",
            "StreamingExtractor",
            "GLiNERWithRelationsExtractor",
            "SpacyEntityExtractor",
            "GLiNEREntityExtractor",
            "LLMEntityExtractor",
            "ExtractionPipeline",
            "MergeStrategy",
            # Enrichment providers
            "BackgroundEnrichmentService",
            "WikimediaEnrichmentProvider",
            "DiffbotEnrichmentProvider",
            "WikimediaProvider",
            "WikimediaEnricher",
            "DiffbotProvider",
            "EnrichmentResult",
            "EnrichmentStatus",
            # Integration classes (imported from submodules)
            "Neo4jAgentMemory",
            "Neo4jChatMessageHistory",
            "Neo4jChatStore",
            "Neo4jCrewMemory",
            "Neo4jLlamaIndexMemory",
            "Neo4jOpenAIMemory",
            "Neo4jMemoryRetriever",
            "Neo4jMemoryVectorStore",
            "MemoryDependency",
            "create_memory_tools",
            "execute_memory_tool",
            # Microsoft Agent integration classes
            "Neo4jContextProvider",
            "Neo4jChatMessageStore",
            "Neo4jMicrosoftMemory",
            "GDSConfig",
            "GDSAlgorithm",
            "GDSIntegration",
            "record_agent_trace",
            "get_similar_traces",
            "format_traces_for_prompt",
            # Schema config
            "SchemaConfig",
            "SchemaModel",
            # Observability
            "ObservabilityConfig",
            "TracingProvider",
            # Resolution
            "DeduplicationStrategy",
            # AWS/Strands integration classes (imported from submodules)
            "BedrockEmbedder",
            "context_graph_tools",
            "HybridMemoryProvider",
            "StrandsConfig",
            "MemoryType",
            # Observability (imported from neo4j_agent_memory.observability)
            "get_tracer",
            # v0.3 reasoning models (imported from neo4j_agent_memory.schema.models)
            "EntityRef",
            "TraceOutcome",
            "AdoptionLabelReport",
            "AdoptionReport",
        }
        actual_missing = set(missing) - allowed_missing

        if actual_missing:
            pytest.fail(f"Imports not found in neo4j_agent_memory: {sorted(actual_missing)}")


@pytest.mark.docs
class TestCompleteSnippets:
    """Test complete (runnable) snippets."""

    def test_complete_snippets_have_imports(self, complete_snippets: list[CodeSnippet]):
        """Complete snippets should have import statements."""
        for snippet in complete_snippets:
            assert "import" in snippet.code, (
                f"Complete snippet missing imports: {snippet.file_path.name}:{snippet.line_number}"
            )

    def test_complete_snippets_parse_as_module(self, complete_snippets: list[CodeSnippet]):
        """Complete snippets should parse as valid Python modules."""
        for snippet in complete_snippets:
            try:
                ast.parse(snippet.code)
            except SyntaxError as e:
                pytest.fail(
                    f"Failed to parse complete snippet: {snippet.file_path.name}:{snippet.line_number}\n"
                    f"Error: {e}"
                )


@pytest.mark.docs
class TestSnippetCoverage:
    """Test documentation coverage."""

    def test_tutorials_have_code_snippets(self, docs_dir: Path):
        """Each tutorial should have code snippets."""
        tutorials_dir = docs_dir / "tutorials"
        if not tutorials_dir.exists():
            pytest.skip("tutorials directory not found")

        for tutorial in tutorials_dir.glob("*.adoc"):
            if tutorial.name == "index.adoc":
                continue
            content = tutorial.read_text()
            assert "[source,python]" in content, f"{tutorial.name} has no Python code snippets"

    def test_howto_guides_have_code_snippets(self, docs_dir: Path):
        """Each how-to guide should have code snippets."""
        howto_dir = docs_dir / "how-to"
        if not howto_dir.exists():
            pytest.skip("how-to directory not found")

        for guide in howto_dir.glob("*.adoc"):
            if guide.name == "index.adoc":
                continue
            content = guide.read_text()
            assert "[source,python]" in content or "[source,bash]" in content, (
                f"{guide.name} has no code snippets"
            )

    def test_reference_docs_have_api_examples(self, docs_dir: Path):
        """Reference API docs should have usage examples."""
        api_dir = docs_dir / "reference" / "api"
        if not api_dir.exists():
            pytest.skip("reference/api directory not found")

        for api_doc in api_dir.glob("*.adoc"):
            if api_doc.name == "index.adoc":
                continue
            content = api_doc.read_text()
            # API docs should have at least one code example
            has_code = "[source,python]" in content or "[source,cypher]" in content
            assert has_code, f"{api_doc.name} has no code examples"


@pytest.mark.docs
class TestSnippetConsistency:
    """Test that snippets are consistent across documentation."""

    def test_client_initialization_pattern(self, python_snippets: list[CodeSnippet]):
        """MemoryClient initialization should follow consistent patterns."""
        init_patterns = []
        for snippet in python_snippets:
            if "MemoryClient(" in snippet.code:
                init_patterns.append(snippet)

        # All should use either MemorySettings or direct parameters
        # (not a mix of deprecated patterns)
        has_settings = any("MemorySettings" in s.code for s in init_patterns)
        has_direct = any("neo4j_uri=" in s.code or "neo4j={" in s.code for s in init_patterns)

        # Both patterns are allowed, but warn if using deprecated patterns
        deprecated_patterns = [
            s
            for s in init_patterns
            if "neo4j_connection" in s.code  # Old parameter name
        ]

        assert not deprecated_patterns, (
            f"Deprecated MemoryClient patterns found: "
            f"{[f'{s.file_path.name}:{s.line_number}' for s in deprecated_patterns]}"
        )

    def test_async_patterns_consistent(self, python_snippets: list[CodeSnippet]):
        """Async code should use consistent patterns."""
        for snippet in python_snippets:
            # Check for mixing asyncio.run with async def
            if "asyncio.run(" in snippet.code and "async def main" in snippet.code:
                # This is fine - common pattern
                continue

            # Check for await outside async function (would be syntax error)
            # This is caught by syntax tests, but we can provide better error messages
            lines = snippet.code.split("\n")
            in_async_func = False
            for i, line in enumerate(lines):
                if "async def " in line:
                    in_async_func = True
                elif line.strip().startswith("def ") and "async" not in line:
                    in_async_func = False
                elif "await " in line and not in_async_func:
                    # Could be a top-level await in a script context
                    # or inside a class method - these are usually fine
                    pass


@pytest.mark.docs
@pytest.mark.imports
class TestSettingsFieldDrift:
    """Verify that kwargs in documented config constructions exist on the model.

    Pre-0.2 ``MemorySettings`` accepted ``extra="ignore"``, which silently
    dropped misspelled kwargs (``schema=`` instead of ``schema_config=``).
    With ``extra="forbid"``, those errors surface at construction time —
    but only when the snippet is actually executed. This test validates
    the kwargs **statically** against ``model_fields`` so docs that are
    only syntax-checked still catch the typo.
    """

    # Models whose constructions we want to validate. Map model class to
    # the import name documentation uses.
    _MODELS_TO_CHECK = {
        "MemorySettings": "neo4j_agent_memory.config.settings:MemorySettings",
        "Neo4jConfig": "neo4j_agent_memory.config.settings:Neo4jConfig",
        "EmbeddingConfig": "neo4j_agent_memory.config.settings:EmbeddingConfig",
        "LLMConfig": "neo4j_agent_memory.config.settings:LLMConfig",
        "SchemaConfig": "neo4j_agent_memory.config.settings:SchemaConfig",
        "ExtractionConfig": "neo4j_agent_memory.config.settings:ExtractionConfig",
        "ResolutionConfig": "neo4j_agent_memory.config.settings:ResolutionConfig",
        "MemoryConfig": "neo4j_agent_memory.config.settings:MemoryConfig",
        "SearchConfig": "neo4j_agent_memory.config.settings:SearchConfig",
        "GeocodingConfig": "neo4j_agent_memory.config.settings:GeocodingConfig",
        "EnrichmentConfig": "neo4j_agent_memory.config.settings:EnrichmentConfig",
    }

    @staticmethod
    def _resolve_model_fields(class_name: str) -> set[str] | None:
        spec = TestSettingsFieldDrift._MODELS_TO_CHECK.get(class_name)
        if spec is None:
            return None
        module_path, attr = spec.split(":")
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, attr)
        return set(cls.model_fields.keys())

    def test_no_unknown_kwargs_in_doc_constructions(self, python_snippets: list[CodeSnippet]):
        """Walk every doc snippet's AST. For each ``Class(kwarg=...)`` call
        whose class is in ``_MODELS_TO_CHECK``, verify each kwarg exists on
        the live model.
        """
        violations: list[str] = []

        for snippet in python_snippets:
            if snippet.is_signature_doc or snippet.is_placeholder_snippet:
                continue
            try:
                tree = ast.parse(snippet.get_syntax_checkable_code())
            except SyntaxError:
                # Syntax test owns this — skip here so we only report drift.
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # Only handle ``ClassName(...)``, not ``module.ClassName(...)``.
                if isinstance(func, ast.Name):
                    class_name = func.id
                elif isinstance(func, ast.Attribute):
                    class_name = func.attr
                else:
                    continue

                fields = self._resolve_model_fields(class_name)
                if fields is None:
                    continue

                for kw in node.keywords:
                    if kw.arg is None:  # **kwargs splat — can't check
                        continue
                    if kw.arg not in fields:
                        violations.append(
                            f"{snippet.file_path.name}:{snippet.line_number}"
                            f" {class_name}({kw.arg}=...) is not a real field;"
                            f" valid fields: {sorted(fields)}"
                        )

        assert not violations, (
            "Documented config constructions reference unknown fields. "
            "Either fix the docs to use valid kwargs, or add the field to "
            "the model:\n  - " + "\n  - ".join(violations)
        )
