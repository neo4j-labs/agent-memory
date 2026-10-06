"""Tests for extraction pipeline."""

import pytest

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
    NoOpExtractor,
)
from neo4j_agent_memory.extraction.pipeline import (
    ExtractionPipeline,
    ExtractorStage,
    MergeStrategy,
    PipelineResult,
    merge_extraction_results,
)


class MockExtractor:
    """Mock extractor for testing."""

    def __init__(
        self,
        entities: list[ExtractedEntity] | None = None,
        relations: list[ExtractedRelation] | None = None,
        preferences: list[ExtractedPreference] | None = None,
        raise_error: bool = False,
    ):
        self.entities = entities or []
        self.relations = relations or []
        self.preferences = preferences or []
        self.raise_error = raise_error
        self.call_count = 0

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        self.call_count += 1
        if self.raise_error:
            raise RuntimeError("Mock extraction error")
        return ExtractionResult(
            entities=self.entities,
            relations=self.relations,
            preferences=self.preferences,
            source_text=text,
        )


class TestMergeStrategies:
    """Tests for extraction result merging."""

    def test_merge_union_keeps_all_unique(self):
        """Test that UNION strategy keeps all unique entities."""
        results = [
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.8),
                    ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.9),
                ]
            ),
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.7),  # Duplicate
                    ExtractedEntity(name="NYC", type="LOCATION", confidence=0.85),
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.UNION)
        assert len(merged.entities) == 3  # John, Acme, NYC

        # Should keep higher confidence for duplicates
        john = next(e for e in merged.entities if e.name == "John")
        assert john.confidence == 0.8

    def test_merge_intersection_keeps_common(self):
        """Test that INTERSECTION strategy keeps entities found by multiple extractors."""
        results = [
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.8),
                    ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.9),
                ]
            ),
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.7),
                    ExtractedEntity(name="NYC", type="LOCATION", confidence=0.85),
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.INTERSECTION)
        assert len(merged.entities) == 1  # Only John found by both
        assert merged.entities[0].name == "John"

    def test_merge_confidence_keeps_highest(self):
        """Test that CONFIDENCE strategy keeps highest confidence per entity."""
        results = [
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.8),
                ]
            ),
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.95),
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.CONFIDENCE)
        assert len(merged.entities) == 1
        assert merged.entities[0].confidence == 0.95

    def test_a_replaced_copy_keeps_its_mention_id_for_relations(self):
        """The regression: spaCy's id-less copy won the merge, and the confidence
        floor then dropped every GLiNER2.5 relation pointing at the old id.
        """
        from neo4j_agent_memory.extraction.pipeline import _apply_min_confidence

        spacy = ExtractionResult(
            entities=[ExtractedEntity(name="John Smith", type="PERSON", confidence=0.85)]
        )
        gliner = ExtractionResult(
            entities=[
                ExtractedEntity(name="John Smith", type="PERSON", confidence=0.7, id="e0"),
                ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.8, id="e1"),
            ],
            relations=[
                ExtractedRelation(
                    source="John Smith",
                    target="Acme",
                    relation_type="EMPLOYED_BY",
                    confidence=0.9,
                    source_id="e0",
                    target_id="e1",
                )
            ],
        )

        merged = merge_extraction_results([spacy, gliner], MergeStrategy.CONFIDENCE)

        john = next(e for e in merged.entities if e.name == "John Smith")
        assert john.confidence == 0.85  # spaCy's copy won...
        assert john.id == "e0"  # ...and carries GLiNER2.5's mention id
        assert len(_apply_min_confidence(merged, 0.5).relations) == 1

    def test_a_replaced_id_is_remapped_onto_the_survivors_id(self):
        low = ExtractionResult(
            entities=[ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.6, id="a")],
            relations=[
                ExtractedRelation(
                    source="Acme", target="Acme", relation_type="X", source_id="a", target_id="a"
                )
            ],
        )
        high = ExtractionResult(
            entities=[ExtractedEntity(name="Acme", type="ORGANIZATION", confidence=0.9, id="b")]
        )

        merged = merge_extraction_results([low, high], MergeStrategy.CONFIDENCE)

        assert [e.id for e in merged.entities] == ["b"]
        assert (merged.relations[0].source_id, merged.relations[0].target_id) == ("b", "b")

    def test_merge_cascade_fills_gaps(self):
        """Test that CASCADE strategy uses first result and fills gaps."""
        results = [
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.8),
                ]
            ),
            ExtractionResult(
                entities=[
                    ExtractedEntity(name="John", type="PERSON", confidence=0.95),  # Ignored
                    ExtractedEntity(name="NYC", type="LOCATION", confidence=0.85),  # Added
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.CASCADE)
        assert len(merged.entities) == 2

        john = next(e for e in merged.entities if e.name == "John")
        assert john.confidence == 0.8  # From first extractor

    def test_merge_relations_deduplicates(self):
        """Test that relations are deduplicated."""
        results = [
            ExtractionResult(
                relations=[
                    ExtractedRelation(source="John", target="Acme", relation_type="WORKS_AT"),
                ]
            ),
            ExtractionResult(
                relations=[
                    ExtractedRelation(source="John", target="Acme", relation_type="WORKS_AT"),
                    ExtractedRelation(source="John", target="NYC", relation_type="LIVES_IN"),
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.UNION)
        assert len(merged.relations) == 2  # Deduplicated

    def test_merge_preferences_deduplicates(self):
        """Test that preferences are deduplicated."""
        results = [
            ExtractionResult(
                preferences=[
                    ExtractedPreference(category="food", preference="vegetarian"),
                ]
            ),
            ExtractionResult(
                preferences=[
                    ExtractedPreference(category="food", preference="vegetarian"),
                    ExtractedPreference(category="music", preference="jazz"),
                ]
            ),
        ]

        merged = merge_extraction_results(results, MergeStrategy.UNION)
        assert len(merged.preferences) == 2  # Deduplicated


class TestExtractionPipeline:
    """Tests for extraction pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_runs_all_stages(self):
        """Test that pipeline runs all stages."""
        extractor1 = MockExtractor(entities=[ExtractedEntity(name="John", type="PERSON")])
        extractor2 = MockExtractor(entities=[ExtractedEntity(name="NYC", type="LOCATION")])

        pipeline = ExtractionPipeline(
            stages=[extractor1, extractor2],
            merge_strategy=MergeStrategy.UNION,
        )

        result = await pipeline.extract("John lives in NYC")

        assert extractor1.call_count == 1
        assert extractor2.call_count == 1
        assert result.entity_count == 2

    @pytest.mark.asyncio
    async def test_pipeline_stop_on_success(self):
        """Test pipeline stops early when stop_on_success is True."""
        extractor1 = MockExtractor(entities=[ExtractedEntity(name="John", type="PERSON")])
        extractor2 = MockExtractor(entities=[ExtractedEntity(name="NYC", type="LOCATION")])

        pipeline = ExtractionPipeline(
            stages=[extractor1, extractor2],
            merge_strategy=MergeStrategy.FIRST_SUCCESS,
            stop_on_success=True,
        )

        result = await pipeline.extract("John lives in NYC")

        assert extractor1.call_count == 1
        assert extractor2.call_count == 0  # Not called
        assert result.entity_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_fallback_on_error(self):
        """Test that pipeline continues on error when fallback_on_error is True."""
        extractor1 = MockExtractor(raise_error=True)
        extractor2 = MockExtractor(entities=[ExtractedEntity(name="NYC", type="LOCATION")])

        pipeline = ExtractionPipeline(
            stages=[extractor1, extractor2],
            merge_strategy=MergeStrategy.UNION,
            fallback_on_error=True,
        )

        result = await pipeline.extract("Text")

        assert result.entity_count == 1
        assert result.entities[0].name == "NYC"

    @pytest.mark.asyncio
    async def test_pipeline_raises_on_error(self):
        """Test that pipeline raises on error when fallback_on_error is False."""
        extractor1 = MockExtractor(raise_error=True)
        extractor2 = MockExtractor(entities=[ExtractedEntity(name="NYC", type="LOCATION")])

        pipeline = ExtractionPipeline(
            stages=[extractor1, extractor2],
            merge_strategy=MergeStrategy.UNION,
            fallback_on_error=False,
        )

        with pytest.raises(RuntimeError):
            await pipeline.extract("Text")

    @pytest.mark.asyncio
    async def test_pipeline_with_details(self):
        """Test extract_with_details returns stage information."""
        extractor1 = MockExtractor(entities=[ExtractedEntity(name="John", type="PERSON")])
        extractor2 = MockExtractor(entities=[ExtractedEntity(name="NYC", type="LOCATION")])

        pipeline = ExtractionPipeline(
            stages=[extractor1, extractor2],
            merge_strategy=MergeStrategy.UNION,
        )

        result = await pipeline.extract_with_details("Text")

        assert isinstance(result, PipelineResult)
        assert result.stages_run == 2
        assert result.successful_stages == 2
        assert len(result.stage_results) == 2
        # Mocked extractors complete sub-resolution on fast machines, so
        # ``time.time()`` may yield identical start/stop readings — the
        # contract here is "duration was measured at all", not "non-zero".
        assert result.total_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_pipeline_empty_text(self):
        """Test pipeline handles empty text."""
        extractor = MockExtractor(entities=[ExtractedEntity(name="John", type="PERSON")])

        pipeline = ExtractionPipeline(stages=[extractor])

        result = await pipeline.extract("")

        # First stage returns no entities for empty text
        assert extractor.call_count == 1

    @pytest.mark.asyncio
    async def test_pipeline_no_stages(self):
        """Test pipeline with no stages returns empty result."""
        pipeline = ExtractionPipeline(stages=[])
        result = await pipeline.extract("Some text")
        assert result.entity_count == 0


class TestExtractorStage:
    """Tests for ExtractorStage wrapper."""

    def test_extractor_stage_name(self):
        """Test that ExtractorStage gets name from extractor."""
        extractor = MockExtractor()
        stage = ExtractorStage(extractor)
        assert stage.name == "MockExtractor"

    def test_extractor_stage_custom_name(self):
        """Test that ExtractorStage can have custom name."""
        extractor = MockExtractor()
        stage = ExtractorStage(extractor, name="CustomName")
        assert stage.name == "CustomName"

    @pytest.mark.asyncio
    async def test_extractor_stage_delegates(self):
        """Test that ExtractorStage delegates to wrapped extractor."""
        entities = [ExtractedEntity(name="Test", type="OBJECT")]
        extractor = MockExtractor(entities=entities)
        stage = ExtractorStage(extractor)

        result = await stage.extract("text")
        assert result.entity_count == 1
        assert extractor.call_count == 1


class TestNoOpExtractor:
    """Tests for NoOpExtractor."""

    @pytest.mark.asyncio
    async def test_noop_returns_empty(self):
        """Test that NoOpExtractor returns empty results."""
        extractor = NoOpExtractor()
        result = await extractor.extract("Some text")

        assert result.entity_count == 0
        assert result.relation_count == 0
        assert result.preference_count == 0
        assert result.source_text == "Some text"
