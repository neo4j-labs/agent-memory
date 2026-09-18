"""Evaluation harness for memory quality.

A small, batteries-included evaluator for the dimensions called out in the
PRD that the field doesn't have a settled benchmark for:

* **Retrieval relevance** — recall@k against a labeled seedset of
  (query, expected_entity_ids) tuples.
* **Audit completeness** — for a list of (entity_id, expected_step_ids),
  verify that ``(:Entity)<-[:TOUCHED]-(:ReasoningStep)`` paths cover
  the expected steps.
* **Preference fidelity** — for a (user_identifier, expected_active_pref_ids)
  list, check that ``get_preferences_for(active_only=True)`` returns
  exactly the expected ids.
* **Extraction quality** — for a list of (text, expected_entities,
  expected_relations), runs the client's configured extractor and scores
  relation F1 (when gold relations are given) or entity F1 otherwise, via
  :mod:`neo4j_agent_memory.core.metrics`.
* **Resolution quality** — for a list of (mentions, gold_clusters), runs the
  client's configured resolver and scores the predicted clustering with
  B-cubed F1 against the gold clusters.

Each dimension is independently runnable. Results are returned as a
dict keyed by dimension. The harness is intentionally simple — it's a
scaffold, not a benchmark.

The extraction and resolution dimensions need a configured extractor /
resolver on the client (``client._extractor`` / ``client._resolver``, set by
``connect()``). A ``llm=None`` client with no local extractor/resolver, or a
NAMS-backed client that hasn't wired one up, has neither — those dimensions
are then skipped cleanly: ``EvalReport.extraction`` / ``.resolution`` stay
``None`` and the dimension name is recorded in ``EvalReport.skipped`` instead
of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from neo4j_agent_memory.core.metrics import (
    DEFAULT_MATCH_POLICY,
    ExpectedEntity,
    ExpectedRelation,
    bcubed,
    calculate_extraction_metrics,
    calculate_relation_metrics,
)

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.extraction.base import EntityExtractor
    from neo4j_agent_memory.resolution.base import EntityResolver


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
class ExtractionCase:
    """A single extraction-quality case: text plus gold entities/relations.

    When ``expected_relations`` is non-empty, the case scores relation F1;
    otherwise it scores entity F1. Dict and tuple entries are coerced into
    :class:`~neo4j_agent_memory.core.metrics.ExpectedEntity` /
    :class:`~neo4j_agent_memory.core.metrics.ExpectedRelation` on
    construction, mirroring :class:`benchmarks.runner.BenchmarkTestCase`.

    Attributes:
        text: The text to run the client's extractor against.
        expected_entities: Gold entities, as ``ExpectedEntity`` objects or
            ``{"name", "type", "aliases"}`` mappings.
        expected_relations: Gold triples, as ``ExpectedRelation`` objects,
            ``(source, relation_type, target)`` tuples, or mappings.
    """

    text: str
    expected_entities: list[ExpectedEntity | dict[str, Any]]
    expected_relations: list[ExpectedRelation | tuple[str, str, str] | dict[str, Any]] = field(
        default_factory=list
    )

    def __post_init__(self) -> None:
        """Coerce dict/tuple entries into ExpectedEntity/ExpectedRelation objects."""
        self.expected_entities = list(self.expected_entity_objects())
        self.expected_relations = list(self.expected_relation_objects())

    def expected_entity_objects(self) -> list[ExpectedEntity]:
        """Return the expected entities as ExpectedEntity objects."""
        return [ExpectedEntity.from_value(entity) for entity in self.expected_entities]

    def expected_relation_objects(self) -> list[ExpectedRelation]:
        """Return the expected relations as ExpectedRelation objects."""
        return [ExpectedRelation.from_value(relation) for relation in self.expected_relations]


@dataclass
class ResolutionCase:
    """A single entity-resolution case: mentions plus their gold clusters.

    Attributes:
        mentions: ``(name, type)`` pairs. A resolver exposing
            ``resolve_episode`` (the ontology resolver) receives the whole
            list in one call, so its intra-episode clustering is what gets
            scored; any other resolver falls back to
            :meth:`~neo4j_agent_memory.resolution.base.EntityResolver.resolve_batch`.
        gold_clusters: Mention name -> gold cluster id. Scored with B-cubed
            against the resolver's predicted clusters. For ``resolve_episode``
            those come from what each mention merged onto (the matched
            entity's id, or the anchor mention's name for an intra-episode
            merge), with unmerged mentions as singletons; for
            ``resolve_batch``, from each resolved entity's ``cluster_id``
            (falling back to ``canonical_name`` when the resolver leaves
            ``cluster_id`` unset). Mentions sharing the same name overwrite
            each other in both the predicted and gold maps — give repeated
            mentions distinct names if that matters for a case.
    """

    mentions: list[tuple[str, str]]
    gold_clusters: dict[str, str]


@dataclass
class EvalSuite:
    """Bundle of cases evaluated together."""

    retrieval: list[RetrievalCase] = field(default_factory=list)
    audit: list[AuditCase] = field(default_factory=list)
    preference: list[PreferenceCase] = field(default_factory=list)
    extraction: list[ExtractionCase] = field(default_factory=list)
    resolution: list[ResolutionCase] = field(default_factory=list)


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
    details: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-case breakdown",
    )


class EvalReport(BaseModel):
    """Aggregate report across all dimensions."""

    retrieval: DimensionReport | None = None
    audit: DimensionReport | None = None
    preference: DimensionReport | None = None
    extraction: DimensionReport | None = None
    resolution: DimensionReport | None = None
    skipped: list[str] = Field(
        default_factory=list,
        description=(
            "Dimensions that had cases and were requested, but were skipped because "
            "the client has no extractor/resolver configured (e.g. a NAMS-backed "
            "client, or an llm=None client with no local resolver)."
        ),
    )

    @property
    def overall_score(self) -> float:
        """Mean score across the dimensions that ran (skipped ones excluded)."""
        scores = [
            d.score
            for d in (
                self.retrieval,
                self.audit,
                self.preference,
                self.extraction,
                self.resolution,
            )
            if d is not None
        ]
        return sum(scores) / len(scores) if scores else 0.0


# -----------------------------------------------------------------------------
# The evaluator
# -----------------------------------------------------------------------------


class EvalMemory:
    """``client.eval`` — runs a labeled :class:`EvalSuite`."""

    def __init__(self, client: MemoryClient[Any, Any, Any]):
        self._client = client

    async def run(
        self,
        suite: EvalSuite,
        *,
        dimensions: list[str] | None = None,
    ) -> EvalReport:
        """Evaluate the suite across the requested dimensions.

        Args:
            suite: Test suite with retrieval / audit / preference / extraction
                / resolution cases.
            dimensions: Subset of ``["retrieval", "audit", "preference",
                "extraction", "resolution"]`` to run. ``None`` runs every
                dimension that has cases.

        Returns:
            :class:`EvalReport` with per-dimension scores. ``extraction`` /
            ``resolution`` are left ``None`` (and noted in ``report.skipped``)
            when the client has no extractor / resolver configured.
        """
        wanted = (
            set(dimensions)
            if dimensions
            else {"retrieval", "audit", "preference", "extraction", "resolution"}
        )

        report = EvalReport()
        if "retrieval" in wanted and suite.retrieval:
            report.retrieval = await self._eval_retrieval(suite.retrieval)
        if "audit" in wanted and suite.audit:
            report.audit = await self._eval_audit(suite.audit)
        if "preference" in wanted and suite.preference:
            report.preference = await self._eval_preference(suite.preference)
        if "extraction" in wanted and suite.extraction:
            extractor = self._client._extractor
            if extractor is None:
                report.skipped.append("extraction")
            else:
                report.extraction = await self._eval_extraction(suite.extraction, extractor)
        if "resolution" in wanted and suite.resolution:
            resolver = self._client._resolver
            if resolver is None:
                report.skipped.append("resolution")
            else:
                report.resolution = await self._eval_resolution(suite.resolution, resolver)
        return report

    async def _eval_retrieval(self, cases: list[RetrievalCase]) -> DimensionReport:
        """Compute recall@k against a labeled seedset.

        Recall = |retrieved ∩ expected| / |expected|.
        """
        details: list[dict[str, Any]] = []
        scores: list[float] = []
        for case in cases:
            results = await self._client.long_term.search_entities(case.query, limit=case.k)
            retrieved_ids: set[str] = set()
            for entity in results:
                # search_entities returns a list of Entity.
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
        details: list[dict[str, Any]] = []
        scores: list[float] = []
        for case in cases:
            # v0.4: use the portable client.query.cypher accessor — runs on
            # bolt (Neo4jClient.execute_read) and NAMS (POST /v1/query).
            # Note that ``:TOUCHED`` audit edges are a bolt-side schema feature;
            # this query returns no rows on NAMS, and the audit dimension is
            # effectively a no-op there. Documented in the v0.4 release notes.
            rows = await self._client.query.cypher(
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

    async def _eval_extraction(
        self,
        cases: list[ExtractionCase],
        extractor: EntityExtractor,
    ) -> DimensionReport:
        """Score extraction quality against gold entities/relations.

        Relation F1 is scored when a case declares ``expected_relations``;
        otherwise entity F1 is scored. Both use the one-to-one matching in
        :mod:`neo4j_agent_memory.core.metrics`, under the default (lenient,
        alias-tolerant) match policy.
        """
        details: list[dict[str, Any]] = []
        scores: list[float] = []
        for case in cases:
            result = await extractor.extract(case.text)
            expected_relations = case.expected_relation_objects()

            if expected_relations:
                extracted_relations = [
                    (relation.source, relation.relation_type, relation.target)
                    for relation in result.relations
                ]
                relation_metrics = calculate_relation_metrics(
                    expected_relations, extracted_relations, DEFAULT_MATCH_POLICY
                )
                score = relation_metrics.f1_score
                details.append(
                    {
                        "text": case.text,
                        "scored": "relations",
                        "precision": relation_metrics.precision,
                        "recall": relation_metrics.recall,
                        "f1": score,
                    }
                )
            else:
                expected_entities = case.expected_entity_objects()
                extracted_entities = [(entity.name, entity.type) for entity in result.entities]
                entity_metrics = calculate_extraction_metrics(expected_entities, extracted_entities)
                score = entity_metrics.micro_f1
                details.append(
                    {
                        "text": case.text,
                        "scored": "entities",
                        "precision": entity_metrics.micro_precision,
                        "recall": entity_metrics.micro_recall,
                        "f1": score,
                    }
                )
            scores.append(score)
        return DimensionReport(
            cases=len(cases),
            score=sum(scores) / len(scores) if scores else 0.0,
            details=details,
        )

    async def _eval_resolution(
        self,
        cases: list[ResolutionCase],
        resolver: EntityResolver,
    ) -> DimensionReport:
        """Score entity resolution with B-cubed F1 against gold clusters."""
        details: list[dict[str, Any]] = []
        scores: list[float] = []
        episode_capable = callable(getattr(resolver, "resolve_episode", None))
        for case in cases:
            if episode_capable:
                predicted = await self._episode_clusters(case, resolver)
            else:
                resolved = await resolver.resolve_batch(case.mentions)
                predicted = {
                    name: (entity.cluster_id or entity.canonical_name)
                    for (name, _entity_type), entity in zip(case.mentions, resolved)
                }
            score = bcubed(predicted, case.gold_clusters)
            scores.append(score.f1_score)
            details.append(
                {
                    "mentions": [name for name, _ in case.mentions],
                    "predicted_clusters": predicted,
                    "gold_clusters": case.gold_clusters,
                    "clustered_by": "resolve_episode" if episode_capable else "resolve_batch",
                    "precision": score.precision,
                    "recall": score.recall,
                    "f1": score.f1_score,
                }
            )
        return DimensionReport(
            cases=len(cases),
            score=sum(scores) / len(scores) if scores else 0.0,
            details=details,
        )

    @staticmethod
    async def _episode_clusters(
        case: ResolutionCase,
        resolver: Any,
    ) -> dict[str, str]:
        """Predicted clusters from one ``resolve_episode`` call.

        ``resolve_batch`` resolves each mention against the live graph
        independently and never clusters the batch among itself, so B-cubed
        over its output is the same whatever the resolver decides — every
        mention comes back as its own canonical name. Resolvers that expose
        ``resolve_episode`` (the two-pass
        :class:`~neo4j_agent_memory.resolution.ontology.OntologyResolver`)
        get the whole case handed over in one call, which is where the
        intra-episode clustering actually happens.

        A merged mention is keyed by whatever it merged onto — the stored
        entity's id, or the anchor mention's name for an intra-episode merge.
        Anything else (``created``, ``review``) is its own singleton, keyed by
        its own name, because that is a node of its own in the graph.
        """
        from neo4j_agent_memory.extraction.base import ExtractedEntity

        mentions = [
            ExtractedEntity(name=name, type=entity_type) for name, entity_type in case.mentions
        ]
        resolutions = await resolver.resolve_episode(mentions)
        predicted: dict[str, str] = {}
        for (name, _entity_type), resolution in zip(case.mentions, resolutions):
            cluster = name
            if getattr(resolution, "action", None) == "merged":
                cluster = str(
                    getattr(resolution, "matched_entity_id", None)
                    or getattr(resolution, "entity_id", None)
                    or getattr(resolution, "matched_entity_name", None)
                    or name
                )
            predicted[name] = cluster
        return predicted

    async def _eval_preference(self, cases: list[PreferenceCase]) -> DimensionReport:
        """Check ``get_preferences_for(active_only=True)`` exact match."""
        details: list[dict[str, Any]] = []
        scores: list[float] = []
        for case in cases:
            prefs = await self._client.long_term.get_preferences_for(
                user_identifier=case.user_identifier, active_only=True
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
