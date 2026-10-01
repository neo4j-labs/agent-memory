"""Smoke tests for the ontology-lifecycle-bolt example.

The fast cases need no Neo4j and no model weights: the files, the pin, parity
with the NAMS twin (same Arrows document, same transcript), and the draft the
local Arrows import plus ``repair_draft()`` produce.

``test_example_runs_end_to_end`` runs ``main()`` against a real database with
real GLiNER2.5 inference. It asserts what the migration has to show — the
extracted tickets were labelled ``:Ticket``, every one of them was relabelled,
and none is left — rather than exact entity lists, which depend on the model.
The example leaves its ontology active by design, so the test restores the
binding it found and removes the run's session afterwards: CI runs every
example test against one database.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "ontology-lifecycle-bolt"
NAMS_TWIN_DIR = EXAMPLES_DIR / "ontology-lifecycle"
MAIN_PATH = EXAMPLE_DIR / "main.py"
ARROWS_PATH = EXAMPLE_DIR / "schemas" / "support-desk.arrows.json"


def _load_example() -> ModuleType:
    """Import the example's ``main.py`` under a throwaway module name."""
    spec = importlib.util.spec_from_file_location("ontology_lifecycle_bolt_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _literal(path: Path, name: str) -> Any:
    """Evaluate one module-level literal assignment without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} defines no module-level {name}")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestExampleStructure:
    @pytest.mark.syntax
    def test_the_files_exist(self):
        for name in ("main.py", "README.md", "requirements.txt", ".env.example"):
            assert (EXAMPLE_DIR / name).exists(), f"ontology-lifecycle-bolt/{name} is missing"
        assert ARROWS_PATH.exists()

    @pytest.mark.syntax
    def test_main_is_valid_python(self):
        ast.parse(MAIN_PATH.read_text(encoding="utf-8"))

    @pytest.mark.syntax
    def test_requirements_pin_the_library_with_the_extras_it_needs(self):
        pin = assert_library_pin(EXAMPLE_DIR / "requirements.txt")
        assert set(pin.extras) == {"gliner2", "sentence-transformers"}

    @pytest.mark.syntax
    def test_readme_carries_the_labs_badge_the_warning_and_the_footer(self):
        content = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in content
        assert "leaves `support-desk` revision 2 (strict) active" in content
        assert re.search(r"[Vv]erified against", content)

    @pytest.mark.syntax
    def test_env_example_documents_the_connection(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        for name in ("NEO4J_URI=", "NEO4J_USERNAME=", "NEO4J_PASSWORD="):
            assert name in content

    @pytest.mark.syntax
    def test_main_drives_the_whole_ontology_surface_on_bolt(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        for call in (
            "client.ontology.list(",
            "client.ontology.get_active(",
            "client.ontology.import_(",
            "client.ontology.create(",
            "client.ontology.update(",
            "client.ontology.activate(",
            "client.ontology.diff(",
            "client.ontology.migrate(",
            "dry_run=True",
            "client.ontology.get_migration(",
            "client.short_term.add_message(",
            "client.query.cypher(",
        ):
            assert call in source, f"the example no longer calls {call}"

    @pytest.mark.syntax
    def test_main_takes_its_ontology_from_the_database(self):
        """An ontology file or template would outrank the active stored version."""
        source = MAIN_PATH.read_text(encoding="utf-8")
        assert "use_active_ontology=True" in source
        for override in ("ontology_path", "custom_schema_path", "ontology_template"):
            assert override not in source, f"{override} would bypass the activated version"
        # MemoryClient(ontology=...) outranks everything; `use_active_ontology=`
        # is the setting the example relies on, hence the word boundary.
        assert not re.search(r"\bontology=", source), "ontology= would bypass the activated version"

    @pytest.mark.syntax
    def test_main_is_keyless_and_has_no_credential_fallbacks(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        assert "llm=None" in source
        assert "enable_llm_fallback=False" in source
        assert 'os.environ["NEO4J_PASSWORD"]' in source
        assert "test-password" not in source


# ---------------------------------------------------------------------------
# Parity with the NAMS twin
# ---------------------------------------------------------------------------


class TestParityWithTheNamsTwin:
    @pytest.mark.syntax
    def test_the_arrows_document_is_identical(self):
        twin = NAMS_TWIN_DIR / "schemas" / "support-desk.arrows.json"
        assert ARROWS_PATH.read_bytes() == twin.read_bytes(), (
            "the two lifecycle examples must import the same Arrows document"
        )

    @pytest.mark.syntax
    def test_the_transcript_is_identical(self):
        assert _literal(MAIN_PATH, "TRANSCRIPT") == _literal(
            NAMS_TWIN_DIR / "main.py", "TRANSCRIPT"
        )

    @pytest.mark.syntax
    def test_the_rename_is_identical(self):
        for name in ("OLD_TYPE", "NEW_TYPE", "DOMAIN_ID"):
            assert _literal(MAIN_PATH, name) == _literal(NAMS_TWIN_DIR / "main.py", name)


# ---------------------------------------------------------------------------
# The draft (local import + repair, no database)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def example_module():
    module = _load_example()
    yield module
    sys.modules.pop("ontology_lifecycle_bolt_main", None)


@pytest.fixture(scope="module")
def repaired(example_module):
    from neo4j_agent_memory.ontology.store import BoltOntology

    # import_ converts locally and never touches the client.
    store = BoltOntology.__new__(BoltOntology)
    draft = asyncio.run(
        BoltOntology.import_(
            store, content=ARROWS_PATH.read_text(encoding="utf-8"), format="arrows"
        )
    )
    assert draft.document is not None
    return draft, example_module.repair_draft(draft.document)


class TestRepairedDraft:
    @pytest.mark.imports
    def test_the_local_import_falls_back_to_object_and_says_so(self, repaired):
        draft, _ = repaired
        assert {et.pole_type for et in draft.document.entity_types} == {"OBJECT"}
        assert {w.code for w in draft.warnings} == {"unmapped_label"}

    @pytest.mark.imports
    def test_repair_sets_the_types_the_hosted_service_infers(self, repaired):
        _, document = repaired
        types = {et.label: (et.pole_type, et.subtype) for et in document.entity_types}
        assert types == {
            "Customer": ("PERSON", "CUSTOMER"),
            "Order": ("EVENT", "ORDER"),
            "Product": ("OBJECT", "PRODUCT"),
            "Ticket": ("EVENT", "TICKET"),
        }
        assert document.domain.id == "support-desk"
        assert document.validate_structure() == []

    @pytest.mark.imports
    def test_every_type_carries_an_annotation_guideline(self, repaired):
        _, document = repaired
        for et in document.entity_types:
            assert et.description and "Never" in et.description, et.label
        ticket = document.entity_type("Ticket")
        assert ticket is not None and "TK-" in (ticket.description or "")

    @pytest.mark.imports
    def test_the_ticket_label_reaches_the_graph(self, repaired):
        _, document = repaired
        assert document.node_label("EVENT", "TICKET") == "Ticket"

    @pytest.mark.imports
    def test_the_rename_rewrites_relationship_endpoints(self, example_module, repaired):
        _, document = repaired
        revised = example_module.rename_entity_type(document, "Ticket", "SupportCase")
        assert "Ticket" not in revised.labels()
        assert "SupportCase" in revised.labels()
        endpoints = {(r.source, r.target) for r in revised.relationships}
        assert not any("Ticket" in pair for pair in endpoints)
        assert revised.validate_structure() == []
        assert revised.node_label("EVENT", "TICKET") == "SupportCase"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

SESSION_CLEANUP = """
MATCH (c:Conversation {session_id: $session_id})
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
DETACH DELETE c, m, e
"""

SESSION_LABELS = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(e:Entity)
WITH DISTINCT e
RETURN count(CASE WHEN 'Ticket' IN labels(e) THEN 1 END) AS tickets,
       count(CASE WHEN 'SupportCase' IN labels(e) THEN 1 END) AS support_cases
"""


@pytest.mark.requires_neo4j
def test_example_runs_end_to_end(neo4j_env):
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("gliner2")

    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.core.exceptions import NotSupportedError

    module = _load_example()

    async def active_version_id() -> str | None:
        async with MemoryClient(module.build_settings()) as client:
            try:
                return (await client.ontology.get_active()).version_id
            except NotSupportedError:
                return None

    before = asyncio.run(active_version_id())
    summary: dict[str, Any] = {}
    try:
        summary = asyncio.run(module.main())

        async def read_back() -> dict[str, Any]:
            async with MemoryClient(module.build_settings()) as client:
                labels = (
                    await client.query.cypher(SESSION_LABELS, {"session_id": summary["session_id"]})
                )[0]
                active = await client.ontology.get_active()
                return {
                    "labels": labels,
                    "active_version": active.version_id,
                    "mode": active.validation_mode,
                    "client_domain": client.ontology_document.domain.id,
                }

        state = asyncio.run(read_back())

        # The extracted tickets were labelled :Ticket, and the migration
        # relabelled every one of them.
        assert summary["dry_run_total"] >= 1
        assert summary["relabelled"] == summary["dry_run_total"]
        assert state["labels"]["tickets"] == 0
        assert state["labels"]["support_cases"] >= 1

        # Revision 2 is the binding the run leaves behind, and a new client
        # adopts it at connect time.
        assert state["active_version"] == summary["v2"]
        assert state["mode"] == "strict"
        assert state["client_domain"] == "support-desk"
        assert summary["previous_version_id"] == before
    finally:

        async def restore() -> None:
            async with MemoryClient(module.build_settings()) as client:
                if before is not None:
                    await client.ontology.activate(before)
                elif summary.get("ontology_id"):
                    await client.ontology.delete(summary["ontology_id"])
                if summary.get("session_id"):
                    await client.graph.execute_write(
                        SESSION_CLEANUP, {"session_id": summary["session_id"]}
                    )

        try:
            asyncio.run(restore())
        finally:
            sys.modules.pop("ontology_lifecycle_bolt_main", None)
