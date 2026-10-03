"""Unit tests for ontology/compile.py.

``gliner2`` is not installed in the unit environment, so the JointIE schema
is recorded by a fake ``joint`` object and the two unavoidable imports are
satisfied by stub modules in ``sys.modules``.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY, get_template
from neo4j_agent_memory.ontology.compile import (
    compile_attribute_schema,
    compile_joint_schema,
    label_map,
    prompt_fragment,
    spacy_label_map,
)
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    PropertyDef,
    RelationshipDef,
)

# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class FakeSchema:
    """Records every call ``compile_joint_schema`` makes."""

    def __init__(self) -> None:
        self.entities: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.relations: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.no_self_loops_calls: list[Any] = []

    def entity(self, *args: Any, **kwargs: Any) -> FakeSchema:
        self.entities.append((args, kwargs))
        return self

    def relation(self, *args: Any, **kwargs: Any) -> FakeSchema:
        self.relations.append((args, kwargs))
        return self

    def no_self_loops(self, relation: Any = None) -> FakeSchema:
        self.no_self_loops_calls.append(relation)
        return self

    # -- helpers used by the assertions ------------------------------------
    @property
    def entity_labels(self) -> list[str]:
        return [args[0] for args, _ in self.entities]

    @property
    def relation_names(self) -> list[str]:
        return [args[0] for args, _ in self.relations]

    def relation_call(self, name: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        for args, kwargs in self.relations:
            if args[0] == name:
                return args, kwargs
        raise AssertionError(f"relation {name!r} was never declared")


class FakeJoint:
    """Stands in for a ``JointIEEngine``."""

    def __init__(self) -> None:
        self.schema = FakeSchema()

    def create_schema(self) -> FakeSchema:
        return self.schema


@pytest.fixture
def joint() -> FakeJoint:
    return FakeJoint()


def _doc(
    entity_types: list[EntityTypeDef],
    relationships: list[RelationshipDef] | None = None,
    **kwargs: Any,
) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id="test", name="test"),
        entity_types=entity_types,
        relationships=relationships or [],
        **kwargs,
    )


PERSON = EntityTypeDef(label="Person", pole_type="PERSON", description="A human.")
COMPANY = EntityTypeDef(label="Company", pole_type="ORGANIZATION", subtype="COMPANY")
CITY = EntityTypeDef(label="City", pole_type="LOCATION", subtype="CITY")


# -----------------------------------------------------------------------------
# compile_joint_schema
# -----------------------------------------------------------------------------


class TestCompileJointSchema:
    def test_entities_carry_label_description_and_threshold(self, joint: FakeJoint):
        doc = _doc(
            [
                PERSON.model_copy(update={"threshold": 0.4}),
                COMPANY,  # no description
            ]
        )
        schema = compile_joint_schema(doc, joint)

        assert schema is joint.schema
        assert schema.entities[0] == (("Person", "A human."), {"threshold": 0.4})
        assert schema.entities[1] == (("Company", None), {"threshold": None})

    def test_same_type_defs_group_into_head_and_tail_lists(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY, CITY],
            [
                RelationshipDef(
                    type="LOCATED_AT", source="Person", target="City", description="At."
                ),
                RelationshipDef(type="LOCATED_AT", source="Company", target="City"),
            ],
        )
        schema = compile_joint_schema(doc, joint)

        assert schema.relation_names == ["LOCATED_AT"]
        args, kwargs = schema.relation_call("LOCATED_AT")
        assert args[0] == "LOCATED_AT"
        assert args[1] == ["Person", "Company"]
        assert args[2] == ["City"]
        assert args[3] == "At."  # the first def's description wins

    def test_head_and_tail_lists_are_deduplicated_in_order(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="R", source="Person", target="Company"),
                RelationshipDef(type="R", source="Person", target="Person"),
                RelationshipDef(type="R", source="Company", target="Company"),
            ],
        )
        args, _ = compile_joint_schema(doc, joint).relation_call("R")
        assert args[1] == ["Person", "Company"]
        assert args[2] == ["Company", "Person"]

    def test_constraints_map_onto_the_jointschema_aliases(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYED_BY",
                    source="Person",
                    target="Company",
                    threshold=0.35,
                    unique_source=True,
                    unique_target=True,
                    acyclic=True,
                )
            ],
        )
        _, kwargs = compile_joint_schema(doc, joint).relation_call("EMPLOYED_BY")

        assert kwargs["threshold"] == 0.35
        assert kwargs["unique_head"] is True
        assert kwargs["unique_tail"] is True
        assert kwargs["acyclic"] is True
        assert kwargs["allow_self"] is False
        assert kwargs["inverse"] is None

    def test_symmetric_is_never_passed(self, joint: FakeJoint):
        # symmetric=True compiles to a constraint set that rejects every
        # candidate edge in gliner2 2.0.0.
        compile_joint_schema(POLEO_ONTOLOGY, joint)
        for args, kwargs in joint.schema.relations:
            assert "symmetric" not in kwargs
            assert True not in args[4:]

    def test_inverse_is_passed_when_the_mirror_survives(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYS", source="Company", target="Person", inverse="WORKS_AT"
                ),
                RelationshipDef(type="WORKS_AT", source="Person", target="Company"),
            ],
        )
        _, kwargs = compile_joint_schema(doc, joint).relation_call("EMPLOYS")
        assert kwargs["inverse"] == "WORKS_AT"

    def test_pruning_a_relation_prunes_its_mirror_too(self, joint: FakeJoint):
        # An inverse pair must mirror endpoints exactly, so a label restriction
        # that prunes one side always prunes the other. Nothing is ever handed
        # to JointSchema.relation() naming a relation that is not there.
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYS", source="Company", target="Person", inverse="WORKS_AT"
                ),
                RelationshipDef(
                    type="WORKS_AT", source="Person", target="Company", inverse="EMPLOYS"
                ),
                RelationshipDef(type="KNOWS", source="Person", target="Person"),
            ],
        )
        schema = compile_joint_schema(doc, joint, labels=["Person"])
        assert schema.relation_names == ["KNOWS"]

        declared = set(schema.relation_names)
        for _, kwargs in schema.relations:
            assert kwargs["inverse"] is None or kwargs["inverse"] in declared

    def test_no_self_loops_is_called_per_relation_after_the_relations(self, joint: FakeJoint):
        doc = _doc(
            [PERSON],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Person"),
                RelationshipDef(
                    type="SUPERVISES", source="Person", target="Person", allow_self=True
                ),
            ],
        )
        schema = compile_joint_schema(doc, joint)
        assert schema.no_self_loops_calls == ["KNOWS"]

    def test_no_self_loops_is_skipped_when_the_document_disables_it(self, joint: FakeJoint):
        doc = _doc(
            [PERSON],
            [RelationshipDef(type="KNOWS", source="Person", target="Person")],
            no_self_loops=False,
        )
        assert compile_joint_schema(doc, joint).no_self_loops_calls == []

    def test_endpoints_are_canonicalised_to_the_declared_label_spelling(self, joint: FakeJoint):
        """The regression: validation is case-insensitive, JointSchema is not.

        ``source='person'`` against a ``Person`` label is a structurally sound
        document, so it passed ``validate_structure()`` and then blew up inside
        ``JointSchema.relation()`` with "unknown entity types".
        """
        doc = _doc(
            [PERSON, COMPANY, CITY],
            [
                RelationshipDef(type="EMPLOYED_BY", source="person", target="COMPANY"),
                RelationshipDef(type="EMPLOYED_BY", source="PERSON", target="city"),
            ],
        )
        schema = compile_joint_schema(doc, joint)
        args, _ = schema.relation_call("EMPLOYED_BY")

        assert args[1] == ["Person"]  # de-duplicated, declared spelling
        assert args[2] == ["Company", "City"]
        assert schema.entity_labels == ["Person", "Company", "City"]

    def test_an_unknown_endpoint_label_is_passed_through_verbatim(self, joint: FakeJoint):
        # Not reachable through compile_joint_schema (validate_structure
        # rejects it first), but the helper must not invent a label.
        from neo4j_agent_memory.ontology.compile import _canonical_label

        assert _canonical_label(_doc([PERSON]), "Nope") == "Nope"
        assert _canonical_label(_doc([PERSON]), "person") == "Person"


class TestSameTypeDefinitionMerge:
    """Several defs share one ``type``; the compiler folds them into one relation."""

    def test_allow_self_is_or_ed_across_the_definitions(self, joint: FakeJoint):
        """The regression: ``defs[0]`` won, so a permissive pair was overridden.

        ``allow_self=True`` on the second def was dropped *and* the relation
        still picked up a ``no_self_loops`` constraint.
        """
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Company"),
                RelationshipDef(type="KNOWS", source="Person", target="Person", allow_self=True),
            ],
        )
        schema = compile_joint_schema(doc, joint)
        _, kwargs = schema.relation_call("KNOWS")

        assert kwargs["allow_self"] is True
        assert schema.no_self_loops_calls == []

    def test_threshold_takes_the_lowest_floor_any_definition_asks_for(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Company", threshold=0.8),
                RelationshipDef(type="KNOWS", source="Person", target="Person", threshold=0.4),
            ],
        )
        _, kwargs = compile_joint_schema(doc, joint).relation_call("KNOWS")
        assert kwargs["threshold"] == 0.4

    def test_threshold_stays_none_when_no_definition_sets_one(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Company"),
                RelationshipDef(type="KNOWS", source="Person", target="Person"),
            ],
        )
        _, kwargs = compile_joint_schema(doc, joint).relation_call("KNOWS")
        assert kwargs["threshold"] is None

    def test_description_is_the_first_non_empty_one(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Company"),
                RelationshipDef(
                    type="KNOWS", source="Person", target="Person", description="Acquainted."
                ),
            ],
        )
        args, _ = compile_joint_schema(doc, joint).relation_call("KNOWS")
        assert args[3] == "Acquainted."

    def test_a_disagreement_on_an_unmergeable_flag_refuses_to_compile(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Company", acyclic=True),
                RelationshipDef(type="KNOWS", source="Person", target="Person", acyclic=False),
            ],
        )
        with pytest.raises(ValueError, match="definitions disagree on acyclic"):
            compile_joint_schema(doc, joint)

    def test_labels_restricts_the_entity_set_case_insensitively(self, joint: FakeJoint):
        doc = _doc([PERSON, COMPANY, CITY])
        schema = compile_joint_schema(doc, joint, labels=["person", "CITY"])
        assert schema.entity_labels == ["Person", "City"]

    def test_labels_prunes_relations_whose_endpoints_do_not_all_survive(self, joint: FakeJoint):
        doc = _doc(
            [PERSON, COMPANY, CITY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Person"),
                # LOCATED_AT spans Person and Company; dropping Company drops it.
                RelationshipDef(type="LOCATED_AT", source="Person", target="City"),
                RelationshipDef(type="LOCATED_AT", source="Company", target="City"),
            ],
        )
        schema = compile_joint_schema(doc, joint, labels=["Person", "City"])
        assert schema.entity_labels == ["Person", "City"]
        assert schema.relation_names == ["KNOWS"]
        assert schema.no_self_loops_calls == ["KNOWS"]

    def test_include_relations_false_compiles_an_entity_only_schema(self, joint: FakeJoint):
        schema = compile_joint_schema(POLEO_ONTOLOGY, joint, include_relations=False)
        assert schema.entity_labels == POLEO_ONTOLOGY.labels()
        assert schema.relations == []
        assert schema.no_self_loops_calls == []

    def test_a_structurally_broken_document_raises(self, joint: FakeJoint):
        doc = _doc([PERSON], [RelationshipDef(type="R", source="Ghost", target="Person")])
        with pytest.raises(ValueError, match="Cannot compile ontology"):
            compile_joint_schema(doc, joint)
        assert joint.schema.entities == []

    def test_poleo_compiles_to_five_entities_and_sixteen_relations(self, joint: FakeJoint):
        schema = compile_joint_schema(POLEO_ONTOLOGY, joint)
        assert len(schema.entities) == 5
        assert len(schema.relations) == 16
        # Every relation forbids self-loops in the default ontology.
        assert sorted(schema.no_self_loops_calls) == sorted(schema.relation_names)
        _, kwargs = schema.relation_call("EMPLOYED_BY")
        assert kwargs["unique_head"] is True
        _, kwargs = schema.relation_call("RELATED_TO")
        assert kwargs["threshold"] == 0.6

    @pytest.mark.parametrize("name", ["poleo", "podcast", "news", "scientific", "medical"])
    def test_every_template_compiles(self, name: str, joint: FakeJoint):
        doc = get_template(name)
        schema = compile_joint_schema(doc, joint)
        assert schema.entity_labels == doc.labels()
        assert schema.relation_names == doc.relationship_types()

    def test_a_joint_without_create_schema_falls_back_to_the_module(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        made: list[FakeSchema] = []

        def _factory() -> FakeSchema:
            schema = FakeSchema()
            made.append(schema)
            return schema

        module = types.ModuleType("gliner2.joint_ie")
        module.JointSchema = _factory  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gliner2", types.ModuleType("gliner2"))
        monkeypatch.setitem(sys.modules, "gliner2.joint_ie", module)

        schema = compile_joint_schema(_doc([PERSON]), object())
        assert made == [schema]
        assert schema.entity_labels == ["Person"]


# -----------------------------------------------------------------------------
# compile_attribute_schema
# -----------------------------------------------------------------------------


class _FakeAttributeGroup:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeAttributeSchema:
    """Mirrors the real ``Schema``: attributes need ``entities()`` first."""

    def __init__(self) -> None:
        self.groups: dict[str, Any] | None = None
        self.declared: dict[str, str] | None = None
        self.calls: list[str] = []

    def entities(self, entities: dict[str, str]) -> _FakeAttributeSchema:
        self.calls.append("entities")
        self.declared = entities
        return self

    def entity_attributes(self, groups: dict[str, Any]) -> _FakeAttributeSchema:
        self.calls.append("entity_attributes")
        if not self.declared:
            raise ValueError("entity_attributes() requires entities() to be called first")
        self.groups = groups
        return self


class _FakeModel:
    def __init__(self) -> None:
        self.schema = _FakeAttributeSchema()

    def create_schema(self) -> _FakeAttributeSchema:
        return self.schema


@pytest.fixture
def gliner2_stub(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("gliner2")
    module.AttributeGroup = _FakeAttributeGroup  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner2", module)
    return module


class TestCompileAttributeSchema:
    def test_none_when_no_property_declares_an_enum(self):
        doc = _doc(
            [PERSON.model_copy(update={"properties": [PropertyDef(name="name", type="string")]})]
        )
        # No gliner2 stub needed: the import is skipped entirely.
        assert compile_attribute_schema(doc, _FakeModel()) is None

    def test_none_for_an_empty_document(self):
        assert compile_attribute_schema(_doc([]), _FakeModel()) is None

    def test_one_group_per_enum_property(self, gliner2_stub: types.ModuleType):
        doc = _doc(
            [
                COMPANY.model_copy(
                    update={
                        "threshold": 0.7,
                        "properties": [
                            PropertyDef(name="tier", type="string", enum=["gold", "silver"]),
                            PropertyDef(name="name", type="string"),
                        ],
                    }
                )
            ]
        )
        model = _FakeModel()
        schema = compile_attribute_schema(doc, model)

        assert schema is model.schema
        assert schema.groups is not None
        assert list(schema.groups) == ["tier"]
        assert schema.groups["tier"].kwargs == {
            "labels": ["gold", "silver"],
            "applies_to": ["Company"],
            "qualify_labels": True,
            "threshold": 0.7,
        }

    def test_threshold_defaults_to_half(self, gliner2_stub: types.ModuleType):
        doc = _doc(
            [
                COMPANY.model_copy(
                    update={"properties": [PropertyDef(name="tier", type="string", enum=["a"])]}
                )
            ]
        )
        schema = compile_attribute_schema(doc, _FakeModel())
        assert schema.groups["tier"].kwargs["threshold"] == 0.5

    def test_entities_are_declared_before_attributes(self, gliner2_stub: types.ModuleType):
        # Real gliner2 raises "entity_attributes() requires entities() to be
        # called first", so the order is part of the contract, not a detail.
        doc = _doc(
            [
                COMPANY.model_copy(
                    update={
                        "description": "A company.",
                        "properties": [
                            PropertyDef(name="tier", type="string", enum=["gold"]),
                        ],
                    }
                )
            ]
        )
        schema = compile_attribute_schema(doc, _FakeModel())

        assert schema.calls == ["entities", "entity_attributes"]
        assert schema.declared == {"Company": "A company."}

    def test_entity_description_falls_back_to_the_label(self, gliner2_stub: types.ModuleType):
        doc = _doc(
            [
                COMPANY.model_copy(
                    update={
                        "description": None,
                        "properties": [PropertyDef(name="tier", type="string", enum=["gold"])],
                    }
                )
            ]
        )
        schema = compile_attribute_schema(doc, _FakeModel())
        assert schema.declared == {"Company": "Company"}

    def test_colliding_property_names_are_qualified_by_label(self, gliner2_stub: types.ModuleType):
        enum_prop = PropertyDef(name="status", type="string", enum=["open"])
        doc = _doc(
            [
                PERSON.model_copy(update={"properties": [enum_prop]}),
                COMPANY.model_copy(update={"properties": [enum_prop]}),
            ]
        )
        schema = compile_attribute_schema(doc, _FakeModel())
        assert list(schema.groups) == ["status", "Company.status"]


# -----------------------------------------------------------------------------
# label_map / prompt_fragment / spacy_label_map
# -----------------------------------------------------------------------------


class TestLabelMap:
    def test_the_document_wins_over_the_default_table(self):
        from neo4j_agent_memory.extraction.label_mapping import DEFAULT_LABEL_MAPPING

        assert DEFAULT_LABEL_MAPPING["company"] == ("ORGANIZATION", "COMPANY")
        doc = _doc([EntityTypeDef(label="company", pole_type="PERSON", subtype="FOUNDER")])
        merged = label_map(doc)
        assert merged["company"] == ("PERSON", "FOUNDER")

    def test_the_default_table_stays_underneath(self):
        merged = label_map(_doc([PERSON]))
        assert merged["person"] == ("PERSON", None)
        assert merged["phone number"] == ("OBJECT", "PHONE")

    def test_the_shared_table_is_not_mutated(self):
        from neo4j_agent_memory.extraction.label_mapping import DEFAULT_LABEL_MAPPING

        before = dict(DEFAULT_LABEL_MAPPING)
        label_map(_doc([EntityTypeDef(label="company", pole_type="PERSON")]))
        assert before == DEFAULT_LABEL_MAPPING


class TestPromptFragment:
    def test_entity_types_render_with_pole_type_and_guideline(self):
        fragment = prompt_fragment(_doc([PERSON, COMPANY]))
        assert "## Entity types" in fragment
        assert "- Person (PERSON): A human." in fragment
        assert "- Company (ORGANIZATION:COMPANY): No description." in fragment

    def test_relationships_render_with_endpoints_and_constraints(self):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYED_BY",
                    source="Person",
                    target="Company",
                    description="Paid work.",
                    unique_source=True,
                    threshold=0.35,
                )
            ],
        )
        fragment = prompt_fragment(doc)
        assert "## Relationship types" in fragment
        assert "- EMPLOYED_BY: Person -> Company — Paid work." in fragment
        assert "at most one per source" in fragment
        assert "no self-loops" in fragment
        assert "confidence floor 0.35" in fragment

    def test_include_relations_false_omits_the_relationship_section(self):
        fragment = prompt_fragment(POLEO_ONTOLOGY, include_relations=False)
        assert "## Relationship types" not in fragment

    def test_empty_document(self):
        fragment = prompt_fragment(_doc([]))
        assert "_None declared._" in fragment

    def test_poleo_fragment_names_every_label_and_relation(self):
        fragment = prompt_fragment(POLEO_ONTOLOGY)
        for label in POLEO_ONTOLOGY.labels():
            assert label in fragment
        for rel_type in POLEO_ONTOLOGY.relationship_types():
            assert rel_type in fragment


class TestSpacyLabelMap:
    def test_none_for_an_empty_document(self):
        assert spacy_label_map(_doc([])) is None

    def test_spacy_labels_resolve_to_the_first_label_of_that_pole_type(self):
        doc = _doc(
            [
                EntityTypeDef(label="Engineer", pole_type="PERSON", subtype="ENGINEER"),
                EntityTypeDef(label="Customer", pole_type="PERSON", subtype="CUSTOMER"),
                COMPANY,
            ]
        )
        mapping = spacy_label_map(doc)
        assert mapping is not None
        assert mapping["PERSON"] == ("PERSON", "ENGINEER")
        assert mapping["ORG"] == ("ORGANIZATION", "COMPANY")
        assert mapping["NORP"] == ("ORGANIZATION", "COMPANY")

    def test_pole_types_the_document_does_not_declare_are_dropped(self):
        mapping = spacy_label_map(_doc([PERSON]))
        assert mapping == {"PERSON": ("PERSON", None)}
        assert "GPE" not in mapping

    def test_poleo_covers_every_spacy_label(self):
        from neo4j_agent_memory.ontology.compile import _SPACY_POLE_TYPES

        mapping = spacy_label_map(POLEO_ONTOLOGY)
        assert mapping is not None
        assert set(mapping) == set(_SPACY_POLE_TYPES)
        assert mapping["GPE"] == ("LOCATION", None)
        assert mapping["DATE"] == ("EVENT", None)
        assert mapping["MONEY"] == ("OBJECT", None)
