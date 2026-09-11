"""Smoke tests for the existing-graph example.

Two layers:

* structure / imports / content — no database, runs in CI's
  ``example-tests-quick`` job.
* an end-to-end class marked ``requires_neo4j`` that seeds a scratch
  database, adopts it, writes messages and retrieves — runs in the
  Neo4j-backed ``example-tests`` job.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXISTING_GRAPH_DIR = EXAMPLES_DIR / "existing-graph"

PYTHON_FILES = ["memory_settings.py", "seed.py", "adopt.py", "memory_io.py", "retrieve.py"]
MODULE_NAMES = {
    "memory_settings": "existing_graph_memory_settings",
    "seed": "existing_graph_seed",
    "adopt": "existing_graph_adopt",
    "memory_io": "existing_graph_memory_io",
    "retrieve": "existing_graph_retrieve",
}


def _load_module(stem: str):
    """Load one of the example scripts under a test-local module name."""
    module_name = MODULE_NAMES[stem]
    spec = importlib.util.spec_from_file_location(module_name, EXISTING_GRAPH_DIR / f"{stem}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _unload_modules() -> None:
    for module_name in MODULE_NAMES.values():
        sys.modules.pop(module_name, None)


@pytest.mark.syntax
class TestExistingGraphExampleStructure:
    def test_required_files_exist(self):
        for filename in [
            "README.md",
            "seed_domain_graph.cypher",
            "run.sh",
            *PYTHON_FILES,
        ]:
            assert (EXISTING_GRAPH_DIR / filename).exists(), f"Missing example file: {filename}"

    def test_python_files_compile(self):
        for filename in PYTHON_FILES:
            ast.parse((EXISTING_GRAPH_DIR / filename).read_text(encoding="utf-8"))


@pytest.mark.imports
class TestExistingGraphExampleImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig  # noqa: F401
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            EmbeddingConfig,
            EmbeddingProvider,
            ExtractionConfig,
            ExtractorType,
            SchemaConfig,
            SchemaModel,
        )
        from neo4j_agent_memory.schema.models import (  # noqa: F401
            AdoptionLabelReport,
            AdoptionReport,
            EntityRef,
        )

    def test_build_settings_uses_custom_schema(self, monkeypatch):
        """The example's build_settings() must produce a CUSTOM-schema config."""
        # The example resolves a SentenceTransformersProvider via from_provider;
        # skip when the extra is not installed.
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")

        try:
            module = _load_module("memory_settings")
            settings = module.build_settings()

            from neo4j_agent_memory.config.settings import ExtractorType, SchemaModel

            assert settings.schema_config.model is SchemaModel.CUSTOM
            assert settings.schema_config.entity_types == ["PERSON", "MOVIE", "GENRE"]
            assert settings.schema_config.strict_types is True
            # No LLM — runs without API keys.
            assert settings.llm is None
            assert settings.extraction.enable_llm_fallback is False
            # Extraction is off: automatic extraction cannot link a message to
            # an adopted node (see the README "Known gaps"), so mentions are
            # linked explicitly instead.
            assert settings.extraction.extractor_type is ExtractorType.NONE
        finally:
            _unload_modules()

    def test_adoption_mapping_matches_seed_labels(self, monkeypatch):
        pytest.importorskip("sentence_transformers")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
        try:
            module = _load_module("memory_settings")
            assert module.LABEL_TO_TYPE == {
                "Person": "PERSON",
                "Movie": "MOVIE",
                "Genre": "GENRE",
            }
            # :Movie uses `title` — the gotcha the example exists to show.
            assert module.NAME_PROPERTY_PER_LABEL == {"Movie": "title"}
            assert module.SEED_LABELS == ["Genre", "Movie", "Person"]
            # Every demo name is part of the seed graph.
            assert set(module.DEMO_NAMES) <= set(module.SEED_NAMES)

            seed = (EXISTING_GRAPH_DIR / "seed_domain_graph.cypher").read_text(encoding="utf-8")
            for label in module.SEED_LABELS:
                assert f":{label}" in seed
        finally:
            _unload_modules()

    def test_seed_statements_parse(self, monkeypatch):
        pytest.importorskip("sentence_transformers")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
        try:
            module = _load_module("seed")
            statements = module.load_statements()
            assert len(statements) >= 10
            assert all("//" not in statement for statement in statements)
            assert all(statement.strip() for statement in statements)
        finally:
            _unload_modules()


@pytest.mark.syntax
class TestExistingGraphExampleContent:
    """Sanity-check that the example demonstrates what its README claims."""

    def test_adopt_calls_adopt_existing_graph_with_dry_run(self):
        source = (EXISTING_GRAPH_DIR / "adopt.py").read_text(encoding="utf-8")
        assert "adopt_existing_graph" in source
        assert "label_to_type" in source
        assert "dry_run" in source and "--dry-run" in source

    def test_seed_creates_pre_library_nodes(self):
        """Seed graph should NOT use :Entity — that's the whole point of adopt."""
        source = (EXISTING_GRAPH_DIR / "seed_domain_graph.cypher").read_text(encoding="utf-8")
        # Strip comment lines before checking for :Entity in actual Cypher.
        cypher = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("//")
        )
        assert ":Person" in cypher and ":Movie" in cypher
        # No ``:Entity`` super-label in the seed — adoption attaches it.
        assert ":Entity" not in cypher

    def test_seed_never_deletes_the_whole_database(self):
        """The one example aimed at production graphs must not wipe one."""
        cypher = (EXISTING_GRAPH_DIR / "seed_domain_graph.cypher").read_text(encoding="utf-8")
        assert "DETACH DELETE" not in cypher.upper()

        seed = (EXISTING_GRAPH_DIR / "seed.py").read_text(encoding="utf-8")
        # Drop the module docstring, which quotes the unscoped statement in
        # order to say the script never runs it.
        docstring = ast.get_docstring(ast.parse(seed)) or ""
        code = seed.replace(docstring, "")
        # The only delete is label-scoped and gated behind --reset plus an
        # explicit confirmation environment variable.
        assert "MATCH (n) DETACH DELETE n" not in code
        assert "{seed_label_predicate()} DETACH DELETE n" in code
        assert "seed_label_predicate" in seed
        assert "EXISTING_GRAPH_ALLOW_RESET" in seed
        assert "--reset" in seed

    def test_runner_defaults_to_the_repo_container_credentials(self):
        run_sh = (EXISTING_GRAPH_DIR / "run.sh").read_text(encoding="utf-8")
        assert "NEO4J_PASSWORD:-test-password" in run_sh
        # No host cypher-shell dependency any more (comments may mention it).
        commands = "\n".join(
            line for line in run_sh.splitlines() if not line.strip().startswith("#")
        )
        assert "cypher-shell" not in commands
        for script in ["seed.py", "adopt.py --dry-run", "memory_io.py", "retrieve.py"]:
            assert script in run_sh

        settings = (EXISTING_GRAPH_DIR / "memory_settings.py").read_text(encoding="utf-8")
        assert '"test-password"' in settings

    def test_reads_go_through_the_portable_cypher_accessor(self):
        for filename in PYTHON_FILES:
            source = (EXISTING_GRAPH_DIR / filename).read_text(encoding="utf-8")
            # client.graph.execute_read is deprecated (removed in v0.6.0).
            assert "execute_read" not in source, filename
            if filename in {"adopt.py", "memory_io.py", "retrieve.py", "seed.py"}:
                assert "client.query.cypher" in source, filename

    def test_memory_io_links_mentions_explicitly_and_asserts_success(self):
        source = (EXISTING_GRAPH_DIR / "memory_io.py").read_text(encoding="utf-8")
        assert 'extraction_mode="explicit"' in source
        assert "explicit_mentions" in source and "EntityRef(" in source
        # Verification counts every label, not just :Person/:Movie.
        assert "collect(DISTINCT labels(n))" in source
        assert "raise SystemExit" in source
        # No NER path is wired up — explicit mentions are the only writer.
        assert "--with-pipeline" not in source

    def test_retrieve_demonstrates_the_payoff(self):
        source = (EXISTING_GRAPH_DIR / "retrieve.py").read_text(encoding="utf-8")
        assert "search_entities" in source
        assert "add_relationship" in source
        assert "get_related_entities" in source
        assert "IN_GENRE" in source

    def test_readme_is_labs_compliant_and_current(self):
        readme = (EXISTING_GRAPH_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j Labs Project" in readme
        assert "Neo4j Community Forum" in readme
        assert "v0.5.0" in readme
        # Bolt-only note (adopt_existing_graph is unsupported on NAMS).
        assert "Bolt only" in readme
        # No retired model ids in the BYOM callout.
        for retired in ["claude-3-5-sonnet", "gpt-4o", "text-embedding-004"]:
            assert retired not in readme


@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestExistingGraphEndToEnd:
    """Seeds, adopts, writes and retrieves against a real database.

    Covers the three failure modes the structural tests cannot see: wrong
    entity types creating duplicate nodes, missing MENTIONS edges, and
    adoption that is not idempotent.
    """

    @pytest.fixture
    def modules(self, neo4j_env):
        pytest.importorskip("sentence_transformers")
        try:
            yield {stem: _load_module(stem) for stem in MODULE_NAMES}
        finally:
            _unload_modules()

    async def _cleanup(self, settings_module) -> None:
        from neo4j_agent_memory import MemoryClient

        # Scoped by label *and* name so a shared test database keeps whatever
        # else lives in it. The name property is per-label -- :Movie uses
        # `title` -- which is the whole point of NAME_PROPERTY_PER_LABEL. A
        # predicate that only read `n.name` left every :Movie node behind, and
        # the next run's "dry_run=True must not write" assertion saw those
        # already-adopted nodes and failed. Order-dependent, so it only showed
        # up when the whole directory was collected.
        name_props = settings_module.NAME_PROPERTY_PER_LABEL
        predicate = " OR ".join(
            f"(n:{label} AND n.{name_props.get(label, 'name')} IN $names)"
            for label in settings_module.SEED_LABELS
        )
        async with MemoryClient(settings_module.build_settings()) as client:
            await client.graph.execute_write(
                f"MATCH (n) WHERE {predicate} DETACH DELETE n",
                {"names": settings_module.SEED_NAMES},
            )
            await client.graph.execute_write(
                """
                MATCH (c:Conversation {session_id: $session_id})
                OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                DETACH DELETE c, m
                """,
                {"session_id": settings_module.SESSION_ID},
            )

    async def test_seed_adopt_write_retrieve(self, modules):
        from neo4j_agent_memory import MemoryClient

        settings_module = modules["memory_settings"]
        # Start from a known state: a leftover adopted seed graph would make
        # the dry-run assertion below meaningless.
        await self._cleanup(settings_module)
        try:
            await modules["seed"].seed(reset=False)

            # Dry run must not mutate: none of *this example's* seed nodes has
            # been given the :Entity super-label yet.
            #
            # Scoped by name, not by label alone. `Person` is also the PascalCase
            # label the library derives from the POLE+O type PERSON, so in a
            # shared test database `MATCH (e:Entity) WHERE e:Person` counts every
            # person any other example created — 17 of them when the whole
            # tests/examples directory runs against one Neo4j.
            await modules["adopt"].adopt(dry_run=True)
            name_props = settings_module.NAME_PROPERTY_PER_LABEL
            seed_node_predicate = " OR ".join(
                f"(e:{label} AND e.{name_props.get(label, 'name')} IN $names)"
                for label in settings_module.SEED_LABELS
            )
            async with MemoryClient(settings_module.build_settings()) as client:
                rows = await client.query.cypher(
                    f"""
                    MATCH (e:Entity) WHERE {seed_node_predicate}
                    RETURN count(e) AS cnt
                    """,
                    {"names": settings_module.SEED_NAMES},
                )
                assert rows[0]["cnt"] == 0, "dry_run=True must not write"

                report = await client.schema.adopt_existing_graph(
                    label_to_type=settings_module.LABEL_TO_TYPE,
                    name_property_per_label=settings_module.NAME_PROPERTY_PER_LABEL,
                )
                assert report.total_migrated == 8
                assert report.total_skipped == 0

                # Idempotent: a second call migrates nothing and reports the
                # same nodes as already adopted.
                #
                # `already_adopted` is an absolute count over every node
                # carrying one of the mapped labels, and `Person` is also the
                # PascalCase label the library derives from POLE+O `PERSON`, so
                # in a shared database it also counts persons other examples
                # created. Assert the delta against the first call's baseline
                # rather than the absolute 8.
                already_before = report.total_already_adopted
                again = await client.schema.adopt_existing_graph(
                    label_to_type=settings_module.LABEL_TO_TYPE,
                    name_property_per_label=settings_module.NAME_PROPERTY_PER_LABEL,
                )
                assert again.total_migrated == 0
                assert again.total_already_adopted == already_before + 8

                # :Movie nodes took their name from `title`.
                rows = await client.query.cypher(
                    "MATCH (m:Movie:Entity) WHERE m.name IN $names RETURN m.name AS name ORDER BY name",
                    {"names": settings_module.SEED_NAMES},
                )
                assert [row["name"] for row in rows] == ["Arrival", "Inception", "The Matrix"]

            # Writes land on the adopted nodes (raises SystemExit otherwise).
            await modules["memory_io"].write_and_verify()

            async with MemoryClient(settings_module.build_settings()) as client:
                # Exactly one node per demo name, across every label.
                rows = await client.query.cypher(
                    """
                    UNWIND $names AS target
                    MATCH (n) WHERE n.name = target
                    RETURN target, count(n) AS total
                    ORDER BY target
                    """,
                    {"names": settings_module.DEMO_NAMES},
                )
                assert [row["total"] for row in rows] == [1, 1, 1, 1]

                # The MENTIONS edge points at the pre-existing :Movie node,
                # identified by the domain id the seed graph assigned.
                rows = await client.query.cypher(
                    """
                    MATCH (c:Conversation {session_id: $session_id})
                          -[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity:Movie)
                    RETURN DISTINCT e.name AS name, e.type AS type, e.id AS id
                    ORDER BY name
                    """,
                    {"session_id": settings_module.SESSION_ID},
                )
                assert [row["name"] for row in rows] == ["Arrival", "Inception"]
                assert all(row["type"] == "MOVIE" for row in rows)
                assert all(row["id"].startswith("c1e3b2d5-") for row in rows)

            # Retrieval half: backfill + semantic search + traversal.
            await modules["retrieve"].main()

            async with MemoryClient(settings_module.build_settings()) as client:
                movies = await client.long_term.search_entities(
                    modules["retrieve"].SEARCH_QUERY,
                    entity_types=["MOVIE"],
                    limit=5,
                    threshold=0.3,
                )
                assert "Inception" in {movie.name for movie in movies}
        finally:
            await self._cleanup(settings_module)
