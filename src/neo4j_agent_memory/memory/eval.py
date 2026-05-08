"""Evaluation harness for memory quality.

A small, batteries-included evaluator for the three dimensions called
out in the PRD that the field doesn't have a settled benchmark for:

* **Retrieval relevance** — recall@k against a labeled seedset of
  (query, expected_entity_ids) tuples.
* **Audit completeness** — for a list of (entity_id, expected_step_ids),
  verify that ``(:Entity)<-[:TOUCHED]-(:ReasoningStep)`` paths cover
  the expected steps.
* **Preference fidelity** — for a (user_identifier, expected_active_pref_ids)
  list, check that ``get_preferences_for(active_only=True)`` returns
  exactly the expected ids.

Each dimension is independently runnable. Results are returned as a
dict keyed by dimension. The harness is intentionally simple — it's a
scaffold, not a benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient


# -----------------------------------------------------------------------------
# Test cases
# -----------------------------------------------------------------------------


@dataclass
class RetrievalCase:
    """A single retrieval-evaluation case."""

    query: str
    expected_entity_ids: set[str]
    k: int = 10


@dataclass
class AuditCase:
    """A single audit-completeness case."""

    entity_id: str
    expected_step_ids: set[str]


@dataclass
class PreferenceCase:
    """A single preference-fidelity case."""

    user_identifier: str
    expected_active_pref_ids: set[str]


@dataclass
class EvalSuite:
    """Bundle of cases evaluated together."""

    retrieval: list[RetrievalCase] = field(default_factory=list)
    audit: list[AuditCase] = field(default_factory=list)
    preference: list[PreferenceCase] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------


class DimensionReport(BaseModel):
    """Per-dimension result."""

    cases: int = Field(description="Number of test cases evaluated")
    score: float = Field(
        description="Aggregate score for this dimension, 0-1",
        ge=0.0,
        le=1.0,
    )
    details: list[dict] = Field(
        default_factory=list,
        description="Per-case breakdown",
    )


class EvalReport(BaseModel):
    """Aggregate report across all dimensions."""

    retrieval: DimensionReport | None = None
    audit: DimensionReport | None = None
    preference: DimensionReport | None = None

    @property
    def overall_score(self) -> float:
        """Mean score across the dimensions that ran."""
        scores = [d.score for d in (self.retrieval, self.audit, self.preference) if d is not None]
        return sum(scores) / len(scores) if scores else 0.0


# -----------------------------------------------------------------------------
# The evaluator
# -----------------------------------------------------------------------------


class EvalMemory:
    """``client.eval`` — runs a labeled :class:`EvalSuite`."""

    def __init__(self, client: MemoryClient):
        self._client = client

    async def run(
        self,
        suite: EvalSuite,
        *,
        dimensions: list[str] | None = None,
    ) -> EvalReport:
        """Evaluate the suite across the requested dimensions.

        Args:
            suite: Test suite with retrieval / audit / preference cases.
            dimensions: Subset of ``["retrieval", "audit", "preference"]``
                to run. ``None`` runs every dimension that has cases.

        Returns:
            :class:`EvalReport` with per-dimension scores.
        """
        wanted = set(dimensions) if dimensions else {"retrieval", "audit", "preference"}

        report = EvalReport()
        if "retrieval" in wanted and suite.retrieval:
            report.retrieval = await self._eval_retrieval(suite.retrieval)
        if "audit" in wanted and suite.audit:
            report.audit = await self._eval_audit(suite.audit)
        if "preference" in wanted and suite.preference:
            report.preference = await self._eval_preference(suite.preference)
        return report

    async def _eval_retrieval(self, cases: list[RetrievalCase]) -> DimensionReport:
        """Compute recall@k against a labeled seedset.

        Recall = |retrieved ∩ expected| / |expected|.
        """
        details: list[dict] = []
        scores: list[float] = []
        for case in cases:
            results = await self._client.long_term.search_entities(case.query, limit=case.k)
            retrieved_ids: set[str] = set()
            for r in results:
                # search_entities returns (Entity, score) tuples.
                entity = r[0] if isinstance(r, tuple) else r
                retrieved_ids.add(str(getattr(entity, "id", "")))

            hits = retrieved_ids & case.expected_entity_ids
            recall = len(hits) / len(case.expected_entity_ids) if case.expected_entity_ids else 1.0
            scores.append(recall)
            details.append(
                {
                    "query": case.query,
                    "k": case.k,
                    "expected": sorted(case.expected_entity_ids),
                    "retrieved": sorted(retrieved_ids),
                    "recall": recall,
                }
            )
        return DimensionReport(
            cases=len(cases),
            score=sum(scores) / len(scores) if scores else 0.0,
            details=details,
        )

    async def _eval_audit(self, cases: list[AuditCase]) -> DimensionReport:
        """Verify each entity has the expected ``:TOUCHED``-bearing steps."""
        details: list[dict] = []
        scores: list[float] = []
        for case in cases:
            rows = await self._client._client.execute_read(
                """
                MATCH (e:Entity {id: $eid})<-[:TOUCHED]-(s:ReasoningStep)
                RETURN s.id AS id
                """,
                {"eid": case.entity_id},
            )
            actual_ids = {r["id"] for r in rows}
            covered = actual_ids & case.expected_step_ids
            recall = len(covered) / len(case.expected_step_ids) if case.expected_step_ids else 1.0
            scores.append(recall)
            details.append(
                {
                    "entity_id": case.entity_id,
                    "expected": sorted(case.expected_step_ids),
                    "actual": sorted(actual_ids),
                    "recall": recall,
                }
            )
        return DimensionReport(
            cases=len(cases),
            score=sum(scores) / len(scores) if scores else 0.0,
            details=details,
        )

    async def _eval_preference(self, cases: list[PreferenceCase]) -> DimensionReport:
        """Check ``get_preferences_for(active_only=True)`` exact match."""
        details: list[dict] = []
        scores: list[float] = []
        for case in cases:
            prefs = await self._client.long_term.get_preferences_for(
                case.user_identifier, active_only=True
            )
            actual_ids = {str(p.id) for p in prefs}
            # Use F1 — either over- or under-returning hurts.
            tp = len(actual_ids & case.expected_active_pref_ids)
            fp = len(actual_ids - case.expected_active_pref_ids)
            fn = len(case.expected_active_pref_ids - actual_ids)
            precision = tp / (tp + fp) if (tp + fp) else 1.0
            recall = tp / (tp + fn) if (tp + fn) else 1.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            scores.append(f1)
            details.append(
                {
                    "user_identifier": case.user_identifier,
                    "expected": sorted(case.expected_active_pref_ids),
                    "actual": sorted(actual_ids),
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                }
            )
        return DimensionReport(
            cases=len(cases),
            score=sum(scores) / len(scores) if scores else 0.0,
            details=details,
        )
