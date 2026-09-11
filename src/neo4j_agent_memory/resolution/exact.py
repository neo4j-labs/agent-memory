"""Exact match entity resolution."""

from neo4j_agent_memory.resolution.base import (
    BaseResolver,
    ResolutionMatch,
    ResolvedEntity,
)


class ExactMatchResolver(BaseResolver):
    """
    Exact match entity resolver.

    Matches entities with identical normalized names (case-insensitive,
    whitespace-normalized).
    """

    def __init__(self, *, case_sensitive: bool = False):
        """
        Initialize exact match resolver.

        Args:
            case_sensitive: Whether to use case-sensitive matching
        """
        self._case_sensitive = case_sensitive

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> ResolvedEntity:
        """Resolve entity using exact matching.

        ``match_type`` is ``"exact"`` only when a candidate actually matched;
        the two non-matching paths report ``"none"`` (the same sentinel
        :class:`~neo4j_agent_memory.resolution.composite.CompositeResolver`
        uses), so callers can read the field instead of re-comparing names.
        """
        if not existing_entities:
            return ResolvedEntity(
                original_name=entity_name,
                canonical_name=entity_name,
                entity_type=entity_type,
                confidence=1.0,
                match_type="none",
            )

        normalized = self._normalize(entity_name) if not self._case_sensitive else entity_name

        for existing in existing_entities:
            existing_normalized = (
                self._normalize(existing) if not self._case_sensitive else existing
            )
            if normalized == existing_normalized:
                return ResolvedEntity(
                    original_name=entity_name,
                    canonical_name=existing,  # Use existing name as canonical
                    entity_type=entity_type,
                    confidence=1.0,
                    merged_from=[entity_name] if entity_name != existing else [],
                    match_type="exact",
                )

        # No match found, entity is its own canonical form
        return ResolvedEntity(
            original_name=entity_name,
            canonical_name=entity_name,
            entity_type=entity_type,
            confidence=1.0,
            match_type="none",
        )

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """Find exact matches from candidates."""
        matches = []
        normalized = self._normalize(entity_name) if not self._case_sensitive else entity_name

        for candidate in candidates:
            candidate_normalized = (
                self._normalize(candidate) if not self._case_sensitive else candidate
            )
            if normalized == candidate_normalized:
                matches.append(
                    ResolutionMatch(
                        entity1_name=entity_name,
                        entity2_name=candidate,
                        similarity_score=1.0,
                        match_type="exact",
                    )
                )

        return matches
