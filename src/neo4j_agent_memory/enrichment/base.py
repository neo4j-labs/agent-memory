"""Base enrichment classes and protocols.

Defines the core interfaces for entity enrichment from external knowledge sources.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class EnrichmentStatus(str, Enum):
    """Status of an enrichment operation."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    SKIPPED = "skipped"  # Entity type not supported by provider


@dataclass
class EnrichmentResult:
    """Result from an enrichment operation.

    Contains enriched data fetched from external sources like Wikipedia or Diffbot.
    """

    entity_name: str
    entity_type: str
    provider: str
    status: EnrichmentStatus

    # Core enrichment data
    description: str | None = None
    summary: str | None = None

    # Structured metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Wikipedia/Wikimedia specific
    wikipedia_url: str | None = None
    wikidata_id: str | None = None

    # Diffbot specific
    diffbot_uri: str | None = None

    # Images and media
    image_url: str | None = None
    images: list[str] = field(default_factory=list)

    # Related entities (for knowledge graph expansion)
    related_entities: list[dict[str, Any]] = field(default_factory=list)

    # Confidence and provenance
    confidence: float = 1.0
    source_url: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Error information
    error_message: str | None = None

    def has_data(self) -> bool:
        """Check if enrichment returned useful data."""
        return self.status == EnrichmentStatus.SUCCESS and (
            self.description is not None or self.summary is not None or bool(self.metadata)
        )

    def to_entity_attributes(self) -> dict[str, Any]:
        """Convert to attributes dict for Entity storage."""
        attrs: dict[str, Any] = {}
        if self.description:
            attrs["enriched_description"] = self.description
        if self.summary:
            attrs["enriched_summary"] = self.summary
        if self.wikipedia_url:
            attrs["wikipedia_url"] = self.wikipedia_url
        if self.wikidata_id:
            attrs["wikidata_id"] = self.wikidata_id
        if self.diffbot_uri:
            attrs["diffbot_uri"] = self.diffbot_uri
        if self.image_url:
            attrs["image_url"] = self.image_url
        if self.metadata:
            attrs["enrichment_metadata"] = self.metadata
        attrs["enrichment_provider"] = self.provider
        attrs["enrichment_timestamp"] = self.retrieved_at.isoformat()
        return attrs


@runtime_checkable
class EnrichmentProvider(Protocol):
    """Protocol for entity enrichment implementations.

    Enrichment providers fetch additional data about entities from external
    knowledge sources like Wikipedia, Wikidata, or Diffbot.

    Example:
        provider = WikimediaProvider()
        result = await provider.enrich("Albert Einstein", "PERSON")
        if result.has_data():
            print(result.description)
    """

    @property
    def name(self) -> str:
        """Provider name for identification."""
        ...

    @property
    def supported_entity_types(self) -> list[str]:
        """Entity types this provider can enrich."""
        ...

    def supports_entity_type(self, entity_type: str) -> bool:
        """Check if provider supports given entity type."""
        ...

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> EnrichmentResult:
        """
        Enrich an entity with external data.

        Args:
            entity_name: Name of the entity to enrich
            entity_type: Type of entity (PERSON, ORGANIZATION, LOCATION, etc.)
            context: Optional context to disambiguate (e.g., "American politician")
            language: Language code for results

        Returns:
            EnrichmentResult with fetched data or error status
        """
        ...


@dataclass
class EnrichmentTask:
    """A task in the enrichment queue."""

    entity_id: UUID
    entity_name: str
    entity_type: str
    context: str | None = None
    priority: int = 0  # Higher = more urgent
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0
    max_retries: int = 3


class NoOpEnrichmentProvider:
    """Enrichment provider that does nothing (for when enrichment is disabled)."""

    @property
    def name(self) -> str:
        return "noop"

    @property
    def supported_entity_types(self) -> list[str]:
        return []

    def supports_entity_type(self, entity_type: str) -> bool:
        return False

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> EnrichmentResult:
        """Return empty result."""
        return EnrichmentResult(
            entity_name=entity_name,
            entity_type=entity_type,
            provider=self.name,
            status=EnrichmentStatus.SKIPPED,
            error_message="Enrichment disabled",
        )
