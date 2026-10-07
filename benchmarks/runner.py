"""Benchmark runner for extraction evaluation.

Provides tools for running extraction benchmarks against
test datasets and measuring quality metrics.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.metrics import (
    BenchmarkResult,
    ExpectedEntity,
    ExpectedRelation,
    MatchPolicy,
    RelationMetrics,
    aggregate_relation_metrics,
    calculate_extraction_metrics,
    calculate_relation_metrics,
)


@dataclass
class BenchmarkTestCase:
    """A single test case for extraction evaluation.

    ``expected_relations`` accepts either :class:`ExpectedRelation` objects or
    plain ``(source, relation_type, target)`` tuples; tuples are coerced on
    construction, so the list always holds ``ExpectedRelation`` at runtime.
    """

    id: str
    text: str
    expected_entities: list[ExpectedEntity]
    expected_relations: list[ExpectedRelation | tuple[str, str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Coerce plain triples into ExpectedRelation objects."""
        self.expected_relations = list(self.expected_relation_objects())

    def expected_relation_objects(self) -> list[ExpectedRelation]:
        """Return the expected relations as ExpectedRelation objects."""
        return [ExpectedRelation.from_value(rel) for rel in self.expected_relations]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkTestCase:
        """Create a test case from a dictionary.

        Args:
            data: Dictionary with test case data. ``expected_relations`` may
                hold either 3-element lists or ExpectedRelation mappings.

        Returns:
            TestCase instance
        """
        entities = [
            ExpectedEntity(
                name=e["name"],
                entity_type=e["type"],
                aliases=e.get("aliases", []),
            )
            for e in data.get("expected_entities", [])
        ]

        relations: list[ExpectedRelation | tuple[str, str, str]] = [
            ExpectedRelation.from_value(r) for r in data.get("expected_relations", [])
        ]

        return cls(
            id=data["id"],
            text=data["text"],
            expected_entities=entities,
            expected_relations=relations,
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "text": self.text,
            "expected_entities": [
                {"name": e.name, "type": e.entity_type, "aliases": e.aliases}
                for e in self.expected_entities
            ],
            "expected_relations": [r.to_dict() for r in self.expected_relation_objects()],
            "metadata": self.metadata,
        }


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark runs.

    Attributes:
        name: Configuration name
        warmup_runs: Iterations run before measurement starts
        num_runs: Measured iterations per test case
        timeout_seconds: Per-extraction timeout
        extract_relations: Ask the extractor for relations (default True)
        entity_types: Restrict extraction to these types
        match_policy: How generously extracted items may match gold items
    """

    name: str = "default"
    warmup_runs: int = 1
    num_runs: int = 3
    timeout_seconds: float = 60.0
    extract_relations: bool = True
    entity_types: list[str] | None = None
    match_policy: MatchPolicy = field(default_factory=MatchPolicy)


@dataclass
class BenchmarkSuite:
    """Collection of test cases for benchmarking."""

    name: str
    description: str
    test_cases: list[BenchmarkTestCase]
    config: BenchmarkConfig = field(default_factory=BenchmarkConfig)

    @classmethod
    def from_json_file(cls, path: str | Path) -> BenchmarkSuite:
        """Load a benchmark suite from a JSON file.

        Args:
            path: Path to JSON file

        Returns:
            BenchmarkSuite instance
        """
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        test_cases = [BenchmarkTestCase.from_dict(tc) for tc in data.get("test_cases", [])]

        config_data = data.get("config", {})
        config = BenchmarkConfig(
            name=config_data.get("name", path.stem),
            warmup_runs=config_data.get("warmup_runs", 1),
            num_runs=config_data.get("num_runs", 3),
            timeout_seconds=config_data.get("timeout_seconds", 60.0),
            extract_relations=config_data.get("extract_relations", True),
            entity_types=config_data.get("entity_types"),
            match_policy=MatchPolicy.from_dict(config_data.get("match_policy")),
        )

        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            test_cases=test_cases,
            config=config,
        )

    def to_json_file(self, path: str | Path) -> None:
        """Save benchmark suite to a JSON file.

        Args:
            path: Path to save to
        """
        path = Path(path)
        data = {
            "name": self.name,
            "description": self.description,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "config": {
                "name": self.config.name,
                "warmup_runs": self.config.warmup_runs,
                "num_runs": self.config.num_runs,
                "timeout_seconds": self.config.timeout_seconds,
                "extract_relations": self.config.extract_relations,
                "entity_types": self.config.entity_types,
                "match_policy": self.config.match_policy.to_dict(),
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


@dataclass
class CaseRun:
    """One extraction run over one test case.

    Attributes:
        entities: Extracted (name, type) tuples
        relations: Extracted (source, relation_type, target) triples
        latency_ms: Wall-clock latency of the extraction call
        error: Exception text when the extraction failed, else None
    """

    entities: list[tuple[str, str]] = field(default_factory=list)
    relations: list[tuple[str, str, str]] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None


def _extracted_relations(result: Any) -> list[tuple[str, str, str]]:
    """Read (source, relation_type, target) triples off an extraction result.

    Extractors that do not produce relations simply have no usable
    ``relations`` attribute, which yields an empty list.

    Args:
        result: An ExtractionResult-like object

    Returns:
        List of extracted triples
    """
    raw = getattr(result, "relations", None)
    if not isinstance(raw, (list, tuple)):
        return []

    triples: list[tuple[str, str, str]] = []
    for relation in raw:
        source = getattr(relation, "source", None)
        target = getattr(relation, "target", None)
        relation_type = getattr(relation, "relation_type", None)
        if source is None or target is None or relation_type is None:
            continue
        triples.append((str(source), str(relation_type), str(target)))
    return triples


class BenchmarkRunner:
    """Runs extraction benchmarks and collects metrics.

    Usage:
        runner = BenchmarkRunner(extractor)
        result = await runner.run_suite(suite)
        print(result.metrics.micro_f1, result.relation_f1)
    """

    def __init__(self, extractor: Any) -> None:
        """Initialize the runner with an extractor.

        Args:
            extractor: An extractor instance with an `extract` method
        """
        self.extractor = extractor
        self._extractor_name = getattr(extractor, "__class__", type(extractor)).__name__

    async def run_case(
        self,
        test_case: BenchmarkTestCase,
        config: BenchmarkConfig,
    ) -> CaseRun:
        """Run extraction on a single test case, keeping entities and relations.

        Args:
            test_case: Test case to run
            config: Benchmark configuration

        Returns:
            CaseRun with extracted entities, extracted relations and latency
        """
        start = time.perf_counter()

        try:
            result = await asyncio.wait_for(
                self.extractor.extract(
                    test_case.text,
                    extract_relations=config.extract_relations,
                ),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return CaseRun(
                latency_ms=(time.perf_counter() - start) * 1000,
                error=f"Timeout after {config.timeout_seconds}s",
            )
        except Exception as exc:
            return CaseRun(latency_ms=(time.perf_counter() - start) * 1000, error=str(exc))

        latency_ms = (time.perf_counter() - start) * 1000

        # Extract (name, type) tuples from result
        entities = []
        for entity in result.entities:
            name = entity.name
            etype = getattr(entity, "type", getattr(entity, "entity_type", "UNKNOWN"))
            entities.append((name, etype))

        return CaseRun(
            entities=entities,
            relations=_extracted_relations(result),
            latency_ms=latency_ms,
        )

    async def run_test_case(
        self,
        test_case: BenchmarkTestCase,
        config: BenchmarkConfig,
    ) -> tuple[list[tuple[str, str]], float]:
        """Run extraction on a single test case.

        Thin compatibility wrapper over :meth:`run_case` that drops the
        extracted relations.

        Args:
            test_case: Test case to run
            config: Benchmark configuration

        Returns:
            Tuple of (extracted entities as (name, type) tuples, latency in ms)
        """
        run = await self.run_case(test_case, config)
        return run.entities, run.latency_ms

    async def run_suite(self, suite: BenchmarkSuite) -> BenchmarkResult:
        """Run a complete benchmark suite.

        Args:
            suite: Benchmark suite to run

        Returns:
            BenchmarkResult with aggregate metrics
        """
        config = suite.config
        all_expected: list[ExpectedEntity] = []
        all_extracted: list[tuple[str, str]] = []
        per_case_relations: list[RelationMetrics] = []
        total_latency = 0.0
        errors: list[str] = []

        # Warmup runs
        if suite.test_cases and config.warmup_runs > 0:
            for _ in range(config.warmup_runs):
                await self.run_case(suite.test_cases[0], config)

        # Actual benchmark runs
        for test_case in suite.test_cases:
            latencies = []
            run = CaseRun()

            for _ in range(config.num_runs):
                run = await self.run_case(test_case, config)
                latencies.append(run.latency_ms)

                if not run.entities and test_case.expected_entities:
                    errors.append(f"No entities extracted for test case {test_case.id}")

            # Use the last run's extractions for metrics
            # (assuming consistent results across runs)
            all_expected.extend(test_case.expected_entities)
            all_extracted.extend(run.entities)

            expected_relations = test_case.expected_relation_objects()
            if expected_relations:
                per_case_relations.append(
                    calculate_relation_metrics(
                        expected_relations,
                        run.relations,
                        config.match_policy,
                    )
                )

            total_latency += sum(latencies) / len(latencies)

        # Calculate metrics
        metrics = calculate_extraction_metrics(
            all_expected,
            all_extracted,
            latency_ms=total_latency,
        )

        relation_metrics = (
            aggregate_relation_metrics(per_case_relations) if per_case_relations else None
        )
        metrics.relation_metrics = relation_metrics

        avg_latency = total_latency / len(suite.test_cases) if suite.test_cases else 0
        throughput = (1000 / avg_latency) if avg_latency > 0 else 0

        return BenchmarkResult(
            name=suite.name,
            extractor_name=self._extractor_name,
            test_count=len(suite.test_cases),
            metrics=metrics,
            total_latency_ms=total_latency,
            avg_latency_ms=avg_latency,
            throughput_docs_per_sec=throughput,
            errors=errors,
            relation_metrics=relation_metrics,
        )

    async def compare_extractors(
        self,
        extractors: list[Any],
        suite: BenchmarkSuite,
    ) -> list[BenchmarkResult]:
        """Compare multiple extractors on the same suite.

        Args:
            extractors: List of extractor instances
            suite: Benchmark suite to run

        Returns:
            List of BenchmarkResults, one per extractor
        """
        results = []
        for extractor in extractors:
            runner = BenchmarkRunner(extractor)
            result = await runner.run_suite(suite)
            results.append(result)
        return results


def create_sample_benchmark_suite() -> BenchmarkSuite:
    """Create a sample benchmark suite for testing.

    Returns:
        A BenchmarkSuite with sample test cases
    """
    test_cases = [
        BenchmarkTestCase(
            id="tc-001",
            text="John Smith works at Acme Corporation in New York City.",
            expected_entities=[
                ExpectedEntity(name="John Smith", entity_type="PERSON"),
                ExpectedEntity(
                    name="Acme Corporation", entity_type="ORGANIZATION", aliases=["Acme Corp"]
                ),
                ExpectedEntity(
                    name="New York City", entity_type="LOCATION", aliases=["NYC", "New York"]
                ),
            ],
        ),
        BenchmarkTestCase(
            id="tc-002",
            text="Dr. Jane Doe presented her research at the MIT conference on artificial intelligence.",
            expected_entities=[
                ExpectedEntity(name="Jane Doe", entity_type="PERSON", aliases=["Dr. Jane Doe"]),
                ExpectedEntity(
                    name="MIT",
                    entity_type="ORGANIZATION",
                    aliases=["Massachusetts Institute of Technology"],
                ),
            ],
        ),
        BenchmarkTestCase(
            id="tc-003",
            text="The meeting between Apple Inc. CEO Tim Cook and Microsoft's Satya Nadella took place in San Francisco.",
            expected_entities=[
                ExpectedEntity(name="Apple Inc.", entity_type="ORGANIZATION", aliases=["Apple"]),
                ExpectedEntity(name="Tim Cook", entity_type="PERSON"),
                ExpectedEntity(name="Microsoft", entity_type="ORGANIZATION"),
                ExpectedEntity(name="Satya Nadella", entity_type="PERSON"),
                ExpectedEntity(name="San Francisco", entity_type="LOCATION"),
            ],
            expected_relations=[
                ExpectedRelation(
                    source="Tim Cook",
                    relation_type="EMPLOYED_BY",
                    target="Apple Inc.",
                    target_aliases=["Apple"],
                ),
                ExpectedRelation(
                    source="Satya Nadella",
                    relation_type="EMPLOYED_BY",
                    target="Microsoft",
                ),
            ],
        ),
    ]

    return BenchmarkSuite(
        name="sample_benchmark",
        description="Sample benchmark suite for testing extraction quality",
        test_cases=test_cases,
        config=BenchmarkConfig(
            name="sample",
            warmup_runs=0,
            num_runs=1,
            timeout_seconds=30.0,
        ),
    )
