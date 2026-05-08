"""Long-term memory for entities, preferences, and facts."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import Field

from neo4j_agent_memory.core.memory import BaseMemory, MemoryEntry
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import build_create_entity_query

# =============================================================================
# DEDUPLICATION CONFIGURATION
# =============================================================================


@dataclass
class DeduplicationConfig:
    """Configuration for entity deduplication on ingest.

    The deduplication process uses embedding similarity to identify potential
    duplicate entities. Depending on the similarity score, entities are either:
    - Auto-merged (score >= auto_merge_threshold)
    - Flagged for review (score >= flag_threshold but < auto_merge_threshold)
    - Treated as distinct (score < flag_threshold)

    Attributes:
        enabled: Whether deduplication is enabled (default True)
        auto_merge_threshold: Similarity threshold for automatic merging (default 0.95)
        flag_threshold: Similarity threshold for flagging potential duplicates (default 0.85)
        use_fuzzy_matching: Also check fuzzy string matching (default True)
        fuzzy_threshold: Threshold for fuzzy matching ratio (default 0.9)
        max_candidates: Maximum number of candidates to check (default 10)
        match_same_type_only: Only match entities of the same type (default True)
    """

    enabled: bool = True
    auto_merge_threshold: float = 0.95
    flag_threshold: float = 0.85
    use_fuzzy_matching: bool = True
    fuzzy_threshold: float = 0.9
    max_candidates: int = 10
    match_same_type_only: bool = True

    def __post_init__(self) -> None:
        """Validate configuration."""
        # Check ranges first
        if not (0 <= self.flag_threshold <= 1):
            raise ValueError("flag_threshold must be between 0 and 1")
        if not (0 <= self.auto_merge_threshold <= 1):
            raise ValueError("auto_merge_threshold must be between 0 and 1")
        if not (0 <= self.fuzzy_threshold <= 1):
            raise ValueError("fuzzy_threshold must be between 0 and 1")
        # Then check threshold order
        if self.auto_merge_threshold < self.flag_threshold:
            raise ValueError("auto_merge_threshold must be >= flag_threshold")


@dataclass
class DeduplicationResult:
    """Result of a deduplication check.

    Attributes:
        is_duplicate: Whether the entity was identified as a duplicate
        action: Action taken ('none', 'merged', 'flagged')
        matched_entity_id: ID of the matched entity (if any)
        matched_entity_name: Name of the matched entity (if any)
        similarity_score: Similarity score with matched entity
        match_type: Type of match ('embedding', 'fuzzy', 'both')
    """

    is_duplicate: bool = False
    action: str = "none"  # 'none', 'merged', 'flagged'
    matched_entity_id: UUID | None = None
    matched_entity_name: str | None = None
    similarity_score: float = 0.0
    match_type: str | None = None


@dataclass
class DuplicateCandidate:
    """A potential duplicate entity.

    Attributes:
        entity_id: ID of the potential duplicate entity
        entity_name: Name of the entity
        canonical_name: Canonical name (if set)
        entity_type: Type of the entity
        similarity_score: Embedding similarity score
        fuzzy_score: Fuzzy string match score (if computed)
        relationship_status: Status of SAME_AS relationship ('pending', 'confirmed', 'rejected')
    """

    entity_id: UUID
    entity_name: str
    canonical_name: str | None
    entity_type: str
    similarity_score: float
    fuzzy_score: float | None = None
    relationship_status: str = "pending"


@dataclass
class DeduplicationStats:
    """Statistics about entity deduplication.

    Attributes:
        total_entities: Total number of entities
        merged_entities: Number of merged entities
        same_as_relationships: Number of SAME_AS relationships
        pending_reviews: Number of pending duplicate reviews
    """

    total_entities: int = 0
    merged_entities: int = 0
    same_as_relationships: int = 0
    pending_reviews: int = 0


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Serialize metadata dict to JSON string for Neo4j storage."""
    if metadata is None or metadata == {}:
        return None
    return json.dumps(metadata)


def _deserialize_metadata(metadata_str: str | None) -> dict[str, Any]:
    """Deserialize metadata from JSON string."""
    if metadata_str is None:
        return {}
    try:
        return json.loads(metadata_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def _to_python_datetime(neo4j_datetime) -> datetime:
    """Convert Neo4j DateTime to Python datetime."""
    if neo4j_datetime is None:
        return datetime.utcnow()
    if isinstance(neo4j_datetime, datetime):
        return neo4j_datetime
    # Neo4j DateTime has to_native() method
    try:
        return neo4j_datetime.to_native()
    except AttributeError:
        return datetime.utcnow()


if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.enrichment.background import BackgroundEnrichmentService
    from neo4j_agent_memory.extraction.base import EntityExtractor
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.resolution.base import EntityResolver
    from neo4j_agent_memory.services.geocoder import Geocoder


class EntityType(str, Enum):
    """Standard entity types (legacy enum for backward compatibility).

    For new code, prefer using string types directly with the POLE+O model:
    - PERSON: Individuals, aliases, personas
    - OBJECT: Physical/digital items (vehicles, phones, documents)
    - LOCATION: Geographic areas, addresses, places
    - EVENT: Incidents connecting entities across time/place
    - ORGANIZATION: Companies, non-profits, groups
    """

    # POLE+O types
    PERSON = "PERSON"
    OBJECT = "OBJECT"
    LOCATION = "LOCATION"
    EVENT = "EVENT"
    ORGANIZATION = "ORGANIZATION"

    # Legacy types (mapped to POLE+O)
    CONCEPT = "CONCEPT"  # -> typically OBJECT or custom
    EMOTION = "EMOTION"  # -> typically OBJECT:EMOTION
    PREFERENCE = "PREFERENCE"  # -> stored separately
    FACT = "FACT"  # -> stored separately


# POLE+O type constants for convenience
POLEO_TYPES = ["PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"]


def normalize_entity_type(entity_type: str | EntityType) -> str:
    """Normalize entity type to uppercase string."""
    if isinstance(entity_type, EntityType):
        return entity_type.value
    return entity_type.upper()


def parse_entity_type(type_str: str) -> tuple[str, str | None]:
    """Parse entity type string into (type, subtype).

    Supports format "TYPE" or "TYPE:SUBTYPE".

    Examples:
        parse_entity_type("PERSON") -> ("PERSON", None)
        parse_entity_type("OBJECT:VEHICLE") -> ("OBJECT", "VEHICLE")
        parse_entity_type("location:address") -> ("LOCATION", "ADDRESS")
    """
    if ":" in type_str:
        parts = type_str.upper().split(":", 1)
        return parts[0], parts[1] if len(parts) > 1 else None
    return type_str.upper(), None


class Entity(MemoryEntry):
    """An entity extracted from conversations or documents.

    Supports the POLE+O data model (Person, Object, Location, Event, Organization)
    with optional subtypes for finer classification.

    Attributes:
        name: Entity name
        canonical_name: Resolved canonical name (after entity resolution)
        type: Entity type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION)
        subtype: Optional subtype (e.g., VEHICLE for OBJECT, ADDRESS for LOCATION)
        description: Optional entity description
        confidence: Confidence score from extraction/resolution
        aliases: Alternative names for this entity
        attributes: Additional flexible attributes
        source_id: ID of source message/document
    """

    name: str = Field(description="Entity name")
    canonical_name: str | None = Field(default=None, description="Resolved canonical name")
    type: str = Field(description="Entity type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION)")
    subtype: str | None = Field(
        default=None, description="Entity subtype (e.g., VEHICLE for OBJECT)"
    )
    description: str | None = Field(default=None, description="Entity description")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    aliases: list[str] = Field(default_factory=list, description="Alternative names")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Additional entity attributes"
    )
    source_id: UUID | None = Field(default=None, description="Source message/document ID")

    @property
    def display_name(self) -> str:
        """Get the display name (canonical if available)."""
        return self.canonical_name or self.name

    @property
    def full_type(self) -> str:
        """Get full type including subtype if present."""
        if self.subtype:
            return f"{self.type}:{self.subtype}"
        return self.type

    @property
    def entity_type(self) -> EntityType | None:
        """Get EntityType enum if type matches, for backward compatibility."""
        try:
            return EntityType(self.type)
        except ValueError:
            return None


class Relationship(MemoryEntry):
    """A relationship between entities with temporal bounds."""

    source_id: UUID = Field(description="Source entity ID")
    target_id: UUID = Field(description="Target entity ID")
    type: str = Field(description="Relationship type")
    description: str | None = Field(default=None, description="Relationship description")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    valid_from: datetime | None = Field(default=None, description="Start of validity")
    valid_until: datetime | None = Field(default=None, description="End of validity")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Additional relationship attributes"
    )


class Preference(MemoryEntry):
    """A user preference."""

    category: str = Field(description="Preference category")
    preference: str = Field(description="The preference statement")
    context: str | None = Field(default=None, description="When/where preference applies")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    linked_entities: list[UUID] = Field(default_factory=list, description="Linked entity IDs")


class Fact(MemoryEntry):
    """A declarative fact about the user or domain."""

    subject: str = Field(description="Fact subject")
    predicate: str = Field(description="Fact predicate/relationship")
    object: str = Field(description="Fact object")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    source_id: UUID | None = Field(default=None, description="Source message/document ID")
    valid_from: datetime | None = Field(default=None, description="Start of validity")
    valid_until: datetime | None = Field(default=None, description="End of validity")

    @property
    def as_triple(self) -> tuple[str, str, str]:
        """Return fact as (subject, predicate, object) triple."""
        return (self.subject, self.predicate, self.object)


class LongTermMemory(BaseMemory[Entity]):
    """
    Long-term/Declarative memory stores facts, preferences, and entities.

    Provides:
    - Entity storage with resolution/deduplication
    - User preferences with categories
    - Facts with temporal validity
    - Relationships between entities

    Supports the POLE+O data model (Person, Object, Location, Event, Organization)
    as the default entity schema, but allows custom entity types via configuration.
    """

    def __init__(
        self,
        client: "Neo4jClient",
        embedder: "Embedder | None" = None,
        extractor: "EntityExtractor | None" = None,
        resolver: "EntityResolver | None" = None,
        geocoder: "Geocoder | None" = None,
        enrichment_service: "BackgroundEnrichmentService | None" = None,
        entity_types: list[str] | None = None,
        strict_types: bool = False,
        deduplication: DeduplicationConfig | None = None,
        *,
        multi_tenant: bool = False,
    ):
        """Initialize long-term memory.

        Args:
            client: Neo4j client for database operations
            embedder: Optional embedder for semantic search
            extractor: Optional entity extractor
            resolver: Optional entity resolver for deduplication
            geocoder: Optional geocoder for Location entities
            enrichment_service: Optional background enrichment service
            entity_types: Allowed entity types (defaults to POLE+O)
            strict_types: If True, reject entities with unknown types
            deduplication: Optional deduplication configuration (defaults to enabled)
            multi_tenant: If True, ``add_preference`` raises when
                ``user_identifier`` is omitted.
        """
        super().__init__(client, embedder, extractor)
        self._multi_tenant = multi_tenant
        self._resolver = resolver
        self._geocoder = geocoder
        self._enrichment_service = enrichment_service
        self._entity_types = entity_types or POLEO_TYPES
        self._strict_types = strict_types
        self._deduplication = deduplication or DeduplicationConfig()

    def _enforce_multi_tenant(self, user_identifier: str | None) -> None:
        """Raise if multi-tenant is on and ``user_identifier`` is missing."""
        if self._multi_tenant and user_identifier is None:
            raise ValueError(
                "MemorySettings.memory.multi_tenant=True but no "
                "user_identifier was supplied. Pass user_identifier= to "
                "scope this preference."
            )

    def _validate_entity_type(self, entity_type: str) -> str:
        """Validate and normalize entity type."""
        normalized = normalize_entity_type(entity_type)
        base_type, _ = parse_entity_type(normalized)

        if self._strict_types and base_type not in self._entity_types:
            raise ValueError(
                f"Unknown entity type: {base_type}. Allowed types: {self._entity_types}"
            )

        return normalized

    async def add(self, content: str, **kwargs: Any) -> Entity:
        """Add content as an entity."""
        name = kwargs.get("name", content)
        entity_type = kwargs.get("type", "OBJECT")
        return await self.add_entity(name, entity_type, **kwargs)

    async def add_entity(
        self,
        name: str,
        entity_type: EntityType | str,
        *,
        subtype: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        resolve: bool = True,
        generate_embedding: bool = True,
        deduplicate: bool = True,
        geocode: bool = True,
        enrich: bool = True,
        coordinates: tuple[float, float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Entity, DeduplicationResult]:
        """
        Add an entity with optional resolution and deduplication.

        Args:
            name: Entity name
            entity_type: Entity type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION)
            subtype: Optional subtype (e.g., VEHICLE for OBJECT)
            description: Optional description
            aliases: Optional list of alternative names
            attributes: Optional additional attributes
            resolve: Whether to resolve against existing entities
            generate_embedding: Whether to generate embedding
            deduplicate: Whether to check for duplicate entities
            geocode: Whether to geocode LOCATION entities (requires geocoder)
            enrich: Whether to queue for background enrichment
            coordinates: Optional (latitude, longitude) tuple to set directly
            metadata: Optional metadata

        Returns:
            Tuple of (entity, deduplication_result). If entity was auto-merged,
            returns the existing entity. The deduplication_result indicates
            what action was taken.
        """
        # Normalize and validate type
        type_str = self._validate_entity_type(
            entity_type.value if isinstance(entity_type, EntityType) else entity_type
        )

        # Parse type and subtype if provided in type string
        parsed_type, parsed_subtype = parse_entity_type(type_str)
        final_subtype = subtype or parsed_subtype

        canonical_name = name
        confidence = 1.0

        # Resolve against existing entities
        if resolve and self._resolver is not None:
            existing = await self._get_existing_entity_names(parsed_type)
            resolved = await self._resolver.resolve(name, parsed_type, existing_entities=existing)
            canonical_name = resolved.canonical_name
            confidence = resolved.confidence

        # Generate embedding
        embedding = None
        if generate_embedding and self._embedder is not None:
            embedding = await self._embedder.embed(name)

        # Check for duplicates using embedding similarity
        dedup_result = DeduplicationResult()
        if deduplicate and self._deduplication.enabled and embedding is not None:
            dedup_result = await self._check_for_duplicates(
                name=name,
                entity_type=parsed_type,
                embedding=embedding,
            )

            # If auto-merged, return the existing entity
            if dedup_result.action == "merged" and dedup_result.matched_entity_id:
                existing_entity = await self._get_entity_by_id(dedup_result.matched_entity_id)
                if existing_entity:
                    # Add the new name as an alias if not already present
                    if name not in existing_entity.aliases and name != existing_entity.name:
                        await self._add_alias_to_entity(dedup_result.matched_entity_id, name)
                        existing_entity.aliases.append(name)
                    return existing_entity, dedup_result

        # Geocode if this is a LOCATION entity
        location_point: dict[str, float] | None = None
        if parsed_type == "LOCATION":
            if coordinates is not None:
                # Use provided coordinates
                location_point = {"latitude": coordinates[0], "longitude": coordinates[1]}
            elif geocode and self._geocoder is not None:
                # Geocode the location name
                geocode_result = await self._geocoder.geocode(name)
                if geocode_result is not None:
                    location_point = geocode_result.as_neo4j_point()

        # Create entity
        entity = Entity(
            id=uuid4(),
            name=name,
            canonical_name=canonical_name,
            type=parsed_type,
            subtype=final_subtype,
            description=description,
            embedding=embedding,
            confidence=confidence,
            aliases=aliases or [],
            attributes=attributes or {},
            metadata=metadata or {},
        )

        # Store coordinates in attributes for later access
        if location_point is not None:
            entity.attributes["coordinates"] = location_point

        # Merge attributes into metadata for storage
        storage_metadata = {**entity.metadata}
        if entity.attributes:
            storage_metadata["attributes"] = entity.attributes
        if entity.aliases:
            storage_metadata["aliases"] = entity.aliases

        # Store entity with dynamic labels for type/subtype
        create_query = build_create_entity_query(entity.type, entity.subtype)
        await self._client.execute_write(
            create_query,
            {
                "id": str(entity.id),
                "name": entity.name,
                "type": entity.type,
                "subtype": entity.subtype,
                "canonical_name": entity.canonical_name,
                "description": entity.description,
                "embedding": entity.embedding,
                "confidence": entity.confidence,
                "metadata": _serialize_metadata(storage_metadata) if storage_metadata else None,
                "location": location_point,  # Neo4j Point for LOCATION entities
            },
        )

        # If flagged for review, create SAME_AS relationship
        if dedup_result.action == "flagged" and dedup_result.matched_entity_id:
            await self._client.execute_write(
                queries.CREATE_SAME_AS_RELATIONSHIP,
                {
                    "source_id": str(entity.id),
                    "target_id": str(dedup_result.matched_entity_id),
                    "confidence": dedup_result.similarity_score,
                    "match_type": dedup_result.match_type or "embedding",
                    "status": "pending",
                },
            )

        # Queue for background enrichment (non-blocking)
        if enrich and self._enrichment_service is not None and self._enrichment_service.is_running:
            await self._enrichment_service.enqueue(
                entity_id=entity.id,
                entity_name=entity.name,
                entity_type=entity.type,
                context=entity.description,
                confidence=entity.confidence,
            )

        return entity, dedup_result

    async def add_preference(
        self,
        category: str,
        preference: str,
        *,
        context: str | None = None,
        confidence: float = 1.0,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
        user_identifier: str | None = None,
        applies_to: "list | None" = None,
    ) -> Preference:
        """
        Add a user preference with deduplication.

        If a preference in the same category already exists with high
        semantic similarity, returns the existing one instead of creating
        a duplicate.

        Args:
            category: Preference category (food, music, communication, etc.)
            preference: The preference statement
            context: When/where preference applies
            confidence: Confidence score
            generate_embedding: Whether to generate embedding
            metadata: Optional metadata
            user_identifier: When provided, writes a
                ``(:User)-[:HAS_PREFERENCE]->(:Preference)`` edge linking
                the preference to the user's :User node. Multi-tenant
                deployments should pass this on every call.
            applies_to: Optional list of :class:`EntityRef` describing the
                entities this preference scopes to. Each ref is materialized
                as a ``(:Preference)-[:APPLIES_TO]->(:Entity)`` edge so
                ``get_preferences_for(user, applies_to=...)`` queries can
                find scoped preferences efficiently.

        Returns:
            The created or existing preference
        """
        self._enforce_multi_tenant(user_identifier)

        # Generate embedding
        embedding = None
        if generate_embedding and self._embedder is not None:
            text = f"{category}: {preference}"
            if context:
                text += f" ({context})"
            embedding = await self._embedder.embed(text)

        # Check for duplicate preferences (same category, high similarity)
        if self._deduplication.enabled and embedding is not None:
            try:
                dupes = await self._client.execute_read(
                    queries.FIND_DUPLICATE_PREFERENCES,
                    {
                        "embedding": embedding,
                        "limit": 3,
                        "threshold": 0.95,
                        "category": category,
                    },
                )
                if dupes:
                    existing_data = dict(dupes[0]["p"])
                    existing = self._parse_preference(existing_data)
                    existing.metadata["deduplicated"] = True
                    # Even on a dedupe hit we still want to attach the user
                    # and applies-to edges, since this caller may be a
                    # different user pinning the same general preference.
                    if user_identifier is not None:
                        await self._link_user_to_preference(user_identifier, str(existing.id))
                    if applies_to:
                        for ref in applies_to:
                            await self._link_preference_to_entity(str(existing.id), ref)
                    return existing
            except Exception:
                pass  # Vector index may not exist yet

        # Create preference
        pref = Preference(
            id=uuid4(),
            category=category,
            preference=preference,
            context=context,
            confidence=confidence,
            embedding=embedding,
            metadata=metadata or {},
        )

        # Store preference
        await self._client.execute_write(
            queries.CREATE_PREFERENCE,
            {
                "id": str(pref.id),
                "category": pref.category,
                "preference": pref.preference,
                "context": pref.context,
                "confidence": pref.confidence,
                "embedding": pref.embedding,
                "metadata": _serialize_metadata(pref.metadata),
            },
        )

        # Set bi-temporal validity bounds on the new preference. ``valid_from``
        # is now; ``valid_until`` stays null until ``supersede_preference``
        # closes the interval.
        await self._client.execute_write(
            "MATCH (p:Preference {id: $id}) SET p.valid_from = datetime()",
            {"id": str(pref.id)},
        )

        # Wire up :HAS_PREFERENCE / :APPLIES_TO when the caller supplied them.
        if user_identifier is not None:
            await self._link_user_to_preference(user_identifier, str(pref.id))
        if applies_to:
            for ref in applies_to:
                await self._link_preference_to_entity(str(pref.id), ref)

        return pref

    async def _link_user_to_preference(self, user_identifier: str, preference_id: str) -> None:
        """Idempotently write ``(:User)-[:HAS_PREFERENCE]->(:Preference)``."""
        await self._client.execute_write(
            """
            MERGE (u:User {identifier: $user_identifier})
            ON CREATE SET u.id = $user_identifier, u.created_at = datetime()
            WITH u
            MATCH (p:Preference {id: $preference_id})
            MERGE (u)-[:HAS_PREFERENCE]->(p)
            """,
            {
                "user_identifier": user_identifier,
                "preference_id": preference_id,
            },
        )

    async def _link_preference_to_entity(
        self,
        preference_id: str,
        ref: Any,
    ) -> None:
        """Write ``(:Preference)-[:APPLIES_TO]->(:Entity)`` for an EntityRef."""
        if getattr(ref, "id", None):
            await self._client.execute_write(
                """
                MATCH (p:Preference {id: $preference_id})
                MATCH (e:Entity {id: $entity_id})
                MERGE (p)-[:APPLIES_TO]->(e)
                """,
                {"preference_id": preference_id, "entity_id": ref.id},
            )
        elif getattr(ref, "type", None):
            await self._client.execute_write(
                """
                MATCH (p:Preference {id: $preference_id})
                MERGE (e:Entity {name: $name, type: $type})
                ON CREATE SET e.id = coalesce(e.id, $name + ':' + $type),
                              e.created_at = datetime()
                MERGE (p)-[:APPLIES_TO]->(e)
                """,
                {
                    "preference_id": preference_id,
                    "name": ref.name,
                    "type": ref.type,
                },
            )
        else:
            await self._client.execute_write(
                """
                MATCH (p:Preference {id: $preference_id})
                MERGE (e:Entity {name: $name})
                ON CREATE SET e.id = coalesce(e.id, $name),
                              e.created_at = datetime()
                MERGE (p)-[:APPLIES_TO]->(e)
                """,
                {"preference_id": preference_id, "name": ref.name},
            )

    async def supersede_preference(
        self,
        old_preference_id: "UUID | str",
        new_preference_id: "UUID | str",
    ) -> None:
        """Mark ``old`` as superseded by ``new``.

        Writes ``(:Preference)-[:SUPERSEDED_BY]->(:Preference)`` and sets
        ``old.valid_until = datetime()`` so time-travel queries
        (``get_preferences_for(..., as_of=...)``) return the right snapshot.

        Idempotent: re-running on an already-superseded preference is a
        no-op (the edge MERGEs and ``valid_until`` is only set if null).
        """
        old_id = str(old_preference_id)
        new_id = str(new_preference_id)
        await self._client.execute_write(
            """
            MATCH (old:Preference {id: $old_id})
            MATCH (new:Preference {id: $new_id})
            MERGE (old)-[:SUPERSEDED_BY]->(new)
            SET old.valid_until = coalesce(old.valid_until, datetime())
            """,
            {"old_id": old_id, "new_id": new_id},
        )

    async def get_preferences_for(
        self,
        user_identifier: str,
        *,
        applies_to: Any | None = None,
        active_only: bool = True,
        as_of: "datetime | None" = None,
    ) -> list[Preference]:
        """Return preferences scoped to a user.

        Args:
            user_identifier: The user whose preferences to return.
            applies_to: Optional :class:`EntityRef` to scope further
                (e.g. preferences scoped to a particular Industry).
            active_only: When ``True`` (default), exclude preferences
                that have been superseded.
            as_of: When provided, only preferences whose validity interval
                contains this timestamp are returned. Implements the
                v0.5 bi-temporal time-travel API on top of valid_from /
                valid_until set by :meth:`add_preference` and
                :meth:`supersede_preference`.
        """
        params: dict[str, Any] = {"user_identifier": user_identifier}
        clauses: list[str] = [
            "MATCH (u:User {identifier: $user_identifier})-[:HAS_PREFERENCE]->(p:Preference)"
        ]
        where: list[str] = []

        if active_only:
            where.append("NOT (p)-[:SUPERSEDED_BY]->(:Preference)")

        if as_of is not None:
            where.append(
                "(p.valid_from IS NULL OR p.valid_from <= datetime($as_of)) "
                "AND (p.valid_until IS NULL OR p.valid_until > datetime($as_of))"
            )
            params["as_of"] = as_of.isoformat()

        if applies_to is not None:
            clauses.append("MATCH (p)-[:APPLIES_TO]->(e:Entity)")
            if getattr(applies_to, "id", None):
                where.append("e.id = $applies_to_id")
                params["applies_to_id"] = applies_to.id
            elif getattr(applies_to, "type", None):
                where.append("e.name = $applies_to_name AND e.type = $applies_to_type")
                params["applies_to_name"] = applies_to.name
                params["applies_to_type"] = applies_to.type
            else:
                where.append("e.name = $applies_to_name")
                params["applies_to_name"] = applies_to.name

        query = "\n".join(clauses)
        if where:
            query += "\nWHERE " + " AND ".join(where)
        query += "\nRETURN DISTINCT p"

        rows = await self._client.execute_read(query, params)
        return [self._parse_preference(dict(r["p"])) for r in rows]

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Fact:
        """
        Add a declarative fact with deduplication.

        If a fact with the same subject and predicate already exists
        with high semantic similarity, updates the existing fact's
        confidence instead of creating a duplicate.

        Args:
            subject: Fact subject
            predicate: Fact predicate/relationship
            obj: Fact object
            confidence: Confidence score
            valid_from: Start of validity
            valid_until: End of validity
            generate_embedding: Whether to generate embedding
            metadata: Optional metadata

        Returns:
            The created or existing fact
        """
        # Generate embedding
        embedding = None
        if generate_embedding and self._embedder is not None:
            text = f"{subject} {predicate} {obj}"
            embedding = await self._embedder.embed(text)

        # Check for duplicate facts (same subject+predicate, high similarity)
        if self._deduplication.enabled and embedding is not None:
            try:
                dupes = await self._client.execute_read(
                    queries.FIND_DUPLICATE_FACTS,
                    {
                        "embedding": embedding,
                        "limit": 3,
                        "threshold": 0.95,
                        "subject": subject,
                        "predicate": predicate,
                    },
                )
                if dupes:
                    existing_data = dict(dupes[0]["f"])
                    await self._client.execute_write(
                        queries.UPDATE_FACT_CONFIDENCE,
                        {
                            "id": existing_data["id"],
                            "confidence": confidence,
                            "object": obj,
                        },
                    )
                    existing = self._parse_fact(existing_data)
                    existing.metadata["deduplicated"] = True
                    return existing
            except Exception:
                pass  # Vector index may not exist yet; proceed with creation

        # Create fact
        fact = Fact(
            id=uuid4(),
            subject=subject,
            predicate=predicate,
            object=obj,
            confidence=confidence,
            embedding=embedding,
            valid_from=valid_from,
            valid_until=valid_until,
            metadata=metadata or {},
        )

        # Store fact
        await self._client.execute_write(
            queries.CREATE_FACT,
            {
                "id": str(fact.id),
                "subject": fact.subject,
                "predicate": fact.predicate,
                "object": fact.object,
                "confidence": fact.confidence,
                "embedding": fact.embedding,
                "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
                "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                "metadata": _serialize_metadata(fact.metadata),
            },
        )

        return fact

    async def add_relationship(
        self,
        source: Entity | UUID,
        target: Entity | UUID,
        relationship_type: str,
        *,
        description: str | None = None,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Relationship:
        """
        Add a relationship between entities.

        Args:
            source: Source entity or ID
            target: Target entity or ID
            relationship_type: Type of relationship
            description: Optional description
            confidence: Confidence score
            valid_from: Start of validity
            valid_until: End of validity
            attributes: Optional additional attributes

        Returns:
            The created relationship
        """
        source_id = source.id if isinstance(source, Entity) else source
        target_id = target.id if isinstance(target, Entity) else target

        relationship = Relationship(
            id=uuid4(),
            source_id=source_id,
            target_id=target_id,
            type=relationship_type,
            description=description,
            confidence=confidence,
            valid_from=valid_from,
            valid_until=valid_until,
            attributes=attributes or {},
        )

        await self._client.execute_write(
            queries.CREATE_ENTITY_RELATIONSHIP,
            {
                "id": str(relationship.id),
                "source_id": str(source_id),
                "target_id": str(target_id),
                "relation_type": relationship_type,
                "description": description,
                "confidence": confidence,
                "valid_from": valid_from.isoformat() if valid_from else None,
                "valid_until": valid_until.isoformat() if valid_until else None,
            },
        )

        return relationship

    async def get_entity_by_name(self, name: str) -> Entity | None:
        """
        Get an entity by name.

        Args:
            name: Entity name to search for (checks name, canonical_name, aliases)

        Returns:
            The entity if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_ENTITY_BY_NAME,
            {"name": name},
        )

        if not results:
            return None

        row = results[0]
        entity_data = dict(row["e"])

        return self._parse_entity(entity_data)

    async def search(self, query: str, **kwargs: Any) -> list[Entity]:
        """Search for entities."""
        return await self.search_entities(query, **kwargs)

    async def search_entities(
        self,
        query: str,
        *,
        entity_types: list[EntityType | str] | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Entity]:
        """
        Search for entities by semantic similarity.

        Args:
            query: Search query
            entity_types: Optional filter by entity types
            limit: Maximum results
            threshold: Minimum similarity threshold

        Returns:
            List of matching entities
        """
        if self._embedder is None:
            return []

        query_embedding = await self._embedder.embed(query)

        results = await self._client.execute_read(
            queries.SEARCH_ENTITIES_BY_EMBEDDING,
            {
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
            },
        )

        # Normalize filter types
        filter_types: set[str] | None = None
        if entity_types:
            filter_types = {normalize_entity_type(t) for t in entity_types}

        entities = []
        for row in results:
            entity_data = dict(row["e"])
            entity_type = entity_data["type"]

            # Filter by type if specified
            if filter_types and entity_type not in filter_types:
                continue

            entity = self._parse_entity(entity_data)
            entity.metadata["similarity"] = row["score"]
            entities.append(entity)

        return entities

    async def search_preferences(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Preference]:
        """
        Search for preferences.

        Args:
            query: Search query
            category: Optional filter by category
            limit: Maximum results
            threshold: Minimum similarity threshold

        Returns:
            List of matching preferences
        """
        if self._embedder is None:
            # Fall back to category-based search
            if category:
                results = await self._client.execute_read(
                    queries.SEARCH_PREFERENCES_BY_CATEGORY,
                    {"category": category, "limit": limit},
                )
                return [self._parse_preference(dict(r["p"])) for r in results]
            return []

        query_embedding = await self._embedder.embed(query)

        results = await self._client.execute_read(
            queries.SEARCH_PREFERENCES_BY_EMBEDDING,
            {
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
            },
        )

        preferences = []
        for row in results:
            pref_data = dict(row["p"])

            # Filter by category if specified
            if category and pref_data.get("category") != category:
                continue

            pref = self._parse_preference(pref_data)
            pref.metadata["similarity"] = row["score"]
            preferences.append(pref)

        return preferences

    async def get_related_entities(
        self,
        entity: Entity | UUID,
        *,
        relationship_types: list[str] | None = None,
        depth: int = 1,
    ) -> list[tuple[Entity, Relationship]]:
        """
        Get entities related to a given entity.

        Args:
            entity: Entity or ID to find relations for
            relationship_types: Optional filter by relationship types
            depth: Traversal depth

        Returns:
            List of (entity, relationship) tuples
        """
        entity_id = entity.id if isinstance(entity, Entity) else entity

        results = await self._client.execute_read(
            queries.GET_ENTITY_RELATIONSHIPS,
            {"entity_id": str(entity_id)},
        )

        related = []
        for row in results:
            # Neo4j relationship object has different access pattern
            rel = row["r"]
            # Get relationship properties - use dict() if available, else access via _properties
            if hasattr(rel, "_properties"):
                rel_data = dict(rel._properties)
            elif hasattr(rel, "items"):
                rel_data = dict(rel)
            else:
                # Fallback: create empty dict and get individual properties
                rel_data = {}
                for key in ["id", "type", "confidence", "description", "valid_from", "valid_until"]:
                    try:
                        val = rel.get(key) if hasattr(rel, "get") else getattr(rel, key, None)
                        if val is not None:
                            rel_data[key] = val
                    except Exception:
                        pass

            other_data = dict(row["other"])

            # Filter by relationship type
            rel_type = rel_data.get("type") or (rel.type if hasattr(rel, "type") else "RELATED_TO")
            if relationship_types and rel_type not in relationship_types:
                continue

            other_entity = self._parse_entity(other_data)

            relationship = Relationship(
                id=UUID(rel_data.get("id", str(uuid4()))),
                source_id=entity_id,
                target_id=other_entity.id,
                type=rel_type,
                confidence=rel_data.get("confidence", 1.0),
            )

            related.append((other_entity, relationship))

        return related

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """
        Get long-term context for LLM prompts.

        Args:
            query: Query to find relevant context
            include_entities: Whether to include entities
            include_preferences: Whether to include preferences
            max_items: Maximum items per category

        Returns:
            Formatted context string
        """
        include_entities = kwargs.get("include_entities", True)
        include_preferences = kwargs.get("include_preferences", True)
        max_items = kwargs.get("max_items", 10)

        parts = []

        # Get relevant preferences
        if include_preferences:
            preferences = await self.search_preferences(query, limit=max_items)
            if preferences:
                parts.append("### User Preferences")
                for pref in preferences:
                    line = f"- [{pref.category}] {pref.preference}"
                    if pref.context:
                        line += f" (context: {pref.context})"
                    parts.append(line)

        # Get relevant entities
        if include_entities:
            entities = await self.search_entities(query, limit=max_items)
            if entities:
                parts.append("\n### Relevant Entities")
                for entity in entities:
                    type_str = entity.full_type
                    line = f"- {entity.display_name} ({type_str})"
                    if entity.description:
                        line += f": {entity.description}"
                    parts.append(line)

        return "\n".join(parts)

    async def _get_existing_entity_names(self, entity_type: str) -> list[str]:
        """Get names of existing entities of a given type."""
        results = await self._client.execute_read(
            queries.SEARCH_ENTITIES_BY_TYPE,
            {"type": entity_type, "limit": 1000},
        )
        names = []
        for row in results:
            entity_data = dict(row["e"])
            names.append(entity_data["name"])
            if entity_data.get("canonical_name"):
                names.append(entity_data["canonical_name"])
        return list(set(names))

    # =========================================================================
    # Entity Deduplication Methods
    # =========================================================================

    async def _check_for_duplicates(
        self,
        name: str,
        entity_type: str,
        embedding: list[float],
    ) -> DeduplicationResult:
        """Check if an entity is a potential duplicate of existing entities.

        Args:
            name: Entity name
            entity_type: Entity type
            embedding: Entity embedding vector

        Returns:
            DeduplicationResult indicating what action to take
        """
        config = self._deduplication

        # Search for similar entities by embedding
        results = await self._client.execute_read(
            queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING,
            {
                "embedding": embedding,
                "limit": config.max_candidates,
                "threshold": config.flag_threshold,
                "type": entity_type if config.match_same_type_only else None,
            },
        )

        if not results:
            return DeduplicationResult()

        # Find the best match
        best_match = None
        best_score = 0.0
        match_type = "embedding"

        for row in results:
            entity_data = dict(row["e"])
            score = row["score"]

            # Skip if this is a merged entity
            if entity_data.get("merged_into"):
                continue

            # Check fuzzy matching if enabled
            fuzzy_score = None
            if config.use_fuzzy_matching:
                try:
                    from rapidfuzz import fuzz

                    # Check against name and canonical name
                    name_score = fuzz.ratio(name.lower(), entity_data["name"].lower()) / 100
                    canonical_name = entity_data.get("canonical_name") or entity_data["name"]
                    canonical_score = fuzz.ratio(name.lower(), canonical_name.lower()) / 100
                    fuzzy_score = max(name_score, canonical_score)

                    # Combine embedding and fuzzy scores
                    if fuzzy_score >= config.fuzzy_threshold:
                        # Boost score if both match
                        combined_score = (score + fuzzy_score) / 2
                        if combined_score > best_score:
                            best_score = combined_score
                            best_match = entity_data
                            match_type = "both"
                        continue
                except ImportError:
                    pass

            if score > best_score:
                best_score = score
                best_match = entity_data
                match_type = "embedding"

        if best_match is None:
            return DeduplicationResult()

        # Determine action based on score
        if best_score >= config.auto_merge_threshold:
            return DeduplicationResult(
                is_duplicate=True,
                action="merged",
                matched_entity_id=UUID(best_match["id"]),
                matched_entity_name=best_match["name"],
                similarity_score=best_score,
                match_type=match_type,
            )
        elif best_score >= config.flag_threshold:
            return DeduplicationResult(
                is_duplicate=True,
                action="flagged",
                matched_entity_id=UUID(best_match["id"]),
                matched_entity_name=best_match["name"],
                similarity_score=best_score,
                match_type=match_type,
            )

        return DeduplicationResult()

    async def _get_entity_by_id(self, entity_id: UUID) -> Entity | None:
        """Get an entity by its ID.

        Args:
            entity_id: Entity UUID

        Returns:
            Entity if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_ENTITY,
            {"id": str(entity_id)},
        )

        if not results:
            return None

        return self._parse_entity(dict(results[0]["e"]))

    async def _add_alias_to_entity(self, entity_id: UUID, alias: str) -> None:
        """Add an alias to an existing entity.

        Args:
            entity_id: Entity UUID
            alias: Alias to add
        """
        # Get current entity
        entity = await self._get_entity_by_id(entity_id)
        if entity is None:
            return

        # Update aliases in metadata
        current_aliases = entity.aliases or []
        if alias not in current_aliases:
            current_aliases.append(alias)

        # Update in database
        storage_metadata = {**entity.metadata}
        storage_metadata["aliases"] = current_aliases
        if entity.attributes:
            storage_metadata["attributes"] = entity.attributes

        await self._client.execute_write(
            """
            MATCH (e:Entity {id: $id})
            SET e.metadata = $metadata
            RETURN e
            """,
            {
                "id": str(entity_id),
                "metadata": _serialize_metadata(storage_metadata),
            },
        )

    async def find_potential_duplicates(
        self,
        *,
        limit: int = 100,
    ) -> list[tuple[Entity, Entity, float]]:
        """Find entities that are flagged as potential duplicates.

        Returns pairs of entities with SAME_AS relationships in 'pending' status.

        Args:
            limit: Maximum number of duplicate pairs to return

        Returns:
            List of (entity1, entity2, confidence) tuples
        """
        results = await self._client.execute_read(
            queries.GET_POTENTIAL_DUPLICATES,
            {"limit": limit},
        )

        duplicates = []
        for row in results:
            entity1 = self._parse_entity(dict(row["e1"]))
            entity2 = self._parse_entity(dict(row["e2"]))

            # Get relationship properties
            rel = row["r"]
            if hasattr(rel, "_properties"):
                rel_data = dict(rel._properties)
            elif hasattr(rel, "items"):
                rel_data = dict(rel)
            else:
                rel_data = {}

            confidence = rel_data.get("confidence", 0.0)
            duplicates.append((entity1, entity2, confidence))

        return duplicates

    async def merge_duplicate_entities(
        self,
        source_id: UUID,
        target_id: UUID,
    ) -> tuple[Entity, Entity] | None:
        """Merge two entities, keeping the target and marking source as merged.

        The source entity's name is added as an alias to the target.
        Any relationships pointing to the source are transferred to the target.

        Args:
            source_id: ID of entity to merge from (will be marked as merged)
            target_id: ID of entity to merge into (will be kept)

        Returns:
            Tuple of (source, target) entities after merge, or None if not found
        """
        results = await self._client.execute_write(
            queries.MERGE_ENTITIES,
            {
                "source_id": str(source_id),
                "target_id": str(target_id),
            },
        )

        if not results:
            return None

        source = self._parse_entity(dict(results[0]["source"]))
        target = self._parse_entity(dict(results[0]["target"]))

        return source, target

    async def review_duplicate(
        self,
        source_id: UUID,
        target_id: UUID,
        *,
        confirm: bool,
    ) -> bool:
        """Review a potential duplicate pair.

        Args:
            source_id: ID of first entity
            target_id: ID of second entity
            confirm: True to confirm as duplicate (merge), False to reject

        Returns:
            True if review was processed successfully
        """
        if confirm:
            # Merge the entities
            result = await self.merge_duplicate_entities(source_id, target_id)
            if result:
                # Update SAME_AS relationship status
                await self._client.execute_write(
                    queries.UPDATE_SAME_AS_STATUS,
                    {
                        "source_id": str(source_id),
                        "target_id": str(target_id),
                        "status": "confirmed",
                    },
                )
                return True
        else:
            # Mark as rejected (not a duplicate)
            await self._client.execute_write(
                queries.UPDATE_SAME_AS_STATUS,
                {
                    "source_id": str(source_id),
                    "target_id": str(target_id),
                    "status": "rejected",
                },
            )
            return True

        return False

    async def get_same_as_cluster(
        self,
        entity_id: UUID,
    ) -> list[Entity]:
        """Get all entities in the same SAME_AS cluster as the given entity.

        Args:
            entity_id: Entity ID to find cluster for

        Returns:
            List of entities in the same cluster (including the input entity)
        """
        results = await self._client.execute_read(
            queries.GET_SAME_AS_CLUSTER,
            {"entity_id": str(entity_id)},
        )

        # Start with the original entity
        original = await self._get_entity_by_id(entity_id)
        entities = [original] if original else []

        for row in results:
            entity = self._parse_entity(dict(row["entity"]))
            entities.append(entity)

        return entities

    async def get_deduplication_stats(self) -> DeduplicationStats:
        """Get statistics about entity deduplication.

        Returns:
            DeduplicationStats with counts
        """
        results = await self._client.execute_read(
            queries.GET_DEDUPLICATION_STATS,
            {},
        )

        if not results:
            return DeduplicationStats()

        row = results[0]
        return DeduplicationStats(
            total_entities=row["total_entities"],
            merged_entities=row["merged_entities"],
            same_as_relationships=row["same_as_relationships"],
            pending_reviews=row["pending_reviews"],
        )

    # =========================================================================
    # Provenance Tracking Methods
    # =========================================================================

    async def register_extractor(
        self,
        name: str,
        *,
        version: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register an extractor for provenance tracking.

        Creates or updates an Extractor node in the database.

        Args:
            name: Unique extractor name (e.g., "GLiNEREntityExtractor", "SpacyNER")
            version: Optional version string
            config: Optional configuration dict (will be JSON serialized)

        Returns:
            Dict with extractor details
        """
        results = await self._client.execute_write(
            queries.CREATE_EXTRACTOR,
            {
                "id": str(uuid4()),
                "name": name,
                "version": version,
                "config": _serialize_metadata(config) if config else None,
            },
        )

        if results and "ex" in results[0]:
            node = results[0]["ex"]
            return {
                "id": node.get("id"),
                "name": node.get("name"),
                "version": node.get("version"),
            }

        return {"name": name, "version": version}

    async def link_entity_to_message(
        self,
        entity: Entity | UUID,
        message_id: UUID | str,
        *,
        confidence: float = 1.0,
        start_pos: int | None = None,
        end_pos: int | None = None,
        context: str | None = None,
    ) -> bool:
        """Link an entity to its source message (EXTRACTED_FROM relationship).

        Creates a provenance link showing which message the entity was extracted from.

        Args:
            entity: Entity or entity ID
            message_id: ID of the source message
            confidence: Extraction confidence score
            start_pos: Start character position in the message
            end_pos: End character position in the message
            context: Surrounding text context

        Returns:
            True if link was created successfully
        """
        entity_id = entity.id if isinstance(entity, Entity) else entity

        results = await self._client.execute_write(
            queries.CREATE_EXTRACTED_FROM_RELATIONSHIP,
            {
                "entity_id": str(entity_id),
                "message_id": str(message_id),
                "confidence": confidence,
                "start_pos": start_pos,
                "end_pos": end_pos,
                "context": context,
            },
        )

        return bool(results)

    async def link_entity_to_extractor(
        self,
        entity: Entity | UUID,
        extractor_name: str,
        *,
        confidence: float = 1.0,
        extraction_time_ms: float | None = None,
    ) -> bool:
        """Link an entity to its extractor (EXTRACTED_BY relationship).

        Creates a provenance link showing which extractor created the entity.

        Args:
            entity: Entity or entity ID
            extractor_name: Name of the extractor
            confidence: Extraction confidence score
            extraction_time_ms: Time taken for extraction in milliseconds

        Returns:
            True if link was created successfully
        """
        entity_id = entity.id if isinstance(entity, Entity) else entity

        # Ensure extractor exists
        await self.register_extractor(extractor_name)

        results = await self._client.execute_write(
            queries.CREATE_EXTRACTED_BY_RELATIONSHIP,
            {
                "entity_id": str(entity_id),
                "extractor_name": extractor_name,
                "confidence": confidence,
                "extraction_time_ms": extraction_time_ms,
            },
        )

        return bool(results)

    async def get_entity_provenance(
        self,
        entity: Entity | UUID,
    ) -> dict[str, Any]:
        """Get provenance information for an entity.

        Returns information about where the entity was extracted from
        and which extractor(s) produced it.

        Args:
            entity: Entity or entity ID

        Returns:
            Dict with 'sources' (messages) and 'extractors' lists
        """
        entity_id = entity.id if isinstance(entity, Entity) else entity

        results = await self._client.execute_read(
            queries.GET_ENTITY_PROVENANCE,
            {"entity_id": str(entity_id)},
        )

        if not results:
            return {"sources": [], "extractors": []}

        row = results[0]

        # Parse sources
        sources = []
        for item in row.get("sources", []):
            if item.get("message"):
                msg = item["message"]
                rel = item.get("relationship", {})
                # Handle relationship properties
                if hasattr(rel, "_properties"):
                    rel_data = dict(rel._properties)
                elif hasattr(rel, "items"):
                    rel_data = dict(rel)
                else:
                    rel_data = {}

                sources.append(
                    {
                        "message_id": msg.get("id"),
                        "content": msg.get("content"),
                        "confidence": rel_data.get("confidence"),
                        "start_pos": rel_data.get("start_pos"),
                        "end_pos": rel_data.get("end_pos"),
                        "context": rel_data.get("context"),
                    }
                )

        # Parse extractors
        extractors = []
        for item in row.get("extractors", []):
            if item.get("extractor"):
                ex = item["extractor"]
                rel = item.get("relationship", {})
                if hasattr(rel, "_properties"):
                    rel_data = dict(rel._properties)
                elif hasattr(rel, "items"):
                    rel_data = dict(rel)
                else:
                    rel_data = {}

                extractors.append(
                    {
                        "name": ex.get("name"),
                        "version": ex.get("version"),
                        "confidence": rel_data.get("confidence"),
                        "extraction_time_ms": rel_data.get("extraction_time_ms"),
                    }
                )

        return {
            "sources": sources,
            "extractors": extractors,
        }

    async def get_entities_from_message(
        self,
        message_id: UUID | str,
    ) -> list[tuple[Entity, dict[str, Any]]]:
        """Get all entities extracted from a message.

        Args:
            message_id: ID of the source message

        Returns:
            List of (entity, extraction_info) tuples, ordered by position
        """
        results = await self._client.execute_read(
            queries.GET_ENTITIES_FROM_MESSAGE,
            {"message_id": str(message_id)},
        )

        entities = []
        for row in results:
            entity = self._parse_entity(dict(row["e"]))
            rel = row.get("r", {})
            if hasattr(rel, "_properties"):
                rel_data = dict(rel._properties)
            elif hasattr(rel, "items"):
                rel_data = dict(rel)
            else:
                rel_data = {}

            extraction_info = {
                "confidence": rel_data.get("confidence"),
                "start_pos": rel_data.get("start_pos"),
                "end_pos": rel_data.get("end_pos"),
                "context": rel_data.get("context"),
            }
            entities.append((entity, extraction_info))

        return entities

    async def get_entities_by_extractor(
        self,
        extractor_name: str,
        *,
        limit: int = 100,
    ) -> list[tuple[Entity, dict[str, Any]]]:
        """Get all entities extracted by a specific extractor.

        Args:
            extractor_name: Name of the extractor
            limit: Maximum number of entities to return

        Returns:
            List of (entity, extraction_info) tuples
        """
        results = await self._client.execute_read(
            queries.GET_ENTITIES_BY_EXTRACTOR,
            {"extractor_name": extractor_name, "limit": limit},
        )

        entities = []
        for row in results:
            entity = self._parse_entity(dict(row["e"]))
            rel = row.get("r", {})
            if hasattr(rel, "_properties"):
                rel_data = dict(rel._properties)
            elif hasattr(rel, "items"):
                rel_data = dict(rel)
            else:
                rel_data = {}

            extraction_info = {
                "confidence": rel_data.get("confidence"),
                "extraction_time_ms": rel_data.get("extraction_time_ms"),
            }
            entities.append((entity, extraction_info))

        return entities

    async def list_extractors(self) -> list[dict[str, Any]]:
        """List all registered extractors with entity counts.

        Returns:
            List of extractor info dicts with name, version, entity_count
        """
        results = await self._client.execute_read(
            queries.LIST_EXTRACTORS,
            {},
        )

        extractors = []
        for row in results:
            ex = row.get("ex")
            if ex:
                extractors.append(
                    {
                        "name": ex.get("name"),
                        "version": ex.get("version"),
                        "entity_count": row.get("entity_count", 0),
                    }
                )

        return extractors

    async def get_extraction_stats(self) -> dict[str, Any]:
        """Get overall extraction statistics.

        Returns:
            Dict with total_entities, source_messages, extractors
        """
        results = await self._client.execute_read(
            queries.GET_EXTRACTION_STATS,
            {},
        )

        if not results:
            return {
                "total_entities": 0,
                "source_messages": 0,
                "extractors": [],
            }

        row = results[0]
        return {
            "total_entities": row.get("total_entities", 0),
            "source_messages": row.get("source_messages", 0),
            "extractors": row.get("extractors", []),
        }

    async def get_extractor_stats(self) -> list[dict[str, Any]]:
        """Get per-extractor statistics.

        Returns:
            List of dicts with name, version, entity_count, avg_confidence
        """
        results = await self._client.execute_read(
            queries.GET_EXTRACTOR_STATS,
            {},
        )

        return [
            {
                "name": row.get("name"),
                "version": row.get("version"),
                "entity_count": row.get("entity_count", 0),
                "avg_confidence": row.get("avg_confidence"),
            }
            for row in results
        ]

    async def delete_entity_provenance(
        self,
        entity: Entity | UUID,
    ) -> int:
        """Delete all provenance links for an entity.

        Args:
            entity: Entity or entity ID

        Returns:
            Number of relationships deleted
        """
        entity_id = entity.id if isinstance(entity, Entity) else entity

        results = await self._client.execute_write(
            queries.DELETE_ENTITY_PROVENANCE,
            {"entity_id": str(entity_id)},
        )

        return results[0].get("deleted", 0) if results else 0

    async def get_preferences_by_category(
        self,
        category: str,
        *,
        limit: int = 100,
    ) -> list[Preference]:
        """
        Get all preferences in a category.

        Args:
            category: The preference category
            limit: Maximum results

        Returns:
            List of preferences in the category
        """
        results = await self._client.execute_read(
            queries.SEARCH_PREFERENCES_BY_CATEGORY,
            {"category": category, "limit": limit},
        )
        return [self._parse_preference(dict(r["p"])) for r in results]

    async def get_facts_about(
        self,
        subject: str,
        *,
        limit: int = 100,
    ) -> list[Fact]:
        """
        Get all facts about a subject.

        Args:
            subject: The fact subject
            limit: Maximum results

        Returns:
            List of facts about the subject
        """
        results = await self._client.execute_read(
            queries.GET_FACTS_BY_SUBJECT,
            {"subject": subject, "limit": limit},
        )
        return [self._parse_fact(dict(r["f"])) for r in results]

    async def search_facts(
        self,
        query: str,
        *,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[Fact]:
        """
        Search for facts by semantic similarity.

        Args:
            query: Search query
            limit: Maximum results
            threshold: Minimum similarity threshold

        Returns:
            List of matching facts
        """
        if self._embedder is None:
            return []

        query_embedding = await self._embedder.embed(query)

        results = await self._client.execute_read(
            queries.SEARCH_FACTS_BY_EMBEDDING,
            {
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
            },
        )

        facts = []
        for row in results:
            fact = self._parse_fact(dict(row["f"]))
            fact.metadata["similarity"] = row["score"]
            facts.append(fact)

        return facts

    async def get_entity_relationships(
        self,
        entity_name: str,
    ) -> list[tuple[Entity, Relationship]]:
        """
        Get relationships for an entity by name.

        Args:
            entity_name: Name of the entity

        Returns:
            List of (related_entity, relationship) tuples
        """
        # First get the entity
        entity = await self.get_entity_by_name(entity_name)
        if entity is None:
            return []

        return await self.get_related_entities(entity)

    def _parse_entity(self, data: dict[str, Any]) -> Entity:
        """Parse entity from database result."""
        metadata = _deserialize_metadata(data.get("metadata"))
        attributes = metadata.pop("attributes", {})
        aliases = metadata.pop("aliases", [])

        return Entity(
            id=UUID(data["id"]),
            name=data["name"],
            canonical_name=data.get("canonical_name"),
            type=data["type"],
            subtype=data.get("subtype"),
            description=data.get("description"),
            embedding=data.get("embedding"),
            confidence=data.get("confidence", 1.0),
            aliases=aliases,
            attributes=attributes,
            created_at=_to_python_datetime(data.get("created_at")),
            metadata=metadata,
        )

    def _parse_preference(self, data: dict[str, Any]) -> Preference:
        """Parse preference from database result."""
        return Preference(
            id=UUID(data["id"]),
            category=data["category"],
            preference=data["preference"],
            context=data.get("context"),
            confidence=data.get("confidence", 1.0),
            embedding=data.get("embedding"),
            created_at=_to_python_datetime(data.get("created_at")),
            metadata=_deserialize_metadata(data.get("metadata")),
        )

    def _parse_fact(self, data: dict[str, Any]) -> Fact:
        """Parse fact from database result."""
        return Fact(
            id=UUID(data["id"]),
            subject=data["subject"],
            predicate=data["predicate"],
            object=data["object"],
            confidence=data.get("confidence", 1.0),
            embedding=data.get("embedding"),
            valid_from=_to_python_datetime(data.get("valid_from"))
            if data.get("valid_from")
            else None,
            valid_until=_to_python_datetime(data.get("valid_until"))
            if data.get("valid_until")
            else None,
            created_at=_to_python_datetime(data.get("created_at")),
            metadata=_deserialize_metadata(data.get("metadata")),
        )

    # =========================================================================
    # Geospatial Methods
    # =========================================================================

    async def geocode_locations(
        self,
        *,
        batch_size: int = 50,
        skip_existing: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """
        Geocode all Location entities that don't have coordinates.

        This is useful for batch processing existing Location entities
        that were created without geocoding enabled.

        Args:
            batch_size: Number of locations to process per batch
            skip_existing: Skip locations that already have coordinates
            on_progress: Progress callback (processed_count, total_count)

        Returns:
            Dict with 'processed' and 'geocoded' counts
        """
        if self._geocoder is None:
            return {"processed": 0, "geocoded": 0}

        # Get locations without coordinates
        results = await self._client.execute_read(
            queries.GET_LOCATIONS_WITHOUT_COORDINATES,
            {},
        )

        if not results:
            return {"processed": 0, "geocoded": 0}

        total = len(results)
        processed = 0
        geocoded = 0

        # Process in batches
        for i in range(0, total, batch_size):
            batch = results[i : i + batch_size]

            for row in batch:
                entity_id = row["id"]
                name = row["name"]

                # Geocode the location
                geocode_result = await self._geocoder.geocode(name)

                if geocode_result is not None:
                    # Update the entity with coordinates
                    await self._client.execute_write(
                        queries.UPDATE_ENTITY_LOCATION,
                        {
                            "id": entity_id,
                            "latitude": geocode_result.latitude,
                            "longitude": geocode_result.longitude,
                        },
                    )
                    geocoded += 1

                processed += 1

            # Report progress after each batch
            if on_progress:
                on_progress(processed, total)

        return {"processed": processed, "geocoded": geocoded}

    async def search_locations_near(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_km: float = 10.0,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[Entity]:
        """
        Find Location entities within a radius of a point.

        Args:
            latitude: Latitude of the center point
            longitude: Longitude of the center point
            radius_km: Search radius in kilometers (default 10km)
            session_id: Optional session ID to filter locations by conversation
            limit: Maximum number of results

        Returns:
            List of Location entities sorted by distance, with distance_meters in metadata
        """
        # Convert km to meters for Neo4j query
        radius_meters = radius_km * 1000

        if session_id:
            # Filter to locations mentioned in the specific conversation
            query = """
                MATCH (e:Entity {type: 'LOCATION'})-[:EXTRACTED_FROM]->(m:Message)<-[:HAS_MESSAGE]-(c:Conversation {session_id: $session_id})
                WITH DISTINCT e
                WHERE e.location IS NOT NULL
                WITH e, point.distance(
                    point({latitude: $latitude, longitude: $longitude}),
                    point({latitude: e.location.latitude, longitude: e.location.longitude})
                ) AS distance_meters
                WHERE distance_meters <= $radius_meters
                RETURN e, distance_meters
                ORDER BY distance_meters ASC
                LIMIT $limit
            """
        else:
            query = queries.SEARCH_LOCATIONS_NEAR

        results = await self._client.execute_read(
            query,
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius_meters": radius_meters,
                "session_id": session_id,
                "limit": limit,
            },
        )

        entities = []
        for row in results:
            entity = self._parse_entity(dict(row["e"]))
            entity.metadata["distance_meters"] = row["distance_meters"]
            entity.metadata["distance_km"] = row["distance_meters"] / 1000
            entities.append(entity)

        return entities

    async def search_locations_in_bounding_box(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[Entity]:
        """
        Find Location entities within a bounding box.

        Args:
            min_lat: Minimum latitude (south)
            min_lon: Minimum longitude (west)
            max_lat: Maximum latitude (north)
            max_lon: Maximum longitude (east)
            session_id: Optional session ID to filter locations by conversation
            limit: Maximum number of results

        Returns:
            List of Location entities within the bounding box
        """
        if session_id:
            # Filter to locations mentioned in the specific conversation
            query = """
                MATCH (e:Entity {type: 'LOCATION'})-[:EXTRACTED_FROM]->(m:Message)<-[:HAS_MESSAGE]-(c:Conversation {session_id: $session_id})
                WITH DISTINCT e
                WHERE e.location IS NOT NULL
                  AND e.location.latitude >= $min_lat
                  AND e.location.latitude <= $max_lat
                  AND e.location.longitude >= $min_lon
                  AND e.location.longitude <= $max_lon
                RETURN e
                LIMIT $limit
            """
        else:
            query = queries.SEARCH_LOCATIONS_IN_BOUNDING_BOX

        results = await self._client.execute_read(
            query,
            {
                "min_lat": min_lat,
                "min_lon": min_lon,
                "max_lat": max_lat,
                "max_lon": max_lon,
                "session_id": session_id,
                "limit": limit,
            },
        )

        return [self._parse_entity(dict(row["e"])) for row in results]

    async def get_location_coordinates(
        self,
        entity_id: UUID | str,
    ) -> tuple[float, float] | None:
        """
        Get coordinates for a Location entity.

        Args:
            entity_id: Entity ID

        Returns:
            (latitude, longitude) tuple or None if not geocoded
        """
        if isinstance(entity_id, UUID):
            entity_id = str(entity_id)

        results = await self._client.execute_read(
            queries.GET_LOCATION_COORDINATES,
            {"id": entity_id},
        )

        if not results:
            return None

        row = results[0]
        return (row["latitude"], row["longitude"])
