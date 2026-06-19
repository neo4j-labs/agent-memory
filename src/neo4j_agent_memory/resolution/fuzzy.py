"""Fuzzy match entity resolution using RapidFuzz."""

from __future__ import annotations

from collections.abc import Callable

from neo4j_agent_memory.core.exceptions import ResolutionError
from neo4j_agent_memory.resolution.base import (
    BaseResolver,
    ResolutionMatch,
    ResolvedEntity,
)


def is_rapidfuzz_available() -> bool:
    """Check if RapidFuzz is available."""
    try:
        import rapidfuzz  # noqa: F401

        return True
    except ImportError:
        return False


class FuzzyMatchResolver(BaseResolver):
    """
    Fuzzy match entity resolver using RapidFuzz.

    Uses token-based fuzzy matching to find similar entity names.
    """

    _scorer: Callable[..., float] | None

    def __init__(
        self,
        *,
        threshold: float = 0.85,
        scorer: str = "token_sort_ratio",
    ):
        """
        Initialize fuzzy match resolver.

        Args:
            threshold: Minimum similarity score (0.0-1.0) to consider a match
            scorer: RapidFuzz scorer to use (token_sort_ratio, ratio, partial_ratio, etc.)
        """
        self._threshold = threshold
        self._scorer_name = scorer
        self._scorer = None
        self._available = is_rapidfuzz_available()

    @property
    def is_available(self) -> bool:
        """Check if this resolver is available (has required dependencies)."""
        return self._available

    def _ensure_scorer(self) -> Callable[..., float]:
        """Ensure the RapidFuzz scorer is loaded and return it."""
        if self._scorer is not None:
            return self._scorer

        try:
            from rapidfuzz import fuzz
        except ImportError:
            raise ResolutionError(
                "RapidFuzz package not installed. Install with: pip install neo4j-agent-memory[fuzzy]"
            )

        scorers: dict[str, Callable[..., float]] = {
            "ratio": fuzz.ratio,
            "partial_ratio": fuzz.partial_ratio,
            "token_sort_ratio": fuzz.token_sort_ratio,
            "token_set_ratio": fuzz.token_set_ratio,
            "WRatio": fuzz.WRatio,
            "QRatio": fuzz.QRatio,
        }

        self._scorer = scorers.get(self._scorer_name, fuzz.token_sort_ratio)
        return self._scorer

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> ResolvedEntity:
        """Resolve entity using fuzzy matching."""
        if not existing_entities:
            return ResolvedEntity(
                original_name=entity_name,
                canonical_name=entity_name,
                entity_type=entity_type,
                confidence=1.0,
                match_type="fuzzy",
            )

        scorer = self._ensure_scorer()
        normalized = self._normalize(entity_name)

        best_match = None
        best_score = 0.0

        for existing in existing_entities:
            existing_normalized = self._normalize(existing)
            score = float(scorer(normalized, existing_normalized)) / 100.0  # Normalize to 0-1

            if score >= self._threshold and score > best_score:
                best_match = existing
                best_score = score

        if best_match is not None:
            return ResolvedEntity(
                original_name=entity_name,
                canonical_name=best_match,
                entity_type=entity_type,
                confidence=best_score,
                merged_from=[entity_name] if entity_name != best_match else [],
                match_type="fuzzy",
            )

        # No match found
        return ResolvedEntity(
            original_name=entity_name,
            canonical_name=entity_name,
            entity_type=entity_type,
            confidence=1.0,
            match_type="fuzzy",
        )

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """Find fuzzy matches from candidates."""
        scorer = self._ensure_scorer()
        matches = []
        normalized = self._normalize(entity_name)

        for candidate in candidates:
            candidate_normalized = self._normalize(candidate)
            score = float(scorer(normalized, candidate_normalized)) / 100.0

            if score >= self._threshold:
                matches.append(
                    ResolutionMatch(
                        entity1_name=entity_name,
                        entity2_name=candidate,
                        similarity_score=score,
                        match_type="fuzzy",
                    )
                )

        # Sort by similarity score descending
        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches
