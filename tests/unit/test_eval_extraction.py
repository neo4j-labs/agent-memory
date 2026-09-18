"""Unit tests for the extraction and resolution eval dimensions.

Exercises :class:`~neo4j_agent_memory.memory.eval.EvalMemory` against a
minimal fake client exposing only ``_extractor`` / ``_resolver`` — the two
attributes these dimensions read — so the tests need neither a real
``MemoryClient`` nor a Neo4j connection. The retrieval / audit / preference
dimensions are untouched by this file; they stay covered by
``tests/integration/test_eval_harness.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from neo4j_agent_memory.core.metrics import ExpectedEntity, ExpectedRelation
from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.memory.eval import (
    EvalMemory,
    EvalSuite,
    ExtractionCase,
    ResolutionCase,
)
from neo4j_agent_memory.resolution.base import ResolvedEntity


class _FakeClient:
    """Stand-in for MemoryClient exposing only what EvalMemory reads."""

    def __init__(self, extractor: Any = None, resolver: Any = None) -> None:
        self._extractor = extractor
        self._resolver = resolver


class _FixedExtractor:
    """Extractor stub that returns one fixed ExtractionResult for any text."""

    def __init__(self, result: ExtractionResult) -> None:
        self._result = result
        self.calls: list[str] = []

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        self.calls.append(text)
        return self._result


class _ClusterResolver:
    """Resolver stub that merges mentions sharing a name prefix into one cluster."""

    def __init__(self, cluster_by_name: dict[str, str]) -> None:
        self._cluster_by_name = cluster_by_name

    async def resolve(self, entity_name: str, entity_type: str, **_: Any) -> ResolvedEntity:
        return (await self.resolve_batch([(entity_name, entity_type)]))[0]

    async def resolve_batch(self, entities: list[tuple[str, str]]) -> list[ResolvedEntity]:
        results = []
        for name, entity_type in entities:
            cluster_id = self._cluster_by_name.get(name)
            results.append(
                ResolvedEntity(
                    original_name=name,
                    canonical_name=name,
                    entity_type=entity_type,
                    cluster_id=cluster_id,
                )
            )
        return results

    async def find_matches(self, *_: Any, **__: Any) -> list[Any]:
        return []


class _EpisodeResolver(_ClusterResolver):
    """Resolver stub that clusters a whole episode, like ``OntologyResolver``.

    ``resolve_batch`` deliberately returns singletons, so a test that scores
    above chance proves the harness went through ``resolve_episode``.
    """

    def __init__(self, merges: dict[str, str], stored: dict[str, str] | None = None) -> None:
        super().__init__({})
        self._merges = merges
        self._stored = stored or {}
        self.episodes: list[list[str]] = []

    async def resolve_episode(
        self, entities: list[Any], *, user_identifier: str | None = None
    ) -> list[Any]:
        self.episodes.append([entity.name for entity in entities])
        results = []
        for entity in entities:
            stored_id = self._stored.get(entity.name)
            if stored_id is not None:
                results.append(
                    SimpleNamespace(
                        action="merged",
                        entity_id=stored_id,
                        matched_entity_id=stored_id,
                        matched_entity_name=None,
                        canonical_name=entity.name,
                    )
                )
            elif entity.name in self._merges:
                anchor = self._merges[entity.name]
                results.append(
                    SimpleNamespace(
                        action="merged",
                        entity_id=None,
                        matched_entity_id=None,
                        matched_entity_name=anchor,
                        canonical_name=anchor,
                    )
                )
            else:
                results.append(
                    SimpleNamespace(
                        action="created",
                        entity_id=None,
                        matched_entity_id=None,
                        matched_entity_name=None,
                        canonical_name=entity.name,
                    )
                )
        return results


# -----------------------------------------------------------------------------
# ExtractionCase / ResolutionCase construction
# -----------------------------------------------------------------------------


def test_extraction_case_coerces_dict_and_tuple_entries() -> None:
    case = ExtractionCase(
        text="John Smith met Acme",
        expected_entities=[{"name": "John Smith", "type": "PERSON"}],
        expected_relations=[("John Smith", "EMPLOYED_BY", "Acme")],
    )

    assert case.expected_entities == [ExpectedEntity(name="John Smith", entity_type="PERSON")]
    assert case.expected_relations == [
        ExpectedRelation(source="John Smith", relation_type="EMPLOYED_BY", target="Acme")
    ]


def test_extraction_case_accepts_expected_entity_objects_directly() -> None:
    entity = ExpectedEntity(name="Acme", entity_type="ORGANIZATION")
    case = ExtractionCase(text="Acme", expected_entities=[entity])

    assert case.expected_entity_objects() == [entity]
    assert case.expected_relations == []


def test_resolution_case_is_a_plain_dataclass() -> None:
    case = ResolutionCase(
        mentions=[("Acme", "ORGANIZATION")],
        gold_clusters={"Acme": "acme"},
    )

    assert case.mentions == [("Acme", "ORGANIZATION")]
    assert case.gold_clusters == {"Acme": "acme"}


# -----------------------------------------------------------------------------
# Extraction dimension
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_extraction_scores_perfect_entity_match(mock_extractor: Any) -> None:
    # MockExtractor tokenises on capitalised words: "John", "Smith" -> PERSON,
    # "Acme" -> ORGANIZATION (it special-cases that name), "met" is skipped.
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="John Smith met Acme",
                expected_entities=[
                    {"name": "John", "type": "PERSON"},
                    {"name": "Smith", "type": "PERSON"},
                    {"name": "Acme", "type": "ORGANIZATION"},
                ],
            )
        ]
    )
    client = _FakeClient(extractor=mock_extractor)
    report = await EvalMemory(client).run(suite, dimensions=["extraction"])

    assert report.extraction is not None
    assert report.extraction.cases == 1
    assert report.extraction.score == 1.0
    assert report.extraction.details[0]["scored"] == "entities"
    assert report.skipped == []


@pytest.mark.asyncio
async def test_eval_extraction_penalises_missed_entities(mock_extractor: Any) -> None:
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="John Smith met Acme",
                expected_entities=[
                    {"name": "John", "type": "PERSON"},
                    {"name": "Smith", "type": "PERSON"},
                    {"name": "Acme", "type": "ORGANIZATION"},
                    {"name": "Nonexistent", "type": "ORGANIZATION"},
                ],
            )
        ]
    )
    client = _FakeClient(extractor=mock_extractor)
    report = await EvalMemory(client).run(suite, dimensions=["extraction"])

    assert report.extraction is not None
    # TP=3, FP=0, FN=1 -> precision=1.0, recall=0.75, f1=6/7.
    assert report.extraction.score == pytest.approx(6 / 7)


@pytest.mark.asyncio
async def test_eval_extraction_scores_relations_when_expected() -> None:
    fixed_result = ExtractionResult(
        entities=[
            ExtractedEntity(name="Jane", type="PERSON"),
            ExtractedEntity(name="Acme", type="ORGANIZATION"),
        ],
        relations=[
            ExtractedRelation(source="Jane", target="Acme", relation_type="EMPLOYED_BY"),
        ],
    )
    extractor = _FixedExtractor(fixed_result)
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="Jane works at Acme",
                expected_entities=[{"name": "Jane", "type": "PERSON"}],
                expected_relations=[("Jane", "EMPLOYED_BY", "Acme")],
            )
        ]
    )
    client = _FakeClient(extractor=extractor)
    report = await EvalMemory(client).run(suite, dimensions=["extraction"])

    assert report.extraction is not None
    assert report.extraction.score == 1.0
    assert report.extraction.details[0]["scored"] == "relations"
    assert extractor.calls == ["Jane works at Acme"]


@pytest.mark.asyncio
async def test_eval_extraction_relation_mismatch_scores_zero() -> None:
    fixed_result = ExtractionResult(
        entities=[
            ExtractedEntity(name="Jane", type="PERSON"),
            ExtractedEntity(name="Acme", type="ORGANIZATION"),
        ],
        relations=[
            ExtractedRelation(source="Jane", target="Acme", relation_type="KNOWS"),
        ],
    )
    extractor = _FixedExtractor(fixed_result)
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="Jane works at Acme",
                expected_entities=[],
                expected_relations=[("Jane", "EMPLOYED_BY", "Acme")],
            )
        ]
    )
    client = _FakeClient(extractor=extractor)
    report = await EvalMemory(client).run(suite, dimensions=["extraction"])

    assert report.extraction is not None
    assert report.extraction.score == 0.0


# -----------------------------------------------------------------------------
# Resolution dimension
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_resolution_scores_bcubed_with_mock_resolver(mock_resolver: Any) -> None:
    # MockResolver never merges without `existing_entities`, so each mention
    # becomes its own singleton predicted cluster while gold groups both
    # under "acme": precision 1.0, recall 0.5 per mention -> f1 = 2/3.
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[("Acme", "ORGANIZATION"), ("Acme Corp", "ORGANIZATION")],
                gold_clusters={"Acme": "acme", "Acme Corp": "acme"},
            )
        ]
    )
    client = _FakeClient(resolver=mock_resolver)
    report = await EvalMemory(client).run(suite, dimensions=["resolution"])

    assert report.resolution is not None
    assert report.resolution.cases == 1
    assert report.resolution.score == pytest.approx(2 / 3)
    assert report.skipped == []


@pytest.mark.asyncio
async def test_eval_resolution_uses_cluster_id_when_set() -> None:
    resolver = _ClusterResolver({"Acme": "acme", "Acme Corp": "acme"})
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[("Acme", "ORGANIZATION"), ("Acme Corp", "ORGANIZATION")],
                gold_clusters={"Acme": "acme", "Acme Corp": "acme"},
            )
        ]
    )
    client = _FakeClient(resolver=resolver)
    report = await EvalMemory(client).run(suite, dimensions=["resolution"])

    assert report.resolution is not None
    assert report.resolution.score == 1.0
    assert report.resolution.details[0]["predicted_clusters"] == {
        "Acme": "acme",
        "Acme Corp": "acme",
    }


@pytest.mark.asyncio
async def test_eval_resolution_prefers_resolve_episode() -> None:
    """``resolve_batch`` never clusters, so B-cubed was blind to resolver quality."""
    resolver = _EpisodeResolver(merges={"Acme Corp": "Acme", "ACME": "Acme"})
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[
                    ("Acme", "ORGANIZATION"),
                    ("Acme Corp", "ORGANIZATION"),
                    ("ACME", "ORGANIZATION"),
                ],
                gold_clusters={"Acme": "acme", "Acme Corp": "acme", "ACME": "acme"},
            )
        ]
    )
    client = _FakeClient(resolver=resolver)
    report = await EvalMemory(client).run(suite, dimensions=["resolution"])

    assert resolver.episodes == [["Acme", "Acme Corp", "ACME"]], (
        "the whole case must go through resolve_episode in one call"
    )
    assert report.resolution is not None
    assert report.resolution.score == 1.0
    assert report.resolution.details[0]["clustered_by"] == "resolve_episode"
    assert report.resolution.details[0]["predicted_clusters"] == {
        "Acme": "Acme",
        "Acme Corp": "Acme",
        "ACME": "Acme",
    }


@pytest.mark.asyncio
async def test_eval_resolution_keys_stored_matches_by_entity_id() -> None:
    """Two mentions merging onto one stored node share that node's id."""
    resolver = _EpisodeResolver(merges={}, stored={"Acme": "e1", "Acme Corp": "e1"})
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[("Acme", "ORGANIZATION"), ("Acme Corp", "ORGANIZATION")],
                gold_clusters={"Acme": "acme", "Acme Corp": "acme"},
            )
        ]
    )
    report = await EvalMemory(_FakeClient(resolver=resolver)).run(suite, dimensions=["resolution"])

    assert report.resolution is not None
    assert report.resolution.details[0]["predicted_clusters"] == {"Acme": "e1", "Acme Corp": "e1"}
    assert report.resolution.score == 1.0


@pytest.mark.asyncio
async def test_eval_resolution_penalises_an_over_merging_episode_resolver() -> None:
    """The score must actually move with resolver quality."""
    resolver = _EpisodeResolver(merges={"Acme Bank": "Acme"})
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[("Acme", "ORGANIZATION"), ("Acme Bank", "ORGANIZATION")],
                gold_clusters={"Acme": "acme", "Acme Bank": "acme-bank"},
            )
        ]
    )
    report = await EvalMemory(_FakeClient(resolver=resolver)).run(suite, dimensions=["resolution"])

    assert report.resolution is not None
    assert report.resolution.score == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_eval_resolution_falls_back_to_resolve_batch() -> None:
    """A resolver without ``resolve_episode`` keeps the old path."""
    resolver = _ClusterResolver({"Acme": "acme", "Acme Corp": "acme"})
    suite = EvalSuite(
        resolution=[
            ResolutionCase(
                mentions=[("Acme", "ORGANIZATION"), ("Acme Corp", "ORGANIZATION")],
                gold_clusters={"Acme": "acme", "Acme Corp": "acme"},
            )
        ]
    )
    report = await EvalMemory(_FakeClient(resolver=resolver)).run(suite, dimensions=["resolution"])

    assert report.resolution is not None
    assert report.resolution.details[0]["clustered_by"] == "resolve_batch"
    assert report.resolution.score == 1.0


# -----------------------------------------------------------------------------
# Report folding and skip behaviour
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overall_score_folds_extraction_and_resolution(mock_extractor: Any) -> None:
    resolver = _ClusterResolver({"Acme": "acme"})
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="Acme", expected_entities=[{"name": "Acme", "type": "ORGANIZATION"}]
            )
        ],
        resolution=[
            ResolutionCase(mentions=[("Acme", "ORGANIZATION")], gold_clusters={"Acme": "acme"})
        ],
    )
    client = _FakeClient(extractor=mock_extractor, resolver=resolver)
    report = await EvalMemory(client).run(suite)

    assert report.retrieval is None
    assert report.audit is None
    assert report.preference is None
    assert report.extraction is not None
    assert report.resolution is not None
    assert report.overall_score == pytest.approx(
        (report.extraction.score + report.resolution.score) / 2
    )
    assert report.skipped == []


@pytest.mark.asyncio
async def test_eval_skips_extraction_when_client_has_no_extractor() -> None:
    suite = EvalSuite(
        extraction=[
            ExtractionCase(text="Acme", expected_entities=[{"name": "Acme", "type": "ORG"}])
        ]
    )
    client = _FakeClient(extractor=None)
    report = await EvalMemory(client).run(suite)

    assert report.extraction is None
    assert report.skipped == ["extraction"]
    assert report.overall_score == 0.0


@pytest.mark.asyncio
async def test_eval_skips_resolution_when_client_has_no_resolver() -> None:
    suite = EvalSuite(
        resolution=[ResolutionCase(mentions=[("Acme", "ORG")], gold_clusters={"Acme": "acme"})]
    )
    client = _FakeClient(resolver=None)
    report = await EvalMemory(client).run(suite)

    assert report.resolution is None
    assert report.skipped == ["resolution"]


@pytest.mark.asyncio
async def test_eval_dimensions_filter_suppresses_unrequested_skips(mock_extractor: Any) -> None:
    """A dimension that is not requested is never scored or marked skipped,
    even when it has cases and the client lacks the capability for it."""
    suite = EvalSuite(
        extraction=[
            ExtractionCase(
                text="Acme", expected_entities=[{"name": "Acme", "type": "ORGANIZATION"}]
            )
        ],
        resolution=[ResolutionCase(mentions=[("Acme", "ORG")], gold_clusters={"Acme": "acme"})],
    )
    client = _FakeClient(extractor=mock_extractor, resolver=None)
    report = await EvalMemory(client).run(suite, dimensions=["extraction"])

    assert report.extraction is not None
    assert report.resolution is None
    assert report.skipped == []
