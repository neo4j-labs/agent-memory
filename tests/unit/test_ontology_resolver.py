"""Unit tests for the ontology-aware entity resolver and its ingestion wiring.

No Neo4j: the client is a recording fake that returns whatever rows each test
declares, so every assertion is about the queries the resolver *chooses* and
the bands it lands in.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

import neo4j_agent_memory.resolution.ontology as ontology_module
from neo4j_agent_memory.config.settings import ResolutionConfig
from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractionResult,
)
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.memory.short_term import Message, MessageRole, ShortTermMemory
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
)
from neo4j_agent_memory.resolution.base import EntityResolver
from neo4j_agent_memory.resolution.ontology import (
    LOW_ENTROPY_PENALTY,
    PREFIX_CORROBORATION_MIN,
    PREFIX_RULE_BARE,
    PREFIX_RULE_CORROBORATED,
    OntologyResolver,
    normalize_name,
)
from tests.conftest import MockEmbedder

# =============================================================================
# Fakes
# =============================================================================


class RecordingClient:
    """Neo4j client stand-in that records queries and replays canned rows."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.rows = rows or {}
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def execute_read(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.reads.append((query, parameters or {}))
        return list(self.rows.get(query, []))

    async def execute_write(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.writes.append((query, parameters or {}))
        return list(self.rows.get(query, []))

    def read_count(self, query: str) -> int:
        """How many times ``query`` was issued as a read."""
        return sum(1 for issued, _ in self.reads if issued == query)

    def writes_matching(self, fragment: str) -> list[dict[str, Any]]:
        """Parameters of every write whose Cypher contains ``fragment``."""
        return [params for query, params in self.writes if fragment in query]


def entity_row(
    name: str,
    *,
    entity_id: str | None = None,
    canonical_name: str | None = None,
    aliases: list[str] | None = None,
    embedding: list[float] | None = None,
    description: str | None = None,
    metadata: str | None = None,
) -> dict[str, Any]:
    """Build a row shaped like ``RETURN e`` from the blocking queries."""
    return {
        "e": {
            "id": entity_id or str(uuid4()),
            "name": name,
            "canonical_name": canonical_name or name,
            "aliases": aliases or [],
            "embedding": embedding,
            "description": description,
            "metadata": metadata,
        }
    }


class StubExtractor:
    """Returns a fixed extraction result."""

    name = "stub"

    def __init__(self, result: ExtractionResult) -> None:
        self._result = result

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        return self._result.model_copy(update={"source_text": text})


def make_resolver(
    client: RecordingClient,
    *,
    ontology: OntologyDocument | None = None,
    embedder: Any = None,
    **config_kwargs: Any,
) -> OntologyResolver:
    """Build a resolver over a recording client."""
    return OntologyResolver(
        client,  # type: ignore[arg-type]
        ontology=ontology,
        embedder=embedder,
        config=ResolutionConfig(**config_kwargs),
    )


def vendor_ontology(**thresholds: float) -> OntologyDocument:
    """A one-label ontology with an alias gazetteer for Acme."""
    return OntologyDocument(
        domain=DomainInfo(id="vendors", name="vendors"),
        entity_types=[
            EntityTypeDef(
                label="Vendor",
                pole_type="ORGANIZATION",
                description="A supplier.",
                aliases={"Acme Corporation": ["ACME", "Acme Co."]},
                **thresholds,
            )
        ],
    )


def shared_surface_ontology() -> OntologyDocument:
    """Two canonicals that declare the *same* short surface form."""
    return OntologyDocument(
        domain=DomainInfo(id="labels", name="labels"),
        entity_types=[
            EntityTypeDef(
                label="Label",
                pole_type="ORGANIZATION",
                description="A record label or a computer company.",
                aliases={
                    "Apple Inc": ["Apple"],
                    "Apple Records": ["Apple"],
                },
            )
        ],
    )


# =============================================================================
# Normalization
# =============================================================================


class TestNormalizeName:
    """Deterministic, model-free name normalization."""

    @pytest.mark.parametrize(
        ("name", "entity_type", "expected"),
        [
            ("Acme Corp", "ORGANIZATION", "acme"),
            ("ACME Corporation", "ORGANIZATION", "acme"),
            ("Acme, Inc.", "ORGANIZATION", "acme"),
            ("Northwind Logistics Ltd", "ORGANIZATION", "northwind logistics"),
            # Legal suffixes are organization-only: a person keeps every token.
            ("Acme Corp", "PERSON", "acme corp"),
            ("Dr. Ada Lovelace Jr.", "PERSON", "ada lovelace"),
            ("Prof. Ada Lovelace III", "PERSON", "ada lovelace"),
            ("The Beatles", "ORGANIZATION", "beatles"),
            ("  Multiple   Spaces ", None, "multiple spaces"),
        ],
    )
    def test_keys(self, name: str, entity_type: str | None, expected: str) -> None:
        assert normalize_name(name, entity_type).key == expected

    def test_a_fully_stripped_name_keeps_a_key(self) -> None:
        """Stripping everything would make every such name collide."""
        assert normalize_name("The", "ORGANIZATION").key == "the"

    def test_tokens_head_tail_and_initials(self) -> None:
        normalized = normalize_name("Northwind Logistics Ltd", "ORGANIZATION")
        assert normalized.tokens == ("northwind", "logistics")
        assert normalized.head == "northwind"
        assert normalized.tail == "logistics"
        assert normalized.initials == "nl"

    @pytest.mark.parametrize(
        ("name", "is_acronym"),
        [
            ("IBM", True),
            ("NWL", True),
            ("N.W.L.", True),
            ("Acme", False),
            ("ACME CORP", False),
            # Two letters: "IT", "US", "AM" match far too much.
            ("IT", False),
            ("I.T.", False),
        ],
    )
    def test_acronym_flag(self, name: str, is_acronym: bool) -> None:
        assert normalize_name(name).is_acronym is is_acronym

    def test_entropy_is_low_for_short_repetitive_names(self) -> None:
        assert normalize_name("N600").entropy < 2.5
        assert normalize_name("Northwind Logistics").entropy > 2.5


# =============================================================================
# Similarity primitives
# =============================================================================


#: Name and context pairs the resolver actually sees, used to hold the
#: :mod:`difflib` fallbacks to RapidFuzz's scores.
SIMILARITY_PAIRS: list[tuple[str, str]] = [
    # The pair that exposed the divergence: one fact, two phrasings, sharing
    # their vocabulary but not their character offsets.
    (
        "Freight out of the Rotterdam depot is handled by Northwind.",
        "Northwind Logistics ships freight out of the Rotterdam depot.",
    ),
    ("northwind", "northwind logistics"),
    ("acme", "acme corporation"),
    ("apple", "apple bank"),
    ("kansas", "kansas city"),
    ("new york university", "new york"),
    ("ada lovelace", "lovelace ada"),
    ("isabel turner", "turner"),
    ("john smith", "jon smyth"),
    ("n600", "n610"),
    ("acme", "globex"),
]


class TestSimilarityFallbacks:
    """The stdlib twins of the RapidFuzz scorers score like RapidFuzz.

    ``rapidfuzz`` is an optional extra, and the CI ``test`` job installs no
    extras — so in CI the fallbacks are what decide every merge. A fallback
    that scores differently means the same corpus resolves differently
    depending on which extras happen to be installed, which is exactly what
    :meth:`TestEpisodeResolution.test_independent_context_still_corroborates`
    caught: the old positional ``SequenceMatcher`` ratio put a corroborating
    context pair under :data:`PREFIX_CORROBORATION_MIN`.
    """

    #: ``difflib``'s Ratcliff-Obershelp matching is not RapidFuzz's indel
    #: distance, so the twins mirror the scorer *composition*, not the metric.
    TOLERANCE = 0.08

    @staticmethod
    def _force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the module behave as if ``rapidfuzz`` were not installed."""
        monkeypatch.setattr(ontology_module, "_rapidfuzz", lambda: None)

    @pytest.mark.parametrize("scorer", ["fuzzy_similarity", "context_similarity"])
    def test_the_two_implementations_agree(
        self, scorer: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every pair, both implementations, one report of what drifted.

        Needs RapidFuzz to have something to compare against, so this is the
        developer-environment half of the guard; the behavioural assertions
        below run everywhere, including the CI job that has no extras.
        """
        pytest.importorskip("rapidfuzz")
        score = getattr(ontology_module, scorer)

        with_rapidfuzz = [score(left, right) for left, right in SIMILARITY_PAIRS]
        self._force_fallback(monkeypatch)
        without = [score(left, right) for left, right in SIMILARITY_PAIRS]

        divergent = {
            pair: (expected, actual)
            for pair, expected, actual in zip(SIMILARITY_PAIRS, with_rapidfuzz, without)
            if expected is None or actual is None or abs(expected - actual) > self.TOLERANCE
        }
        assert not divergent, f"{scorer} fallback diverges from RapidFuzz on {divergent}"

    def test_context_fallback_clears_the_corroboration_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runs with or without RapidFuzz, and pins the regression itself."""
        from difflib import SequenceMatcher

        left, right = SIMILARITY_PAIRS[0]
        # What the fallback used to be: a positional ratio over two windows
        # onto the same fact, which does not clear the floor.
        positional = SequenceMatcher(None, left.lower(), right.lower()).ratio()
        assert positional < PREFIX_CORROBORATION_MIN

        self._force_fallback(monkeypatch)
        score = ontology_module.context_similarity(left, right)

        assert score is not None
        assert score >= PREFIX_CORROBORATION_MIN

    def test_fuzzy_fallback_is_insensitive_to_word_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``token_sort`` semantics, not character offsets."""
        self._force_fallback(monkeypatch)

        assert ontology_module.fuzzy_similarity("ada lovelace", "lovelace ada") == 1.0

    def test_fuzzy_fallback_scores_whole_substring_containment_high(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``partial``/``WRatio`` semantics, which ``_score`` exists to cap.

        Without them the fallback rated "northwind" against "northwind
        logistics" at 0.64, so the whole-substring safety net never mattered
        and the two environments banded prefix pairs differently.
        """
        self._force_fallback(monkeypatch)

        assert ontology_module.fuzzy_similarity("northwind", "northwind logistics") >= 0.85


# =============================================================================
# Scoring rules
# =============================================================================


@pytest.mark.asyncio
class TestScoringRules:
    """Each rule, in the band it is supposed to produce."""

    async def test_exact_normalized_match_merges(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Acme Corp", entity_id="e1")]}
        )
        resolution = await make_resolver(client).resolve_one("ACME Corporation", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.match_type == "exact"
        assert resolution.score == 1.0
        assert resolution.matched_entity_id == "e1"
        assert resolution.canonical_name == "Acme Corp"

    async def test_declared_alias_merges_without_any_similarity(self) -> None:
        """A gazetteer beats similarity: 'ACME' and 'Acme Corporation' agree."""
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [
                    entity_row("Acme Corporation", entity_id="e1")
                ]
            }
        )
        resolver = make_resolver(client, ontology=vendor_ontology())
        resolution = await resolver.resolve_one("Acme Co.", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.match_type in {"alias", "exact"}
        assert resolution.matched_entity_id == "e1"

    async def test_alias_surface_forms_become_blocking_keys(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client, ontology=vendor_ontology())
        await resolver.resolve_one("ACME", "ORGANIZATION")

        keys_query = [
            params
            for query, params in client.reads
            if query == queries.FIND_ENTITIES_BY_NORMALIZED_KEYS
        ]
        assert keys_query, "the exact-key blocking query must run"
        assert "acme corporation" in keys_query[0]["keys"]
        assert "acme co." in keys_query[0]["keys"]

    async def test_alias_gazetteer_can_be_switched_off(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client, ontology=vendor_ontology(), use_alias_gazetteer=False)
        await resolver.resolve_one("ACME", "ORGANIZATION")

        params = [
            params
            for query, params in client.reads
            if query == queries.FIND_ENTITIES_BY_NORMALIZED_KEYS
        ][0]
        assert "acme corporation" not in params["keys"]

    async def test_a_surface_form_two_canonicals_share_merges_nothing(self) -> None:
        """Unioning the two groups made "Apple Inc" an alias of "Apple Records"."""
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [
                    entity_row("Apple Records", entity_id="records-1")
                ]
            }
        )
        resolver = make_resolver(client, ontology=shared_surface_ontology())

        resolution = await resolver.resolve_one("Apple Inc", "ORGANIZATION")

        assert resolution.match_type != "alias"
        assert resolution.action != "merged", (
            "'Apple' is declared by both canonicals, so it identifies neither"
        )

    async def test_a_group_of_its_own_still_merges_at_one(self) -> None:
        """Per-group keying must not cost an unambiguous gazetteer hit."""
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [
                    entity_row("Apple Records", entity_id="records-1")
                ]
            }
        )
        resolver = make_resolver(client, ontology=shared_surface_ontology())

        resolution = await resolver.resolve_one("apple records", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.matched_entity_id == "records-1"

    async def test_an_unambiguous_alias_group_still_merges(self) -> None:
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [
                    entity_row("Acme Corporation", entity_id="e1")
                ]
            }
        )
        resolver = make_resolver(client, ontology=vendor_ontology())

        resolution = await resolver.resolve_one("Acme Co.", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.matched_entity_id == "e1"

    async def test_acronym_expansion_scores_below_one(self) -> None:
        client = RecordingClient(
            {
                queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING: [
                    entity_row("International Business Machines", entity_id="e1")
                ]
            }
        )
        resolver = make_resolver(client, embedder=MockEmbedder(dimensions=64))
        resolution = await resolver.resolve_one("IBM", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.match_type == "acronym"
        assert resolution.score == pytest.approx(0.97)

    async def test_bare_prefix_lands_in_the_review_band(self) -> None:
        """'Apple' and 'Apple Bank' are different companies."""
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [entity_row("Apple Bank", entity_id="e1")]}
        )
        resolution = await make_resolver(client).resolve_one("Apple", "ORGANIZATION")

        assert resolution.action == "review"
        assert resolution.match_type == "prefix"
        assert resolution.score == pytest.approx(0.88)
        assert resolution.matched_entity_id == "e1"

    async def test_corroborated_prefix_merges(self) -> None:
        """Context (or embedding) agreement is what clears the auto-merge line."""
        embedder = MockEmbedder(dimensions=64)
        candidate_embedding = await embedder.embed("Northwind Logistics")
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [
                    entity_row(
                        "Northwind Logistics",
                        entity_id="e1",
                        embedding=candidate_embedding,
                    )
                ]
            }
        )
        resolver = make_resolver(client, embedder=embedder)
        resolution = await resolver.resolve_one("Northwind", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.score == pytest.approx(0.92)

    @pytest.mark.parametrize(
        ("mention", "stored", "entity_type"),
        [
            # RapidFuzz rates every whole-token prefix pair at exactly 0.90,
            # which *was* the auto-merge line: with the rule limited to
            # organizations, each of these merged silently and appended the
            # real name as an alias with no SAME_AS edge to recover from.
            ("Kansas", "Kansas City", "LOCATION"),
            ("Kansas City", "Kansas", "LOCATION"),
            ("Paris", "Paris Hilton", "PERSON"),
            ("Paris Hilton", "Paris", "PERSON"),
            ("Ford", "Ford Explorer", "OBJECT"),
            ("Ada", "Ada Lovelace", "PERSON"),
            ("New York", "New York University", "LOCATION"),
            ("Apple", "Apple Bank", "ORGANIZATION"),
        ],
    )
    async def test_a_bare_prefix_never_merges_whatever_the_type(
        self, mention: str, stored: str, entity_type: str
    ) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [entity_row(stored, entity_id="e1")]}
        )
        resolution = await make_resolver(client).resolve_one(mention, entity_type)

        assert resolution.action == "review", f"{mention!r} must not merge onto {stored!r}"
        assert resolution.match_type == "prefix"
        assert resolution.score == pytest.approx(PREFIX_RULE_BARE)
        assert resolution.matched_entity_id == "e1"
        # The mention keeps its own name, so the caller creates its own node.
        assert resolution.canonical_name == mention

    async def test_fuzzy_alone_cannot_merge_a_superstring_pair(self) -> None:
        """Containment that is not a whole-token prefix is capped too.

        "Isabel Turner" contains "Turner" mid-name, so the prefix rule does
        not fire, but RapidFuzz still rates the pair at the auto-merge line.
        """
        resolver = make_resolver(
            RecordingClient(), auto_merge_threshold=0.80, review_threshold=0.70
        )
        mention = resolver._to_mention(0, ExtractedEntity(name="Turner", type="PERSON"))
        score, _ = resolver._score(
            mention, resolver._candidate_from_name("Isabel Turner", "PERSON")
        )

        assert score <= resolver.config.review_threshold

    async def test_a_corroborated_prefix_still_merges_for_any_type(self) -> None:
        """The rule holds prefixes back; evidence still clears the line."""
        embedder = MockEmbedder(dimensions=64)
        candidate_embedding = await embedder.embed("Kansas City")
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [
                    entity_row("Kansas City", entity_id="e1", embedding=candidate_embedding)
                ]
            }
        )
        resolution = await make_resolver(client, embedder=embedder).resolve_one(
            "Kansas", "LOCATION"
        )

        assert resolution.action == "merged"
        assert resolution.score == pytest.approx(PREFIX_RULE_CORROBORATED)

    @pytest.mark.parametrize(
        ("acronym", "expansion", "entity_type"),
        [
            # Two letters match far too much to be identity evidence.
            ("IT", "Isabel Turner", "PERSON"),
            ("IT", "Interstate Trucking", "ORGANIZATION"),
            # Three letters are a real convention -- but only for orgs.
            ("ABC", "Ada Beatrice Carter", "PERSON"),
        ],
    )
    async def test_the_acronym_rule_is_narrow(
        self, acronym: str, expansion: str, entity_type: str
    ) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [entity_row(expansion, entity_id="e1")]}
        )
        resolution = await make_resolver(client).resolve_one(acronym, entity_type)

        assert resolution.action != "merged"
        assert resolution.match_type != "acronym"

    async def test_a_three_letter_org_acronym_still_expands(self) -> None:
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [
                    entity_row("Northwind Wholesale Logistics", entity_id="e1")
                ]
            }
        )
        resolution = await make_resolver(client).resolve_one("NWL", "ORGANIZATION")

        assert resolution.action == "merged"
        assert resolution.match_type == "acronym"

    async def test_the_low_entropy_penalty_is_order_independent(self) -> None:
        """'Dana Whitfield' -> 'Dana' merged while the reverse did not."""
        resolver = make_resolver(RecordingClient())
        long_first, _ = resolver._score(
            resolver._to_mention(0, ExtractedEntity(name="Dana Whitfield", type="PERSON")),
            resolver._candidate_from_name("Dana", "PERSON"),
        )
        short_first, _ = resolver._score(
            resolver._to_mention(0, ExtractedEntity(name="Dana", type="PERSON")),
            resolver._candidate_from_name("Dana Whitfield", "PERSON"),
        )

        assert long_first == pytest.approx(short_first)
        assert long_first < resolver.config.auto_merge_threshold

    async def test_the_low_entropy_penalty_applies_to_the_shorter_side(self) -> None:
        """Only the mention's entropy was checked, so one order escaped it."""
        resolver = make_resolver(RecordingClient())
        penalised, _ = resolver._score(
            resolver._to_mention(0, ExtractedEntity(name="Alexander Ross", type="PERSON")),
            resolver._candidate_from_name("Ada", "PERSON"),
        )
        reversed_, _ = resolver._score(
            resolver._to_mention(0, ExtractedEntity(name="Ada", type="PERSON")),
            resolver._candidate_from_name("Alexander Ross", "PERSON"),
        )

        assert penalised == pytest.approx(reversed_)

    async def test_low_entropy_names_are_penalised(self) -> None:
        """'N600' vs 'N600X' is exactly where a resolver over-merges."""
        resolver = make_resolver(RecordingClient())
        mention = resolver._to_mention(0, ExtractedEntity(name="N600", type="OBJECT"))
        candidate = resolver._candidate_from_name("N600X", "OBJECT")

        penalised, _ = resolver._score(mention, candidate)
        unpenalised, _ = resolver._score(
            resolver._to_mention(0, ExtractedEntity(name="Northwind", type="OBJECT")),
            resolver._candidate_from_name("NorthwindX", "OBJECT"),
        )

        assert penalised == pytest.approx(unpenalised - LOW_ENTROPY_PENALTY, abs=0.06)
        assert penalised < 0.85

    async def test_nothing_similar_creates(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Globex", entity_id="e1")]}
        )
        resolution = await make_resolver(client).resolve_one("Acme", "ORGANIZATION")

        assert resolution.action == "created"
        assert resolution.matched_entity_id is None
        assert resolution.canonical_name == "Acme"


class TestNormalizedKeyQueryGuards:
    """The blocking queries must not warn on a database with no aliases yet.

    ``e.aliases`` and ``e.canonical_name`` are optional properties. Naming
    either directly in a WHERE clause — even inside ``coalesce(e.prop, ...)``
    — makes the Neo4j driver log a ``property key does not exist`` warning to
    stderr on a database where no entity has ever had the property set (see
    CLAUDE.md item 23). Both must instead be guarded with
    ``'prop' IN keys(e)`` before being read.
    """

    @pytest.mark.parametrize(
        "query",
        [
            queries.FIND_ENTITIES_BY_NORMALIZED_KEYS,
            queries.FIND_ENTITIES_BY_NORMALIZED_KEYS_FOR_USER,
        ],
    )
    def test_aliases_and_canonical_name_are_guarded_by_keys(self, query: str) -> None:
        assert "'aliases' IN keys(e)" in query
        assert "'canonical_name' IN keys(e)" in query
        # Neither may be referenced unguarded, including inside coalesce():
        # that still triggers the warning on a fresh database.
        assert "coalesce(e.aliases" not in query
        assert "coalesce(e.canonical_name" not in query


# =============================================================================
# Bands, blocking and scope
# =============================================================================


@pytest.mark.asyncio
class TestBandsAndBlocking:
    """Thresholds, per-type overrides, batching and tenant scoping."""

    async def test_thresholds_come_from_the_config(self) -> None:
        rows = {queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [entity_row("Apple Bank", entity_id="e1")]}
        # 0.88 (bare prefix) sits above a lowered auto-merge line.
        merged = await make_resolver(
            RecordingClient(rows), auto_merge_threshold=0.80, review_threshold=0.70
        ).resolve_one("Apple", "ORGANIZATION")
        assert merged.action == "merged"

        # ...and below a raised review line.
        created = await make_resolver(
            RecordingClient(rows), auto_merge_threshold=0.95, review_threshold=0.90
        ).resolve_one("Apple", "ORGANIZATION")
        assert created.action == "created"

    async def test_per_type_overrides_win(self) -> None:
        """EntityTypeDef thresholds beat the global ones for that type."""
        rows = {
            queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING: [
                entity_row("International Business Machines", entity_id="e1")
            ]
        }
        embedder = MockEmbedder(dimensions=64)
        resolver = make_resolver(
            RecordingClient(rows),
            embedder=embedder,
            ontology=vendor_ontology(resolution_threshold=0.99, review_threshold=0.95),
        )
        # An acronym expansion scores 0.97: merged by default, review at 0.99.
        resolution = await resolver.resolve_one("IBM", "ORGANIZATION")
        assert resolution.action == "review"
        assert resolution.score == pytest.approx(0.97)

        default_bands = make_resolver(
            RecordingClient(rows), embedder=embedder, ontology=vendor_ontology()
        )
        assert (await default_bands.resolve_one("IBM", "ORGANIZATION")).action == "merged"

    async def test_one_keys_query_per_type_per_episode(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client)
        entities = [
            ExtractedEntity(name="Acme", type="ORGANIZATION"),
            ExtractedEntity(name="Globex", type="ORGANIZATION"),
            ExtractedEntity(name="Initech", type="ORGANIZATION"),
            ExtractedEntity(name="Ada Lovelace", type="PERSON"),
            ExtractedEntity(name="Alan Turing", type="PERSON"),
        ]

        resolutions = await resolver.resolve_episode(entities)

        assert len(resolutions) == len(entities)
        assert client.read_count(queries.FIND_ENTITIES_BY_NORMALIZED_KEYS) == 2

    async def test_a_deterministic_match_skips_the_wider_buckets(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Acme", entity_id="e1")]}
        )
        await make_resolver(client).resolve_one("Acme", "ORGANIZATION")

        assert client.read_count(queries.FIND_ENTITIES_BY_TOKEN_PREFIX) == 0
        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING) == 0

    async def test_oversized_prefix_buckets_are_dropped(self) -> None:
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [
                    entity_row(f"Apple {index}", entity_id=f"e{index}") for index in range(61)
                ]
            }
        )
        resolution = await make_resolver(client).resolve_one("Apple", "ORGANIZATION")

        assert resolution.action == "created"

    async def test_embedding_blocking_is_optional(self) -> None:
        embedder = MockEmbedder(dimensions=64)
        client = RecordingClient()
        await make_resolver(client, embedder=embedder).resolve_one("Acme", "ORGANIZATION")
        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING) == 1

        client = RecordingClient()
        await make_resolver(client, embedder=embedder, use_embedding_blocking=False).resolve_one(
            "Acme", "ORGANIZATION"
        )
        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING) == 0

    async def test_user_scope_selects_the_tenant_scoped_queries(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client, scope="user")

        await resolver.resolve_one("Acme", "ORGANIZATION", user_identifier="alice")

        keys_reads = [
            params
            for query, params in client.reads
            if query == queries.FIND_ENTITIES_BY_NORMALIZED_KEYS_FOR_USER
        ]
        prefix_reads = [
            params
            for query, params in client.reads
            if query == queries.FIND_ENTITIES_BY_TOKEN_PREFIX_FOR_USER
        ]
        assert keys_reads and keys_reads[0]["user_identifier"] == "alice"
        assert prefix_reads and prefix_reads[0]["user_identifier"] == "alice"
        assert client.read_count(queries.FIND_ENTITIES_BY_NORMALIZED_KEYS) == 0

    async def test_user_scope_also_scopes_the_vector_index_bucket(self) -> None:
        """The vector index is global; the unscoped query leaked across tenants."""
        client = RecordingClient()
        resolver = make_resolver(client, embedder=MockEmbedder(dimensions=64), scope="user")

        await resolver.resolve_one("Alice Chen", "PERSON", user_identifier="tenant-b")

        embedding_reads = [
            params
            for query, params in client.reads
            if query == queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING_FOR_USER
        ]
        assert embedding_reads, "the tenant-scoped vector query must be the one issued"
        assert embedding_reads[0]["user_identifier"] == "tenant-b"
        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING) == 0

    async def test_global_scope_keeps_the_unscoped_vector_query(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client, embedder=MockEmbedder(dimensions=64))

        await resolver.resolve_one("Alice Chen", "PERSON", user_identifier="tenant-b")

        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING) == 1
        assert client.read_count(queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING_FOR_USER) == 0

    async def test_raw_surface_forms_reach_the_exact_key_bucket(self) -> None:
        """A trailing period must not cost the exact bucket its hit."""
        client = RecordingClient()
        await make_resolver(client).resolve_one("Acme Corp.", "ORGANIZATION")

        params = [
            params
            for query, params in client.reads
            if query == queries.FIND_ENTITIES_BY_NORMALIZED_KEYS
        ][0]
        # The predicate compares the raw stored ``e.name``, so the raw form
        # goes in with and without its trailing punctuation.
        assert "acme corp." in params["keys"]
        assert "acme corp" in params["keys"]
        assert "acme" in params["keys"]

    async def test_global_scope_ignores_the_user_identifier(self) -> None:
        client = RecordingClient()
        await make_resolver(client).resolve_one("Acme", "ORGANIZATION", user_identifier="alice")

        assert client.read_count(queries.FIND_ENTITIES_BY_NORMALIZED_KEYS) == 1
        assert client.read_count(queries.FIND_ENTITIES_BY_NORMALIZED_KEYS_FOR_USER) == 0

    async def test_blocking_never_crosses_types(self) -> None:
        client = RecordingClient()
        await make_resolver(client).resolve_one("Apple", "OBJECT")

        for _, params in client.reads:
            assert params["type"] == "OBJECT"


# =============================================================================
# Two-pass episode resolution
# =============================================================================


@pytest.mark.asyncio
class TestEpisodeResolution:
    """The second pass is what stops one message creating two nodes."""

    async def test_variants_in_one_message_collapse_onto_the_first_mention(self) -> None:
        client = RecordingClient()
        resolver = make_resolver(client)

        resolutions = await resolver.resolve_episode(
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Acme Corp", type="ORGANIZATION"),
            ]
        )

        assert resolutions[0].action == "created"
        assert resolutions[1].action == "merged"
        # No stored entity to anchor to: the merge names the first mention.
        assert resolutions[1].matched_entity_id is None
        assert resolutions[1].matched_entity_name == "Acme"

    @pytest.mark.parametrize(
        ("first", "second", "entity_type"),
        [
            # One message, one context window: the two mentions "corroborated"
            # each other at context similarity 1.0 and merged at 0.92, even
            # though the sentence says the opposite.
            ("Apple Bank", "Apple", "ORGANIZATION"),
            ("New York University", "New York", "LOCATION"),
        ],
    )
    async def test_two_mentions_of_one_message_do_not_corroborate_each_other(
        self, first: str, second: str, entity_type: str
    ) -> None:
        context = f"{first} has no relationship with {second}, which is something else."
        resolver = make_resolver(RecordingClient())

        resolutions = await resolver.resolve_episode(
            [
                ExtractedEntity(name=first, type=entity_type, context=context),
                ExtractedEntity(name=second, type=entity_type, context=context),
            ]
        )

        assert resolutions[0].action == "created"
        assert resolutions[1].action == "review", (
            f"{second!r} must not merge into {first!r} on shared-window context alone"
        )
        assert resolutions[1].score == pytest.approx(PREFIX_RULE_BARE)
        assert resolutions[1].matched_entity_name == first

    async def test_independent_context_still_corroborates(self) -> None:
        """Dropping same-message context must not disable the signal entirely."""
        stored_context = "Northwind Logistics ships freight out of the Rotterdam depot."
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [
                    entity_row(
                        "Northwind Logistics",
                        entity_id="e1",
                        description=stored_context,
                    )
                ]
            }
        )
        resolution = await make_resolver(client).resolve_one(
            "Northwind",
            "ORGANIZATION",
            context="Freight out of the Rotterdam depot is handled by Northwind.",
        )

        assert resolution.action == "merged"
        assert resolution.score == pytest.approx(PREFIX_RULE_CORROBORATED)

    async def test_a_stored_match_wins_over_an_episode_sibling(self) -> None:
        client = RecordingClient(
            {
                queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [
                    entity_row("Acme Corporation", entity_id="stored-1")
                ]
            }
        )
        resolver = make_resolver(client)

        resolutions = await resolver.resolve_episode(
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Acme Corp", type="ORGANIZATION"),
            ]
        )

        assert [r.matched_entity_id for r in resolutions] == ["stored-1", "stored-1"]
        assert {r.action for r in resolutions} == {"merged"}

    async def test_unrelated_mentions_stay_separate(self) -> None:
        resolver = make_resolver(RecordingClient())

        resolutions = await resolver.resolve_episode(
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Globex", type="ORGANIZATION"),
            ]
        )

        assert [r.action for r in resolutions] == ["created", "created"]

    async def test_empty_episode(self) -> None:
        assert await make_resolver(RecordingClient()).resolve_episode([]) == []

    async def test_mentions_are_embedded_in_one_batch(self) -> None:
        class CountingEmbedder(MockEmbedder):
            def __init__(self) -> None:
                super().__init__(dimensions=64)
                self.batch_calls = 0

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                self.batch_calls += 1
                return await super().embed_batch(texts)

        embedder = CountingEmbedder()
        resolver = make_resolver(RecordingClient(), embedder=embedder)
        await resolver.resolve_episode(
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Globex", type="ORGANIZATION"),
                ExtractedEntity(name="Ada Lovelace", type="PERSON"),
            ]
        )

        assert embedder.batch_calls == 1


# =============================================================================
# EntityResolver protocol
# =============================================================================


def test_satisfies_the_entity_resolver_protocol() -> None:
    assert isinstance(make_resolver(RecordingClient()), EntityResolver)


@pytest.mark.asyncio
class TestProtocolSurface:
    """The pre-v0.7 ``EntityResolver`` semantics survive."""

    async def test_existing_entities_are_scored_in_memory(self) -> None:
        client = RecordingClient()
        resolved = await make_resolver(client).resolve(
            "ACME Corporation", "ORGANIZATION", existing_entities=["Globex", "Acme Corp"]
        )

        assert resolved.canonical_name == "Acme Corp"
        assert resolved.match_type == "exact"
        assert resolved.merged_from == ["ACME Corporation"]
        assert client.reads == [], "existing_entities must not hit the database"

    async def test_existing_entities_without_a_match(self) -> None:
        resolved = await make_resolver(RecordingClient()).resolve(
            "Acme", "ORGANIZATION", existing_entities=["Globex"]
        )

        assert resolved.canonical_name == "Acme"
        assert resolved.match_type == "none"

    async def test_resolve_without_existing_entities_queries_the_graph(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Acme Corp", entity_id="e1")]}
        )
        resolved = await make_resolver(client).resolve("ACME Corporation", "ORGANIZATION")

        assert resolved.canonical_name == "Acme Corp"
        assert client.read_count(queries.FIND_ENTITIES_BY_NORMALIZED_KEYS) == 1

    async def test_find_matches_scores_candidates(self) -> None:
        matches = await make_resolver(RecordingClient()).find_matches(
            "Acme Corp", "ORGANIZATION", ["ACME Corporation", "Globex"]
        )

        assert [match.entity2_name for match in matches] == ["ACME Corporation"]
        assert matches[0].similarity_score == pytest.approx(1.0)


# =============================================================================
# Ingestion wiring
# =============================================================================


def short_term_with(
    client: RecordingClient,
    entities: list[ExtractedEntity],
    *,
    resolver: Any = None,
    config: ResolutionConfig | None = None,
) -> ShortTermMemory:
    """A ShortTermMemory whose extractor returns ``entities``."""
    return ShortTermMemory(
        client,  # type: ignore[arg-type]
        extractor=StubExtractor(ExtractionResult(entities=entities, relations=[], preferences=[])),
        resolver=resolver,
        resolution_config=config,
    )


def a_message(content: str = "Acme shipped it") -> Message:
    return Message(id=uuid4(), role=MessageRole.USER, content=content)


@pytest.mark.asyncio
class TestIngestionWiring:
    """``_persist_entities`` is the one entity-writing path."""

    async def test_merged_mention_links_the_existing_node_and_adds_an_alias(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_NORMALIZED_KEYS: [entity_row("Acme Corp", entity_id="e1")]}
        )
        memory = short_term_with(
            client,
            [ExtractedEntity(name="ACME Corporation", type="ORGANIZATION")],
            resolver=make_resolver(client),
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message())

        assert client.writes_matching("MERGE (e:Entity") == [], "no second node for a variant"
        aliases = client.writes_matching("SET e.aliases")
        assert aliases and aliases[0] == {"id": "e1", "alias": "ACME Corporation"}
        links = client.writes_matching("MERGE (m)-[r:MENTIONS]->(e)")
        assert links and links[0]["entity_id"] == "e1"

    async def test_review_mention_creates_a_node_and_a_pending_same_as(self) -> None:
        client = RecordingClient(
            {queries.FIND_ENTITIES_BY_TOKEN_PREFIX: [entity_row("Apple Bank", entity_id="e1")]}
        )
        memory = short_term_with(
            client,
            [ExtractedEntity(name="Apple", type="ORGANIZATION")],
            resolver=make_resolver(client),
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message("Apple shipped it"))

        created = client.writes_matching("MERGE (e:Entity")
        assert len(created) == 1
        same_as = client.writes_matching("SAME_AS")
        assert same_as and same_as[0]["status"] == "pending"
        assert same_as[0]["target_id"] == "e1"
        assert same_as[0]["source_id"] == created[0]["id"]

    async def test_created_mention_records_the_resolution_in_metadata(self) -> None:
        client = RecordingClient()
        memory = short_term_with(
            client,
            [
                ExtractedEntity(
                    name="Globex",
                    type="ORGANIZATION",
                    start_pos=0,
                    end_pos=6,
                    context="Globex shipped it",
                    extractor="stub",
                    attributes={"gliner2_label": "company"},
                )
            ],
            resolver=make_resolver(client),
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message("Globex shipped it"))

        created = client.writes_matching("MERGE (e:Entity")
        assert len(created) == 1
        metadata = json.loads(created[0]["metadata"])
        assert metadata["resolution"]["action"] == "created"
        assert metadata["extracted_by"] == "stub"
        assert metadata["gliner2_label"] == "company"
        assert metadata["start_pos"] == 0 and metadata["end_pos"] == 6
        assert metadata["context"] == "Globex shipped it"

    async def test_variants_in_one_message_produce_one_node(self) -> None:
        client = RecordingClient()
        memory = short_term_with(
            client,
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Acme Corp", type="ORGANIZATION"),
            ],
            resolver=make_resolver(client),
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message())

        created = client.writes_matching("MERGE (e:Entity")
        assert len(created) == 1
        links = client.writes_matching("MERGE (m)-[r:MENTIONS]->(e)")
        assert {link["entity_id"] for link in links} == {created[0]["id"]}
        aliases = client.writes_matching("SET e.aliases")
        assert aliases and aliases[0]["alias"] == "Acme Corp"

    async def test_resolve_on_ingest_false_reproduces_the_old_path(self) -> None:
        entities = [
            ExtractedEntity(name="Acme", type="ORGANIZATION"),
            ExtractedEntity(name="Acme Corp", type="ORGANIZATION"),
        ]
        switched_off = RecordingClient()
        memory = short_term_with(
            switched_off,
            entities,
            resolver=make_resolver(switched_off),
            config=ResolutionConfig(resolve_on_ingest=False),
        )
        await memory._extract_and_link_entities(a_message())

        no_resolver = RecordingClient()
        await short_term_with(no_resolver, entities)._extract_and_link_entities(a_message())

        assert len(switched_off.writes_matching("MERGE (e:Entity")) == 2
        assert switched_off.reads == [], "resolution must not query when switched off"
        assert len(switched_off.writes) == len(no_resolver.writes)

    async def test_a_non_ontology_resolver_leaves_the_path_alone(self) -> None:
        from neo4j_agent_memory.resolution.fuzzy import FuzzyMatchResolver

        client = RecordingClient()
        memory = short_term_with(
            client,
            [
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="Acme Corp", type="ORGANIZATION"),
            ],
            resolver=FuzzyMatchResolver(),
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message())

        assert len(client.writes_matching("MERGE (e:Entity")) == 2

    async def test_resolution_failure_falls_back_to_storing_mentions(self) -> None:
        class ExplodingResolver(OntologyResolver):
            async def resolve_episode(self, entities, *, user_identifier=None):  # type: ignore[no-untyped-def]
                raise RuntimeError("blocking query failed")

        client = RecordingClient()
        memory = short_term_with(
            client,
            [ExtractedEntity(name="Acme", type="ORGANIZATION")],
            resolver=ExplodingResolver(client, config=ResolutionConfig()),  # type: ignore[arg-type]
            config=ResolutionConfig(),
        )

        await memory._extract_and_link_entities(a_message())

        assert len(client.writes_matching("MERGE (e:Entity")) == 1

    async def test_the_node_id_the_database_returns_wins(self) -> None:
        """MERGE on (name, type) may match an existing node with another id."""
        client = RecordingClient()
        memory = short_term_with(client, [ExtractedEntity(name="Acme", type="ORGANIZATION")])

        # Return a pre-existing node from the entity MERGE.
        original_execute_write = client.execute_write

        async def execute_write(query: str, parameters: dict[str, Any] | None = None):
            await original_execute_write(query, parameters)
            if "MERGE (e:Entity" in query:
                return [{"e": {"id": "pre-existing"}}]
            return []

        client.execute_write = execute_write  # type: ignore[method-assign]
        await memory._extract_and_link_entities(a_message())

        links = client.writes_matching("MERGE (m)-[r:MENTIONS]->(e)")
        assert links and links[0]["entity_id"] == "pre-existing"
