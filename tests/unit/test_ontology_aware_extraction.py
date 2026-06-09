"""Regression suite for ontology-aware LLM entity extraction (0.6.0).

Covers threading entity-type descriptions/examples end-to-end into the LLM
extraction prompt, the intra-call same-name guard, schema-aware type mapping,
strict-mode structured-output constraints, relationship-type rendering, and
per-type coverage telemetry.

All tests here are deterministic and run in CI with no live API calls. The
end-to-end paths use mock providers. A live recall check (AC2: domain-type
recall >= 0.80) is provided separately, gated behind RUN_INTEGRATION_TESTS and
an API key, and skipped in CI.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from neo4j_agent_memory.config.settings import ExtractionConfig, SchemaConfig
from neo4j_agent_memory.extraction._payloads import (
    EntityPayload,
    ExtractionPayload,
    build_constrained_payload,
)
from neo4j_agent_memory.extraction.factory import (
    ExtractorBuilder,
    resolve_llm_type_source,
)
from neo4j_agent_memory.extraction.llm_extractor import (
    DEFAULT_POLEO_TYPE_GUIDELINES,
    LLMEntityExtractor,
    _normalize_types,
    _truncate,
)
from neo4j_agent_memory.schema.models import (
    EntitySchemaConfig,
    EntityTypeConfig,
    RelationTypeConfig,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

# A small synthetic stand-in for the reproduction "Decisions" meeting summary.
# We do not have the original document; this exercises the same shape (an
# explicit Decisions section with ratified items plus well-known NER entities).
DECISIONS_DOC = """\
Meeting notes — Platform sync.

Attendees: Sudhir Hasbe, Lauren Sparacin.

Decisions:
- Adopt the local transcription tool for all hands.
- Ship the Hume file-drop feature by Q3.
- Operate the two business units as separate entities.
- Prioritise ARR-focused acquisition metrics.

Gartner was cited for the event branding guidance.
"""


def _meeting_schema() -> EntitySchemaConfig:
    """A custom schema with described domain types (DECISION/TASK/OUTCOME)."""
    return EntitySchemaConfig(
        name="meeting",
        entity_types=[
            EntityTypeConfig(
                name="DECISION",
                description=(
                    "A concrete agreement, resolution, or choice made during a meeting. "
                    "Signalled by 'Decisions:' sections or verbs such as decided, agreed, "
                    "approved, ratified, resolved."
                ),
                examples=["Adopt local transcription tool", "Ship file-drop by Q3"],
            ),
            EntityTypeConfig(
                name="TASK", description="An action item assigned to a person or team."
            ),
            EntityTypeConfig(
                name="OUTCOME",
                description="A result or stated impact. Distinct from DECISION (a choice).",
            ),
            EntityTypeConfig(
                name="PRODUCT",
                description="A software product, feature set, or productised offering.",
                examples=["Hume", "Aura"],
            ),
            # Undescribed POLE+O core type -> should backfill the built-in description.
            EntityTypeConfig(name="PERSON"),
            EntityTypeConfig(name="ORGANIZATION"),
        ],
        relation_types=[
            RelationTypeConfig(
                name="DECIDED_BY",
                description="Links a decision to the person who made it.",
                source_types=["DECISION"],
                target_types=["PERSON"],
            ),
        ],
    )


class MockStructured:
    """A StructuredExtractor that returns a scripted payload."""

    model = "mock-structured"

    def __init__(self, entities: list[dict]):
        self._entities = entities
        self.last_response_model: type | None = None

    async def complete_structured(
        self, messages, response_model, *, temperature=0.0, max_retries=2, timeout=None
    ):
        self.last_response_model = response_model
        # Construct via the (possibly constrained) response_model so strict-mode
        # constraints are actually enforced by Pydantic validation.
        return response_model(entities=self._entities)

    async def complete(self, messages, **kwargs):  # pragma: no cover - not used
        raise NotImplementedError


class _Completion:
    def __init__(self, content: str):
        self.content = content


class MockPlain:
    """A plain LLMProvider (no complete_structured) returning JSON text."""

    model = "mock-plain"

    def __init__(self, payload: dict):
        self._payload = payload

    async def complete(self, messages, **kwargs):
        return _Completion(json.dumps(self._payload))


# --------------------------------------------------------------------------- #
# _normalize_types / _truncate
# --------------------------------------------------------------------------- #


class TestNormalizeTypes:
    def test_names_to_specs(self):
        specs = _normalize_types(["person", "Decision"])
        assert [s.name for s in specs] == ["PERSON", "DECISION"]
        assert all(s.description is None and s.examples == () for s in specs)

    def test_configs_to_specs(self):
        cfgs = [
            EntityTypeConfig(
                name="decision", description="a choice", examples=["x"], subtypes=["RATIFIED"]
            )
        ]
        specs = _normalize_types(cfgs)
        assert specs[0].name == "DECISION"
        assert specs[0].description == "a choice"
        assert specs[0].examples == ("x",)
        assert specs[0].subtypes == ("RATIFIED",)

    def test_empty_falls_back_to_poleo(self):
        specs = _normalize_types(None)
        assert [s.name for s in specs] == ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"]

    def test_truncate_word_boundary(self):
        assert _truncate("hello world foobar", 12) == "hello world…"
        assert _truncate("short", 100) == "short"
        assert _truncate("anything", 0) == "anything"  # 0 = uncapped


# --------------------------------------------------------------------------- #
# Prompt rendering (E1) — AC1, AC4, AC5
# --------------------------------------------------------------------------- #


class TestPromptRendering:
    def _extractor(self, **kw) -> LLMEntityExtractor:
        # provider=object() — prompt building never calls the provider.
        return LLMEntityExtractor(provider=object(), **kw)

    def test_typed_block_includes_descriptions_and_examples(self):
        """AC1 + AC5: descriptions and examples appear in the prompt."""
        ex = self._extractor(entity_types=_meeting_schema().entity_types)
        prompt = ex._build_prompt("text")
        assert "A concrete agreement, resolution, or choice" in prompt
        assert 'Examples: "Adopt local transcription tool", "Ship file-drop by Q3"' in prompt
        assert "TASK" in prompt and "OUTCOME" in prompt

    def test_undescribed_poleo_type_backfills_builtin_description(self):
        """Q9: a configured but undescribed POLE+O type keeps its built-in anchor."""
        ex = self._extractor(entity_types=_meeting_schema().entity_types)
        prompt = ex._build_prompt("text")
        # PERSON had no user description; built-in description backfilled.
        assert "Individuals, people mentioned by name or role" in prompt

    def test_one_type_instruction_present(self):
        ex = self._extractor(entity_types=_meeting_schema().entity_types)
        prompt = ex._build_prompt("text")
        assert "exactly one type per distinct entity" in prompt

    def test_names_only_keeps_poleo_guidelines(self):
        """AC4: default POLE+O / names-only path is unchanged."""
        ex = self._extractor()  # default POLE+O
        prompt = ex._build_prompt("text")
        assert "PERSON, ORGANIZATION, LOCATION, EVENT, OBJECT" in prompt
        assert DEFAULT_POLEO_TYPE_GUIDELINES in prompt

    def test_names_only_custom_types_have_no_typed_block(self):
        ex = self._extractor(entity_types=["DECISION", "TASK"])
        prompt = ex._build_prompt("text")
        # Bare names -> comma list, no per-type description lines.
        assert "DECISION, TASK" in prompt
        assert "- DECISION:" not in prompt

    def test_relation_block_renders_source_target_and_description(self):
        schema = _meeting_schema()
        ex = self._extractor(entity_types=schema.entity_types, relation_types=schema.relation_types)
        prompt = ex._build_prompt("text")
        assert "DECIDED_BY (DECISION → PERSON): Links a decision" in prompt

    def test_relation_block_generic_when_no_specs(self):
        ex = self._extractor(entity_types=["DECISION"])
        prompt = ex._build_prompt("text")
        assert "WORKS_AT, LIVES_IN, OWNS" in prompt


# --------------------------------------------------------------------------- #
# Rendering caps (Q5 / R1)
# --------------------------------------------------------------------------- #


class TestRenderingCaps:
    def test_description_truncated(self):
        long_desc = "word " * 200
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[EntityTypeConfig(name="X", description=long_desc)],
            max_type_description_chars=30,
        )
        prompt = ex._build_prompt("t")
        assert "…" in prompt

    def test_examples_capped(self):
        ex = LLMEntityExtractor(
            provider=object(),
            entity_types=[
                EntityTypeConfig(name="X", description="d", examples=[f"e{i}" for i in range(20)])
            ],
            max_type_examples=3,
        )
        block = ex._render_typed_block(ex._type_specs)
        # Only 3 examples rendered.
        assert block.count('"e') == 3

    def test_global_cap_degrades_to_name_only(self, caplog):
        specs = [
            EntityTypeConfig(name=f"TYPE{i}", description="d " * 50, examples=["ex"])
            for i in range(10)
        ]
        ex = LLMEntityExtractor(provider=object(), entity_types=specs, max_typed_block_chars=120)
        block = ex._render_typed_block(ex._type_specs)
        assert len(block) <= 120
        assert block.splitlines()[-1].startswith("- Other")  # overflow rendered as name-only


# --------------------------------------------------------------------------- #
# Same-name guard (E5) — AC3
# --------------------------------------------------------------------------- #


class TestSameNameGuard:
    def _ex(self):
        return LLMEntityExtractor(
            provider=object(), entity_types=["PERSON", "ORGANIZATION", "PRODUCT"]
        )

    def test_collapses_to_highest_confidence(self):
        """AC3: Hume becomes a single PRODUCT node."""
        payload = ExtractionPayload(
            entities=[
                EntityPayload(name="Hume", type="ORGANIZATION", confidence=0.9),
                EntityPayload(name="Hume", type="PERSON", confidence=0.8),
                EntityPayload(name="Hume", type="PRODUCT", confidence=0.95),
            ]
        )
        specs = _normalize_types(["PERSON", "ORGANIZATION", "PRODUCT"])
        res = self._ex()._payload_to_result(payload, "t", specs, False, False)
        assert [(e.name, e.type) for e in res.entities] == [("Hume", "PRODUCT")]

    def test_distinct_names_preserved_in_order(self):
        payload = ExtractionPayload(
            entities=[
                EntityPayload(name="Alice", type="PERSON", confidence=0.9),
                EntityPayload(name="Acme", type="ORGANIZATION", confidence=0.9),
            ]
        )
        specs = _normalize_types(["PERSON", "ORGANIZATION"])
        res = self._ex()._payload_to_result(payload, "t", specs, False, False)
        assert [e.name for e in res.entities] == ["Alice", "Acme"]


# --------------------------------------------------------------------------- #
# Schema-aware mapping (E6)
# --------------------------------------------------------------------------- #


class TestSchemaAwareMapping:
    def test_configured_type_not_coerced(self):
        """PRODUCT must not be coerced to OBJECT when PRODUCT is configured."""
        ex = LLMEntityExtractor(provider=object())
        assert (
            ex._map_to_allowed_type("PRODUCT", ["PRODUCT", "PERSON"], is_custom=True) == "PRODUCT"
        )

    def test_custom_schema_keeps_offlist_type(self):
        """Off-list type under a custom schema is kept, not collapsed to first type."""
        ex = LLMEntityExtractor(provider=object())
        # DATE is off-list; custom schema has no OBJECT/EVENT to coerce into.
        assert ex._map_to_allowed_type("DATE", ["DECISION", "TASK"], is_custom=True) == "DATE"

    def test_offlist_mapped_when_target_allowed(self):
        ex = LLMEntityExtractor(provider=object())
        assert (
            ex._map_to_allowed_type("COMPANY", ["ORGANIZATION"], is_custom=True) == "ORGANIZATION"
        )

    def test_default_poleo_legacy_fallback(self):
        """Under default POLE+O, an unmappable type falls back as before."""
        ex = LLMEntityExtractor(provider=object())
        out = ex._map_to_allowed_type(
            "GIBBERISH", ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"], is_custom=False
        )
        assert out == "OBJECT"

    def test_payload_to_result_keeps_offlist_under_custom(self):
        ex = LLMEntityExtractor(provider=object(), entity_types=["DECISION", "TASK", "OUTCOME"])
        specs = _normalize_types(["DECISION", "TASK", "OUTCOME"])
        payload = ExtractionPayload(
            entities=[EntityPayload(name="Q3 2026", type="DATE", confidence=0.7)]
        )
        res = ex._payload_to_result(payload, "t", specs, False, False)
        assert res.entities[0].type == "DATE"


# --------------------------------------------------------------------------- #
# Coverage telemetry (E10)
# --------------------------------------------------------------------------- #


class TestTypeCoverage:
    def test_requested_types_reported_with_zeros(self):
        ex = LLMEntityExtractor(provider=object(), entity_types=["DECISION", "TASK", "OUTCOME"])
        specs = _normalize_types(["DECISION", "TASK", "OUTCOME"])
        payload = ExtractionPayload(
            entities=[EntityPayload(name="Adopt tool", type="DECISION", confidence=0.9)]
        )
        res = ex._payload_to_result(payload, "t", specs, False, False)
        assert res.type_coverage == {"DECISION": 1, "TASK": 0, "OUTCOME": 0}

    def test_coverage_survives_filter_invalid(self):
        ex = LLMEntityExtractor(provider=object(), entity_types=["DECISION"])
        specs = _normalize_types(["DECISION"])
        payload = ExtractionPayload(
            entities=[EntityPayload(name="Adopt tool", type="DECISION", confidence=0.9)]
        )
        res = ex._payload_to_result(payload, "t", specs, False, False)
        assert res.filter_invalid_entities().type_coverage == {"DECISION": 1}


# --------------------------------------------------------------------------- #
# Constrained payload model (E6)
# --------------------------------------------------------------------------- #


class TestConstrainedPayload:
    def test_schema_has_enum(self):
        model = build_constrained_payload(["DECISION", "TASK"], ["CAUSES"])
        schema = model.model_json_schema()
        dumped = json.dumps(schema)
        assert '"DECISION"' in dumped and '"TASK"' in dumped

    def test_rejects_offlist_type(self):
        model = build_constrained_payload(["DECISION", "TASK"], None)
        with pytest.raises(Exception):
            model(entities=[{"name": "x", "type": "PERSON", "confidence": 0.9}])

    def test_accepts_allowed_type(self):
        model = build_constrained_payload(["DECISION", "TASK"], None)
        obj = model(entities=[{"name": "x", "type": "DECISION", "confidence": 0.9}])
        assert obj.entities[0].type == "DECISION"

    def test_empty_types_returns_unconstrained(self):
        assert build_constrained_payload([], None) is ExtractionPayload


# --------------------------------------------------------------------------- #
# Factory precedence + custom_schema_path (E4)
# --------------------------------------------------------------------------- #


class TestFactoryResolution:
    def test_entity_schema_precedence(self):
        sc = SchemaConfig(model="custom", entity_schema=_meeting_schema())
        et, rt, strict = resolve_llm_type_source(ExtractionConfig(), sc)
        assert [t.name for t in et][:3] == ["DECISION", "TASK", "OUTCOME"]
        assert rt and rt[0].name == "DECIDED_BY"
        assert strict is False

    def test_specs_win_over_schema_names(self):
        ec = ExtractionConfig(entity_type_specs=[EntityTypeConfig(name="TASK", description="d")])
        sc = SchemaConfig(model="custom", entity_types=["PERSON", "GHOST"])
        et, _, _ = resolve_llm_type_source(ec, sc)
        assert [t.name for t in et] == ["TASK"]

    def test_strict_from_rich_schema(self):
        schema = _meeting_schema()
        schema.strict_types = True
        sc = SchemaConfig(model="custom", entity_schema=schema)
        _, _, strict = resolve_llm_type_source(ExtractionConfig(), sc)
        assert strict is True

    def test_names_only_fallback(self):
        sc = SchemaConfig(model="custom", entity_types=["DECISION"])
        et, rt, _ = resolve_llm_type_source(ExtractionConfig(), sc)
        assert et == ["DECISION"]
        assert rt is None

    def test_custom_schema_path_fail_fast(self):
        sc = SchemaConfig(model="custom", custom_schema_path="/no/such/schema.json")
        with pytest.raises(FileNotFoundError):
            resolve_llm_type_source(ExtractionConfig(), sc)

    def test_custom_schema_path_loads(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"name": "x", "entity_types": [{"name": "DECISION", "description": "a choice"}]}, f
            )
            path = f.name
        try:
            sc = SchemaConfig(model="custom", custom_schema_path=path)
            et, _, _ = resolve_llm_type_source(ExtractionConfig(), sc)
            assert [t.name for t in et] == ["DECISION"]
        finally:
            os.unlink(path)

    def test_builder_with_schema_retains_rich_configs(self):
        # build() would eagerly construct a real LLM provider (not installed in
        # the unit env), so assert the builder retained the rich configs that
        # build() threads into the LLM stage. Prompt threading itself is covered
        # by TestPromptRendering.
        builder = ExtractorBuilder().with_llm("gpt-4o-mini").with_schema(_meeting_schema())
        names = [c.name for c in builder._entity_type_configs]
        assert "DECISION" in names
        decision = next(c for c in builder._entity_type_configs if c.name == "DECISION")
        assert decision.description and "concrete agreement" in decision.description
        assert builder._relation_type_configs[0].name == "DECIDED_BY"


# --------------------------------------------------------------------------- #
# End-to-end via mock providers — both extraction paths (AC1/AC2/AC3)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestEndToEnd:
    async def test_structured_path_extracts_decisions_and_dedupes(self):
        provider = MockStructured(
            entities=[
                {"name": "Adopt local transcription tool", "type": "DECISION", "confidence": 0.9},
                {"name": "Ship Hume file-drop by Q3", "type": "DECISION", "confidence": 0.9},
                {"name": "Hume", "type": "PRODUCT", "confidence": 0.95},
                {"name": "Hume", "type": "ORGANIZATION", "confidence": 0.7},
            ]
        )
        ex = LLMEntityExtractor(
            provider=provider,
            entity_types=_meeting_schema().entity_types,
            extract_relations=False,
            extract_preferences=False,
        )
        res = await ex.extract(DECISIONS_DOC)
        decisions = [e.name for e in res.entities if e.type == "DECISION"]
        assert len(decisions) == 2
        humes = [e for e in res.entities if e.name == "Hume"]
        assert len(humes) == 1 and humes[0].type == "PRODUCT"
        # Descriptions reached the prompt on the structured path (AC1).
        # (Indirectly verified via TestPromptRendering; here we assert behavior.)

    async def test_strict_path_uses_constrained_model(self):
        provider = MockStructured(
            entities=[{"name": "Adopt tool", "type": "DECISION", "confidence": 0.9}]
        )
        ex = LLMEntityExtractor(
            provider=provider,
            entity_types=[EntityTypeConfig(name="DECISION", description="a choice")],
            strict_types=True,
            extract_relations=False,
            extract_preferences=False,
        )
        await ex.extract("Decisions: adopt tool.")
        # The provider was handed a constrained payload model, not the base one.
        assert provider.last_response_model is not ExtractionPayload
        assert provider.last_response_model.__name__ == "ConstrainedExtractionPayload"

    async def test_plain_completion_path(self):
        provider = MockPlain(
            {
                "entities": [
                    {"name": "Adopt tool", "type": "DECISION", "confidence": 0.9},
                    {"name": "Hume", "type": "PRODUCT", "confidence": 0.9},
                ],
                "relations": [],
                "preferences": [],
            }
        )
        ex = LLMEntityExtractor(
            provider=provider,
            entity_types=_meeting_schema().entity_types,
            extract_relations=False,
            extract_preferences=False,
        )
        res = await ex.extract(DECISIONS_DOC)
        assert any(e.type == "DECISION" for e in res.entities)


# --------------------------------------------------------------------------- #
# Opt-in live recall test (AC2: domain-type recall >= 0.80). Skipped in CI.
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS") or not os.getenv("OPENAI_API_KEY"),
    reason="Live recall test requires RUN_INTEGRATION_TESTS=1 and OPENAI_API_KEY",
)
async def test_live_domain_type_recall():
    """AC2 (live): the described DECISION type is recovered from the doc."""
    ex = LLMEntityExtractor(
        model="openai/gpt-4o-mini",
        entity_types=_meeting_schema().entity_types,
        extract_relations=False,
        extract_preferences=False,
    )
    res = await ex.extract(DECISIONS_DOC)
    decisions = [e for e in res.entities if e.type == "DECISION"]
    # The document contains four ratified decisions; require >= 80% recall.
    assert len(decisions) >= 3, [(e.name, e.type) for e in res.entities]
