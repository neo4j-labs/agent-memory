"""Tests for the GLiNER2.5 extractor.

Everything but the ``slow`` smoke test runs against the ``gliner2_stub``
fixture, so the suite passes in the CI ``test`` job which installs no
extras — and forces the stub even where the real package is installed.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import types
import warnings
from typing import Any

import pytest

from neo4j_agent_memory.extraction.domain_schemas import get_schema
from neo4j_agent_memory.extraction.gliner2_extractor import (
    DEFAULT_GLINER2_5_MODEL,
    GLiNER2Extractor,
    is_gliner2_available,
)
from neo4j_agent_memory.ontology import POLEO_ONTOLOGY, get_template
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    PropertyDef,
    RelationshipDef,
)

from .conftest import (
    StubJointEntity,
    StubJointIEEngine,
    StubJointRelation,
    StubJointResult,
    StubModel,
)

pytestmark = pytest.mark.usefixtures("gliner2_stub")


def _engine(extractor: GLiNER2Extractor) -> StubJointIEEngine:
    """The stub engine behind an extractor (forcing the lazy load)."""
    engine: Any = extractor.joint
    return engine


def _stage(pipeline: Any, index: int) -> Any:
    """The extractor behind a pipeline stage.

    ``ExtractionPipeline`` only wraps a stage in ``ExtractorStage`` when it
    does not already satisfy the ``ExtractionStage`` protocol, which an
    extractor carrying ``name`` + ``extract`` does.
    """
    stage = pipeline.stages[index]
    return getattr(stage, "_extractor", stage)


def _queue(extractor: GLiNER2Extractor, *results: StubJointResult) -> StubJointIEEngine:
    """Queue decoded results on the extractor's engine."""
    engine = _engine(extractor)
    engine.results.extend(results)
    return engine


def _tiny_ontology() -> OntologyDocument:
    """A three-label, one-relation ontology."""
    return OntologyDocument(
        domain=DomainInfo(id="tiny", name="tiny"),
        entity_types=[
            EntityTypeDef(label="person", pole_type="PERSON", description="A human being."),
            EntityTypeDef(label="company", pole_type="ORGANIZATION", subtype="COMPANY"),
            EntityTypeDef(label="city", pole_type="LOCATION", subtype="CITY"),
        ],
        relationships=[
            RelationshipDef(
                type="WORKS_AT",
                source="person",
                target="company",
                description="Employment.",
                unique_source=True,
            )
        ],
    )


# -----------------------------------------------------------------------------
# Construction and identity
# -----------------------------------------------------------------------------


class TestConstruction:
    """Ontology coercion, identity and the legacy-model guard."""

    def test_defaults_to_poleo(self) -> None:
        extractor = GLiNER2Extractor()
        assert extractor.ontology is POLEO_ONTOLOGY
        assert extractor.model_id == DEFAULT_GLINER2_5_MODEL

    def test_name_version_and_model_id(self) -> None:
        extractor = GLiNER2Extractor(model="fastino/gliner2.5-small-v1")
        assert extractor.name == "gliner2"
        assert extractor.model_id == "fastino/gliner2.5-small-v1"
        assert isinstance(extractor.version, str) and extractor.version

    def test_ontology_document_passes_through(self) -> None:
        doc = _tiny_ontology()
        assert GLiNER2Extractor(ontology=doc).ontology is doc

    def test_domain_schema_is_converted(self) -> None:
        extractor = GLiNER2Extractor(ontology=get_schema("podcast"))
        assert extractor.ontology.labels() == list(get_schema("podcast").entity_types)

    def test_entity_schema_config_is_converted(self) -> None:
        from neo4j_agent_memory.schema.models import EntitySchemaConfig, EntityTypeConfig

        config = EntitySchemaConfig(
            name="custom",
            entity_types=[EntityTypeConfig(name="PERSON", subtypes=["SUSPECT"])],
        )
        labels = [label.lower() for label in GLiNER2Extractor(ontology=config).ontology.labels()]
        assert "suspect" in labels or "person" in labels

    def test_entity_labels_list_builds_ad_hoc_ontology(self) -> None:
        extractor = GLiNER2Extractor(entity_labels=["person", "vehicle"])
        assert extractor.ontology.labels() == ["person", "vehicle"]
        assert extractor.ontology.label_map()["vehicle"] == ("OBJECT", "VEHICLE")
        assert extractor.ontology.relationships == []

    def test_entity_labels_dict_keeps_descriptions(self) -> None:
        extractor = GLiNER2Extractor(entity_labels={"person": "A human being."})
        assert extractor.ontology.entity_types[0].description == "A human being."

    def test_ontology_wins_over_entity_labels(self) -> None:
        doc = _tiny_ontology()
        extractor = GLiNER2Extractor(ontology=doc, entity_labels=["ignored"])
        assert extractor.ontology is doc

    def test_unconvertible_ontology_raises(self) -> None:
        with pytest.raises(TypeError, match="OntologyDocument"):
            GLiNER2Extractor(ontology=object())  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "model",
        [
            "urchade/gliner_medium-v2.1",
            "gliner-community/gliner_medium-v2.5",
            "numind/NuNER_Zero",
        ],
    )
    def test_legacy_model_rejected_before_download(self, model: str) -> None:
        with pytest.raises(ValueError, match="GLiNER v1 checkpoint"):
            GLiNER2Extractor(model=model)

    def test_for_schema_uses_builtin_template(self) -> None:
        extractor = GLiNER2Extractor.for_schema("news")
        assert extractor.ontology == get_template("news")
        assert extractor.ontology.relationships

    def test_for_schema_forwards_kwargs(self) -> None:
        extractor = GLiNER2Extractor.for_schema("news", threshold=0.7, device="mps")
        assert (extractor.threshold, extractor.device) == (0.7, "mps")

    def test_for_poleo_and_for_ontology(self) -> None:
        assert GLiNER2Extractor.for_poleo().ontology is POLEO_ONTOLOGY
        doc = _tiny_ontology()
        assert GLiNER2Extractor.for_ontology(doc).ontology is doc

    def test_is_gliner2_available_sees_the_stub(self) -> None:
        assert is_gliner2_available() is True


class TestLoading:
    """The two model handles and how they are configured."""

    def test_device_is_passed_as_map_location(self) -> None:
        extractor = GLiNER2Extractor(device="cuda", quantize=True, compile=True)
        model: Any = extractor.model
        assert model.load_kwargs == {
            "map_location": "cuda",
            "quantize": True,
            "compile": True,
        }
        assert model.model_id == DEFAULT_GLINER2_5_MODEL

    def test_model_is_loaded_once(self) -> None:
        extractor = GLiNER2Extractor()
        assert extractor.model is extractor.model

    def test_engine_shares_the_loaded_weights(self) -> None:
        extractor = GLiNER2Extractor(device="mps")
        engine = _engine(extractor)
        assert engine.model is extractor.model
        assert engine.device == "mps"
        assert extractor.joint is engine

    @pytest.mark.asyncio
    async def test_concurrent_extracts_load_the_checkpoint_once(
        self, gliner2_stub: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Four racing extract() calls share one model and one engine.

        Each extract() hops into the default executor, so without a lock all
        four threads see ``self._model is None``, download the checkpoint and
        build their own JointIE engine — and the attribute pass could then run
        on different weights than the decode.
        """
        calls: list[str] = []
        lock = threading.Lock()

        class SlowAutoExtractor:
            @classmethod
            def from_pretrained(cls, model_id: str, **kwargs: Any) -> Any:
                with lock:
                    calls.append(model_id)
                # Wide enough for the other three threads to reach the guard.
                time.sleep(0.2)
                return StubModel(model_id, **kwargs)

        monkeypatch.setattr(gliner2_stub, "AutoExtractor", SlowAutoExtractor)
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())

        await asyncio.gather(*(extractor.extract(f"text {i}") for i in range(4)))

        assert len(calls) == 1, f"loaded the checkpoint {len(calls)} times"
        assert len(StubJointIEEngine.instances) == 1
        assert extractor.joint.model is extractor.model

    def test_missing_gliner2_explains_the_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both first-touch import paths name the extra to install.

        The constructor imports nothing, so ``model`` and ``joint`` are where a
        missing ``gliner2`` is first noticed; a bare ``ModuleNotFoundError``
        from deep inside JointIE would not tell anybody what to install.
        """
        import neo4j_agent_memory.extraction.gliner2_extractor as module

        def no_gliner2(name: str) -> Any:
            raise ImportError(f"No module named {name!r}")

        monkeypatch.setattr(module.importlib, "import_module", no_gliner2)

        with pytest.raises(ImportError, match=r"neo4j-agent-memory\[gliner2\]"):
            _ = GLiNER2Extractor().model
        with pytest.raises(ImportError, match=r"neo4j-agent-memory\[gliner2\]"):
            _ = GLiNER2Extractor().joint


class TestWindowingArguments:
    """``max_words`` / ``chunk_overlap`` are checked in the constructor."""

    @pytest.mark.parametrize(
        ("max_words", "chunk_overlap", "match"),
        [
            (0, 0, "max_words must be greater than 0"),
            (-1, 0, "max_words must be greater than 0"),
            (100, -1, "chunk_overlap must be non-negative"),
            (64, 64, "chunk_overlap must be smaller than max_words"),
            (10, 64, "chunk_overlap must be smaller than max_words"),
        ],
    )
    def test_impossible_windows_are_rejected_at_construction(
        self, max_words: int, chunk_overlap: int, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            GLiNER2Extractor(max_words=max_words, chunk_overlap=chunk_overlap)

    def test_valid_window_is_accepted(self) -> None:
        extractor = GLiNER2Extractor(max_words=64, chunk_overlap=63)
        assert (extractor.max_words, extractor.chunk_overlap) == (64, 63)


# -----------------------------------------------------------------------------
# Schema compilation
# -----------------------------------------------------------------------------


class TestSchemaCompilation:
    """What reaches ``JointSchema``, and how often it is rebuilt."""

    def test_entities_and_relations_are_declared(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        schema: Any = extractor._schema_for(None, True)
        assert schema.entity_names == ["person", "company", "city"]
        assert schema.relation_names == ["WORKS_AT"]
        name, head, tail, description, kwargs = schema.relations[0]
        assert (head, tail, description) == (["person"], ["company"], "Employment.")
        assert kwargs["unique_head"] is True
        assert schema.no_self_loop_calls == ["WORKS_AT"]

    def test_symmetric_is_never_passed(self) -> None:
        extractor = GLiNER2Extractor(ontology=get_template("poleo"))
        schema: Any = extractor._schema_for(None, True)
        assert all("symmetric" not in kwargs for *_, kwargs in schema.relations)

    def test_label_subset_prunes_relations(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        schema: Any = extractor._schema_for(["person", "city"], True)
        assert schema.entity_names == ["person", "city"]
        assert schema.relation_names == []

    def test_relations_can_be_compiled_out(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        schema: Any = extractor._schema_for(None, False)
        assert schema.relation_names == []

    def test_schema_is_cached_per_label_subset(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        first = extractor._schema_for(None, True)
        assert extractor._schema_for(None, True) is first
        assert extractor._schema_for(["person"], True) is not first
        assert extractor._schema_for(None, False) is not first

    def test_unknown_entity_type_filter_keeps_every_label(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        assert extractor._labels_for(["unheard-of"]) is None

    def test_entity_type_filter_accepts_pole_types(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        assert extractor._labels_for(["ORGANIZATION"]) == ["company"]


# -----------------------------------------------------------------------------
# Result conversion
# -----------------------------------------------------------------------------


class TestExtract:
    """Field-by-field conversion of a ``JointResult``."""

    @pytest.mark.asyncio
    async def test_empty_text_short_circuits(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        result = await extractor.extract("   ")
        assert result.entities == [] and result.relations == []

    @pytest.mark.asyncio
    async def test_entity_fields(self) -> None:
        text = "Ada Lovelace works at Analytical Engines in London."
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), context_window=5)
        _queue(
            extractor,
            StubJointResult(
                text=text,
                entities=[
                    StubJointEntity("e1", "person", "Ada Lovelace", 0, 12, 0.91, 0, False),
                    StubJointEntity("e2", "company", "Analytical Engines", 22, 40, None, 0, True),
                ],
            ),
        )

        result = await extractor.extract(text)

        person, company = result.entities
        assert (person.id, person.name, person.type, person.subtype) == (
            "e1",
            "Ada Lovelace",
            "PERSON",
            None,
        )
        assert (person.start_pos, person.end_pos) == (0, 12)
        assert person.confidence == pytest.approx(0.91)
        assert person.extractor == "gliner2"
        assert person.attributes["gliner2_label"] == "person"
        assert person.attributes["rescued"] is False
        assert person.context == text[0:17]
        # A missing confidence means "certain", not "zero".
        assert company.confidence == 1.0
        assert (company.type, company.subtype) == ("ORGANIZATION", "COMPANY")
        assert company.attributes["rescued"] is True
        assert text[person.start_pos : person.end_pos] == person.name

    @pytest.mark.asyncio
    async def test_relations_carry_endpoint_ids(self) -> None:
        text = "Ada works at Analytical Engines."
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(
            extractor,
            StubJointResult(
                text=text,
                entities=[
                    StubJointEntity("e1", "person", "Ada", 0, 3, 0.9),
                    StubJointEntity("e2", "company", "Analytical Engines", 13, 31, 0.8),
                ],
                relations=[StubJointRelation("works at", "e1", "e2", 0.77, True)],
            ),
        )

        result = await extractor.extract(text)

        relation = result.relations[0]
        assert relation.relation_type == "WORKS_AT"
        assert (relation.source, relation.target) == ("Ada", "Analytical Engines")
        assert (relation.source_id, relation.target_id) == ("e1", "e2")
        assert relation.confidence == pytest.approx(0.77)
        assert relation.derived is True

    @pytest.mark.asyncio
    async def test_relation_with_filtered_endpoint_is_dropped(self) -> None:
        text = "Ada works at Analytical Engines."
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(
            extractor,
            StubJointResult(
                text=text,
                entities=[
                    StubJointEntity("e1", "person", "Ada", 0, 3, 0.9),
                    StubJointEntity("e2", "company", "Analytical Engines", 13, 31, 0.8),
                ],
                relations=[StubJointRelation("WORKS_AT", "e1", "e2", 0.9)],
            ),
        )

        result = await extractor.extract(text, entity_types=["PERSON"])

        assert [e.name for e in result.entities] == ["Ada"]
        assert result.relations == []

    @pytest.mark.asyncio
    async def test_relation_threshold_filters(self) -> None:
        text = "Ada works at Analytical Engines."
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), relation_threshold=0.8)
        _queue(
            extractor,
            StubJointResult(
                text=text,
                entities=[
                    StubJointEntity("e1", "person", "Ada", 0, 3),
                    StubJointEntity("e2", "company", "Analytical Engines", 13, 31),
                ],
                relations=[StubJointRelation("WORKS_AT", "e1", "e2", 0.5)],
            ),
        )
        assert (await extractor.extract(text)).relations == []

    @pytest.mark.asyncio
    async def test_relations_skipped_when_disabled_or_undeclared(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        assert extractor._wants_relations(True) is True
        assert extractor._wants_relations(False) is False

        off = GLiNER2Extractor(ontology=_tiny_ontology(), extract_relations=False)
        assert off._wants_relations(True) is False

        no_relations = GLiNER2Extractor(entity_labels=["person"])
        assert no_relations._wants_relations(True) is False

    @pytest.mark.asyncio
    async def test_infeasible_result_warns(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(extractor, StubJointResult(text="Ada.", feasible=False))

        with pytest.warns(RuntimeWarning, match="NOT the same as 'no facts"):
            result = await extractor.extract("Ada.")

        assert result.entities == []

    @pytest.mark.asyncio
    async def test_feasible_result_does_not_warn(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(extractor, StubJointResult(text="Ada."))
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            await extractor.extract("Ada.")


class TestLongInput:
    """Windowing for input past ``max_words``."""

    @pytest.mark.asyncio
    async def test_short_input_uses_extract(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), max_words=10, chunk_overlap=3)
        engine = _engine(extractor)
        await extractor.extract("one two three")
        assert len(engine.extract_calls) == 1 and engine.long_calls == []

    @pytest.mark.asyncio
    async def test_long_input_is_windowed(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), max_words=10, chunk_overlap=3)
        engine = _engine(extractor)
        await extractor.extract(" ".join(["word"] * 40))
        assert engine.extract_calls == []
        _, _, _, chunk_size, chunk_overlap = engine.long_calls[0]
        assert (chunk_size, chunk_overlap) == (10, 3)

    @pytest.mark.asyncio
    async def test_punctuation_is_counted_the_way_gliner2_counts_it(self) -> None:
        """Words are counted with gliner2's splitter, not ``str.split()``.

        gliner2's whitespace splitter emits every punctuation mark as its own
        token, so this input is *past* ``max_words`` for the library while
        ``str.split()`` still sees it as short — the case where the old guard
        skipped windowing and sailed past the relation-recall cliff.
        """
        text = " ".join(["a, b; c."] * 6)
        assert len(text.split()) < 20 < len(re.findall(r"\w+|[^\w\s]", text))

        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), max_words=20, chunk_overlap=5)
        engine = _engine(extractor)

        await extractor.extract(text)

        assert engine.extract_calls == [], "punctuation-heavy input should be windowed"
        assert [call[0] for call in engine.long_calls] == [text]

    @pytest.mark.asyncio
    async def test_windowed_results_never_report_infeasibility(self) -> None:
        """``extract_long`` cannot surface ``feasible=False``.

        gliner2's ``extract_long_text`` merges its chunk fragments into a fresh
        ``JointResult`` and drops the per-chunk flag, so the windowed path is
        outside what the infeasibility warning can cover. The stub mirrors that,
        which keeps a queued ``feasible=False`` from making a test pass
        vacuously here.
        """
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), max_words=5, chunk_overlap=2)
        _queue(extractor, StubJointResult(text="x", feasible=False))

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            await extractor.extract(" ".join(["word"] * 20))


class TestExtractBatch:
    """Batched decoding, slicing and progress reporting."""

    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        assert await GLiNER2Extractor().extract_batch([]) == []

    @pytest.mark.asyncio
    async def test_batches_are_sliced_and_progress_reported(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        engine = _engine(extractor)
        texts = [f"text {i}" for i in range(5)]
        seen: list[tuple[int, int]] = []

        results = await extractor.extract_batch(
            texts, batch_size=2, on_progress=lambda c, t: seen.append((c, t))
        )

        assert [r.source_text for r in results] == texts
        assert [len(batch) for batch, _, _ in engine.batch_calls] == [2, 2, 1]
        assert seen == [(2, 5), (4, 5), (5, 5)]

    @pytest.mark.asyncio
    async def test_one_schema_per_text_is_passed(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        engine = _engine(extractor)
        await extractor.extract_batch(["a", "b"], batch_size=8)
        _, schemas, _ = engine.batch_calls[0]
        assert len(schemas) == 2 and schemas[0] is schemas[1]

    @pytest.mark.asyncio
    async def test_long_items_fall_back_to_windowing(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), max_words=5, chunk_overlap=2)
        engine = _engine(extractor)
        long_text = " ".join(["word"] * 20)

        results = await extractor.extract_batch(["short one", long_text, "short two"])

        assert [r.source_text for r in results] == ["short one", long_text, "short two"]
        assert [batch for batch, _, _ in engine.batch_calls] == [["short one", "short two"]]
        assert [call[0] for call in engine.long_calls] == [long_text]

    @pytest.mark.asyncio
    async def test_relations_can_be_skipped_per_call(self) -> None:
        """``extract_batch`` takes the same ``extract_relations`` as ``extract``."""
        text = "Ada works at Analytical Engines."
        entities = [
            StubJointEntity("e1", "person", "Ada", 0, 3, 0.9),
            StubJointEntity("e2", "company", "Analytical Engines", 13, 31, 0.8),
        ]
        relations = [StubJointRelation("WORKS_AT", "e1", "e2", 0.9)]

        extractor = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(extractor, StubJointResult(text=text, entities=entities, relations=relations))
        with_relations = await extractor.extract_batch([text])
        assert with_relations[0].relations

        off = GLiNER2Extractor(ontology=_tiny_ontology())
        _queue(off, StubJointResult(text=text, entities=entities, relations=relations))
        without = await off.extract_batch([text], extract_relations=False)
        assert without[0].relations == []
        assert [e.name for e in without[0].entities] == ["Ada", "Analytical Engines"]
        # Relations were also compiled out of the schema for that pass.
        engine: Any = off.joint
        _, schemas, _ = engine.batch_calls[0]
        assert schemas[0].relation_names == []

    @pytest.mark.asyncio
    async def test_progress_callback_errors_are_swallowed(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology())

        def boom(completed: int, total: int) -> None:
            raise RuntimeError("callback blew up")

        results = await extractor.extract_batch(["a"], on_progress=boom)
        assert len(results) == 1


class TestAttributePass:
    """The opt-in span-attribute join."""

    def _ontology_with_enum(self) -> OntologyDocument:
        return OntologyDocument(
            domain=DomainInfo(id="tiny", name="tiny"),
            entity_types=[
                EntityTypeDef(
                    label="person",
                    pole_type="PERSON",
                    properties=[PropertyDef(name="role", type="string", enum=["founder", "hire"])],
                )
            ],
        )

    @pytest.mark.asyncio
    async def test_disabled_by_default(self) -> None:
        extractor = GLiNER2Extractor(ontology=self._ontology_with_enum())
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )
        await extractor.extract("Ada.")
        model: Any = extractor.model
        assert model.extract_calls == []

    @pytest.mark.asyncio
    async def test_attributes_join_on_offset_within_tolerance(self) -> None:
        extractor = GLiNER2Extractor(ontology=self._ontology_with_enum(), extract_attributes=True)
        model: Any = extractor.model
        model.attribute_result = {
            "entities": {
                "person": [
                    {
                        "text": "Ada",
                        "confidence": 0.9,
                        "start": 1,
                        "end": 4,
                        "role": {"label": "founder", "confidence": 0.8},
                    }
                ]
            }
        }
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )

        result = await extractor.extract("Ada.")

        assert result.entities[0].attributes["role"] == {"label": "founder", "confidence": 0.8}
        assert "text" not in result.entities[0].attributes

    @pytest.mark.asyncio
    async def test_offset_outside_tolerance_is_not_joined(self) -> None:
        extractor = GLiNER2Extractor(
            ontology=self._ontology_with_enum(), extract_attributes=True, attribute_tolerance=1
        )
        model: Any = extractor.model
        model.attribute_result = {
            "entities": {"person": [{"text": "Ada", "start": 40, "end": 43, "role": "founder"}]}
        }
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )

        result = await extractor.extract("Ada.")
        assert "role" not in result.entities[0].attributes

    @pytest.mark.asyncio
    async def test_nearest_span_wins_not_the_first(self) -> None:
        """Two candidates in range: the closer offset is this entity's."""
        extractor = GLiNER2Extractor(
            ontology=self._ontology_with_enum(), extract_attributes=True, attribute_tolerance=5
        )
        model: Any = extractor.model
        model.attribute_result = {
            "entities": {
                "person": [
                    {"text": "Grace", "start": 4, "end": 9, "role": "hire"},
                    {"text": "Ada", "start": 1, "end": 4, "role": "founder"},
                ]
            }
        }
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )

        result = await extractor.extract("Ada.")

        # Both spans are within tolerance=5 of offset 0; start=1 is nearer.
        assert result.entities[0].attributes["role"] == "founder"

    @pytest.mark.asyncio
    async def test_entity_threshold_reaches_the_attribute_pass(self) -> None:
        """The facade's ``extract`` defaults to 0.5 unless we pass ours."""
        extractor = GLiNER2Extractor(
            ontology=self._ontology_with_enum(), extract_attributes=True, threshold=0.8
        )
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )

        await extractor.extract("Ada.")

        model: Any = extractor.model
        _, _, kwargs = model.extract_calls[0]
        assert kwargs["threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_no_enum_properties_skips_the_second_pass(self) -> None:
        extractor = GLiNER2Extractor(ontology=_tiny_ontology(), extract_attributes=True)
        _queue(
            extractor,
            StubJointResult(text="Ada.", entities=[StubJointEntity("e1", "person", "Ada", 0, 3)]),
        )
        await extractor.extract("Ada.")
        model: Any = extractor.model
        assert model.extract_calls == []


# -----------------------------------------------------------------------------
# Real inference
# -----------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("RUN_SLOW_TESTS") != "1",
    reason="downloads a checkpoint and runs inference; set RUN_SLOW_TESTS=1 to run",
)
class TestRealInference:
    """One end-to-end pass on a real checkpoint (downloads ~296 MB).

    ``make test-unit`` must stay offline, and it does not deselect markers — so
    the ``slow`` marker alone is not enough here: in any ``--all-extras``
    environment this class would otherwise download the model mid-suite. Run it
    with ``RUN_SLOW_TESTS=1 pytest tests/unit/test_gliner2_extractor.py -m slow``.
    """

    @pytest.mark.asyncio
    async def test_offsets_and_endpoints_hold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The stub is installed by the module-level fixture through this same
        # monkeypatch instance; drop it first so the real package is imported.
        monkeypatch.undo()
        pytest.importorskip("gliner2")

        extractor = GLiNER2Extractor.for_ontology(
            _tiny_ontology(), model="fastino/gliner2.5-small-v1"
        )
        text = "Ada Lovelace works at Analytical Engines, a company based in London."

        result = await extractor.extract(text)

        assert result.entities, "expected at least one entity from a real checkpoint"
        for entity in result.entities:
            assert entity.start_pos is not None and entity.end_pos is not None
            assert text[entity.start_pos : entity.end_pos] == entity.name
        ids = {entity.id for entity in result.entities}
        for relation in result.relations:
            assert relation.source_id in ids and relation.target_id in ids


# -----------------------------------------------------------------------------
# Factory and builder wiring
# -----------------------------------------------------------------------------


class TestFactoryWiring:
    """``create_gliner_extractor`` / ``ExtractorBuilder`` build this extractor."""

    def test_config_settings_reach_the_extractor(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_gliner_extractor

        config = ExtractionConfig(
            gliner_model="fastino/gliner2.5-small-v1",
            gliner_threshold=0.42,
            gliner_relation_threshold=0.3,
            gliner_device="mps",
            gliner_quantize=True,
            gliner_max_words=256,
            gliner_chunk_overlap=16,
            gliner_overlap_policy="nested",
            gliner_extract_attributes=True,
        )

        extractor = create_gliner_extractor(config)

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.model_id == "fastino/gliner2.5-small-v1"
        assert (extractor.threshold, extractor.relation_threshold) == (0.42, 0.3)
        assert (extractor.device, extractor.quantize) == ("mps", True)
        assert (extractor.max_words, extractor.chunk_overlap) == (256, 16)
        assert extractor.overlap_policy == "nested"
        assert extractor.extract_attributes is True

    def test_gliner_schema_selects_the_template(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_gliner_extractor

        extractor = create_gliner_extractor(ExtractionConfig(gliner_schema="news"))

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology == get_template("news")

    def test_ontology_argument_is_used_when_no_schema_is_named(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_gliner_extractor

        doc = _tiny_ontology()
        extractor = create_gliner_extractor(ExtractionConfig(), ontology=doc)

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology is doc

    def test_default_is_the_builtin_poleo_ontology(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_gliner_extractor

        extractor = create_gliner_extractor(ExtractionConfig())

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology is POLEO_ONTOLOGY

    def test_unknown_schema_falls_back_to_the_default(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_gliner_extractor

        extractor = create_gliner_extractor(ExtractionConfig(gliner_schema="nope"))

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology is POLEO_ONTOLOGY

    def test_builder_with_ontology(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder

        doc = _tiny_ontology()
        extractor = ExtractorBuilder().with_gliner().with_ontology(doc).build()

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology is doc

    def test_with_ontology_does_not_enable_a_gliner_stage(self) -> None:
        """Naming an ontology says *what* to extract, not *how*."""
        from neo4j_agent_memory.extraction.base import NoOpExtractor
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder
        from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

        builder = ExtractorBuilder().with_ontology(_tiny_ontology())
        assert builder._enable_gliner is False
        assert isinstance(builder.build(), NoOpExtractor)

        llm_only = ExtractorBuilder().with_ontology(_tiny_ontology()).with_llm().build()
        assert isinstance(llm_only, LLMEntityExtractor)

    def test_builder_with_gliner_defaults_to_the_new_model(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder

        extractor = ExtractorBuilder().with_gliner().build()

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.model_id == DEFAULT_GLINER2_5_MODEL

    def test_builder_with_gliner_schema(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder

        extractor = (
            ExtractorBuilder().with_gliner_schema("podcast", relation_threshold=0.25).build()
        )

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology == get_template("podcast")
        assert extractor.relation_threshold == 0.25

    def test_builder_with_schema_converts_to_an_ontology(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder
        from neo4j_agent_memory.schema.models import EntitySchemaConfig, EntityTypeConfig

        config = EntitySchemaConfig(
            name="custom",
            entity_types=[EntityTypeConfig(name="PERSON", subtypes=["SUSPECT"])],
        )
        builder = ExtractorBuilder().with_gliner().with_schema(config)
        extractor = builder.build()

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.ontology.entity_types
        # The flat type set the LLM stage works from is still derived.
        assert builder._entity_types == ["PERSON"]

    def test_builder_extract_relations_reaches_the_extractor(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder

        extractor = ExtractorBuilder().with_gliner().extract_relations(False).build()

        assert isinstance(extractor, GLiNER2Extractor)
        assert extractor.extract_relations is False


class TestPipelineOntologyIsResolvedOnce:
    """One ontology for every stage *and* for the pipeline's validation."""

    def _config(self, **kwargs: Any) -> Any:
        from neo4j_agent_memory.config.settings import ExtractionConfig

        defaults: dict[str, Any] = {
            "enable_spacy": False,
            "enable_gliner": True,
            "enable_llm_fallback": False,
        }
        defaults.update(kwargs)
        return ExtractionConfig(**defaults)

    def test_gliner_schema_wins_for_stage_and_pipeline(self) -> None:
        """The stage decoded podcast relations; validation must know that.

        ``gliner_schema`` overrides the ``ontology=`` argument for the GLiNER
        stage. Handing the pipeline the *other* ontology made
        ``validate_relations(mode="drop")`` drop every relation the stage had
        just decoded.
        """
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline
        from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline

        pipeline = create_extraction_pipeline(
            self._config(gliner_schema="podcast"), ontology=get_template("poleo")
        )

        assert isinstance(pipeline, ExtractionPipeline)
        assert pipeline.ontology == get_template("podcast")
        stage = _stage(pipeline, 0)
        assert isinstance(stage, GLiNER2Extractor)
        assert stage.ontology is pipeline.ontology

    def test_explicit_ontology_reaches_stage_and_pipeline(self) -> None:
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline

        doc = _tiny_ontology()
        pipeline = create_extraction_pipeline(self._config(), ontology=doc)

        assert pipeline.ontology is doc
        assert _stage(pipeline, 0).ontology is doc

    def test_default_is_poleo_everywhere(self) -> None:
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline

        pipeline = create_extraction_pipeline(self._config())

        assert pipeline.ontology is POLEO_ONTOLOGY
        assert _stage(pipeline, 0).ontology is POLEO_ONTOLOGY

    @pytest.mark.asyncio
    async def test_decoded_relations_survive_the_pipeline(self) -> None:
        """End to end: a podcast relation is not dropped by validation."""
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline

        pipeline = create_extraction_pipeline(
            self._config(gliner_schema="podcast"), ontology=get_template("poleo")
        )
        stage: Any = _stage(pipeline, 0)
        podcast = get_template("podcast")
        relationship = podcast.relationships[0]
        source = podcast.entity_type(relationship.source)
        target = podcast.entity_type(relationship.target)
        assert source is not None and target is not None

        text = "Ada and Acme."
        _queue(
            stage,
            StubJointResult(
                text=text,
                entities=[
                    StubJointEntity("e1", source.label, "Ada", 0, 3, 0.9),
                    StubJointEntity("e2", target.label, "Acme", 8, 12, 0.9),
                ],
                relations=[StubJointRelation(relationship.type, "e1", "e2", 0.9)],
            ),
        )

        result = await pipeline.extract(text)

        assert [r.relation_type for r in result.relations] == [
            relationship.type.upper().replace(" ", "_")
        ]

    def test_missing_gliner2_skips_the_stage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No ``gliner2`` installed: warn and skip, do not build a doomed stage.

        ``GLiNER2Extractor.__init__`` imports nothing, so the old
        ``except ImportError`` around the stage was dead code — the stage was
        added and every later message failed with a raw ``ModuleNotFoundError``.
        """
        import neo4j_agent_memory.extraction.gliner2_extractor as gliner2_module
        from neo4j_agent_memory.extraction.base import NoOpExtractor
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline

        monkeypatch.setattr(gliner2_module, "is_gliner2_available", lambda: False)

        pipeline = create_extraction_pipeline(self._config())

        assert [type(_stage(pipeline, i)) for i in range(len(pipeline.stages))] == [NoOpExtractor]


class TestConfidenceThresholdIsApplied:
    """``confidence_threshold`` is a floor on the merged result, not a no-op."""

    def test_config_threshold_reaches_the_pipeline(self) -> None:
        from neo4j_agent_memory.config.settings import ExtractionConfig
        from neo4j_agent_memory.extraction.factory import create_extraction_pipeline

        pipeline = create_extraction_pipeline(
            ExtractionConfig(enable_spacy=False, enable_llm_fallback=False, gliner_threshold=0.1)
        )
        assert pipeline.min_confidence == 0.5

        strict = create_extraction_pipeline(
            ExtractionConfig(
                enable_spacy=False, enable_llm_fallback=False, confidence_threshold=0.75
            )
        )
        assert strict.min_confidence == 0.75

    def test_builder_threshold_reaches_the_pipeline(self) -> None:
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder
        from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline

        pipeline = (
            ExtractorBuilder().with_gliner().with_llm().with_confidence_threshold(0.7).build()
        )
        assert isinstance(pipeline, ExtractionPipeline)
        assert pipeline.min_confidence == 0.7

    @pytest.mark.asyncio
    async def test_low_confidence_entities_and_their_relations_are_dropped(self) -> None:
        from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractedRelation
        from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline

        class Stub:
            name = "stub"

            async def extract(self, text: str, **kwargs: Any) -> Any:
                from neo4j_agent_memory.extraction.base import ExtractionResult

                return ExtractionResult(
                    entities=[
                        ExtractedEntity(id="e1", name="Ada", type="PERSON", confidence=0.9),
                        ExtractedEntity(id="e2", name="Acme", type="ORGANIZATION", confidence=0.2),
                    ],
                    relations=[
                        ExtractedRelation(
                            source="Ada",
                            target="Acme",
                            source_id="e1",
                            target_id="e2",
                            relation_type="EMPLOYED_BY",
                            confidence=0.95,
                        )
                    ],
                    source_text=text,
                )

        pipeline = ExtractionPipeline(stages=[Stub()], min_confidence=0.5)
        result = await pipeline.extract("Ada works at Acme.")

        assert [e.name for e in result.entities] == ["Ada"]
        # The edge cleared the floor itself but lost an endpoint.
        assert result.relations == []

        kept = await ExtractionPipeline(stages=[Stub()]).extract("Ada works at Acme.")
        assert len(kept.entities) == 2 and len(kept.relations) == 1
