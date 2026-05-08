"""Entity schema models for knowledge graph configuration.

This module provides the POLE+O (Person, Object, Location, Event, Organization)
data model as the default schema, with support for custom schemas.

The POLE model is commonly used in law enforcement and intelligence analysis:
- Person: Individuals involved in events or associated with objects/locations
- Object: Physical or digital items (vehicles, phones, documents, etc.)
- Location: Geographical areas, addresses, or specific places
- Event: Incidents that connect entities across time and place
- Organization: Companies, non-profits, government agencies, groups

This implementation extends POLE with Organizations as a first-class entity type.
"""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class POLEOEntityType(str, Enum):
    """POLE+O entity types for knowledge graph.

    This is the default entity type system based on the POLE model
    extended with Organizations.
    """

    PERSON = "PERSON"
    OBJECT = "OBJECT"
    LOCATION = "LOCATION"
    EVENT = "EVENT"
    ORGANIZATION = "ORGANIZATION"


class SchemaModel(str, Enum):
    """Available schema models."""

    POLEO = "poleo"  # Person, Object, Location, Event, Organization
    LEGACY = "legacy"  # Original EntityType enum for backward compatibility
    CUSTOM = "custom"  # User-defined schema


# Legacy entity types for backward compatibility
LEGACY_ENTITY_TYPES = [
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "EVENT",
    "CONCEPT",
    "EMOTION",
    "PREFERENCE",
    "FACT",
]


# Mapping from legacy types to POLE+O types
LEGACY_TO_POLEO_MAPPING = {
    "PERSON": "PERSON",
    "ORGANIZATION": "ORGANIZATION",
    "LOCATION": "LOCATION",
    "EVENT": "EVENT",
    "CONCEPT": "OBJECT",  # Concepts map to Objects
    "EMOTION": "OBJECT",  # Emotions map to Objects
    "PREFERENCE": "OBJECT",  # Preferences map to Objects
    "FACT": "OBJECT",  # Facts map to Objects
}


class EntityTypeConfig(BaseModel):
    """Configuration for a single entity type."""

    name: str = Field(description="Entity type name (e.g., PERSON)")
    description: str | None = Field(default=None, description="Description of the entity type")
    subtypes: list[str] = Field(default_factory=list, description="Valid subtypes for this entity")
    attributes: list[str] = Field(
        default_factory=list, description="Common attributes for this entity type"
    )
    color: str | None = Field(default=None, description="Color for visualization (hex)")


class RelationTypeConfig(BaseModel):
    """Configuration for a relationship type."""

    name: str = Field(description="Relationship type name")
    description: str | None = Field(default=None)
    source_types: list[str] = Field(default_factory=list, description="Valid source entity types")
    target_types: list[str] = Field(default_factory=list, description="Valid target entity types")
    properties: list[str] = Field(
        default_factory=list, description="Properties on this relationship"
    )


class User(BaseModel):
    """First-class user identity for multi-tenant deployments.

    Pre-0.4 every consumer of the library reinvented user scoping.
    ``client.users.upsert_user(...)`` makes user identity a first-class
    concept; pass ``user_identifier=`` to short-term, long-term, and
    reasoning APIs to scope writes and reads by user.
    """

    id: str = Field(description="UUID for the User node (auto-generated)")
    identifier: str = Field(
        description=(
            "Stable identifier for the user (commonly an email address). "
            "Unique constraint enforced at the database level."
        ),
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary per-user attributes (name, role, etc.)",
    )


class EntityRef(BaseModel):
    """A lightweight reference to an entity, used by reasoning APIs.

    Used as the input shape for ``record_tool_call(touched_entities=[...])``,
    ``add_message(explicit_mentions=[...])``, ``add_preference(applies_to=[...])``,
    and the structured ``related_entities`` field on :class:`TraceOutcome`.

    Identity precedence (most specific wins): ``id`` > ``name + type``.
    """

    label: str | None = Field(
        default=None,
        description=(
            "Optional source domain label (e.g. 'Client'). Used when adopting "
            "an existing graph and the caller wants to disambiguate by label."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Entity display name (matches Entity.name)",
    )
    type: str | None = Field(
        default=None,
        description="POLE+O or custom entity type (matches Entity.type)",
    )
    id: str | None = Field(
        default=None,
        description=(
            "Optional explicit entity id. When set, the reference identifies "
            "exactly one entity regardless of name/type."
        ),
    )

    @model_validator(mode="after")
    def _require_name_or_id(self) -> "EntityRef":
        if not self.name and not self.id:
            raise ValueError("EntityRef requires at least one of 'name' or 'id'")
        return self


class TraceOutcome(BaseModel):
    """Structured outcome attached to a reasoning trace at completion time.

    Pre-0.3 callers passed an outcome ``str`` and a boolean ``success`` to
    ``complete_trace``. ``TraceOutcome`` lets a caller attach an indexable
    error kind, structured related entities, and metrics — while remaining
    fully back-compatible with the legacy string + boolean API.
    """

    success: bool = Field(description="Whether the task succeeded")
    summary: str = Field(description="Human-readable outcome summary")
    error_kind: str | None = Field(
        default=None,
        description=(
            "Indexed error category, e.g. 'timeout', 'no_results', "
            "'user_aborted'. Stored on the ReasoningTrace for fast filtering."
        ),
    )
    related_entities: list[EntityRef] = Field(
        default_factory=list,
        description="Entities the trace touched (for case-based retrieval)",
    )
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description=("Arbitrary numeric metrics keyed by name (e.g. 'latency_ms', 'tools_called')"),
    )


class ConsolidationCandidate(BaseModel):
    """A single candidate action identified by a consolidation primitive.

    Used by :class:`ConsolidationReport.candidates` to describe what
    *would* happen in a dry-run, or what *did* happen in a real run.
    """

    kind: str = Field(
        description=(
            "What action this represents. Examples: 'dedupe_entity_pair', "
            "'summarize_trace', 'supersede_preference'."
        ),
    )
    description: str = Field(description="Human-readable summary")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific details (entity IDs, trace ID, etc.)",
    )
    action_taken: bool = Field(
        default=False,
        description=(
            "True if the consolidation actually mutated the graph, "
            "False if dry_run was set or the candidate was below threshold."
        ),
    )


class ConsolidationReport(BaseModel):
    """Result of a consolidation primitive.

    Returned by ``client.consolidation.dedupe_entities()``,
    ``summarize_long_traces()``, etc. When ``dry_run`` is True the
    report describes candidates only — no writes happen. When False,
    the report records what was mutated and a corresponding
    ``:ConsolidationRun`` audit node is created in Neo4j.
    """

    kind: str = Field(description="Consolidation kind, e.g. 'dedupe_entities'")
    dry_run: bool = Field(
        default=True,
        description="True if no mutations occurred",
    )
    candidates: list[ConsolidationCandidate] = Field(
        default_factory=list,
        description="Per-candidate breakdown",
    )
    run_id: str | None = Field(
        default=None,
        description=("ID of the ``:ConsolidationRun`` audit node, set when ``dry_run=False``."),
    )

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def actions_taken(self) -> int:
        return sum(1 for c in self.candidates if c.action_taken)


class AdoptionLabelReport(BaseModel):
    """Per-label result of :meth:`SchemaManager.adopt_existing_graph`.

    Reports how many nodes of a given label were newly adopted as
    ``:Entity``, how many were already adopted, and how many were skipped
    because they lack the configured name property.
    """

    label: str = Field(description="Source domain label, e.g. 'Person'")
    type: str = Field(description="Library entity type assigned, e.g. 'PERSON'")
    name_property: str = Field(description="Property used as the entity name (defaults to 'name')")
    migrated_count: int = Field(default=0, description="Number of nodes adopted in this run")
    already_adopted_count: int = Field(
        default=0, description="Number of nodes that already carried :Entity"
    )
    skipped_count: int = Field(
        default=0,
        description=(
            "Number of nodes that couldn't be adopted because the configured name property is null"
        ),
    )


class AdoptionReport(BaseModel):
    """Result of :meth:`SchemaManager.adopt_existing_graph`.

    Aggregates per-label results plus a summary suitable for printing
    after a migration run.
    """

    by_label: list[AdoptionLabelReport] = Field(
        default_factory=list,
        description="Per-label adoption results, one per input label",
    )
    dry_run: bool = Field(
        default=False,
        description="True if no mutations were performed (counts are projected)",
    )

    @property
    def total_migrated(self) -> int:
        """Total nodes adopted in this run across all labels."""
        return sum(r.migrated_count for r in self.by_label)

    @property
    def total_already_adopted(self) -> int:
        """Total nodes already adopted (no-op portion of an idempotent run)."""
        return sum(r.already_adopted_count for r in self.by_label)

    @property
    def total_skipped(self) -> int:
        """Total nodes that couldn't be adopted (missing name property)."""
        return sum(r.skipped_count for r in self.by_label)


class EntitySchemaConfig(BaseModel):
    """Configuration for the knowledge graph entity schema.

    This defines what entity types are valid, their subtypes,
    and how they can be related.
    """

    # Schema identification
    name: str = Field(default="poleo", description="Schema name")
    version: str = Field(default="1.0", description="Schema version")
    description: str | None = Field(
        default="POLE+O entity schema for knowledge graphs", description="Schema description"
    )

    # Entity types
    entity_types: list[EntityTypeConfig] = Field(
        default_factory=lambda: _get_poleo_entity_types(),
        description="List of valid entity types with their configurations",
    )

    # Relationship types
    relation_types: list[RelationTypeConfig] = Field(
        default_factory=lambda: _get_poleo_relation_types(),
        description="List of valid relationship types",
    )

    # Defaults
    default_entity_type: str = Field(
        default="OBJECT", description="Default type when entity type cannot be determined"
    )

    # Features
    enable_subtypes: bool = Field(default=True, description="Whether to track entity subtypes")
    strict_types: bool = Field(default=False, description="Whether to reject unknown entity types")

    def get_entity_type_names(self) -> list[str]:
        """Get list of valid entity type names."""
        return [et.name for et in self.entity_types]

    def get_subtypes(self, entity_type: str) -> list[str]:
        """Get valid subtypes for an entity type."""
        for et in self.entity_types:
            if et.name.upper() == entity_type.upper():
                return et.subtypes
        return []

    def is_valid_type(self, entity_type: str) -> bool:
        """Check if an entity type is valid in this schema."""
        if not self.strict_types:
            return True
        return entity_type.upper() in [et.name.upper() for et in self.entity_types]

    def normalize_type(self, entity_type: str) -> str:
        """Normalize an entity type to schema standard."""
        type_upper = entity_type.upper()
        for et in self.entity_types:
            if et.name.upper() == type_upper:
                return et.name
        # Return as-is if not found (and not strict)
        return type_upper if not self.strict_types else self.default_entity_type

    def get_relation_types(self) -> list[str]:
        """Get list of valid relationship type names."""
        return [rt.name for rt in self.relation_types]


def _get_poleo_entity_types() -> list[EntityTypeConfig]:
    """Get default POLE+O entity type configurations."""
    return [
        EntityTypeConfig(
            name="PERSON",
            description="Individuals involved in events or associated with objects/locations",
            subtypes=["INDIVIDUAL", "ALIAS", "PERSONA", "SUSPECT", "WITNESS", "VICTIM"],
            attributes=["name", "aliases", "date_of_birth", "nationality", "occupation"],
            color="#4CAF50",
        ),
        EntityTypeConfig(
            name="OBJECT",
            description="Physical or digital items such as vehicles, phones, documents",
            subtypes=[
                "VEHICLE",
                "PHONE",
                "EMAIL",
                "DOCUMENT",
                "DEVICE",
                "WEAPON",
                "MONEY",
                "DRUG",
                "EVIDENCE",
                "SOFTWARE",
            ],
            attributes=["identifier", "make", "model", "serial_number", "description"],
            color="#2196F3",
        ),
        EntityTypeConfig(
            name="LOCATION",
            description="Geographical areas, addresses, or specific places",
            subtypes=["ADDRESS", "CITY", "REGION", "COUNTRY", "LANDMARK", "COORDINATES"],
            attributes=["address", "city", "country", "coordinates", "type"],
            color="#FF9800",
        ),
        EntityTypeConfig(
            name="EVENT",
            description="Incidents that connect entities across time and place",
            subtypes=[
                "INCIDENT",
                "MEETING",
                "TRANSACTION",
                "COMMUNICATION",
                "CRIME",
                "TRAVEL",
                "EMPLOYMENT",
                "OBSERVATION",
            ],
            attributes=["date", "time", "duration", "description", "outcome"],
            color="#9C27B0",
        ),
        EntityTypeConfig(
            name="ORGANIZATION",
            description="Companies, non-profits, government agencies, criminal groups",
            subtypes=[
                "COMPANY",
                "NONPROFIT",
                "GOVERNMENT",
                "EDUCATIONAL",
                "CRIMINAL",
                "POLITICAL",
                "RELIGIOUS",
                "MILITARY",
            ],
            attributes=["name", "type", "jurisdiction", "registration_number"],
            color="#F44336",
        ),
    ]


def _get_poleo_relation_types() -> list[RelationTypeConfig]:
    """Get default POLE+O relationship type configurations."""
    return [
        # Person relationships
        RelationTypeConfig(
            name="KNOWS",
            description="Personal relationship between people",
            source_types=["PERSON"],
            target_types=["PERSON"],
        ),
        RelationTypeConfig(
            name="ALIAS_OF",
            description="Alternative identity of a person",
            source_types=["PERSON"],
            target_types=["PERSON"],
        ),
        RelationTypeConfig(
            name="MEMBER_OF",
            description="Person is member of organization",
            source_types=["PERSON"],
            target_types=["ORGANIZATION"],
            properties=["role", "start_date", "end_date"],
        ),
        RelationTypeConfig(
            name="EMPLOYED_BY",
            description="Person employed by organization",
            source_types=["PERSON"],
            target_types=["ORGANIZATION"],
            properties=["position", "start_date", "end_date"],
        ),
        # Object relationships
        RelationTypeConfig(
            name="OWNS",
            description="Ownership of an object",
            source_types=["PERSON", "ORGANIZATION"],
            target_types=["OBJECT"],
            properties=["acquisition_date", "status"],
        ),
        RelationTypeConfig(
            name="USES",
            description="Usage of an object",
            source_types=["PERSON"],
            target_types=["OBJECT"],
        ),
        # Location relationships
        RelationTypeConfig(
            name="LOCATED_AT",
            description="Entity is located at a place",
            source_types=["PERSON", "OBJECT", "ORGANIZATION", "EVENT"],
            target_types=["LOCATION"],
            properties=["from_date", "to_date", "status"],
        ),
        RelationTypeConfig(
            name="RESIDES_AT",
            description="Person resides at location",
            source_types=["PERSON"],
            target_types=["LOCATION"],
            properties=["from_date", "to_date"],
        ),
        RelationTypeConfig(
            name="HEADQUARTERS_AT",
            description="Organization headquarters location",
            source_types=["ORGANIZATION"],
            target_types=["LOCATION"],
        ),
        # Event relationships
        RelationTypeConfig(
            name="PARTICIPATED_IN",
            description="Entity participated in an event",
            source_types=["PERSON", "ORGANIZATION"],
            target_types=["EVENT"],
            properties=["role"],
        ),
        RelationTypeConfig(
            name="OCCURRED_AT",
            description="Event occurred at location",
            source_types=["EVENT"],
            target_types=["LOCATION"],
        ),
        RelationTypeConfig(
            name="INVOLVED",
            description="Object involved in event",
            source_types=["EVENT"],
            target_types=["OBJECT"],
            properties=["role"],
        ),
        # Organization relationships
        RelationTypeConfig(
            name="SUBSIDIARY_OF",
            description="Organization is subsidiary of another",
            source_types=["ORGANIZATION"],
            target_types=["ORGANIZATION"],
        ),
        RelationTypeConfig(
            name="PARTNER_WITH",
            description="Organizations have partnership",
            source_types=["ORGANIZATION"],
            target_types=["ORGANIZATION"],
        ),
        # Generic relationships
        RelationTypeConfig(
            name="RELATED_TO",
            description="Generic relationship between entities",
            source_types=["PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"],
            target_types=["PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"],
            properties=["type", "description", "confidence"],
        ),
        RelationTypeConfig(
            name="MENTIONS",
            description="Entity mentions another entity",
            source_types=["PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"],
            target_types=["PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"],
        ),
    ]


def get_default_schema() -> EntitySchemaConfig:
    """Get the default POLE+O schema configuration."""
    return EntitySchemaConfig()


def get_legacy_schema() -> EntitySchemaConfig:
    """Get the legacy schema for backward compatibility."""
    return EntitySchemaConfig(
        name="legacy",
        version="1.0",
        description="Legacy entity schema for backward compatibility",
        entity_types=[
            EntityTypeConfig(name="PERSON", subtypes=[]),
            EntityTypeConfig(name="ORGANIZATION", subtypes=[]),
            EntityTypeConfig(name="LOCATION", subtypes=[]),
            EntityTypeConfig(name="EVENT", subtypes=[]),
            EntityTypeConfig(name="CONCEPT", subtypes=[]),
            EntityTypeConfig(name="EMOTION", subtypes=[]),
            EntityTypeConfig(name="PREFERENCE", subtypes=[]),
            EntityTypeConfig(name="FACT", subtypes=[]),
        ],
        default_entity_type="CONCEPT",
        enable_subtypes=False,
    )


def load_schema_from_file(path: str | Path) -> EntitySchemaConfig:
    """Load a custom schema from a JSON or YAML file.

    Args:
        path: Path to schema definition file (.json or .yaml/.yml)

    Returns:
        EntitySchemaConfig loaded from file

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is not supported
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".json":
        import json

        with open(path) as f:
            data = json.load(f)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml

            with open(path) as f:
                data = yaml.safe_load(f)
        except ImportError:
            raise ValueError(
                "PyYAML is required to load YAML schema files. Install with: pip install pyyaml"
            )
    else:
        raise ValueError(f"Unsupported schema file format: {suffix}. Use .json or .yaml")

    return EntitySchemaConfig(**data)


def create_schema_for_types(
    entity_types: list[str],
    enable_subtypes: bool = False,
) -> EntitySchemaConfig:
    """Create a simple schema with just the specified entity types.

    Args:
        entity_types: List of entity type names
        enable_subtypes: Whether to enable subtypes

    Returns:
        EntitySchemaConfig with the specified types
    """
    return EntitySchemaConfig(
        name="custom",
        version="1.0",
        description="Custom entity schema",
        entity_types=[EntityTypeConfig(name=t.upper(), subtypes=[]) for t in entity_types],
        default_entity_type=entity_types[0].upper() if entity_types else "OBJECT",
        enable_subtypes=enable_subtypes,
    )
