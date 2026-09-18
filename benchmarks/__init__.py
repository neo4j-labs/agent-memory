"""Extraction quality benchmarks for neo4j-agent-memory.

This module provides tools for measuring extraction quality:
- Precision, recall, and F1 scores by entity type
- Relation (triple) precision, recall, and F1 under an explicit MatchPolicy
- B-cubed scores for entity resolution
- Latency and throughput measurements
- Cost tracking for LLM-based extraction
- Comparison across different extractors

Gold data for the POLE+O model lives in ``benchmarks/data/``.
"""

from benchmarks.metrics import (
    DEFAULT_MATCH_POLICY,
    STRICT_MATCH_POLICY,
    BCubedScore,
    BenchmarkResult,
    EntityMetrics,
    ExpectedEntity,
    ExpectedRelation,
    ExtractionMetrics,
    MatchPolicy,
    RelationMetrics,
    ResolutionGold,
    ResolutionMention,
    aggregate_relation_metrics,
    bcubed,
    calculate_entity_metrics,
    calculate_extraction_metrics,
    calculate_relation_metrics,
    load_resolution_gold,
    normalize_name,
)
from benchmarks.runner import (
    BenchmarkConfig,
    BenchmarkRunner,
    BenchmarkSuite,
    BenchmarkTestCase,
    CaseRun,
    create_sample_benchmark_suite,
)

__all__ = [
    "DEFAULT_MATCH_POLICY",
    "STRICT_MATCH_POLICY",
    "BCubedScore",
    "BenchmarkResult",
    "BenchmarkConfig",
    "BenchmarkRunner",
    "BenchmarkSuite",
    "BenchmarkTestCase",
    "CaseRun",
    "EntityMetrics",
    "ExpectedEntity",
    "ExpectedRelation",
    "ExtractionMetrics",
    "MatchPolicy",
    "RelationMetrics",
    "ResolutionGold",
    "ResolutionMention",
    "aggregate_relation_metrics",
    "bcubed",
    "calculate_entity_metrics",
    "calculate_extraction_metrics",
    "calculate_relation_metrics",
    "create_sample_benchmark_suite",
    "load_resolution_gold",
    "normalize_name",
]
