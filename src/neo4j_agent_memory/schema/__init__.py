"""Schema configuration for knowledge graph entity models."""

from neo4j_agent_memory.schema.models import (
    AdoptionLabelReport,
    AdoptionReport,
    ConsolidationCandidate,
    ConsolidationReport,
    EntityRef,
    EntitySchemaConfig,
    EntityTypeConfig,
    POLEOEntityType,
    RelationTypeConfig,
    SchemaModel,
    TraceOutcome,
    User,
    create_schema_for_types,
    get_default_schema,
    get_legacy_schema,
    load_schema_from_file,
)
from neo4j_agent_memory.schema.persistence import (
    SchemaListItem,
    SchemaManager,
    StoredSchema,
)

__all__ = [
    # Models
    "AdoptionLabelReport",
    "AdoptionReport",
    "ConsolidationCandidate",
    "ConsolidationReport",
    "EntityRef",
    "EntitySchemaConfig",
    "EntityTypeConfig",
    "POLEOEntityType",
    "RelationTypeConfig",
    "SchemaModel",
    "TraceOutcome",
    "User",
    # Factory functions
    "create_schema_for_types",
    "get_default_schema",
    "get_legacy_schema",
    "load_schema_from_file",
    # Persistence
    "SchemaListItem",
    "SchemaManager",
    "StoredSchema",
]
