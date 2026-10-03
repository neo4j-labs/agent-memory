"""Metrics calculation for extraction benchmarks.

The reusable scoring engine — precision/recall/F1 for entities and
relations, match policies, and B-cubed resolution scoring — lives in
:mod:`neo4j_agent_memory.core.metrics` so it ships with the installed
package (``benchmarks/`` is a repository-only dev tool, excluded from the
wheel) and :mod:`neo4j_agent_memory.memory.eval` can use it without a
checkout of this repository. This module re-exports that engine for
benchmark code and adds :class:`BenchmarkResult`, which packages a report
around one benchmark-suite run and has no reason to live in the installed
package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j_agent_memory.core.metrics import (
    DEFAULT_MATCH_POLICY,
    STRICT_MATCH_POLICY,
    BCubedScore,
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

__all__ = [
    "DEFAULT_MATCH_POLICY",
    "STRICT_MATCH_POLICY",
    "BCubedScore",
    "BenchmarkResult",
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
    "load_resolution_gold",
    "normalize_name",
]


@dataclass
class BenchmarkResult:
    """Result of running a benchmark suite.

    Relation scores are exposed both under the suite's configured match
    policy (``relation_*``) and under the strict policy
    (``strict_relation_*``). When the policy was already strict the two are
    identical.
    """

    name: str
    extractor_name: str
    test_count: int
    metrics: ExtractionMetrics
    total_latency_ms: float
    avg_latency_ms: float
    throughput_docs_per_sec: float
    errors: list[str] = field(default_factory=list)
    relation_metrics: RelationMetrics | None = None

    @property
    def relation_precision(self) -> float:
        """Relation precision under the configured match policy."""
        return self.relation_metrics.precision if self.relation_metrics else 0.0

    @property
    def relation_recall(self) -> float:
        """Relation recall under the configured match policy."""
        return self.relation_metrics.recall if self.relation_metrics else 0.0

    @property
    def relation_f1(self) -> float:
        """Relation F1 under the configured match policy."""
        return self.relation_metrics.f1_score if self.relation_metrics else 0.0

    @property
    def _strict_relation_metrics(self) -> RelationMetrics | None:
        """The strict counterpart, falling back to the lenient metrics."""
        if self.relation_metrics is None:
            return None
        return self.relation_metrics.strict or self.relation_metrics

    @property
    def strict_relation_precision(self) -> float:
        """Relation precision under the strict match policy."""
        strict = self._strict_relation_metrics
        return strict.precision if strict else 0.0

    @property
    def strict_relation_recall(self) -> float:
        """Relation recall under the strict match policy."""
        strict = self._strict_relation_metrics
        return strict.recall if strict else 0.0

    @property
    def strict_relation_f1(self) -> float:
        """Relation F1 under the strict match policy."""
        strict = self._strict_relation_metrics
        return strict.f1_score if strict else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "extractor_name": self.extractor_name,
            "test_count": self.test_count,
            "metrics": self.metrics.to_dict(),
            "total_latency_ms": self.total_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "throughput_docs_per_sec": self.throughput_docs_per_sec,
            "errors": self.errors,
            "relation_metrics": (
                self.relation_metrics.to_dict() if self.relation_metrics is not None else None
            ),
            "relation_precision": self.relation_precision,
            "relation_recall": self.relation_recall,
            "relation_f1": self.relation_f1,
            "strict_relation_precision": self.strict_relation_precision,
            "strict_relation_recall": self.strict_relation_recall,
            "strict_relation_f1": self.strict_relation_f1,
        }
