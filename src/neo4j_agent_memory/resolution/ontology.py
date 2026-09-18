"""Ontology-aware, type-constrained entity resolution.

Five deliberately separable stages — ``normalize -> block -> score -> band ->
cluster`` — with blocking pushed into Cypher and every decision reported as an
:class:`EntityResolution` so the caller (the message-ingestion path, or
``LongTermMemory.add_entity``) decides what to write.

The design follows the reference implementation measured in
``johnymontana/extraction-knowledge-graph-experiments``:

* Blocking is **always** type-constrained. "Apple" the company and "Apple" the
  product embed almost identically.
* Deterministic evidence (exact normalized match, a declared ontology alias, an
  acronym expansion) short-circuits the weighted score.
* A whole-token prefix match is *not* an identity, for any type: ``Apple`` /
  ``Apple Bank``, ``Kansas`` / ``Kansas City`` and ``Paris`` / ``Paris
  Hilton`` only clear the auto-merge line when the surrounding context or the
  embeddings agree. Fuzzy similarity alone never clears it, because RapidFuzz
  scores every whole-token prefix pair at exactly the auto-merge line.
* Low-entropy names ("N600", "Ada") are penalised unless a rule fired.
* Resolution needs **two passes**. An episode that mentions "Acme" and "Acme
  Corp" for the first time must not create two nodes, and neither had a stored
  entity to anchor to.
"""

from __future__ import annotations

import json
import logging
import math
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal

from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.resolution.base import (
    BaseResolver,
    ResolutionMatch,
    ResolvedEntity,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.config.settings import ResolutionConfig
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.extraction.base import ExtractedEntity
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.ontology.models import OntologyDocument

logger = logging.getLogger(__name__)

# =============================================================================
# Normalization vocabulary
# =============================================================================

#: POLE+O types treated as organizations for legal-suffix stripping and for
#: the acronym-expansion rule (acronyms are a real naming convention for
#: organizations; "IT" is not Isabel Turner).
ORG_TYPES: frozenset[str] = frozenset({"ORGANIZATION", "ORG", "COMPANY"})

#: POLE+O types treated as people for title/suffix stripping.
PERSON_TYPES: frozenset[str] = frozenset({"PERSON", "USER"})

#: Legal-form suffixes stripped from organization names.
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc",
        "corp",
        "corporation",
        "llc",
        "ltd",
        "limited",
        "plc",
        "gmbh",
        "co",
        "company",
        "ag",
        "sa",
        "srl",
        "pty",
        "nv",
        "bv",
        "oy",
        "ab",
    }
)

#: Honorifics stripped from the front of person names.
PERSON_TITLES: frozenset[str] = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "sir"})

#: Generational/professional suffixes stripped from the end of person names.
PERSON_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv"})

#: Leading determiners stripped from every type.
DETERMINERS: frozenset[str] = frozenset({"the", "a", "an"})

# Scoring constants. Starting weights come from the reference repo; PR6's
# benchmark sweep is what moves them.
WEIGHT_FUZZY = 0.45
WEIGHT_EMBED = 0.40
WEIGHT_CONTEXT = 0.15
EXACT_SCORE = 1.0
ALIAS_SCORE = 1.0
ACRONYM_SCORE = 0.97
PREFIX_RULE_CORROBORATED = 0.92
PREFIX_RULE_BARE = 0.88
PREFIX_CORROBORATION_MIN = 0.60
LOW_ENTROPY_BITS = 2.5
LOW_ENTROPY_PENALTY = 0.25

#: Shortest acronym the acronym-expansion rule will accept. Two letters match
#: far too much ("IT" against "Isabel Turner", "US" against "Ursula Schmidt")
#: to be evidence of identity.
MIN_ACRONYM_LETTERS = 3

#: Token-prefix buckets larger than this carry no signal and are discarded.
#: Exact-key buckets are never discarded.
PREFIX_BUCKET_LIMIT = 60

#: Upper bound on the batched exact-key fetch for one (episode, type) pair.
MAX_EPISODE_CANDIDATES = 200

#: How much of a mention's context window is persisted with the node.
MAX_STORED_CONTEXT_CHARS = 300

#: Trailing punctuation stripped when deriving a raw blocking key, so a mention
#: of "Acme Corp." also blocks on "acme corp".
TRAILING_PUNCTUATION = " .,;:!?"


# =============================================================================
# Normalization
# =============================================================================


@dataclass(frozen=True)
class NormalizedName:
    """A name reduced to its comparable form, plus the signals scoring needs.

    Attributes:
        raw: The surface form exactly as extracted.
        key: The normalized comparison key (NFC, casefolded, punctuation and
            determiners/legal suffixes/person titles removed, whitespace
            collapsed). Never empty.
        tokens: ``key`` split into tokens.
        head: First token, or ``None`` when there are none.
        tail: Last token, or ``None`` when there are none.
        initials: First letter of each token, concatenated.
        is_acronym: Whether the raw surface form looks like an acronym
            ("IBM", "NWL", "N.W.L.") rather than a word. Two-letter forms are
            deliberately excluded — see :data:`MIN_ACRONYM_LETTERS`.
        entropy: Shannon entropy of ``key`` in bits. Low entropy means a short,
            repetitive name where fuzzy similarity is nearly meaningless.
    """

    raw: str
    key: str
    tokens: tuple[str, ...]
    head: str | None
    tail: str | None
    initials: str
    is_acronym: bool
    entropy: float


def _shannon_entropy(text: str) -> float:
    """Shannon entropy of ``text``'s character distribution, in bits."""
    stripped = text.replace(" ", "")
    if not stripped:
        return 0.0
    counts: dict[str, int] = defaultdict(int)
    for char in stripped:
        counts[char] += 1
    total = len(stripped)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _strip_punctuation(text: str) -> str:
    """Replace punctuation with spaces, keeping word characters and ``&``."""
    return "".join(
        char if (char.isalnum() or char.isspace() or char == "&") else " " for char in text
    )


def normalize_name(name: str, entity_type: str | None = None) -> NormalizedName:
    """Reduce a surface form to its comparable key.

    Deterministic and model-free, so benchmarks and evaluation can reuse it to
    compare gold names against extracted ones the same way the resolver does.

    Args:
        name: The surface form as extracted.
        entity_type: POLE+O type driving type-specific stripping —
            organizations lose their legal suffix ("Acme Corp" -> "acme"),
            people lose titles and generational suffixes ("Dr. Ada Lovelace
            Jr." -> "ada lovelace"). ``None`` applies only the type-agnostic
            rules.

    Returns:
        The :class:`NormalizedName` for this surface form.
    """
    folded = unicodedata.normalize("NFC", name).casefold()
    tokens = [token for token in _strip_punctuation(folded).split() if token]

    while len(tokens) > 1 and tokens[0] in DETERMINERS:
        tokens.pop(0)

    etype = (entity_type or "").upper()
    if etype in ORG_TYPES:
        while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
            tokens.pop()
    elif etype in PERSON_TYPES:
        while len(tokens) > 1 and tokens[0] in PERSON_TITLES:
            tokens.pop(0)
        while len(tokens) > 1 and tokens[-1] in PERSON_SUFFIXES:
            tokens.pop()

    key = " ".join(tokens)
    if not key:
        # Everything was stripped (e.g. the name was literally "The"). Fall
        # back to the folded text so the key is never empty — an empty key
        # would collide with every other fully-stripped name.
        key = " ".join(folded.split())
        tokens = [key] if key else []

    bare = name.strip()
    letters = bare.replace(".", "")
    is_acronym = (
        MIN_ACRONYM_LETTERS <= len(letters) <= 6
        and " " not in bare
        and letters.isalpha()
        and letters.isupper()
    )

    return NormalizedName(
        raw=name,
        key=key,
        tokens=tuple(tokens),
        head=tokens[0] if tokens else None,
        tail=tokens[-1] if tokens else None,
        initials="".join(token[0] for token in tokens if token),
        is_acronym=is_acronym,
        entropy=_shannon_entropy(key),
    )


# =============================================================================
# Similarity primitives
# =============================================================================


def _rapidfuzz() -> Any | None:
    """Return ``rapidfuzz.fuzz`` when installed, else ``None``."""
    try:
        from rapidfuzz import fuzz
    except ImportError:  # pragma: no cover - exercised by the fallback path
        return None
    return fuzz


# ``rapidfuzz`` is an optional extra, so every similarity primitive below has a
# stdlib twin built on :class:`difflib.SequenceMatcher`. The twins mirror
# RapidFuzz's *scorer composition* (which strings get compared against which),
# not its edit-distance metric: Ratcliff-Obershelp is not the indel distance, so
# the two agree exactly on realistic name/context pairs and drift by a few
# points on word salad. Mirroring the composition is what matters — the plain
# ``SequenceMatcher(left, right).ratio()`` this replaces scored the *same*
# inputs on either side of the auto-merge and corroboration lines differently
# depending on whether an optional dependency happened to be installed.

#: RapidFuzz's ``WRatio`` weights, reproduced so the fallback bands match.
_WRATIO_UNBASE_SCALE = 0.95
_WRATIO_LENGTH_RATIO_FLOOR = 1.5
_WRATIO_LENGTH_RATIO_CEILING = 8.0
_WRATIO_PARTIAL_SCALE = 0.9
_WRATIO_LONG_PARTIAL_SCALE = 0.6

#: A partial match at or above this ratio is treated as whole-substring
#: containment, exactly as RapidFuzz's ``partial_ratio`` does.
_PARTIAL_RATIO_CEILING = 0.995


def _difflib_ratio(left: str, right: str) -> float:
    """``SequenceMatcher`` ratio, with RapidFuzz's empty-string handling.

    ``difflib`` scores two empty strings at 1.0; RapidFuzz scores anything
    involving an empty string at 0.
    """
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _sorted_tokens(text: str) -> str:
    """``text``'s whitespace-separated tokens, sorted and rejoined."""
    return " ".join(sorted(text.split()))


def _partial_ratio_fallback(left: str, right: str) -> float:
    """``difflib`` stand-in for RapidFuzz's ``partial_ratio``.

    The shorter string is compared against every window of the longer one that
    an existing matching block could align it to — bounded work, unlike a scan
    of every offset.
    """
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if not shorter or not longer:
        return 0.0
    best = 0.0
    for block in SequenceMatcher(None, shorter, longer).get_matching_blocks():
        start = max(0, block.b - block.a)
        window = longer[start : start + len(shorter)]
        best = max(best, _difflib_ratio(shorter, window))
        if best >= _PARTIAL_RATIO_CEILING:
            return 1.0
    return best


def _token_sort_ratio_fallback(left: str, right: str) -> float:
    """``difflib`` stand-in for RapidFuzz's ``token_sort_ratio``."""
    return _difflib_ratio(_sorted_tokens(left), _sorted_tokens(right))


def _token_set_ratio_fallback(left: str, right: str) -> float:
    """``difflib`` stand-in for RapidFuzz's ``token_set_ratio``.

    The shared tokens are compared against themselves plus each side's
    leftovers, so two sentences built from the same vocabulary in a different
    order score on what they have in common rather than on character offsets.
    """
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = " ".join(sorted(left_tokens & right_tokens))
    combined_left = f"{intersection} {' '.join(sorted(left_tokens - right_tokens))}".strip()
    combined_right = f"{intersection} {' '.join(sorted(right_tokens - left_tokens))}".strip()
    return max(
        _difflib_ratio(intersection, combined_left),
        _difflib_ratio(intersection, combined_right),
        _difflib_ratio(combined_left, combined_right),
    )


def _partial_token_ratio_fallback(left: str, right: str) -> float:
    """``difflib`` stand-in for RapidFuzz's ``partial_token_ratio``.

    Any shared token makes the partial *token-set* comparison a perfect
    substring match, which is how RapidFuzz reaches 100 there.
    """
    if set(left.split()) & set(right.split()):
        return 1.0
    return _partial_ratio_fallback(_sorted_tokens(left), _sorted_tokens(right))


def _wratio_fallback(left: str, right: str) -> float:
    """``difflib`` stand-in for RapidFuzz's ``WRatio``.

    Same cascade: a plain ratio, a length-ratio-gated token comparison, then
    partial comparisons discounted by how lopsided the two lengths are.
    """
    if not left or not right:
        return 0.0
    base = _difflib_ratio(left, right)
    length_ratio = max(len(left), len(right)) / min(len(left), len(right))
    token = max(_token_sort_ratio_fallback(left, right), _token_set_ratio_fallback(left, right))
    if length_ratio < _WRATIO_LENGTH_RATIO_FLOOR:
        return max(base, token * _WRATIO_UNBASE_SCALE)
    partial_scale = (
        _WRATIO_PARTIAL_SCALE
        if length_ratio < _WRATIO_LENGTH_RATIO_CEILING
        else _WRATIO_LONG_PARTIAL_SCALE
    )
    return max(
        base,
        _partial_ratio_fallback(left, right) * partial_scale,
        _partial_token_ratio_fallback(left, right) * _WRATIO_UNBASE_SCALE * partial_scale,
    )


def fuzzy_similarity(left: str, right: str) -> float:
    """String similarity in ``[0, 1]``.

    Uses the maximum of RapidFuzz's ``WRatio`` and ``token_sort_ratio`` (the
    former handles substrings and partial matches, the latter word order), and
    falls back to the :mod:`difflib` twins of those two scorers when RapidFuzz
    is not installed — ``rapidfuzz`` is an optional extra.

    Args:
        left: First string (already normalized by the caller).
        right: Second string.

    Returns:
        Similarity in ``[0, 1]``.
    """
    if not left or not right:
        return 0.0
    fuzz = _rapidfuzz()
    if fuzz is None:
        return max(_wratio_fallback(left, right), _token_sort_ratio_fallback(left, right))
    return max(float(fuzz.WRatio(left, right)), float(fuzz.token_sort_ratio(left, right))) / 100.0


def context_similarity(left: str | None, right: str | None) -> float | None:
    """Token-set similarity between two context windows.

    Two windows onto the same fact rarely share a character alignment — "…out
    of the Rotterdam depot is handled by Northwind" against "Northwind
    Logistics ships freight out of the Rotterdam depot" — so this is a
    token-set comparison (RapidFuzz's ``token_set_ratio``, or its :mod:`difflib`
    twin when RapidFuzz is not installed), never a positional one.

    Args:
        left: The mention's ±``context_window_chars`` window, if captured.
        right: The candidate's stored context or description, if any.

    Returns:
        Similarity in ``[0, 1]``, or ``None`` when either side is missing — in
        which case the caller renormalizes the remaining weights rather than
        scoring the pair as if the contexts disagreed.
    """
    if not left or not right:
        return None
    fuzz = _rapidfuzz()
    if fuzz is None:
        return _token_set_ratio_fallback(left.lower(), right.lower())
    return float(fuzz.token_set_ratio(left.lower(), right.lower())) / 100.0


def _is_token_prefix(left: NormalizedName, right: NormalizedName) -> bool:
    """Whether one name's tokens are a whole-token prefix of the other's.

    ``Kansas`` / ``Kansas City``, ``Apple`` / ``Apple Bank``. Two identical
    keys are not a prefix pair — that is the exact-match rule's business.
    """
    if not left.tokens or not right.tokens or left.key == right.key:
        return False
    short, long_ = sorted((left, right), key=lambda name: len(name.tokens))
    return long_.tokens[: len(short.tokens)] == short.tokens


def _is_token_superstring(left: NormalizedName, right: NormalizedName) -> bool:
    """Whether one name's tokens appear as a contiguous run inside the other's.

    A superset of :func:`_is_token_prefix`: also true for ``Kansas`` /
    ``City of Kansas`` and ``Turner`` / ``Isabel Turner``. Every such pair
    scores at or near RapidFuzz's whole-substring ceiling, so string
    containment on its own must never be allowed to auto-merge.
    """
    if not left.tokens or not right.tokens or left.key == right.key:
        return False
    short, long_ = sorted((left, right), key=lambda name: len(name.tokens))
    span = len(short.tokens)
    return any(
        long_.tokens[start : start + span] == short.tokens
        for start in range(len(long_.tokens) - span + 1)
    )


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    """Cosine similarity of two vectors, clamped to ``[0, 1]``.

    Args:
        left: First vector, or ``None``.
        right: Second vector, or ``None``.

    Returns:
        Similarity in ``[0, 1]``, or ``None`` when either vector is missing or
        degenerate.
    """
    if not left or not right or len(left) != len(right):
        return None
    dot = float(sum(x * y for x, y in zip(left, right)))
    norm_left = float(sum(x * x for x in left)) ** 0.5
    norm_right = float(sum(y * y for y in right)) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return None
    return float(min(1.0, max(0.0, dot / (norm_left * norm_right))))


# =============================================================================
# Result and internal records
# =============================================================================


@dataclass
class EntityResolution:
    """What the resolver decided about one mention.

    Attributes:
        action: ``"created"`` (no match — store a new node), ``"merged"``
            (store nothing new; link and alias onto the matched entity) or
            ``"review"`` (store a new node plus a pending ``SAME_AS`` edge to
            the matched entity).
        entity_id: Id of the node this mention resolved onto, when it is
            already known (a stored match). ``None`` when the caller must
            create the node, or when the match is another mention from the
            same episode that has not been written yet.
        canonical_name: The name the mention resolves to — the matched
            entity's name for ``"merged"``, the mention's own name otherwise.
        score: Best score observed for this mention (0.0 when no candidate was
            even proposed).
        match_type: How the winning evidence was obtained — ``"exact"``,
            ``"alias"``, ``"acronym"``, ``"prefix"``, ``"fuzzy"``,
            ``"embedding"`` or ``"both"``. ``None`` when nothing matched.
        matched_entity_id: Id of the matched *stored* entity, when there is
            one. ``None`` for an intra-episode match.
        matched_entity_name: Name of the matched entity, stored or
            intra-episode. For an intra-episode match this is the name the
            caller must look its freshly created node up by.
    """

    action: Literal["created", "merged", "review"]
    entity_id: str | None = None
    canonical_name: str = ""
    score: float = 0.0
    match_type: str | None = None
    matched_entity_id: str | None = None
    matched_entity_name: str | None = None


@dataclass
class _Candidate:
    """One entity a mention could resolve onto."""

    entity_id: str | None
    name: str
    canonical_name: str | None = None
    aliases: tuple[str, ...] = ()
    embedding: list[float] | None = None
    context: str | None = None
    #: Similarity reported by the vector index, when this candidate came from
    #: embedding blocking and the resolver has no stored vector to compare.
    index_score: float | None = None
    normalized: NormalizedName | None = None
    #: Whether this candidate's context comes from the same message/episode as
    #: the mention being scored. Two mentions in one sentence share the same
    #: context window, so their contexts agree at 1.0 and would "corroborate"
    #: each other into an auto-merge ("Apple Bank has no relationship with
    #: Apple"). Set, the context component is treated as unavailable.
    same_source: bool = False

    def surface_forms(self) -> tuple[str, ...]:
        """Every name this candidate is known by (name, canonical, aliases)."""
        forms = [self.name]
        if self.canonical_name:
            forms.append(self.canonical_name)
        forms.extend(self.aliases)
        return tuple(forms)


@dataclass
class _Mention:
    """One extracted mention being resolved."""

    index: int
    name: str
    entity_type: str
    subtype: str | None
    context: str | None
    normalized: NormalizedName
    embedding: list[float] | None = None
    keys: frozenset[str] = field(default_factory=frozenset)
    #: Declared alias groups this mention unambiguously belongs to, resolved
    #: once. One entry per group, never unioned across groups: two canonicals
    #: that happen to share a surface form ("Apple" for both "Apple Inc" and
    #: "Apple Records") are different groups and must not merge into one.
    alias_groups: frozenset[frozenset[str]] = field(default_factory=frozenset)


# =============================================================================
# Resolver
# =============================================================================


class OntologyResolver(BaseResolver):
    """Type-constrained resolver that blocks in Cypher and scores in Python.

    Composes (rather than replaces) the simpler strategies: the fuzzy
    component is RapidFuzz with a ``difflib`` fallback, and the embedding
    component is cosine similarity over the same embedder the rest of the
    client uses.
    """

    def __init__(
        self,
        client: Neo4jClient,
        *,
        ontology: OntologyDocument | None = None,
        embedder: Embedder | None = None,
        config: ResolutionConfig | None = None,
    ):
        """Initialize the resolver.

        Args:
            client: Connected Neo4j client used for blocking queries.
            ontology: Effective ontology. Supplies the alias gazetteer and the
                per-type threshold overrides. ``None`` disables both.
            embedder: Embedder used to vectorise mention names once per
                episode. ``None`` turns the embedding component off and
                renormalizes the remaining score weights.
            config: Resolution settings. Defaults to
                :class:`~neo4j_agent_memory.config.settings.ResolutionConfig`.
        """
        if config is None:
            from neo4j_agent_memory.config.settings import ResolutionConfig

            config = ResolutionConfig()
        self._client = client
        self._ontology = ontology
        self._embedder = embedder
        self._config = config
        self._alias_index = self._build_alias_index(ontology)

    # -- configuration -----------------------------------------------------

    @property
    def config(self) -> ResolutionConfig:
        """The resolution settings this resolver was built with."""
        return self._config

    @property
    def ontology(self) -> OntologyDocument | None:
        """The ontology supplying aliases and per-type thresholds."""
        return self._ontology

    @staticmethod
    def _build_alias_index(
        ontology: OntologyDocument | None,
    ) -> dict[str, frozenset[frozenset[str]]]:
        """Index the declared alias groups by each of their lookup keys.

        ``EntityTypeDef.aliases`` maps a canonical name to its known surface
        forms. Membership is symmetric for resolution purposes, so each group
        is indexed under every member's normalized key (both the type-aware
        and the type-agnostic normalization, since a stored "Acme Corp" and a
        mention "Acme" must land on the same group) and under each member's
        lowercased raw form.

        One lookup key can identify **several** groups: with
        ``{"Apple Inc": ["Apple"], "Apple Records": ["Apple"]}`` the key
        ``"apple"`` belongs to both. The groups are therefore kept separate
        rather than unioned — unioning them made "Apple Inc" a declared alias
        of "Apple Records" and merged the two at score 1.0. A key that
        identifies more than one group is ambiguous and carries no identity
        evidence at all (see :meth:`_alias_groups`).

        Args:
            ontology: The ontology to read, or ``None``.

        Returns:
            Lookup key -> the set of groups it identifies, each group being
            the lowercased surface forms of one canonical.
        """
        index: dict[str, frozenset[frozenset[str]]] = {}
        if ontology is None:
            return index

        for entity_type in ontology.entity_types:
            for canonical, surfaces in (entity_type.aliases or {}).items():
                members = [canonical, *(surfaces or [])]
                group = frozenset(member.strip().lower() for member in members if member.strip())
                if len(group) < 2:
                    continue
                for member in members:
                    if not member.strip():
                        continue
                    for lookup in (
                        member.strip().lower(),
                        normalize_name(member).key,
                        normalize_name(member, entity_type.pole_type).key,
                    ):
                        index[lookup] = index.get(lookup, frozenset()) | {group}
        return index

    def _alias_groups(self, lookups: Sequence[str]) -> frozenset[frozenset[str]]:
        """Declared alias groups that ``lookups`` identify *unambiguously*.

        A lookup key mapping to more than one group ("apple", declared by both
        "Apple Inc" and "Apple Records") is dropped: on its own it proves
        nothing about which canonical a surface form means, so the pair falls
        through to the weighted blend instead of short-circuiting to 1.0.
        """
        if not self._config.use_alias_gazetteer or not self._alias_index:
            return frozenset()
        groups: set[frozenset[str]] = set()
        for key in lookups:
            hits = self._alias_index.get(key)
            if hits is not None and len(hits) == 1:
                groups |= hits
        return frozenset(groups)

    def _thresholds(self, entity_type: str, subtype: str | None) -> tuple[float, float]:
        """Auto-merge and review thresholds for one POLE+O pair.

        Args:
            entity_type: POLE+O type of the mention.
            subtype: Optional subtype.

        Returns:
            ``(auto_merge_threshold, review_threshold)``, with the ontology's
            per-type overrides applied when it declares a matching label.
        """
        auto = self._config.auto_merge_threshold
        review = self._config.review_threshold
        if self._ontology is not None:
            label = self._ontology.label_for(entity_type, subtype)
            declared = self._ontology.entity_type(label) if label else None
            if declared is not None:
                if declared.resolution_threshold is not None:
                    auto = declared.resolution_threshold
                if declared.review_threshold is not None:
                    review = declared.review_threshold
        return auto, review

    # -- public API --------------------------------------------------------

    async def resolve_episode(
        self,
        entities: Sequence[ExtractedEntity],
        *,
        user_identifier: str | None = None,
    ) -> list[EntityResolution]:
        """Resolve every mention extracted from one message.

        Two passes, because one is not enough: pass 1 matches each mention
        against the stored entities, pass 2 clusters the still-unmatched
        remainder among themselves. Without pass 2, a message that mentions
        "Acme" and "Acme Corp" for the first time creates two nodes, since
        neither had a stored entity to anchor to.

        Blocking is batched: one exact-key query per entity type for the whole
        episode, and mention names are embedded in a single
        ``embed_batch`` call.

        Args:
            entities: Mentions in the order the caller will persist them.
            user_identifier: Tenant whose entities to block against, when
                ``config.scope == "user"``.

        Returns:
            One :class:`EntityResolution` per input mention, in input order.
        """
        if not entities:
            return []

        mentions = [self._to_mention(index, entity) for index, entity in enumerate(entities)]
        await self._attach_embeddings(mentions)

        by_type: dict[str, list[_Mention]] = defaultdict(list)
        for mention in mentions:
            by_type[mention.entity_type].append(mention)

        results: list[EntityResolution | None] = [None] * len(mentions)
        for entity_type, group in by_type.items():
            pool = await self._fetch_key_candidates(
                entity_type, group, user_identifier=user_identifier
            )
            for mention in group:
                results[mention.index] = await self._resolve_mention(
                    mention, pool, user_identifier=user_identifier
                )

        resolved = [result for result in results if result is not None]
        self._cluster_unmatched(mentions, resolved)
        return resolved

    async def resolve_one(
        self,
        name: str,
        entity_type: str,
        *,
        subtype: str | None = None,
        embedding: list[float] | None = None,
        context: str | None = None,
        user_identifier: str | None = None,
    ) -> EntityResolution:
        """Resolve a single name against the stored entities.

        Args:
            name: Surface form to resolve.
            entity_type: POLE+O type; blocking never crosses it.
            subtype: Optional subtype, used for per-type threshold lookup.
            embedding: Precomputed embedding of ``name``. When omitted and an
                embedder is configured, one is generated.
            context: Text window around the mention, compared against the
                candidates' stored context/description.
            user_identifier: Tenant to scope candidates to when
                ``config.scope == "user"``.

        Returns:
            The :class:`EntityResolution` for this name.
        """
        mention = _Mention(
            index=0,
            name=name,
            entity_type=entity_type.upper(),
            subtype=subtype,
            context=context,
            normalized=normalize_name(name, entity_type),
            embedding=embedding,
        )
        self._prepare(mention)
        if mention.embedding is None:
            await self._attach_embeddings([mention])

        pool = await self._fetch_key_candidates(
            mention.entity_type, [mention], user_identifier=user_identifier
        )
        return await self._resolve_mention(mention, pool, user_identifier=user_identifier)

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> ResolvedEntity:
        """Resolve one name to its canonical form (``EntityResolver`` protocol).

        Args:
            entity_name: Name to resolve.
            entity_type: POLE+O type.
            existing_entities: When supplied, the name is scored against this
                in-memory list instead of querying Neo4j — the pre-v0.7
                protocol semantics, which ``LongTermMemory.add_entity`` relies
                on.

        Returns:
            A :class:`~neo4j_agent_memory.resolution.base.ResolvedEntity` whose
            ``canonical_name`` is the matched name when the score cleared the
            auto-merge line, and the input name otherwise.
        """
        if existing_entities is not None:
            mention = self._to_mention(
                0,
                _NameOnly(name=entity_name, type=entity_type),
            )
            candidates = [
                self._candidate_from_name(name, entity_type) for name in existing_entities
            ]
            best, match_type = self._best(mention, candidates)
            auto, _ = self._thresholds(mention.entity_type, None)
            if best is not None and best[1] >= auto:
                candidate = best[0]
                return ResolvedEntity(
                    original_name=entity_name,
                    canonical_name=candidate.name,
                    entity_type=entity_type,
                    confidence=min(1.0, best[1]),
                    merged_from=[entity_name] if entity_name != candidate.name else [],
                    match_type=match_type,
                )
            return ResolvedEntity(
                original_name=entity_name,
                canonical_name=entity_name,
                entity_type=entity_type,
                confidence=1.0,
                match_type="none",
            )

        resolution = await self.resolve_one(entity_name, entity_type)
        merged = resolution.action == "merged"
        return ResolvedEntity(
            original_name=entity_name,
            canonical_name=resolution.canonical_name if merged else entity_name,
            entity_type=entity_type,
            confidence=min(1.0, resolution.score) if merged else 1.0,
            merged_from=[entity_name]
            if merged and resolution.canonical_name != entity_name
            else [],
            match_type=resolution.match_type or "none",
        )

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """Score ``candidates`` against ``entity_name`` (protocol method).

        Args:
            entity_name: Name to match.
            entity_type: POLE+O type.
            candidates: Candidate names, scored in memory (no queries).

        Returns:
            Matches at or above the review threshold, best first.
        """
        mention = self._to_mention(0, _NameOnly(name=entity_name, type=entity_type))
        _, review = self._thresholds(mention.entity_type, None)
        matches: list[ResolutionMatch] = []
        for name in candidates:
            score, match_type = self._score(mention, self._candidate_from_name(name, entity_type))
            if score >= review:
                matches.append(
                    ResolutionMatch(
                        entity1_name=entity_name,
                        entity2_name=name,
                        similarity_score=min(1.0, score),
                        match_type=match_type or "fuzzy",
                    )
                )
        matches.sort(key=lambda match: match.similarity_score, reverse=True)
        return matches

    # -- mention preparation ----------------------------------------------

    def _to_mention(self, index: int, entity: Any) -> _Mention:
        """Build the internal record for one extracted entity."""
        entity_type = (getattr(entity, "type", None) or "").upper()
        context = getattr(entity, "context", None)
        mention = _Mention(
            index=index,
            name=entity.name,
            entity_type=entity_type,
            subtype=getattr(entity, "subtype", None),
            context=self._trim_context(context),
            normalized=normalize_name(entity.name, entity_type),
        )
        self._prepare(mention)
        return mention

    def _prepare(self, mention: _Mention) -> None:
        """Fill in a mention's alias groups and blocking keys (once).

        The exact-key blocking predicate compares the *raw* stored
        ``e.name``/``e.canonical_name``/aliases against these keys, so the raw
        surface form goes in alongside the normalized one — both with and
        without trailing punctuation, and in its type-agnostic normalization.
        Without that, a mention of "Acme Corp." never hit the exact bucket for
        a stored "Acme Corp" and had to be rescued by the wider token-prefix
        bucket.
        """
        raw = mention.name.strip()
        lookups = [
            mention.normalized.key,
            raw.lower(),
            raw.rstrip(TRAILING_PUNCTUATION).lower(),
            normalize_name(mention.name).key,
        ]
        mention.alias_groups = self._alias_groups(lookups)
        keys = {key for key in lookups if key}
        for group in mention.alias_groups:
            keys |= group
        mention.keys = frozenset(key for key in keys if key)

    def _trim_context(self, context: str | None) -> str | None:
        """Clip a context window to ``config.context_window_chars`` each side.

        Extractors capture their own window width; the resolver compares a
        window of its own size, centred on the same span.
        """
        if not context:
            return None
        width = self._config.context_window_chars
        if width <= 0:
            return None
        span = width * 2
        if len(context) <= span:
            return context
        start = (len(context) - span) // 2
        return context[start : start + span]

    async def _attach_embeddings(self, mentions: Sequence[_Mention]) -> None:
        """Embed every mention name in one batch, when an embedder exists."""
        if self._embedder is None:
            return
        pending = [mention for mention in mentions if mention.embedding is None]
        if not pending:
            return
        try:
            vectors = await self._embedder.embed_batch([mention.name for mention in pending])
        except Exception:  # pragma: no cover - embedder failures must not block ingest
            logger.warning("Embedding mentions for resolution failed; continuing without vectors")
            return
        for mention, vector in zip(pending, vectors):
            mention.embedding = vector

    # -- blocking ----------------------------------------------------------

    async def _fetch_key_candidates(
        self,
        entity_type: str,
        mentions: Sequence[_Mention],
        *,
        user_identifier: str | None,
    ) -> list[_Candidate]:
        """One exact-key blocking query for a whole (episode, type) bucket."""
        keys = sorted({key for mention in mentions for key in mention.keys})
        if not keys:
            return []
        limit = min(
            MAX_EPISODE_CANDIDATES,
            self._config.candidate_limit * max(1, len(mentions)),
        )
        params: dict[str, Any] = {"type": entity_type, "keys": keys, "limit": limit}
        query = queries.FIND_ENTITIES_BY_NORMALIZED_KEYS
        if self._config.scope == "user" and user_identifier is not None:
            query = queries.FIND_ENTITIES_BY_NORMALIZED_KEYS_FOR_USER
            params["user_identifier"] = user_identifier
        rows = await self._client.execute_read(query, params)
        return [self._candidate_from_row(row, entity_type) for row in rows]

    async def _fetch_prefix_candidates(
        self,
        mention: _Mention,
        *,
        user_identifier: str | None,
    ) -> list[_Candidate]:
        """Head/tail token bucket for one mention, dropped when oversized."""
        head = mention.normalized.head
        tail = mention.normalized.tail
        if head is None and tail is None:
            return []
        params: dict[str, Any] = {
            "type": mention.entity_type,
            "head": head,
            "tail": tail,
            # One row more than the cap, so an oversized bucket is detectable.
            "limit": PREFIX_BUCKET_LIMIT + 1,
        }
        query = queries.FIND_ENTITIES_BY_TOKEN_PREFIX
        if self._config.scope == "user" and user_identifier is not None:
            query = queries.FIND_ENTITIES_BY_TOKEN_PREFIX_FOR_USER
            params["user_identifier"] = user_identifier
        rows = await self._client.execute_read(query, params)
        if len(rows) > PREFIX_BUCKET_LIMIT:
            logger.debug(
                "Skipping oversized token-prefix bucket for %r (%d candidates)",
                mention.name,
                len(rows),
            )
            return []
        return [self._candidate_from_row(row, mention.entity_type) for row in rows]

    async def _fetch_embedding_candidates(
        self,
        mention: _Mention,
        *,
        user_identifier: str | None,
    ) -> list[_Candidate]:
        """Approximate-nearest-neighbour bucket from the entity vector index.

        Tenant-scoped like the other two buckets: the vector index is global,
        so without the ``_FOR_USER`` variant tenant B's "Alice Chen" merged
        straight onto tenant A's node under ``scope="user"``.
        """
        if not self._config.use_embedding_blocking or mention.embedding is None:
            return []
        params: dict[str, Any] = {
            "embedding": mention.embedding,
            "limit": self._config.candidate_limit,
            "threshold": 0.0,
            "type": mention.entity_type,
        }
        query = queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING
        if self._config.scope == "user" and user_identifier is not None:
            query = queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING_FOR_USER
            params["user_identifier"] = user_identifier
        rows = await self._client.execute_read(query, params)
        candidates = []
        for row in rows:
            candidate = self._candidate_from_row(row, mention.entity_type)
            score = row.get("score")
            if isinstance(score, (int, float)):
                candidate.index_score = float(score)
            candidates.append(candidate)
        return candidates

    def _candidate_from_row(self, row: dict[str, Any], entity_type: str) -> _Candidate:
        """Build a candidate from a returned ``:Entity`` node."""
        node = dict(row.get("e") or {})
        name = node.get("name") or ""
        aliases = node.get("aliases") or []
        return _Candidate(
            entity_id=node.get("id"),
            name=name,
            canonical_name=node.get("canonical_name"),
            aliases=tuple(str(alias) for alias in aliases),
            embedding=node.get("embedding"),
            context=self._candidate_context(node),
            normalized=normalize_name(name, entity_type),
        )

    @staticmethod
    def _candidate_context(node: dict[str, Any]) -> str | None:
        """The candidate's stored context window, else its description."""
        metadata = node.get("metadata")
        if isinstance(metadata, str) and metadata:
            try:
                parsed = json.loads(metadata)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                context = parsed.get("context")
                if isinstance(context, str) and context:
                    return context
        description = node.get("description")
        return description if isinstance(description, str) and description else None

    def _candidate_from_name(self, name: str, entity_type: str) -> _Candidate:
        """Build an id-less candidate from a bare name (in-memory scoring)."""
        return _Candidate(
            entity_id=None,
            name=name,
            normalized=normalize_name(name, entity_type),
        )

    @staticmethod
    def _candidate_from_mention(mention: _Mention) -> _Candidate:
        """Treat an earlier mention as a candidate (the second pass).

        ``same_source=True``: both mentions were extracted from the same
        message, so their context windows are the same text and agree at 1.0
        no matter what the sentence says. Counting that as corroboration
        merged "Apple" into "Apple Bank" out of "Apple Bank has no
        relationship with Apple, the phone maker."
        """
        return _Candidate(
            entity_id=None,
            name=mention.name,
            embedding=mention.embedding,
            context=mention.context,
            normalized=mention.normalized,
            same_source=True,
        )

    # -- scoring -----------------------------------------------------------

    def _score(self, mention: _Mention, candidate: _Candidate) -> tuple[float, str | None]:
        """Score one (mention, candidate) pair.

        Deterministic evidence short-circuits: an exact normalized match, a
        declared ontology alias, or (for organizations) an acronym expansion.
        Otherwise the whole-token prefix rule if it fires, else a weighted
        blend of fuzzy / embedding / context similarity (weights renormalized
        over whichever components are available) with the low-entropy penalty
        applied.

        Args:
            mention: The mention being resolved.
            candidate: The candidate entity.

        Returns:
            ``(score, match_type)``. ``match_type`` is ``None`` when no
            evidence at all was available.
        """
        normalized = candidate.normalized or normalize_name(candidate.name, mention.entity_type)

        # 1. Exact normalized match.
        if normalized.key and normalized.key == mention.normalized.key:
            return EXACT_SCORE, "exact"

        # 2. Declared alias gazetteer — a controlled vocabulary beats any
        #    similarity computation, and it works with embeddings off. Mention
        #    and candidate must land in the *same* group; a surface form
        #    several canonicals share identifies no group at all.
        if mention.alias_groups:
            candidate_forms = {form.strip().lower() for form in candidate.surface_forms()}
            candidate_forms |= {normalized.key}
            candidate_groups = self._alias_groups(sorted(candidate_forms))
            if mention.alias_groups & candidate_groups:
                return ALIAS_SCORE, "alias"

        # 3. Acronym expansion, either direction. Organizations only:
        #    initialisms are how companies are named, whereas for a person
        #    "IT" against "Isabel Turner" is a coincidence, not an identity.
        if mention.entity_type in ORG_TYPES and self._is_acronym_expansion(
            mention.normalized, normalized
        ):
            return ACRONYM_SCORE, "acronym"

        # 4. Weighted blend of the available evidence.
        fuzzy = fuzzy_similarity(mention.normalized.key, normalized.key)
        embed = cosine_similarity(mention.embedding, candidate.embedding)
        if embed is None:
            embed = candidate.index_score
        context = self._context_component(mention, candidate)

        weighted = 0.0
        total_weight = 0.0
        components: list[str] = []
        for value, weight, label in (
            (fuzzy, WEIGHT_FUZZY, "fuzzy"),
            (embed, WEIGHT_EMBED, "embedding"),
            (context, WEIGHT_CONTEXT, "context"),
        ):
            if value is None:
                continue
            weighted += weight * value
            total_weight += weight
            components.append(label)
        base = weighted / total_weight if total_weight else 0.0

        # 5. The whole-token-prefix rule. A prefix alone is not proof —
        #    "Apple"/"Apple Bank" are different companies, "Kansas"/"Kansas
        #    City" different places, "Paris"/"Paris Hilton" different people —
        #    so a bare prefix lands in the review band instead. The rule
        #    *replaces* the blend rather than being max'd with it: RapidFuzz
        #    scores every whole-token prefix pair at exactly 0.90, which would
        #    merge precisely the pairs the rule exists to hold back.
        rule = self._prefix_rule(mention, normalized, embed=embed, context=context)

        score = rule if rule > 0.0 else base
        match_type: str | None
        if rule > 0.0:
            match_type = "prefix"
        elif "fuzzy" in components and "embedding" in components:
            match_type = "both"
        elif "embedding" in components:
            match_type = "embedding"
        elif "fuzzy" in components:
            match_type = "fuzzy"
        else:
            match_type = None

        # 6. Low-entropy penalty: short repetitive names ("N600", "Ada") make
        #    fuzzy similarity nearly meaningless. Taken over the *lower*
        #    entropy of the pair, so the penalty does not depend on which side
        #    happens to be the mention — one-sided, "Dana Whitfield" -> "Dana"
        #    merged while "Dana" -> "Dana Whitfield" did not.
        pair_entropy = min(mention.normalized.entropy, normalized.entropy)
        if rule <= 0.0 and pair_entropy < LOW_ENTROPY_BITS:
            score = max(0.0, score - LOW_ENTROPY_PENALTY)

        # 7. Safety net for the fuzzy-only case the prefix rule does not
        #    cover. With no embedder and no usable context the blend *is* the
        #    fuzzy score, and RapidFuzz rates any whole-substring pair at
        #    exactly the auto-merge line -- "Turner" against "Isabel Turner",
        #    "Kansas" against "City of Kansas". String containment is never
        #    proof of identity on its own, so cap it at the review threshold.
        #    Only when the rule did not fire: the rule's own 0.88 is a
        #    deliberate band, and an ontology is allowed to lower the
        #    auto-merge line under it for a type where prefixes do mean
        #    identity.
        if (
            rule <= 0.0
            and embed is None
            and context is None
            and _is_token_superstring(mention.normalized, normalized)
        ):
            _, review = self._thresholds(mention.entity_type, mention.subtype)
            score = min(score, review)

        return score, match_type

    def _context_component(self, mention: _Mention, candidate: _Candidate) -> float | None:
        """Context similarity, or ``None`` when it is not independent evidence.

        Two mentions extracted from the same message carry the same ±
        ``context_window_chars`` window, so their contexts agree at 1.0
        whatever the sentence asserts — enough to push a bare prefix pair from
        the review band to an auto-merge. The component is dropped when the
        candidate is another mention of the current episode, and when its
        stored context is the same underlying text as the mention's (a
        re-ingest of the same message, or a quote of it).
        """
        if candidate.same_source:
            return None
        left, right = mention.context, candidate.context
        if not left or not right:
            return None
        left_flat = " ".join(left.split()).lower()
        right_flat = " ".join(right.split()).lower()
        if left_flat in right_flat or right_flat in left_flat:
            return None
        return context_similarity(left, right)

    def _prefix_rule(
        self,
        mention: _Mention,
        candidate: NormalizedName,
        *,
        embed: float | None,
        context: float | None,
    ) -> float:
        """Whole-token prefix rule, for every type. 0.0 when it does not fire."""
        if not _is_token_prefix(mention.normalized, candidate):
            return 0.0
        corroborated = max(embed or 0.0, context or 0.0) >= PREFIX_CORROBORATION_MIN
        return PREFIX_RULE_CORROBORATED if corroborated else PREFIX_RULE_BARE

    @staticmethod
    def _is_acronym_expansion(left: NormalizedName, right: NormalizedName) -> bool:
        """Whether one name is the other's acronym ("NWL" / "Northwind Ltd")."""
        for acronym, expansion in ((left, right), (right, left)):
            if not acronym.is_acronym or len(expansion.tokens) < 2:
                continue
            letters = acronym.key.replace(" ", "")
            if letters and letters == expansion.initials:
                return True
        return False

    def _best(
        self,
        mention: _Mention,
        candidates: Sequence[_Candidate],
    ) -> tuple[tuple[_Candidate, float] | None, str | None]:
        """Highest-scoring candidate for a mention, with its match type."""
        best: tuple[_Candidate, float] | None = None
        best_type: str | None = None
        for candidate in candidates:
            score, match_type = self._score(mention, candidate)
            if best is None or score > best[1]:
                best = (candidate, score)
                best_type = match_type
        return best, best_type

    # -- bands -------------------------------------------------------------

    async def _resolve_mention(
        self,
        mention: _Mention,
        pool: Sequence[_Candidate],
        *,
        user_identifier: str | None,
    ) -> EntityResolution:
        """Score a mention against its blocked candidates and pick a band.

        The wider buckets (token prefix, vector index) are only fetched when
        the exact-key bucket produced no deterministic match, which keeps the
        common case at one query per type per episode.
        """
        candidates: dict[str, _Candidate] = {}
        ordered: list[_Candidate] = []

        def _add(items: Sequence[_Candidate]) -> None:
            for item in items:
                key = item.entity_id or f"name:{item.name.lower()}"
                if key in candidates:
                    continue
                candidates[key] = item
                ordered.append(item)

        _add(pool)
        best, match_type = self._best(mention, ordered)
        if best is None or best[1] < EXACT_SCORE:
            _add(await self._fetch_prefix_candidates(mention, user_identifier=user_identifier))
            _add(await self._fetch_embedding_candidates(mention, user_identifier=user_identifier))
            best, match_type = self._best(mention, ordered)

        auto, review = self._thresholds(mention.entity_type, mention.subtype)
        if best is None:
            return EntityResolution(action="created", canonical_name=mention.name)

        candidate, score = best
        if score >= auto:
            return EntityResolution(
                action="merged",
                entity_id=candidate.entity_id,
                canonical_name=candidate.name,
                score=min(1.0, score),
                match_type=match_type,
                matched_entity_id=candidate.entity_id,
                matched_entity_name=candidate.name,
            )
        if score >= review:
            return EntityResolution(
                action="review",
                canonical_name=mention.name,
                score=min(1.0, score),
                match_type=match_type,
                matched_entity_id=candidate.entity_id,
                matched_entity_name=candidate.name,
            )
        return EntityResolution(
            action="created",
            canonical_name=mention.name,
            score=min(1.0, score),
        )

    # -- second pass -------------------------------------------------------

    def _cluster_unmatched(
        self,
        mentions: Sequence[_Mention],
        results: list[EntityResolution],
    ) -> None:
        """Cluster the mentions that matched nothing stored, among themselves.

        Merges target the mention seen **first** in the episode, so the node
        the caller creates for it becomes the anchor every later variant links
        to. Mutates ``results`` in place.
        """
        anchors: dict[str, list[_Mention]] = defaultdict(list)
        for mention in mentions:
            result = results[mention.index]
            if result.action == "merged":
                continue
            if result.action == "created":
                bucket = anchors[mention.entity_type]
                best: tuple[_Mention, float, str | None] | None = None
                for anchor in bucket:
                    score, match_type = self._score(mention, self._candidate_from_mention(anchor))
                    if best is None or score > best[1]:
                        best = (anchor, score, match_type)
                if best is not None:
                    anchor, score, match_type = best
                    auto, review = self._thresholds(mention.entity_type, mention.subtype)
                    if score >= auto:
                        results[mention.index] = EntityResolution(
                            action="merged",
                            canonical_name=anchor.name,
                            score=min(1.0, score),
                            match_type=match_type,
                            matched_entity_name=anchor.name,
                        )
                        continue
                    if score >= review:
                        results[mention.index] = EntityResolution(
                            action="review",
                            canonical_name=mention.name,
                            score=min(1.0, score),
                            match_type=match_type,
                            matched_entity_name=anchor.name,
                        )
            # A created *or* reviewed mention becomes a node, so it can anchor
            # the ones that follow it in this episode.
            anchors[mention.entity_type].append(mention)


@dataclass
class _NameOnly:
    """Minimal stand-in for an ``ExtractedEntity`` (name + type only)."""

    name: str
    type: str
    subtype: str | None = None
    context: str | None = None
