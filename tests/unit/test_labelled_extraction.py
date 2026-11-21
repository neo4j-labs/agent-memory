"""Unit tests for the step that asks the LLM for a custom ontology's own labels,
for the reserved-label check, and for the count of what an ontology drops."""

from __future__ import annotations

from typing import Any, get_args

import pytest

from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractedRelation, ExtractionResult
from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor, _labelled_payload_model
from neo4j_agent_memory.memory.short_term import ShortTermMemory
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)


def ontology(*labels: tuple[str, str, str | None]) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id="test", name="test", description="A test ontology."),
        entity_types=[
            EntityTypeDef(label=label, pole_type=pole, subtype=subtype, description=f"A {label}.")
            for label, pole, subtype in labels
        ],
        relationships=[RelationshipDef(type="USES", source="Project", target="Technology")],
    )


CODING = ontology(
    ("Person", "PERSON", None),
    ("Project", "OBJECT", "PROJECT"),
    ("Technology", "OBJECT", "TECHNOLOGY"),
    ("Item", "OBJECT", None),
)


class StructuredProvider:
    """A provider with structured output that answers from a canned payload."""

    model = "fake/model"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.models: list[type] = []
        self.prompts: list[str] = []

    async def complete(self, messages: Any, **_: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("the structured path should be used")

    async def complete_structured(self, messages: Any, response_model: type, **_: Any) -> Any:
        self.models.append(response_model)
        self.prompts.append(messages[-1].content)
        return response_model.model_validate(self.answer)


class TestLabelledPayload:
    def test_labels_and_relation_types_are_enumerated(self) -> None:
        model = _labelled_payload_model(CODING)
        assert model is not None
        entity = get_args(model.model_fields["entities"].annotation)[0]
        assert get_args(entity.model_fields["label"].annotation) == (
            "Person",
            "Project",
            "Technology",
            "Item",
        )
        relation = get_args(model.model_fields["relations"].annotation)[0]
        assert get_args(relation.model_fields["relation_type"].annotation) == ("USES",)

    def test_every_field_is_required(self) -> None:
        model = _labelled_payload_model(CODING)
        assert model is not None
        schema = model.model_json_schema()
        for definition in [schema, *schema["$defs"].values()]:
            assert sorted(definition["required"]) == sorted(definition["properties"])

    def test_the_built_in_ontology_keeps_the_default_path(self) -> None:
        assert _labelled_payload_model(POLEO_ONTOLOGY) is None


class TestLabelledExtraction:
    @pytest.mark.asyncio
    async def test_a_label_becomes_its_type_and_subtype(self) -> None:
        provider = StructuredProvider(
            {
                "entities": [
                    {"name": "budget tracker", "label": "Project", "confidence": 0.9},
                    {"name": "Flask", "label": "Technology", "confidence": 0.95},
                    {"name": "Craig", "label": "Person", "confidence": 1.0},
                ],
                "relations": [
                    {
                        "source": "budget tracker",
                        "target": "Flask",
                        "relation_type": "USES",
                        "confidence": 0.8,
                    },
                    {
                        "source": "budget tracker",
                        "target": "Django",
                        "relation_type": "USES",
                        "confidence": 0.8,
                    },
                ],
                "preferences": [],
            }
        )
        extractor = LLMEntityExtractor(provider=provider, ontology=CODING)
        result = await extractor.extract("Craig builds the budget tracker with Flask.")

        assert [(e.name, e.type, e.subtype) for e in result.entities] == [
            ("budget tracker", "OBJECT", "PROJECT"),
            ("Flask", "OBJECT", "TECHNOLOGY"),
            ("Craig", "PERSON", None),
        ]
        # A relation to something that was not extracted is left out.
        assert [(r.source, r.relation_type, r.target) for r in result.relations] == [
            ("budget tracker", "USES", "Flask")
        ]
        prompt = provider.prompts[0]
        assert "- Technology (OBJECT:TECHNOLOGY): A Technology." in prompt
        # The POLE+O guidelines of the default prompt do not describe this ontology.
        assert "PERSON: Individuals" not in prompt

    @pytest.mark.asyncio
    async def test_a_label_outside_the_ontology_is_rejected_by_the_schema(self) -> None:
        provider = StructuredProvider(
            {
                "entities": [{"name": "x", "label": "Gadget", "confidence": 1.0}],
                "relations": [],
                "preferences": [],
            }
        )
        model = _labelled_payload_model(CODING)
        assert model is not None
        with pytest.raises(ValueError):
            model.model_validate(provider.answer)

    @pytest.mark.asyncio
    async def test_a_custom_prompt_keeps_the_default_path(self) -> None:
        extractor = LLMEntityExtractor(
            provider=StructuredProvider({}), ontology=CODING, extraction_prompt="{text}"
        )
        assert extractor._labelled_model is None


class TestReservedLabels:
    def test_colliding_labels_are_reported(self) -> None:
        doc = ontology(
            ("Fact", "OBJECT", "FACT"), ("tool", "OBJECT", "TOOL"), ("Project", "OBJECT", None)
        )
        assert doc.reserved_labels() == ["Fact", "tool"]

    def test_the_built_in_ontology_has_none(self) -> None:
        assert POLEO_ONTOLOGY.reserved_labels() == []


class TestDropCounts:
    def test_dropped_relations_are_counted_by_pattern(self) -> None:
        memory = ShortTermMemory(None, ontology=CODING)  # type: ignore[arg-type]
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="Craig", type="PERSON", confidence=1.0),
                ExtractedEntity(
                    name="budget tracker", type="OBJECT", subtype="PROJECT", confidence=1.0
                ),
                ExtractedEntity(name="Flask", type="OBJECT", subtype="TECHNOLOGY", confidence=1.0),
            ],
            relations=[
                ExtractedRelation(
                    source="budget tracker", target="Flask", relation_type="USES", confidence=1.0
                ),
                ExtractedRelation(
                    source="Craig", target="Flask", relation_type="USES", confidence=1.0
                ),
                ExtractedRelation(
                    source="Craig", target="budget tracker", relation_type="OWNS", confidence=1.0
                ),
            ],
        )
        kept = memory._apply_ontology(result)
        assert [(r.source, r.target) for r in kept.relations] == [("budget tracker", "Flask")]
        assert memory.extraction_drops == {
            "entities": 0,
            "relations": 2,
            "relation_patterns": {
                "Person -[USES]-> Technology": 1,
                "Person -[OWNS]-> Project": 1,
            },
        }

    def test_nothing_dropped_to_start_with(self) -> None:
        memory = ShortTermMemory(None, ontology=CODING)  # type: ignore[arg-type]
        assert memory.extraction_drops == {"entities": 0, "relations": 0, "relation_patterns": {}}
