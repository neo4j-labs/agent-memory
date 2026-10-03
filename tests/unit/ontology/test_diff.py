"""Unit tests for ontology/diff.py — structural diff between two documents."""

from __future__ import annotations

from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY
from neo4j_agent_memory.ontology.diff import diff_documents
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDiff,
    OntologyDocument,
    RelationshipDef,
)

PERSON = EntityTypeDef(label="Person", pole_type="PERSON", description="A human.")
COMPANY = EntityTypeDef(label="Company", pole_type="ORGANIZATION", subtype="COMPANY")
WORKS_AT = RelationshipDef(type="WORKS_AT", source="Person", target="Company")


def _doc(entity_types=None, relationships=None) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id="test", name="test"),
        entity_types=entity_types or [],
        relationships=relationships or [],
    )


class TestDiffShape:
    def test_an_identical_document_diffs_to_nothing(self):
        doc = _doc([PERSON, COMPANY], [WORKS_AT])
        diff = diff_documents(doc, doc)

        assert isinstance(diff, OntologyDiff)
        for section in (diff.entity_types, diff.relationships):
            assert section == {"added": [], "removed": [], "renamed": [], "modified": []}
        assert diff.mode_change is None

    def test_every_section_carries_the_four_nams_keys(self):
        diff = diff_documents(_doc([PERSON]), _doc([COMPANY]))
        for section in (diff.entity_types, diff.relationships):
            assert set(section) == {"added", "removed", "renamed", "modified"}

    def test_renamed_is_always_empty(self):
        # A rename is structurally indistinguishable from a removal + addition.
        renamed = PERSON.model_copy(update={"label": "Human"})
        diff = diff_documents(_doc([PERSON]), _doc([renamed]))
        assert diff.entity_types["renamed"] == []
        assert len(diff.entity_types["added"]) == 1
        assert len(diff.entity_types["removed"]) == 1

    def test_revisions_are_unset_for_a_pure_document_diff(self):
        diff = diff_documents(_doc(), _doc())
        assert diff.from_revision is None
        assert diff.to_revision is None


class TestEntityTypeDiff:
    def test_added_and_removed_carry_the_full_definition(self):
        diff = diff_documents(_doc([PERSON]), _doc([PERSON, COMPANY]))
        assert [e["label"] for e in diff.entity_types["added"]] == ["Company"]
        assert diff.entity_types["added"][0]["pole_type"] == "ORGANIZATION"
        assert diff.entity_types["removed"] == []

        diff = diff_documents(_doc([PERSON, COMPANY]), _doc([PERSON]))
        assert [e["label"] for e in diff.entity_types["removed"]] == ["Company"]

    def test_modified_reports_field_level_changes(self):
        after = PERSON.model_copy(update={"description": "A named human.", "threshold": 0.4})
        diff = diff_documents(_doc([PERSON]), _doc([after]))

        assert diff.entity_types["modified"] == [
            {
                "label": "Person",
                "changes": {
                    "description": {"from": "A human.", "to": "A named human."},
                    "threshold": {"from": None, "to": 0.4},
                },
            }
        ]

    def test_extension_fields_are_diffed(self):
        after = PERSON.model_copy(
            update={
                "aliases": {"Acme": ["ACME"]},
                "resolution_threshold": 0.93,
                "review_threshold": 0.8,
            }
        )
        changes = diff_documents(_doc([PERSON]), _doc([after])).entity_types["modified"][0][
            "changes"
        ]
        assert set(changes) == {"aliases", "resolution_threshold", "review_threshold"}

    def test_property_changes_are_reported(self):
        from neo4j_agent_memory.ontology.models import PropertyDef

        after = PERSON.model_copy(update={"properties": [PropertyDef(name="dob", type="date")]})
        changes = diff_documents(_doc([PERSON]), _doc([after])).entity_types["modified"][0][
            "changes"
        ]
        assert changes["properties"]["from"] == []
        assert changes["properties"]["to"][0]["name"] == "dob"

    def test_labels_match_case_insensitively(self):
        diff = diff_documents(_doc([PERSON]), _doc([PERSON.model_copy(update={"label": "PERSON"})]))
        assert diff.entity_types["added"] == []
        assert diff.entity_types["removed"] == []
        assert diff.entity_types["modified"] == []


class TestRelationshipDiff:
    def test_identity_is_the_type_source_target_triple(self):
        other_target = RelationshipDef(type="WORKS_AT", source="Person", target="Person")
        diff = diff_documents(_doc([PERSON, COMPANY], [WORKS_AT]), _doc([PERSON], [other_target]))

        assert [r["target"] for r in diff.relationships["added"]] == ["Person"]
        assert [r["target"] for r in diff.relationships["removed"]] == ["Company"]

    def test_modified_reports_constraint_changes(self):
        after = WORKS_AT.model_copy(
            update={"unique_source": True, "threshold": 0.35, "description": "Paid work."}
        )
        diff = diff_documents(_doc([PERSON, COMPANY], [WORKS_AT]), _doc([PERSON, COMPANY], [after]))

        assert diff.relationships["modified"] == [
            {
                "type": "WORKS_AT",
                "source": "Person",
                "target": "Company",
                "changes": {
                    "description": {"from": None, "to": "Paid work."},
                    "threshold": {"from": None, "to": 0.35},
                    "unique_source": {"from": False, "to": True},
                },
            }
        ]

    def test_every_constraint_field_is_diffed(self):
        after = WORKS_AT.model_copy(
            update={
                "inverse": "EMPLOYS",
                "unique_target": True,
                "acyclic": True,
                "allow_self": True,
            }
        )
        changes = diff_documents(
            _doc([PERSON, COMPANY], [WORKS_AT]), _doc([PERSON, COMPANY], [after])
        ).relationships["modified"][0]["changes"]
        assert set(changes) == {"inverse", "unique_target", "acyclic", "allow_self"}

    def test_several_defs_of_one_type_diff_independently(self):
        second = RelationshipDef(type="WORKS_AT", source="Person", target="Person")
        diff = diff_documents(
            _doc([PERSON, COMPANY], [WORKS_AT]),
            _doc([PERSON, COMPANY], [WORKS_AT, second]),
        )
        assert len(diff.relationships["added"]) == 1
        assert diff.relationships["removed"] == []


class TestAgainstBuiltins:
    def test_poleo_against_itself(self):
        diff = diff_documents(POLEO_ONTOLOGY, POLEO_ONTOLOGY)
        assert diff.entity_types["modified"] == []
        assert diff.relationships["modified"] == []

    def test_poleo_against_an_empty_document(self):
        diff = diff_documents(POLEO_ONTOLOGY, _doc())
        assert len(diff.entity_types["removed"]) == len(POLEO_ONTOLOGY.entity_types)
        assert len(diff.relationships["removed"]) == len(POLEO_ONTOLOGY.relationships)
        assert diff.entity_types["added"] == []

    def test_dropping_one_relationship_type_from_poleo(self):
        trimmed = POLEO_ONTOLOGY.model_copy(
            update={
                "relationships": [r for r in POLEO_ONTOLOGY.relationships if r.type != "MENTIONS"]
            }
        )
        diff = diff_documents(POLEO_ONTOLOGY, trimmed)
        assert {r["type"] for r in diff.relationships["removed"]} == {"MENTIONS"}
        assert diff.relationships["added"] == []
        assert diff.entity_types["removed"] == []

    def test_the_diff_serialises(self):
        diff = diff_documents(POLEO_ONTOLOGY, _doc([PERSON]))
        payload = diff.model_dump(mode="json")
        assert set(payload["entity_types"]) == {
            "added",
            "removed",
            "renamed",
            "modified",
        }
