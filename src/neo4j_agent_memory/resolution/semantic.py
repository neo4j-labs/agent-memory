"""Semantic match entity resolution using embeddings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from neo4j_agent_memory.resolution.base import (
    BaseResolver,
    ResolutionMatch,
    ResolvedEntity,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder


class SemanticMatchResolver(BaseResolver):
    """
    Semantic match entity resolver using embeddings.

    Uses cosine similarity between embeddings to find semantically similar entities.
    """

    def __init__(
        self,
        embedder: Embedder,
        *,
        threshold: float = 0.8,
    ):
        """
        Initialize semantic match resolver.

        Args:
            embedder: Embedder to use for generating embeddings
            threshold: Minimum cosine similarity (0.0-1.0) to consider a match
        """
        self._embedder = embedder
        self._threshold = threshold
        # Cache embeddings to avoid recomputation
        self._embedding_cache: dict[str, list[float]] = {}

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding for text, using cache if available."""
        normalized = self._normalize(text)
        if normalized not in self._embedding_cache:
            self._embedding_cache[normalized] = await self._embedder.embed(text)
        return self._embedding_cache[normalized]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot_product: float = sum(x * y for x, y in zip(a, b))
        norm_a: float = sum(x * x for x in a) ** 0.5
        norm_b: float = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        # Clamp to [0.0, 1.0] to handle floating point precision issues
        return float(min(1.0, max(0.0, dot_product / (norm_a * norm_b))))

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
    ) -> ResolvedEntity:
        """Resolve entity using semantic similarity."""
        if not existing_entities:
            return ResolvedEntity(
                original_name=entity_name,
                canonical_name=entity_name,
                entity_type=entity_type,
                confidence=1.0,
                match_type="semantic",
            )

        entity_embedding = await self._get_embedding(entity_name)
        best_match = None
        best_score = 0.0

        for existing in existing_entities:
            existing_embedding = await self._get_embedding(existing)
            score = self._cosine_similarity(entity_embedding, existing_embedding)

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
                match_type="semantic",
            )

        # No match found
        return ResolvedEntity(
            original_name=entity_name,
            canonical_name=entity_name,
            entity_type=entity_type,
            confidence=1.0,
            match_type="semantic",
        )

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """Find semantic matches from candidates."""
        matches = []
        entity_embedding = await self._get_embedding(entity_name)

        for candidate in candidates:
            candidate_embedding = await self._get_embedding(candidate)
            score = self._cosine_similarity(entity_embedding, candidate_embedding)

            if score >= self._threshold:
                matches.append(
                    ResolutionMatch(
                        entity1_name=entity_name,
                        entity2_name=candidate,
                        similarity_score=score,
                        match_type="semantic",
                    )
                )

        # Sort by similarity score descending
        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._embedding_cache.clear()
