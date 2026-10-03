"""Shared scoring engine for extraction and resolution quality.

This is the single implementation of entity/relation F1 and B-cubed
resolution scoring. It lives under ``neo4j_agent_memory.core`` (rather than
the top-level ``benchmarks/`` module) so it ships with the installed
package: ``benchmarks/`` is a repository-only dev tool excluded from the
wheel (see ``[tool.hatch.build.targets.wheel] packages`` in
``pyproject.toml``), but :mod:`neo4j_agent_memory.memory.eval` needs these
functions at runtime for installed users too.

``benchmarks/metrics.py`` re-exports everything here and adds
``BenchmarkResult``, which packages a report around one benchmark-suite run
and has no reason to ship with the installed package.

Three matching rules are shared by every metric in this module:

1. Names are compared after :func:`normalize_name`, the same normalisation
   used for both entities and relation endpoints.
2. Relation names are compared exactly (upper-cased); the relation
   vocabulary is closed, so a near-miss is a miss.
3. Matching is one-to-one: each gold item and each predicted item is
   consumed at most once, which is the difference between precision and
   wishful thinking.

Loosening (aliases, fuzzy names, undirected triples) is configured through
:class:`MatchPolicy` and is *recorded* rather than hidden: a lenient run also
reports the strict numbers, so the two can be read side by side.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_DETERMINERS = ("the ", "a ", "an ")


def normalize_name(name: str) -> str:
    """Normalise an entity name for comparison.

    Applies NFC unicode normalisation, case folding, whitespace collapsing,
    and strips a single leading determiner ("the", "a", "an"). This is the
    one normalisation used for entity names and relation endpoints alike.

    Args:
        name: Raw surface form of an entity name

    Returns:
        The normalised name (may be an empty string)
    """
    text = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", name).casefold().strip())
    for determiner in _LEADING_DETERMINERS:
        if text.startswith(determiner):
            return text[len(determiner) :].strip()
    return text


def _fuzzy_ratio(left: str, right: str) -> float:
    """Return a 0..1 similarity ratio, importing rapidfuzz lazily.

    Args:
        left: First string
        right: Second string

    Returns:
        Similarity in the range 0.0 to 1.0

    Raises:
        RuntimeError: If rapidfuzz is not installed
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "MatchPolicy(fuzzy_threshold=...) requires rapidfuzz, which is not "
            'installed. Install it with: pip install "neo4j-agent-memory[fuzzy]"'
        ) from exc
    return float(fuzz.ratio(left, right)) / 100.0


@dataclass(frozen=True)
class MatchPolicy:
    """How generously a predicted item may match a gold item.

    Attributes:
        normalize_names: Compare names through :func:`normalize_name`
        use_aliases: Accept the gold item's declared alias surface forms
        fuzzy_threshold: Accept names above this 0..1 similarity (needs
            rapidfuzz); None disables fuzzy matching
        directed: When False, a reversed triple also counts as a match
    """

    normalize_names: bool = True
    use_aliases: bool = True
    fuzzy_threshold: float | None = None
    directed: bool = True

    def __post_init__(self) -> None:
        """Validate the fuzzy threshold.

        Raises:
            ValueError: If fuzzy_threshold is outside the range 0.0 to 1.0
        """
        if self.fuzzy_threshold is not None and not 0.0 <= self.fuzzy_threshold <= 1.0:
            raise ValueError(
                f"fuzzy_threshold must be between 0.0 and 1.0, got {self.fuzzy_threshold}"
            )

    @property
    def is_lenient(self) -> bool:
        """Whether this policy loosens matching beyond the strict rules."""
        return self.use_aliases or self.fuzzy_threshold is not None or not self.directed

    def as_strict(self) -> MatchPolicy:
        """Return the strict counterpart of this policy.

        Name normalisation is part of the base rule and is preserved; alias,
        fuzzy and undirected loosening are switched off.

        Returns:
            A strict MatchPolicy
        """
        return replace(self, use_aliases=False, fuzzy_threshold=None, directed=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "normalize_names": self.normalize_names,
            "use_aliases": self.use_aliases,
            "fuzzy_threshold": self.fuzzy_threshold,
            "directed": self.directed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MatchPolicy:
        """Create a policy from a dictionary, filling defaults.

        Args:
            data: Mapping with any subset of the policy fields, or None

        Returns:
            A MatchPolicy
        """
        if not data:
            return cls()
        return cls(
            normalize_names=bool(data.get("normalize_names", True)),
            use_aliases=bool(data.get("use_aliases", True)),
            fuzzy_threshold=data.get("fuzzy_threshold"),
            directed=bool(data.get("directed", True)),
        )

    def prepare(self, name: str) -> str:
        """Normalise a name according to this policy.

        Args:
            name: Raw surface form

        Returns:
            The comparison key for the name
        """
        return normalize_name(name) if self.normalize_names else name.strip()

    def name_matches(
        self,
        candidate: str,
        target: str,
        aliases: Sequence[str] = (),
    ) -> tuple[bool, bool]:
        """Check whether a predicted name matches a gold name.

        Args:
            candidate: Predicted surface form
            target: Gold canonical name
            aliases: Gold alias surface forms

        Returns:
            Tuple of (matched, needed_loosening). The second element is True
            when the match only succeeded through an alias or fuzzy ratio.
        """
        prepared = self.prepare(candidate)
        if prepared == self.prepare(target):
            return True, False

        if self.use_aliases:
            for alias in aliases:
                if prepared == self.prepare(alias):
                    return True, True

        if self.fuzzy_threshold is not None:
            surfaces = [target, *aliases] if self.use_aliases else [target]
            for surface in surfaces:
                if _fuzzy_ratio(prepared, self.prepare(surface)) >= self.fuzzy_threshold:
                    return True, True

        return False, False


DEFAULT_MATCH_POLICY = MatchPolicy()
"""The default policy: normalised names, aliases allowed, directed, no fuzzy."""

STRICT_MATCH_POLICY = MatchPolicy(use_aliases=False)
"""Canonical names only, directed, no fuzzy."""


@dataclass
class EntityMetrics:
    """Metrics for a single entity type."""

    entity_type: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        """Calculate precision (TP / (TP + FP))."""
        if self.true_positives + self.false_positives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Calculate recall (TP / (TP + FN))."""
        if self.true_positives + self.false_negatives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_negatives)

    @property
    def f1_score(self) -> float:
        """Calculate F1 score (2 * precision * recall / (precision + recall))."""
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entity_type": self.entity_type,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
        }


@dataclass
class RelationMetrics:
    """Metrics for extracted relations (triples).

    Mirrors :class:`EntityMetrics`, with two additions that keep a lenient
    run auditable: ``lenient_matches`` counts the true positives that only
    matched through alias or fuzzy loosening, and ``strict`` carries the
    same counts recomputed under :meth:`MatchPolicy.as_strict`.

    Attributes:
        relation_type: Relation name these counts cover ("ALL" when mixed)
        true_positives: Predicted triples matched to a gold triple
        false_positives: Predicted triples with no gold match
        false_negatives: Gold triples never matched
        lenient_matches: True positives that needed alias/fuzzy loosening
        strict: The strict-policy counterpart, or None when the policy was
            already strict
    """

    relation_type: str = "ALL"
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    lenient_matches: int = 0
    strict: RelationMetrics | None = None

    @property
    def precision(self) -> float:
        """Calculate precision (TP / (TP + FP))."""
        if self.true_positives + self.false_positives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Calculate recall (TP / (TP + FN))."""
        if self.true_positives + self.false_negatives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_negatives)

    @property
    def f1_score(self) -> float:
        """Calculate F1 score (2 * precision * recall / (precision + recall))."""
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "relation_type": self.relation_type,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "lenient_matches": self.lenient_matches,
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
            "strict": self.strict.to_dict() if self.strict is not None else None,
        }


@dataclass
class ExtractionMetrics:
    """Aggregate metrics for extraction evaluation."""

    entity_metrics: dict[str, EntityMetrics] = field(default_factory=dict)
    total_true_positives: int = 0
    total_false_positives: int = 0
    total_false_negatives: int = 0
    latency_ms: float = 0.0
    token_count: int = 0
    relation_metrics: RelationMetrics | None = None

    @property
    def micro_precision(self) -> float:
        """Calculate micro-averaged precision."""
        if self.total_true_positives + self.total_false_positives == 0:
            return 0.0
        return self.total_true_positives / (self.total_true_positives + self.total_false_positives)

    @property
    def micro_recall(self) -> float:
        """Calculate micro-averaged recall."""
        if self.total_true_positives + self.total_false_negatives == 0:
            return 0.0
        return self.total_true_positives / (self.total_true_positives + self.total_false_negatives)

    @property
    def micro_f1(self) -> float:
        """Calculate micro-averaged F1 score."""
        if self.micro_precision + self.micro_recall == 0:
            return 0.0
        return (
            2
            * self.micro_precision
            * self.micro_recall
            / (self.micro_precision + self.micro_recall)
        )

    @property
    def macro_precision(self) -> float:
        """Calculate macro-averaged precision."""
        if not self.entity_metrics:
            return 0.0
        return sum(m.precision for m in self.entity_metrics.values()) / len(self.entity_metrics)

    @property
    def macro_recall(self) -> float:
        """Calculate macro-averaged recall."""
        if not self.entity_metrics:
            return 0.0
        return sum(m.recall for m in self.entity_metrics.values()) / len(self.entity_metrics)

    @property
    def macro_f1(self) -> float:
        """Calculate macro-averaged F1 score."""
        if not self.entity_metrics:
            return 0.0
        return sum(m.f1_score for m in self.entity_metrics.values()) / len(self.entity_metrics)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entity_metrics": {k: v.to_dict() for k, v in self.entity_metrics.items()},
            "micro_precision": self.micro_precision,
            "micro_recall": self.micro_recall,
            "micro_f1": self.micro_f1,
            "macro_precision": self.macro_precision,
            "macro_recall": self.macro_recall,
            "macro_f1": self.macro_f1,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "relation_metrics": (
                self.relation_metrics.to_dict() if self.relation_metrics is not None else None
            ),
        }


@dataclass
class ExpectedEntity:
    """Expected entity in a test case."""

    name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)

    def matches(self, extracted_name: str, extracted_type: str) -> bool:
        """Check if an extracted entity matches this expected entity.

        Args:
            extracted_name: Name of extracted entity
            extracted_type: Type of extracted entity

        Returns:
            True if the extracted entity matches
        """
        # Type must match (case-insensitive)
        if extracted_type.upper() != self.entity_type.upper():
            return False

        # Name must match (normalised) or be an alias
        extracted_key = normalize_name(extracted_name)
        if extracted_key == normalize_name(self.name):
            return True

        # Check aliases
        return any(extracted_key == normalize_name(alias) for alias in self.aliases)

    def surface_forms(self) -> list[str]:
        """Return the canonical name followed by every alias."""
        return [self.name, *self.aliases]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpectedEntity:
        """Create an ExpectedEntity from a JSON mapping.

        Args:
            data: Mapping with ``name``, ``type`` (or ``entity_type``) and
                an optional ``aliases`` list.

        Returns:
            An ExpectedEntity.
        """
        entity_type = data.get("type") or data.get("entity_type") or ""
        return cls(
            name=str(data["name"]),
            entity_type=str(entity_type),
            aliases=[str(alias) for alias in data.get("aliases", [])],
        )

    @classmethod
    def from_value(cls, value: ExpectedEntity | dict[str, Any]) -> ExpectedEntity:
        """Coerce a mapping or an existing instance into an ExpectedEntity.

        Args:
            value: An ExpectedEntity, or a mapping with ``name``/``type``/
                ``aliases`` keys.

        Returns:
            An ExpectedEntity.
        """
        if isinstance(value, ExpectedEntity):
            return value
        return cls.from_dict(value)


@dataclass
class ExpectedRelation:
    """Expected relation (triple) in a test case.

    Attributes:
        source: Canonical name of the source entity
        relation_type: Relation name from the closed vocabulary
        target: Canonical name of the target entity
        source_aliases: Alternative surface forms for the source
        target_aliases: Alternative surface forms for the target
    """

    source: str
    relation_type: str
    target: str
    source_aliases: list[str] = field(default_factory=list)
    target_aliases: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpectedRelation:
        """Create an ExpectedRelation from a JSON mapping.

        Args:
            data: Mapping with ``source``, ``relation_type`` (or ``relation``)
                and ``target`` keys, plus optional alias lists

        Returns:
            An ExpectedRelation
        """
        relation_type = data.get("relation_type") or data.get("relation") or ""
        return cls(
            source=str(data["source"]),
            relation_type=str(relation_type),
            target=str(data["target"]),
            source_aliases=[str(alias) for alias in data.get("source_aliases", [])],
            target_aliases=[str(alias) for alias in data.get("target_aliases", [])],
        )

    @classmethod
    def from_value(cls, value: Any) -> ExpectedRelation:
        """Coerce a JSON or tuple representation into an ExpectedRelation.

        Args:
            value: An ExpectedRelation, a (source, relation_type, target)
                sequence, or a mapping with those keys

        Returns:
            An ExpectedRelation

        Raises:
            ValueError: If the value cannot be interpreted as a triple
        """
        if isinstance(value, ExpectedRelation):
            return value
        if isinstance(value, dict):
            return cls.from_dict(value)
        items = list(value)
        if len(items) != 3:
            raise ValueError(f"Expected a (source, relation_type, target) triple, got {value!r}")
        return cls(source=str(items[0]), relation_type=str(items[1]), target=str(items[2]))

    def as_triple(self) -> tuple[str, str, str]:
        """Return the canonical (source, relation_type, target) triple."""
        return (self.source, self.relation_type.upper(), self.target)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source": self.source,
            "relation_type": self.relation_type,
            "target": self.target,
            "source_aliases": self.source_aliases,
            "target_aliases": self.target_aliases,
        }

    def match_detail(
        self,
        source: str,
        relation_type: str,
        target: str,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    ) -> tuple[bool, bool]:
        """Check an extracted triple against this expected relation.

        Args:
            source: Extracted source name
            relation_type: Extracted relation name
            target: Extracted target name
            policy: Matching policy

        Returns:
            Tuple of (matched, needed_loosening). The second element is True
            when the match relied on aliases, fuzzy names, or a reversed
            triple under ``directed=False``.
        """
        # Relation names are a closed vocabulary: exact match after upper-casing.
        if relation_type.strip().upper() != self.relation_type.strip().upper():
            return False, False

        forward_source, forward_source_lenient = policy.name_matches(
            source, self.source, self.source_aliases
        )
        if forward_source:
            forward_target, forward_target_lenient = policy.name_matches(
                target, self.target, self.target_aliases
            )
            if forward_target:
                return True, forward_source_lenient or forward_target_lenient

        if policy.directed:
            return False, False

        reverse_source, _ = policy.name_matches(target, self.source, self.source_aliases)
        if not reverse_source:
            return False, False
        reverse_target, _ = policy.name_matches(source, self.target, self.target_aliases)
        if not reverse_target:
            return False, False
        return True, True

    def matches(
        self,
        source: str,
        relation_type: str,
        target: str,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    ) -> bool:
        """Check if an extracted triple matches this expected relation.

        Args:
            source: Extracted source name
            relation_type: Extracted relation name
            target: Extracted target name
            policy: Matching policy

        Returns:
            True if the extracted triple matches
        """
        return self.match_detail(source, relation_type, target, policy)[0]


def calculate_entity_metrics(
    expected: list[ExpectedEntity],
    extracted: list[tuple[str, str]],  # (name, type) tuples
) -> EntityMetrics:
    """Calculate metrics for a single entity type.

    Args:
        expected: List of expected entities
        extracted: List of (name, type) tuples for extracted entities

    Returns:
        EntityMetrics with TP, FP, FN counts
    """
    if not expected:
        entity_type = extracted[0][1] if extracted else "UNKNOWN"
    else:
        entity_type = expected[0].entity_type

    metrics = EntityMetrics(entity_type=entity_type)

    # Track which expected entities were found
    found_expected = set()

    for ext_name, ext_type in extracted:
        matched = False
        for i, exp in enumerate(expected):
            if i not in found_expected and exp.matches(ext_name, ext_type):
                metrics.true_positives += 1
                found_expected.add(i)
                matched = True
                break

        if not matched:
            metrics.false_positives += 1

    # Count expected entities that weren't found
    metrics.false_negatives = len(expected) - len(found_expected)

    return metrics


def _score_relations(
    expected: Sequence[ExpectedRelation],
    extracted: Sequence[tuple[str, str, str]],
    policy: MatchPolicy,
) -> tuple[int, int, int, int]:
    """Greedily match extracted triples against expected triples, one-to-one.

    Each expected relation and each extracted triple is consumed at most
    once, so a duplicated extracted triple scores one true positive and one
    false positive. An exact match is preferred over a lenient one.

    Args:
        expected: Gold relations
        extracted: Extracted (source, relation_type, target) triples
        policy: Matching policy

    Returns:
        Tuple of (true_positives, false_positives, false_negatives,
        lenient_matches)
    """
    consumed: set[int] = set()
    true_positives = 0
    false_positives = 0
    lenient_matches = 0

    for source, relation_type, target in extracted:
        chosen: int | None = None
        chosen_lenient = False

        for index, expectation in enumerate(expected):
            if index in consumed:
                continue
            matched, needed_loosening = expectation.match_detail(
                source, relation_type, target, policy
            )
            if not matched:
                continue
            if not needed_loosening:
                chosen, chosen_lenient = index, False
                break
            if chosen is None:
                chosen, chosen_lenient = index, True

        if chosen is None:
            false_positives += 1
            continue

        consumed.add(chosen)
        true_positives += 1
        if chosen_lenient:
            lenient_matches += 1

    return true_positives, false_positives, len(expected) - len(consumed), lenient_matches


def calculate_relation_metrics(
    expected: list[ExpectedRelation],
    extracted: list[tuple[str, str, str]],
    policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    *,
    relation_type: str = "ALL",
) -> RelationMetrics:
    """Calculate precision/recall/F1 over extracted relations.

    Matching is one-to-one and relation names must match exactly (after
    upper-casing). When the policy is lenient, the strict counterpart is
    computed as well and attached as ``RelationMetrics.strict`` so both
    numbers can be reported.

    Args:
        expected: Gold relations for this text
        extracted: Extracted (source, relation_type, target) triples
        policy: Matching policy
        relation_type: Label for the returned metrics

    Returns:
        RelationMetrics with TP, FP, FN counts
    """
    true_positives, false_positives, false_negatives, lenient = _score_relations(
        expected, extracted, policy
    )
    metrics = RelationMetrics(
        relation_type=relation_type,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        lenient_matches=lenient,
    )

    if policy.is_lenient:
        strict_tp, strict_fp, strict_fn, _ = _score_relations(
            expected, extracted, policy.as_strict()
        )
        metrics.strict = RelationMetrics(
            relation_type=relation_type,
            true_positives=strict_tp,
            false_positives=strict_fp,
            false_negatives=strict_fn,
        )

    return metrics


def aggregate_relation_metrics(
    metrics: Iterable[RelationMetrics],
    *,
    relation_type: str = "ALL",
) -> RelationMetrics:
    """Micro-aggregate per-case relation metrics into one result.

    Counts are summed rather than re-matched, so a triple from one document
    can never match gold from another.

    Args:
        metrics: Per-case relation metrics
        relation_type: Label for the returned metrics

    Returns:
        RelationMetrics with summed counts (including the strict counterpart
        when any input carried one)
    """
    total = RelationMetrics(relation_type=relation_type)
    strict_total = RelationMetrics(relation_type=relation_type)
    saw_strict = False

    for item in metrics:
        total.true_positives += item.true_positives
        total.false_positives += item.false_positives
        total.false_negatives += item.false_negatives
        total.lenient_matches += item.lenient_matches

        strict_source = item.strict if item.strict is not None else item
        if item.strict is not None:
            saw_strict = True
        strict_total.true_positives += strict_source.true_positives
        strict_total.false_positives += strict_source.false_positives
        strict_total.false_negatives += strict_source.false_negatives

    if saw_strict:
        total.strict = strict_total
    return total


@dataclass(frozen=True)
class BCubedScore:
    """B-cubed precision, recall and F1 over entity-resolution clusters.

    Unpacks as ``precision, recall, f1 = bcubed(...)`` while still carrying
    the audit counts.

    Attributes:
        precision: B-cubed precision
        recall: B-cubed recall
        f1_score: Harmonic mean of precision and recall
        evaluated_mentions: Mentions present on both sides
        ignored_mentions: Mentions missing from either side
    """

    precision: float
    recall: float
    f1_score: float
    evaluated_mentions: int = 0
    ignored_mentions: int = 0

    def __iter__(self) -> Iterator[float]:
        """Iterate as (precision, recall, f1_score)."""
        yield self.precision
        yield self.recall
        yield self.f1_score

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
            "evaluated_mentions": self.evaluated_mentions,
            "ignored_mentions": self.ignored_mentions,
        }


def bcubed(predicted: dict[str, str], gold: dict[str, str]) -> BCubedScore:
    """Calculate B-cubed precision, recall and F1 for entity resolution.

    Both arguments map a mention id to a cluster id. Mentions missing from
    either side are ignored (a system that never proposes a mention should
    not be scored on it); the number ignored is reported on the result, and
    the result unpacks as ``(precision, recall, f1)``.

    Args:
        predicted: Mention id -> predicted cluster id
        gold: Mention id -> gold cluster id

    Returns:
        BCubedScore over the mentions present on both sides
    """
    shared = sorted(set(predicted) & set(gold))
    ignored = len(set(predicted) | set(gold)) - len(shared)

    if not shared:
        return BCubedScore(0.0, 0.0, 0.0, evaluated_mentions=0, ignored_mentions=ignored)

    predicted_clusters: dict[str, set[str]] = {}
    gold_clusters: dict[str, set[str]] = {}
    for mention in shared:
        predicted_clusters.setdefault(predicted[mention], set()).add(mention)
        gold_clusters.setdefault(gold[mention], set()).add(mention)

    precision_sum = 0.0
    recall_sum = 0.0
    for mention in shared:
        predicted_cluster = predicted_clusters[predicted[mention]]
        gold_cluster = gold_clusters[gold[mention]]
        overlap = len(predicted_cluster & gold_cluster)
        precision_sum += overlap / len(predicted_cluster)
        recall_sum += overlap / len(gold_cluster)

    precision = precision_sum / len(shared)
    recall = recall_sum / len(shared)
    f1_score = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return BCubedScore(
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        evaluated_mentions=len(shared),
        ignored_mentions=ignored,
    )


@dataclass(frozen=True)
class ResolutionMention:
    """A single gold mention in an entity-resolution suite.

    Attributes:
        id: Stable mention id, conventionally "<doc>:<n>"
        text: Surface form as it appears in the document
        entity_type: POLE+O type of the mention
        doc: Id of the document the mention came from
    """

    id: str
    text: str
    entity_type: str
    doc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {"id": self.id, "text": self.text, "type": self.entity_type, "doc": self.doc}


@dataclass
class ResolutionGold:
    """Gold entity-resolution clusters for a document set.

    Attributes:
        mentions: Every gold mention
        clusters: Mention id -> gold cluster id
    """

    mentions: list[ResolutionMention] = field(default_factory=list)
    clusters: dict[str, str] = field(default_factory=dict)

    def cluster_map(self) -> dict[str, str]:
        """Return the mention id -> cluster id map restricted to known mentions."""
        known = {mention.id for mention in self.mentions}
        return {key: value for key, value in self.clusters.items() if key in known}

    def by_doc(self) -> dict[str, list[ResolutionMention]]:
        """Group mentions by their document id (unknown docs land under "")."""
        grouped: dict[str, list[ResolutionMention]] = {}
        for mention in self.mentions:
            grouped.setdefault(mention.doc or "", []).append(mention)
        return grouped

    def score(self, predicted: dict[str, str]) -> BCubedScore:
        """Score a predicted clustering against this gold standard.

        Args:
            predicted: Mention id -> predicted cluster id

        Returns:
            BCubedScore over the mentions present on both sides
        """
        return bcubed(predicted, self.cluster_map())


def load_resolution_gold(path: str | Path) -> ResolutionGold:
    """Load gold entity-resolution clusters from a JSON file.

    The file has the shape::

        {
          "mentions": [{"id": "doc1:m1", "text": "Acme Corp",
                        "type": "ORGANIZATION", "doc": "doc1"}],
          "clusters": {"doc1:m1": "acme"}
        }

    Args:
        path: Path to the JSON file

    Returns:
        ResolutionGold with mentions and their gold cluster ids
    """
    with open(Path(path)) as handle:
        data = json.load(handle)

    mentions = [
        ResolutionMention(
            id=item["id"],
            text=item["text"],
            entity_type=item.get("type", item.get("entity_type", "UNKNOWN")),
            doc=item.get("doc"),
        )
        for item in data.get("mentions", [])
    ]
    return ResolutionGold(mentions=mentions, clusters=dict(data.get("clusters", {})))


def calculate_extraction_metrics(
    expected_entities: list[ExpectedEntity],
    extracted_entities: list[tuple[str, str]],
    latency_ms: float = 0.0,
    token_count: int = 0,
    *,
    expected_relations: list[ExpectedRelation] | None = None,
    extracted_relations: list[tuple[str, str, str]] | None = None,
    match_policy: MatchPolicy = DEFAULT_MATCH_POLICY,
) -> ExtractionMetrics:
    """Calculate comprehensive extraction metrics.

    Groups entities by type and calculates per-type and aggregate metrics.
    When gold relations are supplied, relation metrics are calculated too and
    attached as ``ExtractionMetrics.relation_metrics``.

    Args:
        expected_entities: List of expected entities
        extracted_entities: List of (name, type) tuples
        latency_ms: Extraction latency in milliseconds
        token_count: Number of tokens processed
        expected_relations: Optional gold relations
        extracted_relations: Optional extracted (source, type, target) triples
        match_policy: Matching policy for relations

    Returns:
        ExtractionMetrics with per-type and aggregate metrics
    """
    metrics = ExtractionMetrics(latency_ms=latency_ms, token_count=token_count)

    # Group by entity type
    expected_by_type: dict[str, list[ExpectedEntity]] = {}
    extracted_by_type: dict[str, list[tuple[str, str]]] = {}

    for exp in expected_entities:
        etype = exp.entity_type.upper()
        if etype not in expected_by_type:
            expected_by_type[etype] = []
        expected_by_type[etype].append(exp)

    for ext_name, ext_type in extracted_entities:
        etype = ext_type.upper()
        if etype not in extracted_by_type:
            extracted_by_type[etype] = []
        extracted_by_type[etype].append((ext_name, ext_type))

    # Calculate per-type metrics
    all_types = set(expected_by_type.keys()) | set(extracted_by_type.keys())

    for etype in all_types:
        exp_list = expected_by_type.get(etype, [])
        ext_list = extracted_by_type.get(etype, [])

        type_metrics = calculate_entity_metrics(exp_list, ext_list)
        metrics.entity_metrics[etype] = type_metrics

        metrics.total_true_positives += type_metrics.true_positives
        metrics.total_false_positives += type_metrics.false_positives
        metrics.total_false_negatives += type_metrics.false_negatives

    if expected_relations is not None or extracted_relations is not None:
        metrics.relation_metrics = calculate_relation_metrics(
            expected_relations or [],
            extracted_relations or [],
            match_policy,
        )

    return metrics


__all__ = [
    "DEFAULT_MATCH_POLICY",
    "STRICT_MATCH_POLICY",
    "BCubedScore",
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
