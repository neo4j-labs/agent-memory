"""Unit tests for ontology/models.py — document shape, lookups, validation."""

from __future__ import annotations

import json

import pytest

from neo4j_agent_memory.ontology.models import (
    POLEO_TYPES,
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    PropertyDef,
    RelationshipDef,
    _as_document_dict,
    _parse_document,
    _parse_version,
)


def _doc(
    entity_types: list[EntityTypeDef] | None = None,
    relationships: list[RelationshipDef] | None = None,
    **kwargs: object,
) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id="test", name="test"),
        entity_types=entity_types or [],
        relationships=relationships or [],
        **kwargs,
    )


PERSON = EntityTypeDef(label="Person", pole_type="PERSON", description="A human.")
COMPANY = EntityTypeDef(label="Company", pole_type="ORGANIZATION", subtype="COMPANY")


class TestLenientParsing:
    """Every payload that parsed before the 0.7 extensions still parses."""

    def test_legacy_nams_payload_without_extension_fields(self):
        payload = {
            "domain": {"id": "healthcare", "name": "Healthcare", "emoji": "🏥"},
            "entity_types": [
                {
                    "label": "Patient",
                    "pole_type": "PERSON",
                    "subtype": "INDIVIDUAL",
                    "color": "#fff",
                    "icon": "user",
                    "properties": [
                        {"name": "mrn", "type": "string", "required": True, "unique": True}
                    ],
                }
            ],
            "relationships": [
                {"type": "DIAGNOSED_WITH", "source": "Patient", "target": "Condition"}
            ],
        }
        doc = OntologyDocument.model_validate(payload)

        assert doc.labels() == ["Patient"]
        assert doc.entity_types[0].description is None
        assert doc.entity_types[0].aliases == {}
        assert doc.entity_types[0].threshold is None
        assert doc.entity_types[0].properties[0].description is None
        rel = doc.relationships[0]
        assert (rel.threshold, rel.inverse, rel.acyclic) == (None, None, False)
        assert (rel.unique_source, rel.unique_target, rel.allow_self) == (False, False, False)
        assert doc.no_self_loops is True

    def test_null_collections_coerce_to_empty(self):
        doc = OntologyDocument.model_validate(
            {
                "domain": {"id": "x", "name": "x"},
                "entity_types": None,
                "relationships": None,
            }
        )
        assert doc.entity_types == []
        assert doc.relationships == []

        et = EntityTypeDef.model_validate(
            {"label": "L", "pole_type": "OBJECT", "properties": None, "aliases": None}
        )
        assert et.properties == []
        assert et.aliases == {}

    def test_unknown_fields_are_ignored(self):
        doc = OntologyDocument.model_validate(
            {
                "domain": {"id": "x", "name": "x", "future_field": 1},
                "entity_types": [{"label": "L", "pole_type": "OBJECT", "server_only": True}],
                "future_top_level": "ignored",
            }
        )
        assert doc.labels() == ["L"]

    def test_structurally_broken_documents_still_parse(self):
        doc = _doc(
            entity_types=[PERSON, PERSON],
            relationships=[RelationshipDef(type="R", source="Nope", target="Person")],
        )
        assert doc.validate_structure()  # reported, not raised

    def test_parse_document_accepts_a_json_string(self):
        raw = json.dumps({"domain": {"id": "x", "name": "x"}, "entity_types": []})
        assert _parse_document(raw) is not None
        assert _parse_document(None) is None
        assert _parse_document("not json") is None
        assert _parse_document(42) is None

    @pytest.mark.parametrize(
        "raw",
        [
            {},  # no domain
            {"entity_types": [{"label": "Person", "pole_type": "PERSON"}]},  # no domain
            {"domain": {"id": "x", "name": "x"}, "entity_types": 7},  # not a list
            {"domain": "x"},  # domain is not a mapping
            {"domain": {"id": "x"}},  # domain.name missing
        ],
    )
    def test_a_document_pydantic_rejects_returns_none_not_a_validation_error(self, raw: object):
        """A corrupt stored document must not escape as a raw pydantic error.

        ``use_active_ontology`` defaults to True, so a ``ValidationError`` out
        of ``get_active()`` failed ``connect()`` outright — including the
        ``client.ontology.delete()`` call needed to recover. Callers turn the
        ``None`` into their own error instead.
        """
        assert _parse_document(raw) is None
        assert _parse_document(json.dumps(raw)) is None

    def test_parse_version_unwraps_schema_json(self):
        version = _parse_version(
            {
                "id": "v1",
                "ontology_id": "o1",
                "revision": 1,
                "validation_mode": "strict",
                "schema_json": json.dumps(
                    {
                        "domain": {"id": "x", "name": "x"},
                        "entity_types": [{"label": "Person", "pole_type": "PERSON"}],
                    }
                ),
            }
        )
        assert version.document is not None
        assert version.document.labels() == ["Person"]


class TestLookups:
    def test_labels_and_label_map(self):
        doc = _doc([PERSON, COMPANY])
        assert doc.labels() == ["Person", "Company"]
        assert doc.label_map() == {
            "person": ("PERSON", None),
            "company": ("ORGANIZATION", "COMPANY"),
        }

    def test_label_map_upper_cases_pole_type_and_subtype(self):
        doc = _doc([EntityTypeDef(label="veh", pole_type="object", subtype="vehicle")])
        assert doc.label_map() == {"veh": ("OBJECT", "VEHICLE")}

    def test_entity_type_lookup_is_case_insensitive(self):
        doc = _doc([PERSON])
        assert doc.entity_type("person") is PERSON
        assert doc.entity_type("PERSON") is PERSON
        assert doc.entity_type("Missing") is None

    def test_relationship_types_are_distinct_and_ordered(self):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="WORKS_AT", source="Person", target="Company"),
                RelationshipDef(type="WORKS_AT", source="Person", target="Person"),
                RelationshipDef(type="KNOWS", source="Person", target="Person"),
            ],
        )
        assert doc.relationship_types() == ["WORKS_AT", "KNOWS"]

    def test_patterns_and_permits(self):
        doc = _doc(
            [PERSON, COMPANY],
            [RelationshipDef(type="WORKS_AT", source="Person", target="Company")],
        )
        assert doc.patterns() == {("Person", "WORKS_AT", "Company")}
        assert doc.permits("Person", "WORKS_AT", "Company")
        # case-insensitive on all three components
        assert doc.permits("person", "works_at", "COMPANY")
        assert not doc.permits("Company", "WORKS_AT", "Person")
        assert not doc.permits("Person", "OWNS", "Company")

    def test_empty_document_lookups(self):
        doc = _doc()
        assert doc.labels() == []
        assert doc.label_map() == {}
        assert doc.relationship_types() == []
        assert doc.patterns() == set()
        assert not doc.permits("a", "B", "c")


class TestValidateStructure:
    def test_sound_document_reports_nothing(self):
        doc = _doc(
            [PERSON, COMPANY],
            [RelationshipDef(type="WORKS_AT", source="Person", target="Company")],
        )
        assert doc.validate_structure() == []

    def test_duplicate_labels(self):
        doc = _doc([PERSON, EntityTypeDef(label="person", pole_type="PERSON")])
        problems = doc.validate_structure()
        assert any("duplicate entity label" in p for p in problems)

    def test_duplicate_relationship_triple(self):
        rel = RelationshipDef(type="WORKS_AT", source="Person", target="Company")
        doc = _doc([PERSON, COMPANY], [rel, rel.model_copy()])
        problems = doc.validate_structure()
        assert any("duplicate relationship" in p for p in problems)

    def test_unknown_source_and_target(self):
        doc = _doc([PERSON], [RelationshipDef(type="R", source="Ghost", target="Phantom")])
        problems = doc.validate_structure()
        assert any("source 'Ghost' is not a declared label" in p for p in problems)
        assert any("target 'Phantom' is not a declared label" in p for p in problems)

    def test_pole_type_must_be_poleo(self):
        doc = _doc([EntityTypeDef(label="Thing", pole_type="CONCEPT")])
        problems = doc.validate_structure()
        assert any("pole_type 'CONCEPT'" in p for p in problems)

    def test_pole_type_casing_is_accepted(self):
        doc = _doc([EntityTypeDef(label="Thing", pole_type="object")])
        assert doc.validate_structure() == []

    def test_unknown_inverse(self):
        doc = _doc(
            [PERSON],
            [RelationshipDef(type="A", source="Person", target="Person", inverse="NOPE")],
        )
        problems = doc.validate_structure()
        assert any("unknown inverse 'NOPE'" in p for p in problems)

    def test_inverse_must_mirror_endpoints(self):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYS", source="Company", target="Person", inverse="WORKS_AT"
                ),
                # Wrong direction: should be Person -> Company.
                RelationshipDef(type="WORKS_AT", source="Company", target="Person"),
            ],
        )
        problems = doc.validate_structure()
        assert any("do not mirror" in p for p in problems)

    def test_correctly_mirrored_inverse_is_accepted(self):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(
                    type="EMPLOYS", source="Company", target="Person", inverse="WORKS_AT"
                ),
                RelationshipDef(type="WORKS_AT", source="Person", target="Company"),
            ],
        )
        assert doc.validate_structure() == []

    def test_allow_self_requires_identical_endpoints(self):
        doc = _doc(
            [PERSON, COMPANY],
            [RelationshipDef(type="R", source="Person", target="Company", allow_self=True)],
        )
        problems = doc.validate_structure()
        assert any("allow_self but its endpoints differ" in p for p in problems)

    def test_allow_self_on_a_same_type_relationship_is_fine(self):
        doc = _doc(
            [PERSON],
            [RelationshipDef(type="R", source="Person", target="Person", allow_self=True)],
        )
        assert doc.validate_structure() == []

    def test_poleo_types_constant(self):
        assert {"PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"} == POLEO_TYPES

    @pytest.mark.parametrize("field", ["inverse", "unique_source", "unique_target", "acyclic"])
    def test_same_type_definitions_must_agree_on_the_unmergeable_flags(self, field: str):
        """``compile_joint_schema`` folds same-``type`` defs into one relation.

        Only ``allow_self``/``threshold``/``description`` have a defensible
        merge; the rest change the relation's meaning, and taking ``defs[0]``
        silently discarded the others.
        """
        values = {"inverse": ("KNOWN_BY", None), "default": (True, False)}
        first, second = values.get(field, values["default"])
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Person", **{field: first}),
                RelationshipDef(type="KNOWS", source="Person", target="Company", **{field: second}),
                # ...plus the inverse the first def names, so the only problem
                # reported is the disagreement itself.
                RelationshipDef(type="KNOWN_BY", source="Person", target="Person"),
                RelationshipDef(type="KNOWN_BY", source="Company", target="Person"),
            ],
        )
        problems = doc.validate_structure()
        assert any(
            f"relationship 'KNOWS' definitions disagree on {field}" in p for p in problems
        ), problems

    def test_agreeing_definitions_report_nothing(self):
        doc = _doc(
            [PERSON, COMPANY],
            [
                RelationshipDef(type="KNOWS", source="Person", target="Person", acyclic=True),
                RelationshipDef(type="KNOWS", source="Person", target="Company", acyclic=True),
            ],
        )
        assert doc.validate_structure() == []

    @pytest.mark.parametrize("field", ["threshold", "resolution_threshold", "review_threshold"])
    @pytest.mark.parametrize("value", [-0.1, 1.5])
    def test_entity_thresholds_outside_the_unit_interval_are_flagged(
        self, field: str, value: float
    ):
        doc = _doc([PERSON.model_copy(update={field: value})])
        problems = doc.validate_structure()
        assert any(f"has {field} {value!r}" in p for p in problems), problems

    @pytest.mark.parametrize("value", [-0.1, 1.5])
    def test_relationship_thresholds_outside_the_unit_interval_are_flagged(self, value: float):
        doc = _doc(
            [PERSON],
            [RelationshipDef(type="KNOWS", source="Person", target="Person", threshold=value)],
        )
        problems = doc.validate_structure()
        assert any("expected a confidence in [0, 1]" in p for p in problems), problems

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_thresholds_at_the_boundaries_are_fine(self, value: float):
        doc = _doc(
            [PERSON.model_copy(update={"threshold": value})],
            [RelationshipDef(type="KNOWS", source="Person", target="Person", threshold=value)],
        )
        assert doc.validate_structure() == []

    @pytest.mark.parametrize("bad", ["works at", "worksAt", "works-at", "_KNOWS", "1KNOWS", ""])
    def test_a_relationship_type_that_is_not_upper_snake_is_flagged(self, bad: str):
        doc = _doc([PERSON], [RelationshipDef(type=bad, source="Person", target="Person")])
        problems = doc.validate_structure()
        assert any("is not UPPER_SNAKE" in p for p in problems), problems

    @pytest.mark.parametrize("good", ["KNOWS", "WORKS_AT", "OWNS_V2", "A"])
    def test_upper_snake_relationship_types_are_accepted(self, good: str):
        doc = _doc([PERSON], [RelationshipDef(type=good, source="Person", target="Person")])
        assert doc.validate_structure() == []

    def test_the_suggested_spelling_is_included(self):
        doc = _doc([PERSON], [RelationshipDef(type="works at", source="Person", target="Person")])
        assert any("'WORKS_AT'" in p for p in doc.validate_structure())


class TestContentKey:
    def test_identical_documents_share_a_key(self):
        a = _doc([PERSON], [RelationshipDef(type="R", source="Person", target="Person")])
        b = _doc(
            [PERSON.model_copy()], [RelationshipDef(type="R", source="Person", target="Person")]
        )
        assert a.content_key() == b.content_key()

    def test_the_key_tracks_content_not_name(self):
        a = _doc([PERSON])
        b = OntologyDocument(domain=DomainInfo(id="other", name="other"), entity_types=[PERSON])
        assert a.content_key() != b.content_key()

    def test_any_change_changes_the_key(self):
        a = _doc([PERSON])
        b = _doc([PERSON.model_copy(update={"threshold": 0.4})])
        assert a.content_key() != b.content_key()

    def test_the_key_is_deterministic_json(self):
        key = _doc([PERSON]).content_key()
        assert json.loads(key)["domain"]["id"] == "test"
        assert key == _doc([PERSON]).content_key()

    def test_declaration_order_is_not_content(self):
        """The docstring promised order-independence; now it holds.

        ``schema_hash`` is a SHA-256 of this key, so a re-ordered document
        used to look like a new revision and to miss the compiled-schema
        cache.
        """
        rels = [
            RelationshipDef(type="KNOWS", source="Person", target="Person"),
            RelationshipDef(type="EMPLOYED_BY", source="Person", target="Company"),
        ]
        forward = _doc([PERSON, COMPANY], rels)
        reversed_ = _doc([COMPANY, PERSON], list(reversed(rels)))

        assert forward.content_key() == reversed_.content_key()
        # Still content-addressed: a real difference still changes the key.
        assert forward.content_key() != _doc([PERSON], rels[:1]).content_key()


class TestExtensionFields:
    def test_entity_type_extensions_round_trip(self):
        et = EntityTypeDef(
            label="Org",
            pole_type="ORGANIZATION",
            description="A company.",
            aliases={"Acme Corp": ["Acme", "ACME Corporation"]},
            threshold=0.4,
            resolution_threshold=0.93,
            review_threshold=0.8,
        )
        restored = EntityTypeDef.model_validate(et.model_dump())
        assert restored == et

    def test_relationship_extensions_round_trip(self):
        rel = RelationshipDef(
            type="EMPLOYED_BY",
            source="Person",
            target="Org",
            description="Paid work.",
            threshold=0.35,
            inverse="EMPLOYS",
            unique_source=True,
            unique_target=False,
            acyclic=True,
            allow_self=False,
        )
        assert RelationshipDef.model_validate(rel.model_dump()) == rel

    def test_property_description(self):
        prop = PropertyDef(name="tier", type="string", enum=["gold"], description="Plan.")
        assert prop.enum == ["gold"]
        assert prop.description == "Plan."

    def test_no_self_loops_defaults_on_and_can_be_disabled(self):
        assert _doc().no_self_loops is True
        assert _doc(no_self_loops=False).no_self_loops is False


class TestAliasCoercion:
    """``aliases`` is a gazetteer, so an unreadable payload is dropped."""

    @pytest.mark.parametrize("raw", [None, ["Acme", "ACME"], "Acme", 7])
    def test_a_non_mapping_payload_becomes_empty(self, raw: object):
        et = EntityTypeDef.model_validate(
            {"label": "Org", "pole_type": "ORGANIZATION", "aliases": raw}
        )
        assert et.aliases == {}

    def test_scalar_and_null_values_are_coerced_to_lists(self):
        et = EntityTypeDef.model_validate(
            {
                "label": "Org",
                "pole_type": "ORGANIZATION",
                "aliases": {"Acme Corp": "Acme", "Globex": None, "Initech": ["IT", 7]},
            }
        )
        assert et.aliases == {
            "Acme Corp": ["Acme"],
            "Globex": [],
            "Initech": ["IT", "7"],
        }


class TestOutboundDocumentDict:
    """``_as_document_dict`` is what NAMS ``create``/``update`` put on the wire.

    v0.7 added fields at every level of the document and NAMS support for them
    is unverified, so a field still at its default is omitted — a v0.6-shaped
    document has to round-trip byte-compatible.
    """

    #: The keys a pre-0.7 payload could carry, per shape.
    DOCUMENT_KEYS = {"domain", "entity_types", "relationships"}
    ENTITY_KEYS = {"label", "pole_type", "subtype", "color", "icon", "properties"}
    PROPERTY_KEYS = {"name", "type", "required", "unique", "enum"}
    RELATIONSHIP_KEYS = {"type", "source", "target"}

    def test_a_v06_shaped_document_dumps_to_exactly_the_pre_07_key_set(self):
        doc = _doc(
            [
                EntityTypeDef(
                    label="Patient",
                    pole_type="PERSON",
                    subtype="INDIVIDUAL",
                    color="#fff",
                    icon="user",
                    properties=[PropertyDef(name="mrn", type="string", required=True)],
                )
            ],
            [RelationshipDef(type="TREATED_BY", source="Patient", target="Patient")],
        )

        dumped = _as_document_dict(doc)

        assert set(dumped) == self.DOCUMENT_KEYS
        assert set(dumped["entity_types"][0]) == self.ENTITY_KEYS
        assert set(dumped["entity_types"][0]["properties"][0]) <= self.PROPERTY_KEYS
        assert set(dumped["relationships"][0]) == self.RELATIONSHIP_KEYS

    def test_poleo_dumps_only_pre_07_keys_plus_the_ones_it_sets(self):
        from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

        dumped = _as_document_dict(POLEO_ONTOLOGY)

        assert set(dumped) == self.DOCUMENT_KEYS  # no_self_loops is at its default
        for entity_type in dumped["entity_types"]:
            assert set(entity_type) - {"description"} <= self.ENTITY_KEYS
            assert "aliases" not in entity_type
        for rel in dumped["relationships"]:
            extras = {"description", "threshold", "unique_source", "acyclic"}
            assert set(rel) - extras <= self.RELATIONSHIP_KEYS
            assert "allow_self" not in rel
            assert "unique_target" not in rel

    def test_a_set_description_survives(self):
        dumped = _as_document_dict(
            _doc([PERSON], [RelationshipDef(type="KNOWS", source="Person", target="Person")])
        )
        assert dumped["entity_types"][0]["description"] == "A human."

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("unique_source", True),
            ("unique_target", True),
            ("acyclic", True),
            ("allow_self", True),
        ],
    )
    def test_a_deliberately_set_flag_is_always_sent(self, field: str, value: bool):
        doc = _doc(
            [PERSON],
            [RelationshipDef(type="KNOWS", source="Person", target="Person", **{field: value})],
        )
        assert _as_document_dict(doc)["relationships"][0][field] is value

    def test_a_non_default_no_self_loops_is_sent(self):
        assert _as_document_dict(_doc(no_self_loops=False))["no_self_loops"] is False

    def test_a_non_empty_aliases_map_is_sent(self):
        doc = _doc([PERSON.model_copy(update={"aliases": {"Ada": ["Ada L."]}})])
        assert _as_document_dict(doc)["entity_types"][0]["aliases"] == {"Ada": ["Ada L."]}

    def test_a_raw_mapping_is_passed_through_untouched(self):
        raw = {"domain": {"id": "x", "name": "x"}, "aliases": {}}
        assert _as_document_dict(raw) is raw


class TestLabelForFallback:
    """A pole type declared only with subtypes must still resolve."""

    def test_a_bare_pole_type_falls_back_to_the_first_declared_label(self):
        from neo4j_agent_memory.ontology.builtin import get_template

        scientific = get_template("scientific")
        # The template's only PERSON is ``author -> PERSON:AUTHOR``.
        assert [et.label for et in scientific.entity_types if et.pole_type == "PERSON"] == [
            "author"
        ]

        assert scientific.label_for("PERSON") == "author"
        assert scientific.declares("PERSON") is True
        assert scientific.label_for("ORGANIZATION") == "institution"
        # Nothing is invented for a type the ontology says nothing about.
        assert scientific.label_for("EVENT") is None
        assert scientific.declares("EVENT") is False

    def test_an_exact_subtype_match_still_wins(self):
        from neo4j_agent_memory.ontology.builtin import get_template

        scientific = get_template("scientific")
        assert scientific.label_for("PERSON", "AUTHOR") == "author"
        assert scientific.label_for("OBJECT", "DATASET") == "dataset"

    def test_a_stated_unknown_subtype_gets_no_sibling_fallback(self):
        """Only a *bare* pole type falls back that far.

        Mapping PERSON:ALIAS onto a ``Customer = PERSON:INDIVIDUAL`` label
        would assert something the caller did not, so strict mode keeps
        rejecting it.
        """
        doc = _doc([EntityTypeDef(label="Customer", pole_type="PERSON", subtype="INDIVIDUAL")])
        assert doc.label_for("PERSON", "ALIAS") is None
        assert doc.declares("PERSON", "ALIAS") is False
        assert doc.label_for("PERSON") == "Customer"

    def test_a_base_declaration_still_absorbs_an_unknown_subtype(self):
        doc = _doc([PERSON, EntityTypeDef(label="Alias", pole_type="PERSON", subtype="ALIAS")])
        assert doc.label_for("PERSON", "PERSONA") == "Person"


@pytest.mark.parametrize("pole_type", sorted(POLEO_TYPES))
def test_every_poleo_type_validates(pole_type: str):
    doc = _doc([EntityTypeDef(label=pole_type.title(), pole_type=pole_type)])
    assert doc.validate_structure() == []


class TestOntologyAPIProtocol:
    """``OntologyAPI`` describes the accessor both backends implement.

    Lives here because the Protocol is written entirely in terms of the models
    above; PR3's bolt store is checked against it too.
    """

    def test_nams_ontology_conforms(self):
        from neo4j_agent_memory.nams.ontology import NamsOntology
        from neo4j_agent_memory.ontology.protocol import OntologyAPI

        assert isinstance(NamsOntology(None), OntologyAPI)

    def test_the_protocol_names_every_nams_method(self):
        from neo4j_agent_memory.nams.ontology import NamsOntology
        from neo4j_agent_memory.ontology.protocol import OntologyAPI

        public = {
            name
            for name in vars(NamsOntology)
            if not name.startswith("_") and callable(getattr(NamsOntology, name))
        }
        declared = {name for name in vars(OntologyAPI) if not name.startswith("_")}
        assert public - declared == set()

    def test_signatures_match_the_nams_accessor(self):
        import inspect

        from neo4j_agent_memory.nams.ontology import NamsOntology
        from neo4j_agent_memory.ontology.protocol import OntologyAPI

        for name in (
            "list",
            "get",
            "get_active",
            "clone",
            "create",
            "update",
            "activate",
            "delete",
            "import_",
            "diff",
            "migrate",
            "get_migration",
        ):
            protocol_params = inspect.signature(getattr(OntologyAPI, name)).parameters
            nams_params = inspect.signature(getattr(NamsOntology, name)).parameters
            assert list(protocol_params) == list(nams_params), name
            assert inspect.iscoroutinefunction(getattr(NamsOntology, name)), name
