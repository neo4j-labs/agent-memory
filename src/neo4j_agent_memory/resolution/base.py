"""Base resolution classes and protocols."""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ResolvedEntity(BaseModel):
    """Result of entity resolution."""

    original_name: str = Field(description="Original entity name")
    canonical_name: str = Field(description="Resolved canonical name")
    entity_type: str = Field(description="Entity type")
    cluster_id: str | None = Field(default=None, description="Resolution cluster ID")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Resolution confidence")
    merged_from: list[str] = Field(
        default_factory=list, description="Names merged into this entity"
    )
    match_type: str | None = Field(
        default=None, description="Type of match (exact, fuzzy, semantic)"
    )

    @property
    def normalized_name(self) -> str:
        """Return normalized canonical name."""
        return self.canonical_name.lower().strip()


class ResolutionMatch(BaseModel):
    """A match between two entities."""

    entity1_name: str = Field(description="First entity name")
    entity2_name: str = Field(description="Second entity name")
    similarity_score: float = Field(ge=0.0, le=1.0, description="Similarity score")
    match_type: str = Field(description="Type of match")


@runtime_checkable
class EntityResolver(Protocol):
    """Protocol for entity resolution strategies."""

    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
        existing_entity_types: dict[str, str] | None = None,
    ) -> ResolvedEntity:
        """
        Resolve an entity to its canonical form.

        Args:
            entity_name: The entity name to resolve
            entity_type: The entity type
            existing_entities: Optional list of existing entity names to match against
            existing_entity_types: Optional mapping of entity names to their types
                for type-aware resolution (prevents cross-type merges)

        Returns:
            ResolvedEntity with canonical name
        """
        ...

    async def resolve_batch(
        self,
        entities: list[tuple[str, str]],
    ) -> list[ResolvedEntity]:
        """
        Resolve multiple entities efficiently.

        Args:
            entities: List of (name, type) tuples

        Returns:
            List of resolved entities
        """
        ...

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """
        Find matching entities from candidates.

        Args:
            entity_name: The entity name to match
            entity_type: The entity type
            candidates: List of candidate entity names

        Returns:
            List of matches sorted by similarity
        """
        ...


class BaseResolver(ABC):
    """Abstract base class for resolver implementations."""

    @abstractmethod
    async def resolve(
        self,
        entity_name: str,
        entity_type: str,
        *,
        existing_entities: list[str] | None = None,
        existing_entity_types: dict[str, str] | None = None,
    ) -> ResolvedEntity:
        """Resolve an entity to its canonical form."""
        pass

    async def resolve_batch(
        self,
        entities: list[tuple[str, str]],
    ) -> list[ResolvedEntity]:
        """
        Resolve multiple entities.

        Default implementation calls resolve() for each entity.
        Subclasses should override for better performance.
        """
        results = []
        for name, entity_type in entities:
            result = await self.resolve(name, entity_type)
            results.append(result)
        return results

    async def find_matches(
        self,
        entity_name: str,
        entity_type: str,
        candidates: list[str],
    ) -> list[ResolutionMatch]:
        """
        Find matching entities from candidates.

        Default implementation returns empty list.
        Subclasses should override to provide matching logic.
        """
        return []

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        return " ".join(text.lower().strip().split())
