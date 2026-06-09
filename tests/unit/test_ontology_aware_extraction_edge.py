"""Edge-case and regression unit tests for ontology-aware LLM extraction (0.6.0).

Complements ``test_ontology_aware_extraction.py`` with deeper coverage of
subtype derivation, per-call overrides, the plain-completion path, the
structured→plain fallback, config round-tripping, cap boundaries, the
desync warning, and pipeline fail-fast — all deterministic, no live API.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from neo4j_agent_memory import MemorySettings
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
    SchemaModel,
)
from neo4j_agent_memory.core.exceptions import ExtractionError
from neo4j_agent_memory.extraction._payloads import build_constrained_payload
from neo4j_agent_memory.extraction.factory import (
    create_extraction_pipeline,
    resolve_llm_type_source,
)
from neo4j_agent_memory.extraction.llm_extractor import (
    POLEO_SUBTYPES,
    LLMEntityExtractor,
)
from neo4j_agent_memory.schema.models import (
    EntitySchemaConfig,
    EntityTypeConfig,
    RelationTypeConfig,
)

# --------------------------------------------------------------------------- #
# Mock providers
# --------------------------------------------------------------------------- #


class _Completion:
    def __init__(self, content: str):
        self.content = content


class MockPlain:
    """Plain LLMProvider (no complete_structured) returning JSON text."""

    model = "mock-plain"

    def __init__(self, content: str):
        self._content = content
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return _Completion(self._content)


class MockStructuredEcho:
    """StructuredExtractor that echoes scripted entities."""

    model = "mock-structured"

    def __init__(self, entities):
        self._entities = entities
        self.calls = 0

    async def complete_structured(self, messages, response_model, **kwargs):
        self.calls += 1
        return response_model(entities=self._entities)

    async def complete(self, messages, **kwargs):  # pragma: no cover
        raise NotImplementedError


class MockStructuredRaises:
    """StructuredExtractor whose structured call fails, with a plain fallback."""

    model = "mock-structured-raises"

    def __init__(self, fallback_json: str):
        self._fallback_json = fallback_json
        self.structured_calls = 0
        self.complete_calls = 0

    async def complete_structured(self, messages, response_model, **kwargs):
        self.structured_calls += 1
        raise RuntimeError("structured boom")

    async def complete(self, messages, **kwargs):
        self.complete_calls += 1
        return _Completion(self._fallback_json)


# --------------------------------------------------------------------------- #
# Subtype derivation
# --------------------------------------------------------------------------- #


class TestSubtypeDerivation:
    def test_default_poleo_backfills_builtin_subtypes(self):
        ex = LLMEntityExtractor(provider=object())
        assert ex._subtypes["PERSON"] == POLEO_SUBTYPES["PERSON"]
        assert ex._subtypes["OBJECT"] == POLEO_SUBTYPES["OBJECT"]

    def test_custom_names_have_no_subtypes(self):
        ex = LLMEntityExtractor(provider=object(), entity_types=["DECISION", "TASK"])
        assert "DECISION" not in ex._subtypes
        assert ex._subtypes == {}

    def test_config_subtypes_used(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="DECISION", subtypes=["RATIFIED", "PROPOSED"])],
        )
        assert ex._subtypes["DECISION"] == ["RATIFIED", "PROPOSED"]

    def test_poleo_named_custom_type_backfills_subtypes(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="PERSON"), EntityTypeConfig(name="DECISION")],
        )
        assert ex._subtypes["PERSON"] == POLEO_SUBTYPES["PERSON"]
        assert "DECISION" not in ex._subtypes

    def test_explicit_subtypes_arg_wins(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="DECISION", subtypes=["X"])],
            subtypes={"DECISION": ["OVERRIDE"]},
        )
        assert ex._subtypes == {"DECISION": ["OVERRIDE"]}

    @pytest.mark.asyncio
    async def test_invalid_subtype_dropped_for_known_type(self):
        from neo4j_agent_memory.extraction._payloads import EntityPayload, ExtractionPayload
        from neo4j_agent_memory.extraction.llm_extractor import _normalize_types

        ex = LLMEntityExtractor(provider=object())
        payload = ExtractionPayload(
            entities=[EntityPayload(name="Alice", type="PERSON", subtype="BOGUS", confidence=0.9)]
        )
        specs = _normalize_types(None)
        res = ex._payload_to_result(payload, "t", specs, False, False)
        assert res.entities[0].subtype is None  # BOGUS not a valid PERSON subtype

    @pytest.mark.asyncio
    async def test_custom_type_subtype_kept(self):
        from neo4j_agent_memory.extraction._payloads import EntityPayload, ExtractionPayload
        from neo4j_agent_memory.extraction.llm_extractor import _normalize_types

        ex = LLMEntityExtractor(provider=object(), entity_types=["DECISION"])
        payload = ExtractionPayload(
            entities=[EntityPayload(name="X", type="DECISION", subtype="RATIFIED", confidence=0.9)]
        )
        specs = _normalize_types(["DECISION"])
        res = ex._payload_to_result(payload, "t", specs, False, False)
        assert res.entities[0].subtype == "RATIFIED"  # no constraint -> kept


# --------------------------------------------------------------------------- #
# Per-call override
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestPerCallOverride:
    async def test_override_with_names_changes_allowed_types(self):
        provider = MockStructuredEcho([{"name": "X", "type": "DECISION", "confidence": 0.9}])
        # Instance configured for POLE+O; per-call override to DECISION.
        ex = LLMEntityExtractor(
            provider=provider, extract_relations=False, extract_preferences=False
        )
        res = await ex.extract("text", entity_types=["DECISION"])
        assert res.entities[0].type == "DECISION"
        assert "DECISION" in res.type_coverage

    async def test_override_with_configs_injects_description(self):
        captured = {}

        class CaptureProvider(MockStructuredEcho):
            async def complete_structured(self, messages, response_model, **kwargs):
                captured["prompt"] = messages[-1].content
                return await super().complete_structured(messages, response_model, **kwargs)

        provider = CaptureProvider([{"name": "X", "type": "DECISION", "confidence": 0.9}])
        ex = LLMEntityExtractor(
            provider=provider, extract_relations=False, extract_preferences=False
        )
        await ex.extract(
            "text",
            entity_types=[EntityTypeConfig(name="DECISION", description="a ratified choice")],
        )
        assert "a ratified choice" in captured["prompt"]


# --------------------------------------------------------------------------- #
# Plain-completion path + structured fallback
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestPlainCompletionPath:
    async def test_markdown_fenced_json_parsed(self):
        fenced = (
            "```json\n"
            + json.dumps(
                {"entities": [{"name": "Adopt tool", "type": "DECISION", "confidence": 0.9}]}
            )
            + "\n```"
        )
        provider = MockPlain(fenced)
        ex = LLMEntityExtractor(
            provider=provider,
            entity_types=["DECISION"],
            extract_relations=False,
            extract_preferences=False,
        )
        res = await ex.extract("Decisions: adopt tool.")
        assert res.entities[0].name == "Adopt tool"
        assert provider.calls == 1

    async def test_invalid_json_raises(self):
        provider = MockPlain("not json at all")
        ex = LLMEntityExtractor(provider=provider, entity_types=["DECISION"])
        with pytest.raises(ExtractionError):
            await ex.extract("text")

    async def test_structured_failure_falls_back_to_plain(self):
        fallback = json.dumps(
            {"entities": [{"name": "Adopt tool", "type": "DECISION", "confidence": 0.9}]}
        )
        provider = MockStructuredRaises(fallback)
        ex = LLMEntityExtractor(
            provider=provider,
            entity_types=["DECISION"],
            extract_relations=False,
            extract_preferences=False,
        )
        res = await ex.extract("text")
        assert provider.structured_calls == 1
        assert provider.complete_calls == 1
        assert res.entities[0].type == "DECISION"


# --------------------------------------------------------------------------- #
# Empty text short-circuit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestEmptyText:
    async def test_empty_and_whitespace_return_empty_without_provider_call(self):
        provider = MockStructuredEcho([{"name": "X", "type": "PERSON", "confidence": 1.0}])
        ex = LLMEntityExtractor(provider=provider)
        for text in ["", "   ", "\n\t"]:
            res = await ex.extract(text)
            assert res.entities == []
        assert provider.calls == 0


# --------------------------------------------------------------------------- #
# for_poleo unchanged
# --------------------------------------------------------------------------- #


class TestForPoleoUnchanged:
    def test_for_poleo_prompt_matches_default(self):
        default = LLMEntityExtractor(provider=object())
        poleo = LLMEntityExtractor.for_poleo(provider=object())
        assert poleo._build_prompt("hello") == default._build_prompt("hello")

    def test_custom_extraction_prompt_back_compat(self):
        # A pre-0.6 custom prompt using only the old placeholders still works.
        ex = LLMEntityExtractor(
            provider=object(),
            extraction_prompt="Types: {entity_types}\nText: {text}",
        )
        prompt = ex._build_prompt("hello world")
        assert "Types: PERSON, ORGANIZATION, LOCATION, EVENT, OBJECT" in prompt
        assert "Text: hello world" in prompt


# --------------------------------------------------------------------------- #
# Relation block edges
# --------------------------------------------------------------------------- #


class TestRelationBlockEdges:
    def test_no_arrow_when_target_missing(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="DECISION")],
            relation_types=[RelationTypeConfig(name="DECIDED", source_types=["DECISION"])],
        )
        block = ex._render_relation_block()
        assert "DECIDED" in block
        assert "→" not in block

    def test_relation_without_description(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="DECISION")],
            relation_types=[
                RelationTypeConfig(
                    name="DECIDED", source_types=["DECISION"], target_types=["PERSON"]
                )
            ],
        )
        block = ex._render_relation_block()
        assert "DECIDED (DECISION → PERSON)" in block
        assert "DECIDED (DECISION → PERSON):" not in block  # no trailing description


# --------------------------------------------------------------------------- #
# Cap boundaries
# --------------------------------------------------------------------------- #


class TestCapBoundaries:
    def test_zero_global_cap_is_uncapped(self):
        specs = [
            EntityTypeConfig(name=f"T{i}", description="d " * 100, examples=["e"])
            for i in range(10)
        ]
        ex = LLMEntityExtractor(provider=object(), entity_types=specs, max_typed_block_chars=0)
        block = ex._render_typed_block(ex._type_specs)
        assert "Other types:" not in block  # nothing dropped
        assert "Examples:" in block  # examples retained

    def test_examples_dropped_before_name_only(self):
        # A block that fits once examples are dropped should keep all descriptions.
        specs = [
            EntityTypeConfig(name=f"T{i}", description="short", examples=["x"]) for i in range(5)
        ]
        ex = LLMEntityExtractor(provider=object(), entity_types=specs, max_typed_block_chars=80)
        block = ex._render_typed_block(ex._type_specs)
        # All five types still present by name; examples were the thing dropped.
        for i in range(5):
            assert f"T{i}" in block
        assert "Examples:" not in block

    def test_name_only_overflow_line_respects_global_cap(self):
        specs = [
            EntityTypeConfig(name=f"VERY_LONG_TYPE_NAME_{i}", description="d " * 50, examples=["x"])
            for i in range(50)
        ]
        ex = LLMEntityExtractor(provider=object(), entity_types=specs, max_typed_block_chars=40)
        block = ex._render_typed_block(ex._type_specs)
        assert len(block) <= 40


# --------------------------------------------------------------------------- #
# Constrained payload — relations
# --------------------------------------------------------------------------- #


class TestConstrainedRelations:
    def test_rejects_offlist_relation_type(self):
        model = build_constrained_payload(["DECISION", "PERSON"], ["DECIDED_BY"])
        with pytest.raises(Exception):
            model(
                entities=[{"name": "x", "type": "DECISION", "confidence": 0.9}],
                relations=[
                    {"source": "x", "target": "y", "relation_type": "BOGUS", "confidence": 0.9}
                ],
            )

    def test_accepts_allowed_relation_type(self):
        model = build_constrained_payload(["DECISION", "PERSON"], ["DECIDED_BY"])
        obj = model(
            entities=[
                {"name": "x", "type": "DECISION", "confidence": 0.9},
                {"name": "y", "type": "PERSON", "confidence": 0.9},
            ],
            relations=[
                {"source": "x", "target": "y", "relation_type": "DECIDED_BY", "confidence": 0.9}
            ],
        )
        assert obj.relations[0].relation_type == "DECIDED_BY"


# --------------------------------------------------------------------------- #
# Desync warning (R5)
# --------------------------------------------------------------------------- #


class TestDesyncWarning:
    def test_warns_when_schema_names_not_in_specs(self, caplog):
        import logging

        ec = ExtractionConfig(entity_type_specs=[EntityTypeConfig(name="TASK")])
        sc = SchemaConfig(model=SchemaModel.CUSTOM, entity_types=["PERSON", "GHOST"])
        with caplog.at_level(logging.WARNING):
            resolve_llm_type_source(ec, sc)
        assert any("GHOST" in r.message or "PERSON" in r.message for r in caplog.records)

    def test_no_warning_when_consistent(self, caplog):
        import logging

        ec = ExtractionConfig(entity_type_specs=[EntityTypeConfig(name="TASK")])
        sc = SchemaConfig(model=SchemaModel.CUSTOM, entity_types=["TASK"])
        with caplog.at_level(logging.WARNING):
            resolve_llm_type_source(ec, sc)
        assert not any("absent" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Pipeline fail-fast on bad custom_schema_path
# --------------------------------------------------------------------------- #


class TestPipelineFailFast:
    def test_pipeline_raises_on_bad_schema_path(self):
        ec = ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=False,
            enable_gliner=False,
            enable_llm_fallback=True,
        )
        sc = SchemaConfig(model=SchemaModel.CUSTOM, custom_schema_path="/no/such/schema.json")
        # The bad path is resolved BEFORE the per-stage try/except, so it must
        # propagate rather than being swallowed into a NoOp pipeline.
        with pytest.raises(FileNotFoundError):
            create_extraction_pipeline(ec, sc, llm_config=None)


# --------------------------------------------------------------------------- #
# Settings round-trip + validation
# --------------------------------------------------------------------------- #


class TestSettingsRoundTrip:
    def test_rich_fields_round_trip(self):
        schema = EntitySchemaConfig(
            name="m", entity_types=[EntityTypeConfig(name="DECISION", description="d")]
        )
        settings = MemorySettings(
            schema_config=SchemaConfig(model=SchemaModel.CUSTOM, entity_schema=schema),
            extraction=ExtractionConfig(
                entity_type_specs=[EntityTypeConfig(name="TASK", description="t")],
                max_type_examples=2,
                max_type_description_chars=120,
                max_typed_block_chars=2000,
            ),
        )
        assert settings.schema_config.entity_schema.entity_types[0].name == "DECISION"
        assert settings.extraction.entity_type_specs[0].name == "TASK"
        assert settings.extraction.max_type_examples == 2

    def test_negative_cap_rejected(self):
        with pytest.raises(ValidationError):
            ExtractionConfig(max_type_description_chars=-1)
        with pytest.raises(ValidationError):
            ExtractionConfig(max_type_examples=-5)
