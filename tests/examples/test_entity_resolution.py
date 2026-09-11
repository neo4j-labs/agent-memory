"""Smoke tests for `examples/entity_resolution.py`.

The example needs no Neo4j, no API key, and no network: it exercises the
resolution layer against an in-memory list of names. That makes it the one
example whose script this suite can actually run end to end, so the subprocess
test below asserts on the process exit status and on substantive output lines,
not only on the closing banner — a swallowed failure or a renamed resolver has
to make this test fail.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

# Lines the script prints on every platform with no extras beyond `[fuzzy]`
# (installed in CI via `uv sync --all-extras`). Each one is produced by a
# different section, so a broken section fails here rather than silently
# printing a SKIPPED line.
EXPECTED_STDOUT_LINES = [
    # Section 1 — exact matching canonicalizes case and whitespace.
    "'john smith' (PERSON) -> 'John Smith' [match_type=exact",
    # Section 2 — fuzzy matching absorbs a typo.
    "'Jon Smith' (PERSON) -> 'John Smith' [match_type=fuzzy",
    # Section 3 — the composite chain falls through exact to fuzzy.
    "'Jhon Smith' (PERSON) -> 'John Smith' [match_type=fuzzy",
    # Section 4 — the type-aware guarantee keeps the club out of the city.
    "type_strict=True : kept separate -> new ORGANIZATION 'New York City FC'",
    "type_strict=False: merged into the stored LOCATION 'New York City'",
    # Section 6 — within-batch deduplication, type-keyed.
    "7 references -> 4 entities",
    "'john smith' folded into 'John Smith'",
]


class TestEntityResolutionExample:
    """Smoke tests for the entity resolution example."""

    def test_example_file_exists(self, examples_dir):
        """Verify the example file exists."""
        example_path = examples_dir / "entity_resolution.py"
        assert example_path.exists(), f"Example file not found: {example_path}"

    def test_example_imports_work(self):
        """Verify the example can import required modules."""
        from neo4j_agent_memory.resolution import (
            CompositeResolver,
            ExactMatchResolver,
            FuzzyMatchResolver,
            ResolvedEntity,
        )

        assert ExactMatchResolver is not None
        assert FuzzyMatchResolver is not None
        assert CompositeResolver is not None
        assert ResolvedEntity is not None

    @pytest.mark.asyncio
    async def test_exact_match_resolver(self):
        """Section 1: exact matching normalizes case and whitespace."""
        from neo4j_agent_memory.resolution import ExactMatchResolver

        resolver = ExactMatchResolver()
        existing = ["John Smith", "Acme Corporation"]

        result = await resolver.resolve("John Smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "John Smith"
        assert result.match_type == "exact"

        # Case-insensitive: the *stored* spelling wins.
        result = await resolver.resolve("john smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "John Smith"
        assert result.match_type == "exact"

        # A miss reports "none", which is what the example branches on.
        result = await resolver.resolve("Jon Smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "Jon Smith"
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_fuzzy_match_resolver(self):
        """Section 2: fuzzy matching at the example's threshold."""
        from neo4j_agent_memory.resolution import FuzzyMatchResolver

        resolver = FuzzyMatchResolver(threshold=0.85)
        if not resolver.is_available:
            pytest.skip("rapidfuzz not installed - fuzzy matching unavailable")

        existing = ["John Smith", "Acme Corporation"]

        # Typo matches at the example's default threshold (0.85).
        result = await resolver.resolve("Jon Smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "John Smith"
        assert result.confidence > 0.85

        # An abbreviation does not — this is the gap section 5 fills.
        result = await resolver.resolve("Acme Corp", "ORGANIZATION", existing_entities=existing)
        assert result.canonical_name == "Acme Corp"

    @pytest.mark.asyncio
    async def test_composite_resolver(self):
        """Section 3: the chained resolver, exact before fuzzy."""
        from neo4j_agent_memory.resolution import CompositeResolver, FuzzyMatchResolver

        if not FuzzyMatchResolver().is_available:
            pytest.skip("rapidfuzz not installed - composite resolution unavailable")

        resolver = CompositeResolver(fuzzy_threshold=0.85)
        existing = ["John Smith"]

        result = await resolver.resolve("john smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "John Smith"
        assert result.match_type == "exact"
        assert result.confidence >= 0.9

        result = await resolver.resolve("Jhon Smith", "PERSON", existing_entities=existing)
        assert result.canonical_name == "John Smith"
        assert result.match_type == "fuzzy"

        result = await resolver.resolve("Totally Different", "PERSON", existing_entities=existing)
        assert result.canonical_name == "Totally Different"
        assert result.match_type == "none"

    @pytest.mark.asyncio
    async def test_type_aware_resolution(self):
        """Section 4: type_strict blocks a cross-type merge, but only with a type map."""
        from neo4j_agent_memory.resolution import CompositeResolver, FuzzyMatchResolver

        if not FuzzyMatchResolver().is_available:
            pytest.skip("rapidfuzz not installed - composite resolution unavailable")

        existing = ["New York City", "Acme Corporation"]
        types = {"New York City": "LOCATION", "Acme Corporation": "ORGANIZATION"}

        strict = CompositeResolver(fuzzy_threshold=0.85)
        loose = CompositeResolver(fuzzy_threshold=0.85, type_strict=False)

        # The ORGANIZATION never folds into the LOCATION.
        result = await strict.resolve(
            "New York City FC",
            "ORGANIZATION",
            existing_entities=existing,
            existing_entity_types=types,
        )
        assert result.canonical_name == "New York City FC"

        # type_strict=False merges them — the behaviour the example contrasts.
        result = await loose.resolve(
            "New York City FC",
            "ORGANIZATION",
            existing_entities=existing,
            existing_entity_types=types,
        )
        assert result.canonical_name == "New York City"

        # Without the type map there is nothing to filter on, so type_strict
        # cannot help: the same wrong merge happens.
        result = await strict.resolve(
            "New York City FC", "ORGANIZATION", existing_entities=existing
        )
        assert result.canonical_name == "New York City"

    @pytest.mark.asyncio
    async def test_batch_resolution(self):
        """Section 6: within-batch deduplication is keyed on (name, type)."""
        from neo4j_agent_memory.resolution import CompositeResolver

        resolver = CompositeResolver(fuzzy_threshold=0.85)

        batch = [
            ("John Smith", "PERSON"),
            ("Jane Doe", "PERSON"),
            ("john smith", "PERSON"),  # duplicate
            ("Paris", "LOCATION"),
            ("Paris", "PERSON"),  # same string, different type
        ]

        results = await resolver.resolve_batch(batch)
        assert len(results) == len(batch)

        assert results[2].canonical_name == "John Smith"

        unique = {(r.canonical_name, r.entity_type) for r in results}
        assert unique == {
            ("John Smith", "PERSON"),
            ("Jane Doe", "PERSON"),
            ("Paris", "LOCATION"),
            ("Paris", "PERSON"),
        }

    @pytest.mark.asyncio
    async def test_example_embedder_helper(self):
        """The optional semantic stage resolves an embedder or declines cleanly."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "entity_resolution_example", EXAMPLES_DIR / "entity_resolution.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(EXAMPLES_DIR))  # the example imports `_env`
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(EXAMPLES_DIR))

        built = module.build_embedder()
        if built is None:
            pytest.skip("no embedder available (no sentence-transformers, no OPENAI_API_KEY)")
        embedder, model = built
        assert model
        assert hasattr(embedder, "embed")

    @pytest.mark.slow
    def test_example_runs_successfully(self):
        """Run the complete example script and verify its output."""
        example_path = EXAMPLES_DIR / "entity_resolution.py"

        # Force UTF-8 stdout in the subprocess so example scripts printing
        # Unicode glyphs don't crash on Windows' default cp1252.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(EXAMPLES_DIR),
            env=env,
            encoding="utf-8",
        )

        assert result.returncode == 0, f"Example exited {result.returncode}:\n{result.stderr}"
        assert "Demo complete" in result.stdout, (
            f"Example failed:\n{result.stdout}\n{result.stderr}"
        )

        for line in EXPECTED_STDOUT_LINES:
            assert line in result.stdout, f"Missing expected output line: {line!r}\n{result.stdout}"

        # Section 5 is optional (needs an embedder), but it must either run or
        # say why it was skipped - never fail silently.
        assert (
            "[match_type=semantic" in result.stdout
            or "SKIPPED: no embedder available" in result.stdout
            or "SKIPPED: embedding backend unavailable" in result.stdout
        ), f"Semantic section neither ran nor reported a skip:\n{result.stdout}"

    def test_example_sections_present(self, examples_dir):
        """Verify the example still covers every documented section."""
        example_path = examples_dir / "entity_resolution.py"
        content = example_path.read_text(encoding="utf-8")

        for heading in (
            "Exact matching",
            "Fuzzy matching",
            "Composite resolution",
            "The type-aware guarantee",
            "Semantic matching",
            "Batch resolution",
            "Where this plugs in",
        ):
            assert heading in content, f"Missing section: {heading}"

        for symbol in (
            "ExactMatchResolver",
            "FuzzyMatchResolver",
            "CompositeResolver",
            "existing_entity_types",
            "type_strict",
            "resolve_batch",
            "is_available",
        ):
            assert symbol in content, f"Missing API reference: {symbol}"
