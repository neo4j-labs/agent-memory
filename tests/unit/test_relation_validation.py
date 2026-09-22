"""Unit tests for ``ExtractionResult.validate_relations`` and the pipeline stage.

Relation validation is the one place in the runtime that turns the ontology's
declared ``(source, type, target)`` patterns into a decision about a specific
extracted edge, so the three modes, endpoint resolution (by mention id and by
name) and the pipeline wiring all get covered here.
"""

from __future__ import annotations

import logging

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline, MergeStrategy
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _ontology(
    entity_types: list[EntityTypeDef],
    relationships: list[RelationshipDef] | None = None,
) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id="test", name="test"),
        entity_types=entity_types,
        relationships=relationships or [],
    )


PERSON = EntityTypeDef(label="Person", pole_type="PERSON")
COMPANY = EntityTypeDef(label="Company", pole_type="ORGANIZATION", subtype="COMPANY")
CITY = EntityTypeDef(label="City", pole_type="LOCATION", subtype="CITY")


@pytest.fixture
def ontology() -> OntologyDocument:
    """Person -[EMPLOYED_BY]-> Company, Company -[LOCATED_IN]-> City."""
    return _ontology(
        [PERSON, COMPANY, CITY],
        [
            RelationshipDef(type="EMPLOYED_BY", source="Person", target="Company"),
            RelationshipDef(type="LOCATED_IN", source="Company", target="City"),
        ],
    )


@pytest.fixture
def result() -> ExtractionResult:
    """One permitted relation (EMPLOYED_BY) and one forbidden (FOUNDED)."""
    return ExtractionResult(
        entities=[
            ExtractedEntity(id="e1", name="Ada", type="PERSON"),
            ExtractedEntity(id="e2", name="Acme", type="ORGANIZATION", subtype="COMPANY"),
        ],
        relations=[
            ExtractedRelation(
                source="Ada",
                target="Acme",
                relation_type="EMPLOYED_BY",
                source_id="e1",
                target_id="e2",
            ),
            ExtractedRelation(
                source="Ada",
                target="Acme",
                relation_type="FOUNDED",
                source_id="e1",
                target_id="e2",
            ),
        ],
        source_text="Ada works at Acme.",
    )


# -----------------------------------------------------------------------------
# Modes
# -----------------------------------------------------------------------------


class TestModes:
    def test_warn_keeps_the_violation_and_logs(self, ontology, result, caplog):
        with caplog.at_level(logging.WARNING, logger="neo4j_agent_memory.extraction.base"):
            validated, violations = result.validate_relations(ontology, mode="warn")

        assert validated is result
        assert validated.relation_count == 2
        assert [r.relation_type for r in violations] == ["FOUNDED"]
        assert "FOUNDED" in caplog.text

    def test_warn_is_the_default_mode(self, ontology, result):
        validated, violations = result.validate_relations(ontology)
        assert validated is result
        assert len(violations) == 1

    def test_drop_removes_only_the_violation(self, ontology, result):
        validated, violations = result.validate_relations(ontology, mode="drop")

        assert validated is not result
        assert [r.relation_type for r in validated.relations] == ["EMPLOYED_BY"]
        assert [r.relation_type for r in violations] == ["FOUNDED"]
        # Entities, preferences and source text survive untouched.
        assert validated.entities == result.entities
        assert validated.source_text == result.source_text
        # The original is not mutated.
        assert result.relation_count == 2

    def test_raise_names_the_offending_triple(self, ontology, result):
        with pytest.raises(ValueError, match=r"Ada -\[FOUNDED\]-> Acme"):
            result.validate_relations(ontology, mode="raise")

    def test_a_clean_result_is_returned_as_is_in_every_mode(self, ontology):
        clean = ExtractionResult(
            entities=[
                ExtractedEntity(id="e1", name="Ada", type="PERSON"),
                ExtractedEntity(id="e2", name="Acme", type="ORGANIZATION", subtype="COMPANY"),
            ],
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    relation_type="EMPLOYED_BY",
                    source_id="e1",
                    target_id="e2",
                )
            ],
        )
        for mode in ("warn", "drop", "raise"):
            validated, violations = clean.validate_relations(ontology, mode=mode)
            assert validated is clean
            assert violations == []


# -----------------------------------------------------------------------------
# Endpoint resolution
# -----------------------------------------------------------------------------


class TestEndpointResolution:
    def test_ids_win_over_names_when_a_name_is_mentioned_twice(self, ontology):
        # Two mentions share the name "Acme": one typed as a Company, one as a
        # City. Only the id distinguishes them, and only the Company endpoint
        # makes EMPLOYED_BY legal.
        res = ExtractionResult(
            entities=[
                ExtractedEntity(id="p", name="Ada", type="PERSON"),
                ExtractedEntity(id="c", name="Acme", type="LOCATION", subtype="CITY"),
                ExtractedEntity(id="o", name="Acme", type="ORGANIZATION", subtype="COMPANY"),
            ],
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    relation_type="EMPLOYED_BY",
                    source_id="p",
                    target_id="o",
                ),
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    relation_type="EMPLOYED_BY",
                    source_id="p",
                    target_id="c",
                ),
            ],
        )
        validated, violations = res.validate_relations(ontology, mode="drop")

        assert [r.target_id for r in validated.relations] == ["o"]
        assert [r.target_id for r in violations] == ["c"]

    def test_names_resolve_when_no_ids_are_present(self, ontology):
        res = ExtractionResult(
            entities=[
                ExtractedEntity(name="Ada", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION", subtype="COMPANY"),
            ],
            relations=[
                ExtractedRelation(source=" ADA ", target="acme", relation_type="EMPLOYED_BY"),
            ],
        )
        validated, violations = res.validate_relations(ontology, mode="drop")

        assert violations == []
        assert validated.relation_count == 1

    def test_an_unresolvable_endpoint_is_a_violation(self, ontology):
        res = ExtractionResult(
            entities=[ExtractedEntity(name="Ada", type="PERSON")],
            relations=[
                ExtractedRelation(source="Ada", target="Ghost", relation_type="EMPLOYED_BY")
            ],
        )
        _, violations = res.validate_relations(ontology, mode="drop")
        assert len(violations) == 1

    def test_an_endpoint_type_the_ontology_omits_is_a_violation(self, ontology):
        # EVENT is not declared, so no label resolves for it.
        res = ExtractionResult(
            entities=[
                ExtractedEntity(id="e1", name="Ada", type="PERSON"),
                ExtractedEntity(id="e2", name="Launch", type="EVENT"),
            ],
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Launch",
                    relation_type="EMPLOYED_BY",
                    source_id="e1",
                    target_id="e2",
                )
            ],
        )
        _, violations = res.validate_relations(ontology, mode="drop")
        assert len(violations) == 1

    def test_a_subtype_the_ontology_omits_falls_back_to_the_base_label(self):
        # Person is declared with no subtype, so PERSON:ALIAS still resolves.
        doc = _ontology(
            [PERSON, COMPANY],
            [RelationshipDef(type="EMPLOYED_BY", source="Person", target="Company")],
        )
        res = ExtractionResult(
            entities=[
                ExtractedEntity(id="e1", name="Ada", type="PERSON", subtype="ALIAS"),
                ExtractedEntity(id="e2", name="Acme", type="ORGANIZATION", subtype="COMPANY"),
            ],
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    relation_type="EMPLOYED_BY",
                    source_id="e1",
                    target_id="e2",
                )
            ],
        )
        _, violations = res.validate_relations(doc, mode="drop")
        assert violations == []

    def test_direction_matters(self, ontology, result):
        reversed_edge = ExtractionResult(
            entities=result.entities,
            relations=[
                ExtractedRelation(
                    source="Acme",
                    target="Ada",
                    relation_type="EMPLOYED_BY",
                    source_id="e2",
                    target_id="e1",
                )
            ],
        )
        _, violations = reversed_edge.validate_relations(ontology, mode="drop")
        assert len(violations) == 1

    def test_relation_type_comparison_is_case_insensitive(self, ontology, result):
        res = ExtractionResult(
            entities=result.entities,
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    relation_type="employed_by",
                    source_id="e1",
                    target_id="e2",
                )
            ],
        )
        _, violations = res.validate_relations(ontology, mode="drop")
        assert violations == []


# -----------------------------------------------------------------------------
# Pass-through cases
# -----------------------------------------------------------------------------


class TestPassthrough:
    def test_an_ontology_with_no_relationships_cannot_express_a_violation(self, result):
        entity_only = _ontology([PERSON, COMPANY])
        validated, violations = result.validate_relations(entity_only, mode="raise")

        assert validated is result
        assert violations == []

    def test_a_result_with_no_relations_passes_through(self, ontology):
        res = ExtractionResult(entities=[ExtractedEntity(name="Ada", type="PERSON")])
        validated, violations = res.validate_relations(ontology, mode="raise")

        assert validated is res
        assert violations == []


# -----------------------------------------------------------------------------
# Pipeline integration
# -----------------------------------------------------------------------------


class _StubExtractor:
    """Returns a canned result, like a single pipeline stage would."""

    def __init__(self, result: ExtractionResult, name: str = "stub") -> None:
        self._result = result
        self.name = name

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        return self._result


class TestPipelineStage:
    @pytest.mark.asyncio
    async def test_the_pipeline_drops_forbidden_relations(self, ontology, result):
        pipeline = ExtractionPipeline(
            stages=[_StubExtractor(result)],
            merge_strategy=MergeStrategy.UNION,
            ontology=ontology,
        )

        merged = await pipeline.extract("Ada works at Acme.")

        assert [r.relation_type for r in merged.relations] == ["EMPLOYED_BY"]

    @pytest.mark.asyncio
    async def test_without_an_ontology_the_pipeline_keeps_everything(self, result):
        pipeline = ExtractionPipeline(
            stages=[_StubExtractor(result)],
            merge_strategy=MergeStrategy.UNION,
        )

        merged = await pipeline.extract("Ada works at Acme.")

        assert {r.relation_type for r in merged.relations} == {"EMPLOYED_BY", "FOUNDED"}

    @pytest.mark.asyncio
    async def test_the_default_pipeline_has_no_ontology(self):
        pipeline = ExtractionPipeline(stages=[_StubExtractor(ExtractionResult())])
        assert pipeline.ontology is None

    @pytest.mark.asyncio
    async def test_validation_runs_after_merging_several_stages(self, ontology):
        # Stage 1 contributes only the permitted edge, stage 2 only the
        # forbidden one — the violation can therefore only be caught after the
        # merge, which is exactly where the pipeline applies it.
        entities = [
            ExtractedEntity(name="Ada", type="PERSON"),
            ExtractedEntity(name="Acme", type="ORGANIZATION", subtype="COMPANY"),
        ]
        first = ExtractionResult(
            entities=entities,
            relations=[ExtractedRelation(source="Ada", target="Acme", relation_type="EMPLOYED_BY")],
        )
        second = ExtractionResult(
            entities=entities,
            relations=[ExtractedRelation(source="Ada", target="Acme", relation_type="FOUNDED")],
        )
        pipeline = ExtractionPipeline(
            stages=[_StubExtractor(first, "a"), _StubExtractor(second, "b")],
            merge_strategy=MergeStrategy.UNION,
            ontology=ontology,
        )

        details = await pipeline.extract_with_details("Ada works at Acme.")

        assert [r.relation_type for r in details.final_result.relations] == ["EMPLOYED_BY"]
        # Per-stage results are untouched; only the merged result is filtered.
        assert any(
            r.relation_type == "FOUNDED"
            for stage in details.stage_results
            for r in stage.result.relations
        )


# -----------------------------------------------------------------------------
# Relationship-type spelling
# -----------------------------------------------------------------------------


class TestRelationTypeNormalization:
    """Declared and extracted type names are compared in one normal form.

    An ontology author may write ``type="works at"``. The extractor emits
    UPPER_SNAKE (``WORKS_AT``), and validation used to compare the extracted
    name against the declared one verbatim — so every relation of a
    tolerantly-spelled type was silently dropped.
    """

    @staticmethod
    def _result(relation_type: str) -> ExtractionResult:
        return ExtractionResult(
            entities=[
                ExtractedEntity(id="e1", name="Ada", type="PERSON"),
                ExtractedEntity(id="e2", name="Acme", type="ORGANIZATION", subtype="COMPANY"),
            ],
            relations=[
                ExtractedRelation(
                    source="Ada",
                    target="Acme",
                    source_id="e1",
                    target_id="e2",
                    relation_type=relation_type,
                )
            ],
        )

    @pytest.mark.parametrize("declared", ["works at", "Works-At", "works  at", "WORKS_AT"])
    def test_loosely_spelled_declaration_still_permits_the_relation(self, declared: str) -> None:
        ontology = _ontology(
            [PERSON, COMPANY], [RelationshipDef(type=declared, source="Person", target="Company")]
        )

        validated, violations = self._result("WORKS_AT").validate_relations(ontology, mode="drop")

        assert violations == []
        assert [r.relation_type for r in validated.relations] == ["WORKS_AT"]

    def test_loosely_spelled_extraction_is_also_matched(self) -> None:
        ontology = _ontology(
            [PERSON, COMPANY], [RelationshipDef(type="WORKS_AT", source="Person", target="Company")]
        )

        validated, violations = self._result("works at").validate_relations(ontology, mode="drop")

        assert violations == []
        assert validated.relation_count == 1

    def test_a_genuinely_different_type_is_still_a_violation(self) -> None:
        ontology = _ontology(
            [PERSON, COMPANY], [RelationshipDef(type="works at", source="Person", target="Company")]
        )

        _, violations = self._result("FOUNDED").validate_relations(ontology, mode="drop")

        assert [r.relation_type for r in violations] == ["FOUNDED"]


class TestNormalizeRelationType:
    """The one helper both sides go through."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("works at", "WORKS_AT"),
            ("Works-At", "WORKS_AT"),
            ("works   at", "WORKS_AT"),
            ("works--at", "WORKS_AT"),
            ("  employed by  ", "EMPLOYED_BY"),
            ("WORKS_AT", "WORKS_AT"),
            ("_works_at_", "WORKS_AT"),
        ],
    )
    def test_upper_snake_is_the_normal_form(self, raw: str, expected: str) -> None:
        from neo4j_agent_memory.extraction.base import normalize_relation_type

        assert normalize_relation_type(raw) == expected
