"""Unit tests for ontology/builtin.py — POLE+O and the domain templates."""

from __future__ import annotations

import pytest

from neo4j_agent_memory.ontology import builtin
from neo4j_agent_memory.ontology.builtin import (
    POLEO_ONTOLOGY,
    get_template,
    list_templates,
)
from neo4j_agent_memory.ontology.models import OntologyDocument

# The sixteen relationship types schema/models.py declares for POLE+O.
EXPECTED_RELATION_TYPES = {
    "KNOWS",
    "ALIAS_OF",
    "MEMBER_OF",
    "EMPLOYED_BY",
    "OWNS",
    "USES",
    "LOCATED_AT",
    "RESIDES_AT",
    "HEADQUARTERS_AT",
    "PARTICIPATED_IN",
    "OCCURRED_AT",
    "INVOLVED",
    "SUBSIDIARY_OF",
    "PARTNER_WITH",
    "RELATED_TO",
    "MENTIONS",
}


def _endpoints_are_declared(doc: OntologyDocument) -> bool:
    labels = {label.lower() for label in doc.labels()}
    return all(
        rel.source.lower() in labels and rel.target.lower() in labels for rel in doc.relationships
    )


class TestPoleoOntology:
    def test_it_validates(self):
        assert POLEO_ONTOLOGY.validate_structure() == []

    def test_five_entity_types_named_after_the_pole_types(self):
        assert POLEO_ONTOLOGY.labels() == [
            "Person",
            "Organization",
            "Location",
            "Event",
            "Object",
        ]
        for et in POLEO_ONTOLOGY.entity_types:
            assert et.pole_type == et.label.upper()
            assert et.subtype is None

    def test_every_entity_type_has_a_guideline_with_a_negative_case(self):
        for et in POLEO_ONTOLOGY.entity_types:
            assert et.description
            assert "Not a" in et.description or "Not an" in et.description

    def test_all_sixteen_relationship_types_are_present(self):
        assert set(POLEO_ONTOLOGY.relationship_types()) == EXPECTED_RELATION_TYPES

    def test_every_relationship_endpoint_is_a_declared_label(self):
        assert _endpoints_are_declared(POLEO_ONTOLOGY)

    def test_every_relationship_is_described(self):
        assert all(rel.description for rel in POLEO_ONTOLOGY.relationships)

    def test_multi_endpoint_relationships_are_expanded_per_pair(self):
        owns = [r for r in POLEO_ONTOLOGY.relationships if r.type == "OWNS"]
        assert {(r.source, r.target) for r in owns} == {
            ("Person", "Object"),
            ("Organization", "Object"),
        }
        located_at = [r for r in POLEO_ONTOLOGY.relationships if r.type == "LOCATED_AT"]
        assert {r.source for r in located_at} == {
            "Person",
            "Object",
            "Organization",
            "Event",
        }
        assert {r.target for r in located_at} == {"Location"}

    @pytest.mark.parametrize("rel_type", ["EMPLOYED_BY", "RESIDES_AT", "HEADQUARTERS_AT"])
    def test_cardinality_constraints(self, rel_type: str):
        defs = [r for r in POLEO_ONTOLOGY.relationships if r.type == rel_type]
        assert defs
        assert all(r.unique_source for r in defs)
        assert all(not r.unique_target for r in defs)

    @pytest.mark.parametrize("rel_type", ["ALIAS_OF", "SUBSIDIARY_OF"])
    def test_acyclic_constraints(self, rel_type: str):
        defs = [r for r in POLEO_ONTOLOGY.relationships if r.type == rel_type]
        assert defs
        assert all(r.acyclic for r in defs)

    def test_alias_of_forbids_self_loops(self):
        defs = [r for r in POLEO_ONTOLOGY.relationships if r.type == "ALIAS_OF"]
        assert all(not r.allow_self for r in defs)

    @pytest.mark.parametrize("rel_type", ["RELATED_TO", "MENTIONS"])
    def test_catch_alls_carry_a_confidence_floor(self, rel_type: str):
        defs = [r for r in POLEO_ONTOLOGY.relationships if r.type == rel_type]
        assert defs
        assert all(r.threshold == 0.6 for r in defs)
        # Both catch-alls span every POLE+O pair.
        assert len(defs) == 25

    def test_no_inverse_is_declared(self):
        # KNOWS / PARTNER_WITH are symmetric-ish; they are symmetrised at read
        # time rather than mirrored during decoding.
        assert all(r.inverse is None for r in POLEO_ONTOLOGY.relationships)

    def test_no_relationship_permits_self_loops(self):
        assert all(not r.allow_self for r in POLEO_ONTOLOGY.relationships)
        assert POLEO_ONTOLOGY.no_self_loops is True

    def test_domain_identity(self):
        assert POLEO_ONTOLOGY.domain.id == "poleo"
        assert POLEO_ONTOLOGY.domain.name == "poleo"


class TestDomainTemplates:
    def test_eight_catalog_templates(self):
        assert list_templates() == [
            "poleo",
            "podcast",
            "news",
            "scientific",
            "business",
            "entertainment",
            "medical",
            "legal",
        ]

    @pytest.mark.parametrize("name", list_templates())
    def test_every_template_validates(self, name: str):
        assert get_template(name).validate_structure() == []

    @pytest.mark.parametrize("name", list_templates())
    def test_every_template_endpoint_is_a_declared_label(self, name: str):
        assert _endpoints_are_declared(get_template(name))

    @pytest.mark.parametrize("name", [n for n in list_templates() if n != "poleo"])
    def test_every_template_label_carries_its_catalog_description(self, name: str):
        # ``poleo`` is excluded: that template is POLEO_ONTOLOGY itself (see
        # ``test_the_poleo_template_is_the_curated_ontology``), not a
        # conversion of the flat catalog entry.
        from neo4j_agent_memory.extraction import DOMAIN_SCHEMAS

        doc = get_template(name)
        catalog = DOMAIN_SCHEMAS[name]
        assert doc.labels() == list(catalog.entity_types)
        for et in doc.entity_types:
            assert et.description == catalog.entity_types[et.label]

    def test_the_poleo_template_is_the_curated_ontology(self):
        # One POLE+O document, not two: the template and the library default
        # must never diverge.
        assert get_template("poleo") is POLEO_ONTOLOGY
        assert set(get_template("poleo").relationship_types()) == EXPECTED_RELATION_TYPES

    @pytest.mark.parametrize("name", list_templates())
    def test_every_template_label_maps_onto_a_poleo_type(self, name: str):
        from neo4j_agent_memory.ontology.models import POLEO_TYPES

        for et in get_template(name).entity_types:
            assert et.pole_type in POLEO_TYPES

    def test_podcast_relationships(self):
        doc = get_template("podcast")
        assert doc.patterns() == {
            ("person", "WORKS_AT", "company"),
            ("person", "FOUNDED", "company"),
            ("company", "LOCATED_IN", "location"),
            ("person", "DISCUSSES", "concept"),
        }

    def test_news_relationships(self):
        doc = get_template("news")
        assert ("person", "WORKS_AT", "organization") in doc.patterns()
        assert ("event", "OCCURRED_AT", "location") in doc.patterns()

    def test_poleo_template_has_relationships(self):
        assert get_template("poleo").relationships

    @pytest.mark.parametrize(
        "name", ["scientific", "business", "entertainment", "medical", "legal"]
    )
    def test_remaining_templates_are_entity_only(self, name: str):
        assert get_template(name).relationships == []

    def test_templates_carry_subtypes_from_the_label_mapping(self):
        doc = get_template("podcast")
        company = doc.entity_type("company")
        assert company is not None
        assert (company.pole_type, company.subtype) == ("ORGANIZATION", "COMPANY")

    def test_templates_are_cached(self):
        assert get_template("news") is get_template("news")

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown ontology template"):
            get_template("nope")

    def test_domain_templates_mapping_is_lazily_materialised(self):
        templates = builtin.DOMAIN_TEMPLATES
        assert set(templates) == set(list_templates())
        assert all(isinstance(doc, OntologyDocument) for doc in templates.values())

    def test_module_getattr_still_rejects_unknown_names(self):
        with pytest.raises(AttributeError):
            _ = builtin.NOT_A_THING
