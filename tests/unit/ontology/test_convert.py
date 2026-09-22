"""Unit tests for ontology/convert.py — every way a schema enters the library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY, get_template
from neo4j_agent_memory.ontology.convert import (
    from_arrows,
    from_domain_schema,
    from_entity_schema,
    is_entity_schema_shape,
    load_ontology,
    to_entity_schema,
)
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)
from neo4j_agent_memory.schema.models import (
    EntitySchemaConfig,
    EntityTypeConfig,
    RelationTypeConfig,
    get_default_schema,
)

ARROWS_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "ontology-lifecycle"
    / "schemas"
    / "support-desk.arrows.json"
)


# -----------------------------------------------------------------------------
# EntitySchemaConfig
# -----------------------------------------------------------------------------


class TestFromEntitySchema:
    def test_a_flat_schema_becomes_one_label_per_type(self):
        schema = EntitySchemaConfig(
            name="flat",
            entity_types=[
                EntityTypeConfig(name="PERSON", description="A human."),
                EntityTypeConfig(name="ORGANIZATION"),
            ],
            relation_types=[
                RelationTypeConfig(
                    name="EMPLOYED_BY",
                    description="Paid work.",
                    source_types=["PERSON"],
                    target_types=["ORGANIZATION"],
                )
            ],
        )
        doc = from_entity_schema(schema)

        assert doc.labels() == ["PERSON", "ORGANIZATION"]
        assert doc.entity_type("PERSON").description == "A human."
        assert doc.patterns() == {("PERSON", "EMPLOYED_BY", "ORGANIZATION")}
        assert doc.validate_structure() == []

    def test_subtypes_become_labels_alongside_the_base_type(self):
        schema = EntitySchemaConfig(
            name="sub",
            entity_types=[
                EntityTypeConfig(name="OBJECT", subtypes=["VEHICLE", "PHONE"]),
            ],
            relation_types=[],
        )
        doc = from_entity_schema(schema)

        assert doc.labels() == ["OBJECT", "VEHICLE", "PHONE"]
        assert doc.label_map() == {
            "object": ("OBJECT", None),
            "vehicle": ("OBJECT", "VEHICLE"),
            "phone": ("OBJECT", "PHONE"),
        }

    def test_subtypes_are_skipped_when_disabled(self):
        schema = EntitySchemaConfig(
            name="nosub",
            entity_types=[EntityTypeConfig(name="OBJECT", subtypes=["VEHICLE"])],
            relation_types=[],
            enable_subtypes=False,
        )
        assert from_entity_schema(schema).labels() == ["OBJECT"]

    def test_empty_endpoint_lists_mean_every_type(self):
        schema = EntitySchemaConfig(
            name="wild",
            entity_types=[
                EntityTypeConfig(name="PERSON"),
                EntityTypeConfig(name="OBJECT"),
            ],
            relation_types=[RelationTypeConfig(name="RELATED_TO")],
        )
        doc = from_entity_schema(schema)
        assert doc.patterns() == {
            ("PERSON", "RELATED_TO", "PERSON"),
            ("PERSON", "RELATED_TO", "OBJECT"),
            ("OBJECT", "RELATED_TO", "PERSON"),
            ("OBJECT", "RELATED_TO", "OBJECT"),
        }

    def test_attributes_become_properties(self):
        schema = EntitySchemaConfig(
            name="attrs",
            entity_types=[EntityTypeConfig(name="PERSON", attributes=["name", "dob"])],
            relation_types=[],
        )
        doc = from_entity_schema(schema)
        assert [p.name for p in doc.entity_types[0].properties] == ["name", "dob"]

    def test_the_default_poleo_schema_converts_soundly(self):
        doc = from_entity_schema(get_default_schema())
        assert doc.validate_structure() == []
        assert set(doc.relationship_types()) == set(POLEO_ONTOLOGY.relationship_types())
        # Relationship endpoints refer to base type labels, not subtypes.
        assert len(doc.relationships) == len(POLEO_ONTOLOGY.relationships)
        # Nothing is pruned when every endpoint's type is declared: five base
        # labels + their subtypes, sixteen relation types, 69 patterns.
        assert len(doc.labels()) == 43
        assert len(doc.relationship_types()) == 16
        assert len(doc.relationships) == 69

    def test_a_subset_schema_drops_relations_with_undeclared_endpoints(self):
        """The regression: a partial schema must still convert into a sound document.

        ``EntitySchemaConfig`` attaches the full sixteen-relation POLE+O
        catalogue by default. Declaring only two of the five types used to emit
        all 69 patterns regardless, so ``validate_structure()`` reported 77
        problems and the document could not be compiled or stored.
        """
        schema = EntitySchemaConfig(
            name="people-and-orgs",
            entity_types=[
                EntityTypeConfig(name="PERSON"),
                EntityTypeConfig(name="ORGANIZATION"),
            ],
        )
        doc = from_entity_schema(schema)

        assert doc.validate_structure() == []
        assert doc.labels() == ["PERSON", "ORGANIZATION"]
        # Only relations whose endpoints are both declared survive; OWNS
        # (-> OBJECT), LOCATED_AT (-> LOCATION) and the EVENT relations go.
        assert set(doc.relationship_types()) == {
            "KNOWS",
            "ALIAS_OF",
            "MEMBER_OF",
            "EMPLOYED_BY",
            "SUBSIDIARY_OF",
            "PARTNER_WITH",
            "RELATED_TO",
            "MENTIONS",
        }
        assert all(
            source in {"PERSON", "ORGANIZATION"} and target in {"PERSON", "ORGANIZATION"}
            for source, _, target in doc.patterns()
        )

    def test_a_subtype_label_is_a_valid_relation_endpoint(self):
        """Pruning keys off declared *labels*, so subtypes stay addressable."""
        schema = EntitySchemaConfig(
            name="subtype-endpoints",
            entity_types=[
                EntityTypeConfig(name="PERSON"),
                EntityTypeConfig(name="OBJECT", subtypes=["VEHICLE"]),
            ],
            relation_types=[
                RelationTypeConfig(name="OWNS", source_types=["PERSON"], target_types=["VEHICLE"])
            ],
        )
        doc = from_entity_schema(schema)

        assert doc.patterns() == {("PERSON", "OWNS", "VEHICLE")}
        assert doc.validate_structure() == []

    def test_a_subtype_name_shared_by_two_types_is_qualified(self):
        """The regression: two types sharing a subtype name broke ``connect()``.

        Subtype names are only unique within their entity type, but ontology
        labels are the document's primary key. ``OTHER`` under both PERSON and
        ORGANIZATION used to be declared twice, so ``validate_structure()``
        reported a duplicate label — and since ``custom_schema_path`` is loaded
        through this converter, an existing deployment could not connect after
        upgrading.
        """
        schema = EntitySchemaConfig(
            name="shared-subtypes",
            entity_types=[
                EntityTypeConfig(name="PERSON", subtypes=["CUSTOMER", "OTHER"]),
                EntityTypeConfig(name="ORGANIZATION", subtypes=["SUPPLIER", "OTHER"]),
            ],
            relation_types=[],
        )
        doc = from_entity_schema(schema)

        assert doc.validate_structure() == []
        # First claimant keeps the bare name; the second is qualified.
        assert doc.labels() == [
            "PERSON",
            "CUSTOMER",
            "OTHER",
            "ORGANIZATION",
            "SUPPLIER",
            "ORGANIZATION_OTHER",
        ]
        # pole_type/subtype are untouched, so the Neo4j labels the storage
        # path derives from them do not change.
        qualified = doc.entity_type("ORGANIZATION_OTHER")
        assert (qualified.pole_type, qualified.subtype) == ("ORGANIZATION", "OTHER")
        assert doc.entity_type("OTHER").pole_type == "PERSON"

    def test_a_subtype_shadowing_a_later_type_is_qualified_too(self):
        schema = EntitySchemaConfig(
            name="shadowing",
            entity_types=[
                EntityTypeConfig(name="PERSON", subtypes=["ORGANIZATION"]),
                EntityTypeConfig(name="ORGANIZATION"),
            ],
            relation_types=[],
        )
        doc = from_entity_schema(schema)

        assert doc.validate_structure() == []
        assert doc.labels() == ["PERSON", "PERSON_ORGANIZATION", "ORGANIZATION"]

    def test_relationship_endpoints_resolve_to_the_qualified_label(self):
        schema = EntitySchemaConfig(
            name="qualified-endpoints",
            entity_types=[
                EntityTypeConfig(name="PERSON", subtypes=["OTHER"]),
                EntityTypeConfig(name="ORGANIZATION", subtypes=["OTHER"]),
            ],
            relation_types=[
                RelationTypeConfig(
                    name="MEMBER_OF",
                    source_types=["OTHER"],
                    target_types=["ORGANIZATION_OTHER"],
                )
            ],
        )
        doc = from_entity_schema(schema)

        assert doc.validate_structure() == []
        assert doc.patterns() == {("OTHER", "MEMBER_OF", "ORGANIZATION_OTHER")}

    def test_a_schema_with_shared_subtypes_still_compiles_and_validates(self):
        """End to end: the document a colliding schema produces is usable."""
        schema = EntitySchemaConfig(
            name="support",
            entity_types=[
                EntityTypeConfig(name="PERSON", subtypes=["CUSTOMER", "OTHER"]),
                EntityTypeConfig(name="ORGANIZATION", subtypes=["SUPPLIER", "OTHER"]),
            ],
        )
        doc = from_entity_schema(schema)

        assert doc.validate_structure() == []
        # Round-tripping collapses the qualified label back onto its subtype.
        back = to_entity_schema(doc)
        by_name = {et.name: et for et in back.entity_types}
        assert by_name["PERSON"].subtypes == ["CUSTOMER", "OTHER"]
        assert by_name["ORGANIZATION"].subtypes == ["SUPPLIER", "OTHER"]


class TestToEntitySchema:
    def test_labels_collapse_onto_pole_types_as_subtypes(self):
        doc = OntologyDocument(
            domain=DomainInfo(id="d", name="d", description="A domain."),
            entity_types=[
                EntityTypeDef(label="Person", pole_type="PERSON", description="A human."),
                EntityTypeDef(label="Engineer", pole_type="PERSON", subtype="ENGINEER"),
                EntityTypeDef(label="Widget", pole_type="OBJECT"),
            ],
            relationships=[RelationshipDef(type="USES", source="Engineer", target="Widget")],
        )
        schema = to_entity_schema(doc)

        assert schema.get_entity_type_names() == ["PERSON", "OBJECT"]
        assert schema.get_subtypes("PERSON") == ["ENGINEER"]
        # A label that is neither the pole type nor carries an explicit
        # subtype still survives as one.
        assert schema.get_subtypes("OBJECT") == ["WIDGET"]
        assert schema.description == "A domain."

    def test_relationship_endpoints_collapse_onto_pole_types(self):
        doc = get_template("podcast")
        schema = to_entity_schema(doc)
        works_at = next(rt for rt in schema.relation_types if rt.name == "WORKS_AT")
        assert works_at.source_types == ["PERSON"]
        assert works_at.target_types == ["ORGANIZATION"]

    def test_poleo_ontology_projects_onto_the_five_types(self):
        schema = to_entity_schema(POLEO_ONTOLOGY)
        assert set(schema.get_entity_type_names()) == {
            "PERSON",
            "ORGANIZATION",
            "LOCATION",
            "EVENT",
            "OBJECT",
        }
        assert set(schema.get_relation_types()) == set(POLEO_ONTOLOGY.relationship_types())


class TestRoundTrips:
    def test_entity_schema_round_trip_preserves_types_and_subtypes(self):
        original = get_default_schema()
        restored = to_entity_schema(from_entity_schema(original))

        assert set(restored.get_entity_type_names()) == set(original.get_entity_type_names())
        for name in original.get_entity_type_names():
            assert restored.get_subtypes(name) == original.get_subtypes(name)

    def test_entity_schema_round_trip_preserves_relation_patterns(self):
        original = get_default_schema()
        restored = to_entity_schema(from_entity_schema(original))

        assert restored.get_relation_types() == original.get_relation_types()
        for before in original.relation_types:
            after = next(rt for rt in restored.relation_types if rt.name == before.name)
            expected_sources = set(before.source_types) or set(original.get_entity_type_names())
            expected_targets = set(before.target_types) or set(original.get_entity_type_names())
            assert set(after.source_types) == expected_sources
            assert set(after.target_types) == expected_targets

    def test_document_round_trip_is_idempotent_on_patterns(self):
        doc = from_entity_schema(get_default_schema())
        again = from_entity_schema(to_entity_schema(doc))
        assert again.patterns() == doc.patterns()
        assert set(again.labels()) == set(doc.labels())

    def test_entity_schema_config_helpers_delegate_here(self):
        schema = get_default_schema()
        doc = schema.to_ontology()
        assert doc.labels() == from_entity_schema(schema).labels()

        restored = EntitySchemaConfig.from_ontology(doc)
        assert restored.get_entity_type_names() == to_entity_schema(doc).get_entity_type_names()


# -----------------------------------------------------------------------------
# DomainSchema
# -----------------------------------------------------------------------------


class _StubDomainSchema:
    """The duck-typed catalog shape (no extractor import needed)."""

    def __init__(self, name, entity_types, relation_types=None):
        self.name = name
        self.entity_types = entity_types
        self.relation_types = relation_types or {}


class TestFromDomainSchema:
    def test_labels_map_through_the_default_table(self):
        schema = _StubDomainSchema(
            "custom",
            {"person": "A human.", "company": "A business.", "widget": "A thing."},
        )
        doc = from_domain_schema(schema)

        assert doc.domain.id == "custom"
        assert doc.labels() == ["person", "company", "widget"]
        assert doc.label_map() == {
            "person": ("PERSON", None),
            "company": ("ORGANIZATION", "COMPANY"),
            "widget": ("OBJECT", "WIDGET"),
        }
        assert doc.entity_type("person").description == "A human."
        assert doc.relationships == []

    def test_supplied_relationships_are_attached(self):
        schema = _StubDomainSchema("custom", {"person": "A human.", "company": "A firm."})
        doc = from_domain_schema(
            schema,
            relationships=[RelationshipDef(type="WORKS_AT", source="person", target="company")],
        )
        assert doc.patterns() == {("person", "WORKS_AT", "company")}
        assert doc.validate_structure() == []

    def test_relation_descriptions_come_from_the_catalog_when_absent(self):
        schema = _StubDomainSchema(
            "custom",
            {"person": "A human.", "company": "A firm."},
            {"WORKS_AT": "Employment."},
        )
        doc = from_domain_schema(
            schema,
            relationships=[
                RelationshipDef(type="WORKS_AT", source="person", target="company"),
                RelationshipDef(
                    type="WORKS_AT",
                    source="person",
                    target="person",
                    description="Explicit.",
                ),
            ],
        )
        assert doc.relationships[0].description == "Employment."
        assert doc.relationships[1].description == "Explicit."

    def test_real_catalog_schemas_convert(self):
        from neo4j_agent_memory.extraction import DOMAIN_SCHEMAS

        doc = from_domain_schema(DOMAIN_SCHEMAS["medical"])
        assert doc.labels() == list(DOMAIN_SCHEMAS["medical"].entity_types)
        assert doc.validate_structure() == []


# -----------------------------------------------------------------------------
# arrows.app
# -----------------------------------------------------------------------------


class TestFromArrows:
    def test_the_shipped_sample_converts(self):
        doc, warnings = from_arrows(ARROWS_SAMPLE.read_text())

        assert doc.labels() == ["Customer", "Order", "Product", "Ticket"]
        assert doc.patterns() == {
            ("Customer", "PLACED", "Order"),
            ("Order", "CONTAINS", "Product"),
            ("Customer", "OPENED", "Ticket"),
            ("Ticket", "ABOUT", "Order"),
            ("Ticket", "CONCERNS", "Product"),
        }
        assert doc.validate_structure() == []
        assert {w.code for w in warnings} == {"unmapped_label"}

    def test_node_properties_become_typed_properties(self):
        doc, _ = from_arrows(ARROWS_SAMPLE.read_text())
        customer = doc.entity_type("Customer")
        assert {(p.name, p.type) for p in customer.properties} == {
            ("email", "string"),
            ("tier", "string"),
            ("signed_up_at", "datetime"),
        }

    def test_unmapped_labels_fall_back_to_object_with_a_warning(self):
        doc, warnings = from_arrows(
            {"nodes": [{"id": "n0", "labels": ["Widget"]}], "relationships": []}
        )
        assert doc.label_map() == {"widget": ("OBJECT", "WIDGET")}
        assert warnings[0].code == "unmapped_label"
        assert "Widget" in warnings[0].message

    def test_mapped_labels_produce_no_warning(self):
        doc, warnings = from_arrows(
            {"nodes": [{"id": "n0", "labels": ["Person"]}, {"id": "n1", "labels": ["City"]}]}
        )
        assert doc.label_map() == {
            "person": ("PERSON", None),
            "city": ("LOCATION", "CITY"),
        }
        assert warnings == []

    def test_a_dict_is_accepted_as_well_as_a_string(self):
        payload = json.loads(ARROWS_SAMPLE.read_text())
        from_dict, _ = from_arrows(payload)
        from_str, _ = from_arrows(json.dumps(payload))
        assert from_dict.labels() == from_str.labels()

    def test_caption_is_used_when_a_node_has_no_labels(self):
        doc, _ = from_arrows({"nodes": [{"id": "n0", "caption": "Person"}]})
        assert doc.labels() == ["Person"]

    def test_a_node_without_any_name_is_skipped_with_a_warning(self):
        doc, warnings = from_arrows({"nodes": [{"id": "n0"}]})
        assert doc.labels() == []
        assert warnings[0].code == "node_without_label"

    def test_extra_labels_are_reported(self):
        doc, warnings = from_arrows({"nodes": [{"id": "n0", "labels": ["Person", "Employee"]}]})
        assert doc.labels() == ["Person"]
        assert any(w.code == "multi_label_node" for w in warnings)

    def test_dangling_relationships_are_skipped_with_a_warning(self):
        doc, warnings = from_arrows(
            {
                "nodes": [{"id": "n0", "labels": ["Person"]}],
                "relationships": [{"id": "r0", "type": "KNOWS", "fromId": "n0", "toId": "n9"}],
            }
        )
        assert doc.relationships == []
        assert any(w.code == "dangling_relationship" for w in warnings)

    def test_duplicate_node_labels_collapse_to_one_entity_type(self):
        doc, _ = from_arrows(
            {
                "nodes": [
                    {"id": "n0", "labels": ["Person"]},
                    {"id": "n1", "labels": ["Person"]},
                ],
                "relationships": [{"id": "r0", "type": "KNOWS", "fromId": "n0", "toId": "n1"}],
            }
        )
        assert doc.labels() == ["Person"]
        assert doc.patterns() == {("Person", "KNOWS", "Person")}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Not valid arrows.app JSON"):
            from_arrows("{nope")
        with pytest.raises(ValueError, match="expected a top-level object"):
            from_arrows("[]")


# -----------------------------------------------------------------------------
# load_ontology
# -----------------------------------------------------------------------------


class TestLoadOntology:
    def test_json_ontology_document(self, tmp_path: Path):
        path = tmp_path / "ontology.json"
        path.write_text(POLEO_ONTOLOGY.model_dump_json())
        doc = load_ontology(path)
        assert doc.labels() == POLEO_ONTOLOGY.labels()
        assert doc.patterns() == POLEO_ONTOLOGY.patterns()

    def test_yaml_ontology_document(self, tmp_path: Path):
        yaml = pytest.importorskip("yaml")
        path = tmp_path / "ontology.yaml"
        path.write_text(yaml.safe_dump(json.loads(POLEO_ONTOLOGY.model_dump_json())))
        assert load_ontology(path).labels() == POLEO_ONTOLOGY.labels()

    def test_yml_suffix(self, tmp_path: Path):
        yaml = pytest.importorskip("yaml")
        path = tmp_path / "ontology.yml"
        path.write_text(
            yaml.safe_dump(
                {
                    "domain": {"id": "x", "name": "x"},
                    "entity_types": [{"label": "Person", "pole_type": "PERSON"}],
                }
            )
        )
        assert load_ontology(path).labels() == ["Person"]

    def test_an_entity_schema_config_file_is_converted(self, tmp_path: Path):
        path = tmp_path / "schema.json"
        path.write_text(
            json.dumps(
                {
                    "name": "medical",
                    "entity_types": [
                        {"name": "PERSON", "subtypes": ["PATIENT"]},
                        {"name": "OBJECT"},
                    ],
                    "relation_types": [
                        {
                            "name": "DIAGNOSED_WITH",
                            "source_types": ["PERSON"],
                            "target_types": ["OBJECT"],
                        }
                    ],
                }
            )
        )
        doc = load_ontology(path)
        assert doc.domain.name == "medical"
        assert doc.labels() == ["PERSON", "PATIENT", "OBJECT"]
        assert doc.patterns() == {("PERSON", "DIAGNOSED_WITH", "OBJECT")}

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_ontology(tmp_path / "nope.json")

    def test_unsupported_suffix(self, tmp_path: Path):
        path = tmp_path / "ontology.toml"
        path.write_text("x = 1")
        with pytest.raises(ValueError, match="Unsupported ontology file format"):
            load_ontology(path)

    def test_non_mapping_contents(self, tmp_path: Path):
        path = tmp_path / "ontology.json"
        path.write_text("[]")
        with pytest.raises(ValueError, match="does not contain a mapping"):
            load_ontology(path)


class TestShapeDetection:
    def test_ontology_documents_are_not_entity_schemas(self):
        assert not is_entity_schema_shape(json.loads(POLEO_ONTOLOGY.model_dump_json()))

    def test_entity_types_keyed_by_name_are_entity_schemas(self):
        assert is_entity_schema_shape({"entity_types": [{"name": "PERSON"}]})

    def test_a_top_level_domain_wins(self):
        assert not is_entity_schema_shape(
            {"domain": {"id": "x", "name": "x"}, "entity_types": [{"name": "PERSON"}]}
        )

    def test_relation_types_without_entity_types_is_an_entity_schema(self):
        assert is_entity_schema_shape({"relation_types": []})

    def test_an_empty_mapping_is_not(self):
        assert not is_entity_schema_shape({})
