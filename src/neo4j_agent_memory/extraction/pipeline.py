"""Multi-stage entity extraction pipeline."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


# Type alias for progress callback: (completed_count, total_count) -> None
ProgressCallback = Callable[[int, int], None]


class MergeStrategy(str, Enum):
    """Strategy for merging results from multiple extractors."""

    UNION = "union"  # Keep all unique entities
    INTERSECTION = "intersection"  # Keep only entities found by multiple extractors
    CONFIDENCE = "confidence"  # Keep highest confidence per entity
    CASCADE = "cascade"  # Use first extractor's results, fill gaps with others
    FIRST_SUCCESS = "first_success"  # Stop at first extractor that returns results


@dataclass
class StageResult:
    """Result from a single extraction stage."""

    stage_name: str
    result: ExtractionResult
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class PipelineResult:
    """Result from the full extraction pipeline."""

    final_result: ExtractionResult
    stage_results: list[StageResult] = field(default_factory=list)
    merge_strategy: MergeStrategy = MergeStrategy.CONFIDENCE
    total_duration_ms: float = 0.0

    @property
    def stages_run(self) -> int:
        """Number of stages that were run."""
        return len(self.stage_results)

    @property
    def successful_stages(self) -> int:
        """Number of stages that succeeded."""
        return sum(1 for s in self.stage_results if s.success)

    def get_entities_by_extractor(self) -> dict[str, list[ExtractedEntity]]:
        """Group final entities by their source extractor."""
        result: dict[str, list[ExtractedEntity]] = {}
        for entity in self.final_result.entities:
            extractor = entity.extractor or "unknown"
            if extractor not in result:
                result[extractor] = []
            result[extractor].append(entity)
        return result


@dataclass
class BatchItemResult:
    """Result for a single item in batch extraction."""

    index: int
    result: ExtractionResult
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class BatchExtractionResult:
    """Result from batch extraction of multiple texts.

    Provides both individual results and aggregate statistics.
    """

    results: list[BatchItemResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def total_items(self) -> int:
        """Total number of items processed."""
        return len(self.results)

    @property
    def successful_items(self) -> int:
        """Number of successfully processed items."""
        return sum(1 for r in self.results if r.success)

    @property
    def failed_items(self) -> int:
        """Number of failed items."""
        return sum(1 for r in self.results if not r.success)

    @property
    def success_rate(self) -> float:
        """Success rate as a fraction (0.0-1.0)."""
        if not self.results:
            return 0.0
        return self.successful_items / len(self.results)

    @property
    def total_entities(self) -> int:
        """Total entities extracted across all successful items."""
        return sum(r.result.entity_count for r in self.results if r.success)

    @property
    def total_relations(self) -> int:
        """Total relations extracted across all successful items."""
        return sum(r.result.relation_count for r in self.results if r.success)

    def get_extraction_results(self) -> list[ExtractionResult]:
        """Get just the ExtractionResult objects (successful only)."""
        return [r.result for r in self.results if r.success]

    def get_all_entities(self) -> list[ExtractedEntity]:
        """Get all entities from successful extractions."""
        entities = []
        for r in self.results:
            if r.success:
                entities.extend(r.result.entities)
        return entities

    def get_errors(self) -> list[tuple[int, str]]:
        """Get list of (index, error_message) for failed items."""
        return [(r.index, r.error or "Unknown error") for r in self.results if not r.success]


@runtime_checkable
class ExtractionStage(Protocol):
    """Protocol for extraction stages in the pipeline."""

    name: str

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Extract entities from text."""
        ...


class ExtractorStage:
    """Wrapper to make any EntityExtractor work as a pipeline stage."""

    def __init__(self, extractor: EntityExtractor, name: str | None = None):
        self._extractor = extractor
        self.name = name or extractor.__class__.__name__

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Delegate to wrapped extractor."""
        return await self._extractor.extract(
            text,
            entity_types=entity_types,
            extract_relations=extract_relations,
            extract_preferences=extract_preferences,
        )


def _entity_key(entity: ExtractedEntity) -> str:
    """Generate a unique key for entity deduplication."""
    # Use normalized name and type for deduplication
    return f"{entity.normalized_name}::{entity.type}"


def _merge_entities_union(
    all_entities: list[list[ExtractedEntity]],
) -> list[ExtractedEntity]:
    """Merge entities keeping all unique ones."""
    seen: dict[str, ExtractedEntity] = {}

    for entities in all_entities:
        for entity in entities:
            key = _entity_key(entity)
            if key not in seen:
                seen[key] = entity
            elif entity.confidence > seen[key].confidence:
                # Keep higher confidence version
                seen[key] = entity

    return list(seen.values())


def _merge_entities_intersection(
    all_entities: list[list[ExtractedEntity]],
) -> list[ExtractedEntity]:
    """Merge entities keeping only those found by multiple extractors."""
    if not all_entities:
        return []

    # Count occurrences
    entity_counts: dict[str, list[ExtractedEntity]] = {}

    for entities in all_entities:
        for entity in entities:
            key = _entity_key(entity)
            if key not in entity_counts:
                entity_counts[key] = []
            entity_counts[key].append(entity)

    # Keep only entities found by more than one extractor
    result = []
    for key, entities in entity_counts.items():
        if len(entities) > 1:
            # Pick highest confidence
            best = max(entities, key=lambda e: e.confidence)
            # Boost confidence for entities found by multiple extractors
            best.confidence = min(1.0, best.confidence * 1.1)
            result.append(best)

    return result


def _merge_entities_confidence(
    all_entities: list[list[ExtractedEntity]],
) -> list[ExtractedEntity]:
    """Merge entities keeping highest confidence per unique entity."""
    best: dict[str, ExtractedEntity] = {}

    for entities in all_entities:
        for entity in entities:
            key = _entity_key(entity)
            if key not in best or entity.confidence > best[key].confidence:
                best[key] = entity

    return list(best.values())


def _merge_entities_cascade(
    all_entities: list[list[ExtractedEntity]],
) -> list[ExtractedEntity]:
    """Merge entities using cascade strategy - fill gaps from later extractors."""
    if not all_entities:
        return []

    # Start with first extractor's results
    result: dict[str, ExtractedEntity] = {}

    # First extractor's entities form the base
    for entity in all_entities[0]:
        key = _entity_key(entity)
        result[key] = entity

    # Later extractors only add new entities
    for entities in all_entities[1:]:
        for entity in entities:
            key = _entity_key(entity)
            if key not in result:
                result[key] = entity

    return list(result.values())


def merge_extraction_results(
    results: list[ExtractionResult],
    strategy: MergeStrategy,
) -> ExtractionResult:
    """
    Merge results from multiple extraction stages.

    Args:
        results: List of extraction results from different stages
        strategy: Strategy for merging entities

    Returns:
        Merged ExtractionResult
    """
    if not results:
        return ExtractionResult()

    if len(results) == 1:
        return results[0]

    # Collect all entities, relations, preferences
    all_entities = [r.entities for r in results if r.entities]
    all_relations = [r.relations for r in results if r.relations]
    all_preferences = [r.preferences for r in results if r.preferences]

    # Merge entities based on strategy
    if strategy == MergeStrategy.UNION:
        merged_entities = _merge_entities_union(all_entities)
    elif strategy == MergeStrategy.INTERSECTION:
        merged_entities = _merge_entities_intersection(all_entities)
    elif strategy == MergeStrategy.CONFIDENCE:
        merged_entities = _merge_entities_confidence(all_entities)
    elif strategy == MergeStrategy.CASCADE:
        merged_entities = _merge_entities_cascade(all_entities)
    elif strategy == MergeStrategy.FIRST_SUCCESS:
        # Should not reach here in normal pipeline flow
        merged_entities = all_entities[0] if all_entities else []
    else:
        merged_entities = _merge_entities_confidence(all_entities)

    # Merge relations (deduplicate by triple)
    relation_keys: set[tuple[str, str, str]] = set()
    merged_relations: list[ExtractedRelation] = []
    for relations in all_relations:
        for rel in relations:
            key = rel.as_triple
            if key not in relation_keys:
                relation_keys.add(key)
                merged_relations.append(rel)

    # Merge preferences (deduplicate by category + preference)
    pref_keys: set[str] = set()
    merged_preferences: list[ExtractedPreference] = []
    for preferences in all_preferences:
        for pref in preferences:
            pref_key = f"{pref.category}::{pref.preference}"
            if pref_key not in pref_keys:
                pref_keys.add(pref_key)
                merged_preferences.append(pref)

    # Use first non-None source text
    source_text = next((r.source_text for r in results if r.source_text), None)

    return ExtractionResult(
        entities=merged_entities,
        relations=merged_relations,
        preferences=merged_preferences,
        source_text=source_text,
    )


class ExtractionPipeline:
    """Multi-stage entity extraction pipeline.

    The pipeline runs multiple extraction stages (spaCy, GLiNER, LLM) and
    merges their results according to a configurable strategy. This allows
    combining fast statistical NER with more accurate LLM-based extraction.

    Example:
        ```python
        pipeline = ExtractionPipeline(
            stages=[
                SpacyEntityExtractor(),
                GLiNEREntityExtractor(),
                LLMEntityExtractor(),
            ],
            merge_strategy=MergeStrategy.CONFIDENCE,
        )

        result = await pipeline.extract("John works at Acme Corp in New York.")
        ```
    """

    def __init__(
        self,
        stages: list[ExtractionStage | EntityExtractor],
        merge_strategy: MergeStrategy = MergeStrategy.CONFIDENCE,
        stop_on_success: bool = False,
        min_entities_for_success: int = 1,
        fallback_on_error: bool = True,
    ):
        """
        Initialize extraction pipeline.

        Args:
            stages: List of extraction stages or extractors to run
            merge_strategy: Strategy for merging results from stages
            stop_on_success: Stop after first stage that returns entities
            min_entities_for_success: Minimum entities to consider a stage successful
            fallback_on_error: Continue to next stage if current stage errors
        """
        # Wrap extractors in stages if needed
        self.stages: list[ExtractionStage] = []
        for stage in stages:
            if isinstance(stage, ExtractionStage):
                self.stages.append(stage)
            else:
                self.stages.append(ExtractorStage(stage))

        self.merge_strategy = merge_strategy
        self.stop_on_success = stop_on_success
        self.min_entities_for_success = min_entities_for_success
        self.fallback_on_error = fallback_on_error

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """
        Run extraction through all stages.

        Args:
            text: Text to extract from
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            extract_preferences: Whether to extract preferences

        Returns:
            Merged ExtractionResult from all stages
        """
        result = await self.extract_with_details(
            text,
            entity_types=entity_types,
            extract_relations=extract_relations,
            extract_preferences=extract_preferences,
        )
        return result.final_result

    async def extract_with_details(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> PipelineResult:
        """
        Run extraction with detailed stage information.

        Returns PipelineResult with both final merged result and per-stage details.
        """
        import time

        start_time = time.time()
        stage_results: list[StageResult] = []
        successful_results: list[ExtractionResult] = []

        for stage in self.stages:
            stage_start = time.time()

            try:
                result = await stage.extract(
                    text,
                    entity_types=entity_types,
                    extract_relations=extract_relations,
                    extract_preferences=extract_preferences,
                )

                stage_duration = (time.time() - stage_start) * 1000

                stage_results.append(
                    StageResult(
                        stage_name=stage.name,
                        result=result,
                        success=True,
                        duration_ms=stage_duration,
                    )
                )

                successful_results.append(result)
                logger.debug(
                    f"Stage '{stage.name}' extracted {result.entity_count} entities "
                    f"in {stage_duration:.1f}ms"
                )

                # Check if we should stop early
                if self.stop_on_success or self.merge_strategy == MergeStrategy.FIRST_SUCCESS:
                    if result.entity_count >= self.min_entities_for_success:
                        logger.debug(f"Stopping pipeline after successful stage '{stage.name}'")
                        break

            except Exception as e:
                stage_duration = (time.time() - stage_start) * 1000
                logger.warning(f"Stage '{stage.name}' failed: {e}")

                stage_results.append(
                    StageResult(
                        stage_name=stage.name,
                        result=ExtractionResult(source_text=text),
                        success=False,
                        error=str(e),
                        duration_ms=stage_duration,
                    )
                )

                if not self.fallback_on_error:
                    raise

        # Merge results
        if self.merge_strategy == MergeStrategy.FIRST_SUCCESS:
            # For FIRST_SUCCESS, just use the first non-empty result
            final_result = next(
                (r for r in successful_results if r.entity_count > 0),
                ExtractionResult(source_text=text),
            )
        else:
            final_result = merge_extraction_results(successful_results, self.merge_strategy)

        total_duration = (time.time() - start_time) * 1000

        return PipelineResult(
            final_result=final_result,
            stage_results=stage_results,
            merge_strategy=self.merge_strategy,
            total_duration_ms=total_duration,
        )

    async def extract_batch(
        self,
        texts: list[str],
        *,
        batch_size: int = 10,
        max_concurrency: int = 5,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
        on_progress: ProgressCallback | None = None,
        fail_fast: bool = False,
    ) -> BatchExtractionResult:
        """
        Extract entities from multiple texts in parallel.

        This method efficiently processes multiple texts using concurrent extraction,
        providing better throughput than sequential extraction. It supports progress
        tracking and configurable error handling.

        Args:
            texts: List of texts to extract from
            batch_size: Number of texts to process in each batch (for memory management)
            max_concurrency: Maximum number of concurrent extractions
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            extract_preferences: Whether to extract preferences
            on_progress: Optional callback called after each text is processed.
                        Receives (completed_count, total_count).
            fail_fast: If True, stop on first error. If False, continue and collect errors.

        Returns:
            BatchExtractionResult containing individual results and aggregate statistics

        Example:
            ```python
            texts = ["John works at Acme.", "Sarah lives in NYC.", ...]

            def progress(done, total):
                print(f"Progress: {done}/{total}")

            result = await pipeline.extract_batch(
                texts,
                max_concurrency=10,
                on_progress=progress
            )

            print(f"Extracted {result.total_entities} entities from {result.successful_items} texts")
            ```
        """
        import time

        start_time = time.time()
        total = len(texts)
        completed = 0
        item_results: list[BatchItemResult] = []

        # Semaphore for concurrency control
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process_one(index: int, text: str) -> BatchItemResult:
            """Process a single text with semaphore control."""
            nonlocal completed

            async with semaphore:
                item_start = time.time()
                try:
                    result = await self.extract(
                        text,
                        entity_types=entity_types,
                        extract_relations=extract_relations,
                        extract_preferences=extract_preferences,
                    )
                    duration = (time.time() - item_start) * 1000
                    return BatchItemResult(
                        index=index,
                        result=result,
                        success=True,
                        duration_ms=duration,
                    )
                except Exception as e:
                    duration = (time.time() - item_start) * 1000
                    logger.warning(f"Batch extraction failed for item {index}: {e}")
                    if fail_fast:
                        raise
                    return BatchItemResult(
                        index=index,
                        result=ExtractionResult(source_text=text),
                        success=False,
                        error=str(e),
                        duration_ms=duration,
                    )

        # Process in batches for memory management
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_texts = texts[batch_start:batch_end]
            batch_indices = range(batch_start, batch_end)

            # Create tasks for this batch
            tasks = [process_one(idx, text) for idx, text in zip(batch_indices, batch_texts)]

            # Run batch concurrently
            batch_results = await asyncio.gather(*tasks, return_exceptions=not fail_fast)

            # Process results
            for result in batch_results:
                if isinstance(result, BaseException):
                    # gather(return_exceptions=True) aggregates failures here.
                    # process_one already converts ordinary Exceptions into failed
                    # BatchItemResults, so any BaseException reaching this point is a
                    # control-flow signal (KeyboardInterrupt/SystemExit/CancelledError)
                    # — re-raise it rather than silently swallowing it.
                    if not isinstance(result, Exception):
                        raise result
                    logger.error(f"Unexpected exception in batch: {result}")
                    continue

                item_results.append(result)
                completed += 1

                # Report progress
                if on_progress:
                    try:
                        on_progress(completed, total)
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")

        # Sort results by index to maintain order
        item_results.sort(key=lambda r: r.index)

        total_duration = (time.time() - start_time) * 1000

        logger.info(
            f"Batch extraction completed: {len(item_results)}/{total} texts, "
            f"{sum(r.result.entity_count for r in item_results if r.success)} entities, "
            f"{total_duration:.1f}ms"
        )

        return BatchExtractionResult(
            results=item_results,
            total_duration_ms=total_duration,
        )

    def add_stage(self, stage: ExtractionStage | EntityExtractor) -> None:
        """Add a stage to the pipeline."""
        if isinstance(stage, ExtractionStage):
            self.stages.append(stage)
        else:
            self.stages.append(ExtractorStage(stage))

    def remove_stage(self, name: str) -> bool:
        """Remove a stage by name. Returns True if found and removed."""
        for i, stage in enumerate(self.stages):
            if stage.name == name:
                self.stages.pop(i)
                return True
        return False

    @property
    def stage_names(self) -> list[str]:
        """Get names of all stages in the pipeline."""
        return [stage.name for stage in self.stages]


class ConditionalPipeline(ExtractionPipeline):
    """Pipeline that selects stages based on conditions.

    Useful for optimizing extraction by skipping expensive stages
    when simpler ones suffice.
    """

    def __init__(
        self,
        stages: list[ExtractionStage | EntityExtractor],
        conditions: dict[str, Callable[[str, ExtractionResult | None], bool]] | None = None,
        **kwargs: Any,
    ):
        """
        Initialize conditional pipeline.

        Args:
            stages: List of extraction stages
            conditions: Dict mapping stage names to condition functions.
                       Function receives (text, previous_result) and returns
                       True if stage should run.
            **kwargs: Additional arguments passed to ExtractionPipeline
        """
        super().__init__(stages, **kwargs)
        self.conditions = conditions or {}

    async def extract_with_details(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> PipelineResult:
        """Run extraction with conditional stage execution."""
        import time

        start_time = time.time()
        stage_results: list[StageResult] = []
        successful_results: list[ExtractionResult] = []
        previous_result: ExtractionResult | None = None

        for stage in self.stages:
            # Check condition
            condition = self.conditions.get(stage.name)
            if condition and not condition(text, previous_result):
                logger.debug(f"Skipping stage '{stage.name}' due to condition")
                continue

            stage_start = time.time()

            try:
                result = await stage.extract(
                    text,
                    entity_types=entity_types,
                    extract_relations=extract_relations,
                    extract_preferences=extract_preferences,
                )

                stage_duration = (time.time() - stage_start) * 1000

                stage_results.append(
                    StageResult(
                        stage_name=stage.name,
                        result=result,
                        success=True,
                        duration_ms=stage_duration,
                    )
                )

                successful_results.append(result)
                previous_result = result

                if self.stop_on_success:
                    if result.entity_count >= self.min_entities_for_success:
                        break

            except Exception as e:
                stage_duration = (time.time() - stage_start) * 1000
                logger.warning(f"Stage '{stage.name}' failed: {e}")

                stage_results.append(
                    StageResult(
                        stage_name=stage.name,
                        result=ExtractionResult(source_text=text),
                        success=False,
                        error=str(e),
                        duration_ms=stage_duration,
                    )
                )

                if not self.fallback_on_error:
                    raise

        final_result = merge_extraction_results(successful_results, self.merge_strategy)
        total_duration = (time.time() - start_time) * 1000

        return PipelineResult(
            final_result=final_result,
            stage_results=stage_results,
            merge_strategy=self.merge_strategy,
            total_duration_ms=total_duration,
        )
