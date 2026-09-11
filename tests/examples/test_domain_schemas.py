"""Smoke tests for the domain-schemas example.

The example is one parametrized runner (``examples/domain-schemas/run.py``) plus
one sample corpus per GLiNER domain schema (``examples/domain-schemas/samples/``).

These tests *execute* ``run.main()`` for all eight schemas against a stub
extractor, so no model is downloaded and no database is needed — that is what
catches print/format bugs (an undefined name, a wrong return type) which the
previous substring-only assertions let through. The storage path has its own
``requires_neo4j`` test at the bottom.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from neo4j_agent_memory.extraction import ExtractedEntity, ExtractedRelation, ExtractionResult
from neo4j_agent_memory.extraction import list_schemas as library_schemas

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"
SCHEMAS_DIR = EXAMPLES_DIR / "domain-schemas"
SAMPLES_DIR = SCHEMAS_DIR / "samples"
DOCS_PAGE = (
    REPO_ROOT / "docs" / "modules" / "ROOT" / "pages" / "how-to" / "entity-extraction-schemas.adoc"
)

# Schema -> the wrapper kept for one release so old links keep working.
WRAPPERS = {
    "poleo": "poleo_investigations.py",
    "podcast": "podcast_transcripts.py",
    "news": "news_articles.py",
    "scientific": "scientific_papers.py",
    "business": "business_reports.py",
    "entertainment": "entertainment_content.py",
    "medical": "medical_records.py",
    "legal": "legal_documents.py",
}
ALL_SCHEMAS = list(WRAPPERS)


# --------------------------------------------------------------------------- #
# Loading the example
# --------------------------------------------------------------------------- #
def _load_runner() -> ModuleType:
    """Import ``examples/domain-schemas/run.py`` under a throwaway module name."""
    if str(SCHEMAS_DIR) not in sys.path:
        # run.py imports the sibling ``samples`` package; running it as a script
        # puts this directory on sys.path, importing it here must do the same.
        sys.path.insert(0, str(SCHEMAS_DIR))
    spec = importlib.util.spec_from_file_location("domain_schemas_run", SCHEMAS_DIR / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return _load_runner()


# --------------------------------------------------------------------------- #
# Stubs: a deterministic extractor, so no GLiNER weights are needed
# --------------------------------------------------------------------------- #
def _entity(name: str, entity_type: str, subtype: str | None = None) -> ExtractedEntity:
    return ExtractedEntity(
        name=name,
        type=entity_type,
        subtype=subtype,
        confidence=0.9,
        extractor="stub",
    )


# Covers every highlight section used by the eight sample sets, so the summary
# code path runs with data for each schema.
STUB_ENTITIES = [
    _entity("Dana Ruiz", "PERSON"),
    _entity("Theo Marchand", "PERSON", "ACTOR"),
    _entity("Dominic Vaillant", "PERSON", "DIRECTOR"),
    _entity("Northwind Payments", "ORGANIZATION"),
    _entity("Commercial Arbitration Tribunal", "ORGANIZATION", "COURT"),
    _entity("Zurich", "LOCATION"),
    _entity("Tech Antitrust Case", "EVENT", "CASE"),
    _entity("Northwind Launchpad", "OBJECT", "PRODUCT"),
    _entity("Kubernetes", "OBJECT", "TECHNOLOGY"),
    _entity("Linear", "OBJECT", "TOOL"),
    _entity("product-market fit", "OBJECT", "CONCEPT"),
    _entity("hierarchical attention", "OBJECT", "METHOD"),
    _entity("activation rate", "OBJECT", "METRIC"),
    _entity("Team Topologies", "OBJECT", "BOOK"),
    _entity("BookSum-Extended", "OBJECT", "DATASET"),
    _entity("myocardial infarction", "OBJECT", "DISEASE"),
    _entity("atorvastatin", "OBJECT", "DRUG"),
    _entity("chest pain", "OBJECT", "SYMPTOM"),
    _entity("cardiac catheterization", "OBJECT", "PROCEDURE"),
    _entity("coronary artery", "OBJECT", "BODY_PART"),
    _entity("EGFR", "OBJECT", "GENE"),
    _entity("Sand Kings", "OBJECT", "FILM"),
    _entity("The Kitchen", "OBJECT", "TV_SHOW"),
    _entity("Golden Reel", "OBJECT", "AWARD"),
    _entity("Paul Varian", "OBJECT", "CHARACTER"),
    _entity("Clayton Act", "OBJECT", "LAW"),
    _entity("$18,750,000", "OBJECT", "MONETARY_AMOUNT"),
    # A span that wrapped across source lines: the runner must flatten it.
    _entity("Meridian\n        Holdings", "ORGANIZATION"),
]

STUB_RELATIONS = [
    ExtractedRelation(
        source="Dana Ruiz",
        target="Northwind Payments",
        relation_type="WORKS_AT",
        confidence=0.82,
    )
]


class StubExtractor:
    """Stand-in for ``GLiNEREntityExtractor`` with no model load."""

    def __init__(self, relations: list[ExtractedRelation] | None = None) -> None:
        self.relations = relations or []
        self.extract_calls = 0
        self.batch_calls = 0

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = False,
    ) -> ExtractionResult:
        self.extract_calls += 1
        return ExtractionResult(
            entities=[entity.model_copy() for entity in STUB_ENTITIES],
            relations=[rel.model_copy() for rel in self.relations],
            source_text=text,
        )

    async def extract_batch(
        self,
        texts: list[str],
        *,
        entity_types: list[str] | None = None,
        batch_size: int = 32,
        on_progress: Any = None,
    ) -> list[ExtractionResult]:
        self.batch_calls += 1
        results = [await self.extract(text) for text in texts]
        if on_progress is not None:
            on_progress(len(texts), len(texts))
        return results


@pytest.fixture
def stub_runner(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``run`` with GLiNER available and the extractors replaced by stubs."""
    extractor = StubExtractor()
    relation_extractor = StubExtractor(relations=STUB_RELATIONS)

    def fake_create_extractor(sample: Any, args: Any) -> tuple[Any, Any]:
        from neo4j_agent_memory import ExtractionConfig

        config = ExtractionConfig(
            gliner_schema=sample.schema_name,
            gliner_threshold=args.threshold if args.threshold is not None else sample.threshold,
        )
        return extractor, config

    monkeypatch.setattr(runner, "is_gliner_available", lambda: True)
    monkeypatch.setattr(runner, "create_extractor", fake_create_extractor)
    monkeypatch.setattr(runner, "create_relation_extractor", lambda *_args: relation_extractor)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setattr(runner, "load_env", lambda: None)
    runner.stub_extractor = extractor  # type: ignore[attr-defined]
    runner.stub_relation_extractor = relation_extractor  # type: ignore[attr-defined]
    return runner


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
class TestStructure:
    def test_runner_and_readme_exist(self) -> None:
        assert (SCHEMAS_DIR / "run.py").exists()
        assert (SCHEMAS_DIR / "README.md").exists()

    @pytest.mark.parametrize("schema", ALL_SCHEMAS)
    def test_sample_module_exists(self, schema: str) -> None:
        assert (SAMPLES_DIR / f"{schema}.py").exists()

    @pytest.mark.parametrize(("schema", "wrapper"), sorted(WRAPPERS.items()))
    def test_wrapper_delegates_to_runner(self, schema: str, wrapper: str) -> None:
        """The old filenames stay one release as thin wrappers (no duplication)."""
        source = (SCHEMAS_DIR / wrapper).read_text(encoding="utf-8")
        assert "from run import main" in source
        assert f'SCHEMA = "{schema}"' in source
        # A wrapper is a handful of lines; a corpus is not.
        assert len(source.splitlines()) < 40

    def test_sample_corpora_are_populated(self, runner: ModuleType) -> None:
        for schema in ALL_SCHEMAS:
            sample = runner.get_sample(schema)
            assert sample.documents, f"{schema} has no documents"
            assert sample.highlights, f"{schema} has no summary highlights"
            for doc in sample.documents:
                assert doc.content.strip(), f"{schema} has an empty document"

    def test_sample_schemas_exist_in_the_library(self, runner: ModuleType) -> None:
        available = library_schemas()
        for schema in runner.list_sample_names():
            assert schema in available, f"{schema} is not a built-in GLiNER schema"

    def test_referenced_schema_names_are_real(self) -> None:
        """Every ``for_schema("x")``/``gliner_schema="x"`` literal must exist.

        Scoped to this example and its paired how-to page; the other docs pages
        still need the same pass (tracked as a docs follow-up).
        """
        available = set(library_schemas())
        pattern = re.compile(r"""(?:for_schema|gliner_schema\s*=)\s*\(?\s*["']([a-z_]+)["']""")
        targets = [*SCHEMAS_DIR.rglob("*.py"), SCHEMAS_DIR / "README.md", DOCS_PAGE]
        checked = 0
        for path in targets:
            if not path.exists():
                continue
            for name in pattern.findall(path.read_text(encoding="utf-8")):
                checked += 1
                assert name in available, f"{path.name} references unknown schema '{name}'"
        assert checked, "expected at least one schema literal to check"


class TestRegressionGuards:
    """Guards for the defects the rewrite fixed."""

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return (SCHEMAS_DIR / "run.py").read_text(encoding="utf-8")

    def test_no_batch_extraction_result_misuse(self, runner_source: str) -> None:
        """``GLiNEREntityExtractor.extract_batch`` returns a plain list."""
        assert "BatchExtractionResult]" not in runner_source
        assert "batch_result.total_items" not in runner_source

    def test_no_private_attribute_access(self, runner_source: str) -> None:
        assert "_model_name" not in runner_source
        assert "entity_labels.keys()" not in runner_source

    def test_gliner_guard_is_the_explicit_check(self, runner_source: str) -> None:
        """A ``try/except ImportError`` around the factory can never fire."""
        assert "is_gliner_available()" in runner_source
        assert "except ImportError as e" not in runner_source

    def test_storage_is_pinned_and_local(self, runner_source: str) -> None:
        assert 'backend="bolt"' in runner_source, "MEMORY_API_KEY must not redirect writes"
        assert "llm=None" in runner_source, "storage must not require an OpenAI key"
        assert "ExtractorType.NONE" in runner_source
        assert "sentence-transformers/all-MiniLM-L6-v2" in runner_source

    def test_password_default_matches_repo_neo4j(self, runner_source: str) -> None:
        assert '"NEO4J_PASSWORD", "test-password"' in runner_source
        assert '"NEO4J_PASSWORD", "password"' not in runner_source

    def test_storage_persists_relations_and_provenance(self, runner_source: str) -> None:
        for method in (
            "register_extractor",
            "link_entity_to_extractor",
            "add_relationship",
            "get_entities_by_extractor",
            "get_related_entities",
        ):
            assert method in runner_source, f"storage step should use {method}"


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
class TestRunnerExecution:
    @pytest.mark.parametrize("schema", ALL_SCHEMAS)
    def test_runs_end_to_end(
        self, stub_runner: ModuleType, capsys: pytest.CaptureFixture[str], schema: str
    ) -> None:
        """Every schema runs to completion and prints its summary."""
        exit_code = asyncio.run(stub_runner.main(["--schema", schema, "--no-store"]))
        out = capsys.readouterr().out

        assert exit_code == 0
        sample = stub_runner.get_sample(schema)
        assert f"{schema.upper()} schema" in out
        assert f"{schema.upper()} KNOWLEDGE GRAPH SUMMARY" in out
        for highlight in sample.highlights:
            assert f"{highlight.label}:" in out
        for doc in sample.documents:
            assert doc.title in out
        assert "Set NEO4J_URI" in out
        # Wrapped spans are flattened before they reach the summary or Neo4j.
        assert "Meridian Holdings" in out

    @pytest.mark.parametrize("schema", ALL_SCHEMAS)
    def test_all_demos_run_for_every_schema(
        self,
        stub_runner: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        schema: str,
    ) -> None:
        """``--relations --batch --streaming`` works for any schema."""
        monkeypatch.setattr(stub_runner, "is_glirel_available", lambda: True)
        exit_code = asyncio.run(
            stub_runner.main(
                ["--schema", schema, "--no-store", "--relations", "--batch", "--streaming"]
            )
        )
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "Dana Ruiz -[WORKS_AT]-> Northwind Payments" in out
        assert "BATCH EXTRACTION" in out
        assert "Total entities (batch):" in out
        assert "STREAMING EXTRACTION" in out
        assert "After deduplication:" in out

    def test_glirel_missing_prints_optin_hint(
        self,
        stub_runner: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(stub_runner, "is_glirel_available", lambda: False)
        exit_code = asyncio.run(stub_runner.main(["--schema", "news", "--no-store", "--relations"]))
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "GLiREL is not installed" in out
        assert "pip install glirel" in out

    def test_extract_only_skips_demos(
        self, stub_runner: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = asyncio.run(
            stub_runner.main(["--schema", "scientific", "--no-store", "--extract-only"])
        )
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "STREAMING EXTRACTION" not in out

    def test_missing_gliner_prints_install_hint(
        self,
        runner: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With GLiNER uninstalled the runner explains itself and exits 0."""
        monkeypatch.setattr(runner, "is_gliner_available", lambda: False)
        monkeypatch.setattr(runner, "load_env", lambda: None)
        exit_code = asyncio.run(runner.main(["--schema", "legal", "--no-store"]))
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "GLiNER is not installed" in out
        assert "uv sync --all-extras" in out

    def test_list_flag(
        self,
        runner: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(runner, "load_env", lambda: None)
        exit_code = asyncio.run(runner.main(["--list"]))
        out = capsys.readouterr().out

        assert exit_code == 0
        for schema in ALL_SCHEMAS:
            assert schema in out

    def test_no_schema_is_a_usage_error(
        self,
        runner: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(runner, "load_env", lambda: None)
        exit_code = asyncio.run(runner.main([]))
        assert exit_code == 2
        assert "No --schema given" in capsys.readouterr().out

    def test_store_without_uri_is_a_usage_error(
        self, stub_runner: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = asyncio.run(stub_runner.main(["--schema", "medical", "--store"]))
        assert exit_code == 2
        assert "NEO4J_URI is not set" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Storage (needs a database)
# --------------------------------------------------------------------------- #
@pytest.mark.requires_neo4j
def test_storage_path_writes_a_graph(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--store`` persists entities, provenance and relations over bolt.

    Runs in CI's ``example-tests`` job (which provides ``NEO4J_URI``); skipped in
    ``example-tests-quick``. A ``MEMORY_API_KEY`` in the environment must not
    redirect the writes — ``backend="bolt"`` is pinned in the example.
    """
    pytest.importorskip("sentence_transformers")
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        pytest.skip("NEO4J_URI not set — the storage test needs a database")

    from neo4j import GraphDatabase

    username = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "test-password")
    try:
        with GraphDatabase.driver(uri, auth=(username, password)) as driver:
            driver.verify_connectivity()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Neo4j not reachable at {uri}: {exc}")

    extractor = StubExtractor()
    relation_extractor = StubExtractor(relations=STUB_RELATIONS)

    def fake_create_extractor(sample: Any, args: Any) -> tuple[Any, Any]:
        from neo4j_agent_memory import ExtractionConfig

        return extractor, ExtractionConfig(gliner_schema=sample.schema_name, gliner_threshold=0.4)

    monkeypatch.setattr(runner, "is_gliner_available", lambda: True)
    monkeypatch.setattr(runner, "is_glirel_available", lambda: True)
    monkeypatch.setattr(runner, "create_extractor", fake_create_extractor)
    monkeypatch.setattr(runner, "create_relation_extractor", lambda *_args: relation_extractor)
    monkeypatch.setattr(runner, "load_env", lambda: None)
    monkeypatch.setenv("MEMORY_API_KEY", "nams_not_a_real_key")

    exit_code = asyncio.run(
        runner.main(["--schema", "poleo", "--store", "--relations", "--limit", "10"])
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "backend: bolt" in out
    assert "Stored" in out
    assert "Linked all of them to the :Extractor node" in out
    assert "Read-back:" in out
