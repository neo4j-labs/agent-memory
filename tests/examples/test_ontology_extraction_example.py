"""Smoke tests for the ontology-extraction example.

The fast cases run with no Neo4j and no model weights — they load
``ontology.yaml``, assert it is structurally sound, and compile it into a
JointIE schema against a fake ``joint`` recorder (``gliner2`` is not installed
in the quick CI job). ``test_example_runs_end_to_end`` runs ``main()`` against
a real database and real GLiNER2.5 inference, and asserts the two claims the
example exists to make: the three surface forms of one organization collapse
onto one node, and the near-miss stays separate.
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

from neo4j_agent_memory.ontology.compile import compile_joint_schema
from neo4j_agent_memory.ontology.convert import load_ontology
from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "ontology-extraction"
ONTOLOGY_PATH = EXAMPLE_DIR / "ontology.yaml"
MAIN_PATH = EXAMPLE_DIR / "main.py"

EXPECTED_LABELS = [
    "Customer",
    "Engineer",
    "Organization",
    "Product",
    "Component",
    "Incident",
]
EXPECTED_RELATION_TYPES = {"EMPLOYED_BY", "REPORTED", "USES", "AFFECTS", "PART_OF"}


class FakeJointSchema:
    """Records the calls ``compile_joint_schema`` makes (no gliner2 needed)."""

    def __init__(self) -> None:
        self.entities: list[tuple[Any, ...]] = []
        self.relations: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.no_self_loops_calls: list[Any] = []

    def entity(self, *args: Any, **kwargs: Any) -> FakeJointSchema:
        self.entities.append(args)
        return self

    def relation(self, *args: Any, **kwargs: Any) -> FakeJointSchema:
        self.relations.append((args, kwargs))
        return self

    def no_self_loops(self, relation: Any = None) -> FakeJointSchema:
        self.no_self_loops_calls.append(relation)
        return self


class FakeJoint:
    """Stands in for a ``JointIEEngine``: only its schema factory is used."""

    def __init__(self) -> None:
        self.schema = FakeJointSchema()

    def create_schema(self) -> FakeJointSchema:
        return self.schema


def _load_example() -> ModuleType:
    """Import the example's ``main.py`` under a throwaway module name."""
    spec = importlib.util.spec_from_file_location("ontology_extraction_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestExampleStructure:
    @pytest.mark.syntax
    def test_the_four_files_exist(self):
        for name in ("main.py", "README.md", "ontology.yaml", "requirements.txt"):
            assert (EXAMPLE_DIR / name).exists(), f"ontology-extraction/{name} is missing"

    @pytest.mark.syntax
    def test_main_is_valid_python(self):
        ast.parse(MAIN_PATH.read_text(encoding="utf-8"))

    @pytest.mark.syntax
    def test_requirements_pin_the_library_with_the_extras_it_needs(self):
        pin = assert_library_pin(EXAMPLE_DIR / "requirements.txt")
        assert set(pin.extras) == {"extraction", "sentence-transformers"}, (
            f"expected the extraction + sentence-transformers extras, got {pin.extras}"
        )

    @pytest.mark.syntax
    def test_readme_carries_the_labs_badge_and_verified_footer(self):
        content = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in content
        assert re.search(r"[Vv]erified against", content), (
            "the README needs the 'Verified against ...' footer the registry test checks"
        )

    @pytest.mark.syntax
    def test_main_demonstrates_the_apis_the_readme_advertises(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        for call in (
            "SchemaConfig(ontology_path=",
            "load_ontology(",
            "client.ontology.create(",
            "client.ontology.activate(",
            "client.ontology.get_active(",
            "client.ontology.update(",
            "client.ontology.diff(",
            "client.ontology.delete(",
            "client.short_term.add_message(",
            "client.long_term.find_potential_duplicates(",
        ):
            assert call in source, f"the example no longer calls {call}"
        # Keyless by construction.
        assert "llm=None" in source
        assert "enable_llm_fallback=False" in source
        assert "sentence-transformers/" in source


# ---------------------------------------------------------------------------
# The ontology itself
# ---------------------------------------------------------------------------


class TestOntologyDocument:
    @pytest.fixture
    def doc(self):
        return load_ontology(ONTOLOGY_PATH)

    @pytest.mark.syntax
    def test_it_loads_and_has_no_structural_problems(self, doc):
        assert doc.validate_structure() == []
        assert doc.domain.name == "support-desk"
        assert doc.labels() == EXPECTED_LABELS

    @pytest.mark.syntax
    def test_labels_map_onto_poleo(self, doc):
        assert doc.label_map() == {
            "customer": ("PERSON", "CUSTOMER"),
            "engineer": ("PERSON", "ENGINEER"),
            "organization": ("ORGANIZATION", None),
            "product": ("OBJECT", "PRODUCT"),
            "component": ("OBJECT", "COMPONENT"),
            "incident": ("EVENT", "INCIDENT"),
        }

    @pytest.mark.syntax
    def test_every_type_carries_an_annotation_guideline(self, doc):
        for et in doc.entity_types:
            assert et.description, f"{et.label} has no description — that is prompt surface"
        incident = doc.entity_type("Incident")
        assert "never the fix for it" in (incident.description or ""), (
            "the guidelines are expected to state the negative case"
        )

    @pytest.mark.syntax
    def test_relationships_are_endpoint_typed_and_constrained(self, doc):
        assert set(doc.relationship_types()) == EXPECTED_RELATION_TYPES
        assert doc.permits("Customer", "EMPLOYED_BY", "Organization")
        assert doc.permits("Engineer", "EMPLOYED_BY", "Organization")
        assert doc.permits("Component", "PART_OF", "Product")
        # Endpoint typing is the point: a product does not employ anybody.
        assert not doc.permits("Product", "EMPLOYED_BY", "Organization")

        employed_by = [rel for rel in doc.relationships if rel.type == "EMPLOYED_BY"]
        assert all(rel.unique_source for rel in employed_by)
        assert all(rel.threshold == pytest.approx(0.35) for rel in employed_by)
        part_of = next(rel for rel in doc.relationships if rel.type == "PART_OF")
        assert part_of.acyclic is True

    @pytest.mark.syntax
    def test_the_alias_gazetteer_declares_the_acme_surface_forms(self, doc):
        aliases = doc.entity_type("Organization").aliases
        assert "Acme" in aliases["Acme Corp"]
        assert "ACME Corporation" in aliases["Acme Corp"]
        # The near-miss must not be declared as an alias of anything.
        assert not any("Acme Bank" in forms for forms in aliases.values())

    @pytest.mark.syntax
    def test_it_compiles_into_a_joint_schema(self, doc):
        joint = FakeJoint()
        schema = compile_joint_schema(doc, joint)
        assert schema is joint.schema

        assert [args[0] for args in schema.entities] == EXPECTED_LABELS
        calls = {args[0]: (args, kwargs) for args, kwargs in schema.relations}
        assert set(calls) == EXPECTED_RELATION_TYPES

        # Two defs share the EMPLOYED_BY type and group into one head list.
        employed_args, employed_kwargs = calls["EMPLOYED_BY"]
        assert employed_args[1] == ["Customer", "Engineer"]
        assert employed_args[2] == ["Organization"]
        assert employed_kwargs["unique_head"] is True
        assert employed_kwargs["threshold"] == pytest.approx(0.35)
        assert calls["PART_OF"][1]["acyclic"] is True

        # symmetric=True is broken in gliner2 2.0.0 and must never be passed.
        assert not any("symmetric" in kwargs for _, kwargs in schema.relations)
        # no_self_loops is applied per relation, never globally.
        assert sorted(schema.no_self_loops_calls) == sorted(EXPECTED_RELATION_TYPES)


# ---------------------------------------------------------------------------
# Settings the example builds
# ---------------------------------------------------------------------------


class TestExampleSettings:
    @pytest.mark.imports
    def test_build_settings_is_keyless_and_ontology_driven(self, monkeypatch):
        # The provider string resolves the local adapter eagerly.
        pytest.importorskip("sentence_transformers")
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
        # A stray hosted key must not redirect the example to NAMS.
        monkeypatch.setenv("MEMORY_API_KEY", "nams_xxxxxxxxxxxxxxxx")

        try:
            module = _load_example()
            settings = module.build_settings()

            assert settings.backend == "bolt"
            assert settings.llm is None
            assert settings.schema_config.ontology_path == str(ONTOLOGY_PATH)
            assert settings.extraction.enable_gliner is True
            assert settings.extraction.enable_spacy is False
            assert settings.extraction.enable_llm_fallback is False
            # Resolution on ingest is what merges the aliases.
            assert settings.resolution.resolve_on_ingest is True
            assert len(module.MESSAGES) == 6
            assert module.SESSIONS == [module.INTAKE, module.FOLLOWUP]
        finally:
            sys.modules.pop("ontology_extraction_main", None)


# ---------------------------------------------------------------------------
# End to end (real database, real GLiNER2.5 inference)
# ---------------------------------------------------------------------------

ORG_NODES = """
MATCH (e:Entity {type: 'ORGANIZATION'})
RETURN e.name AS name, coalesce(e.aliases, []) AS aliases
"""

ORG_MENTIONS = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id IN $sessions AND e.type = 'ORGANIZATION'
RETURN count(*) AS mentions
"""

TYPED_EDGES = """
MATCH (:Entity)-[r:RELATED_TO]->(:Entity)
RETURN r.type AS relation_type, r.support AS support, r.derived AS derived
"""

STORED_ONTOLOGIES = """
MATCH (o:Ontology)
OPTIONAL MATCH (v:OntologyVersion)
RETURN count(DISTINCT o) AS ontologies, count(DISTINCT v) AS versions
"""


@pytest.mark.requires_neo4j
def test_example_runs_end_to_end(neo4j_env):
    """Run main() and assert the resolution and provenance claims hold."""
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("gliner2")

    from neo4j_agent_memory import MemoryClient

    try:
        module = _load_example()
        asyncio.run(module.main())
        sessions = list(module.SESSIONS)

        async def read_back() -> dict[str, Any]:
            async with MemoryClient(module.build_settings()) as client:
                return {
                    "orgs": list(await client.query.cypher(ORG_NODES)),
                    "org_mentions": int(
                        (await client.query.cypher(ORG_MENTIONS, {"sessions": sessions}))[0][
                            "mentions"
                        ]
                    ),
                    "edges": list(await client.query.cypher(TYPED_EDGES)),
                    "ontologies": (await client.query.cypher(STORED_ONTOLOGIES))[0],
                }

        state = asyncio.run(read_back())

        orgs = {row["name"]: list(row["aliases"]) for row in state["orgs"]}

        # 1. Fewer organization nodes than organization mentions: the three
        #    surface forms of Acme Corp resolved onto one node.
        assert len(orgs) < state["org_mentions"], (
            f"expected the Acme aliases to merge; got {orgs} from {state['org_mentions']} mentions"
        )
        assert "Acme Corp" in orgs, f"canonical organization missing: {orgs}"
        assert {"Acme", "ACME Corporation"} <= set(orgs["Acme Corp"]), (
            f"the alias gazetteer did not fold the surface forms in: {orgs['Acme Corp']}"
        )

        # 2. The near-miss is its own node and was not absorbed.
        assert "Acme Bank" in orgs, f"Acme Bank must stay separate; got {orgs}"
        assert "Acme Bank" not in orgs["Acme Corp"]

        # 3. Typed relations with provenance: the ontology's types, at least
        #    one triple observed twice.
        types_seen = {row["relation_type"] for row in state["edges"]}
        assert types_seen <= EXPECTED_RELATION_TYPES, (
            f"edges carry types the ontology does not declare: {types_seen}"
        )
        assert len(types_seen) >= 3, f"expected several relation types, got {types_seen}"
        assert max(int(row["support"]) for row in state["edges"]) >= 2, (
            "no triple reached support >= 2, so the cross-session merge did not land"
        )
        assert all(row["derived"] is False for row in state["edges"])

        # 4. The demo cleaned its ontology up (it activates one, which would
        #    otherwise drive the next client that connects).
        assert int(state["ontologies"]["ontologies"]) == 0
        assert int(state["ontologies"]["versions"]) == 0
    finally:
        sys.modules.pop("ontology_extraction_main", None)
