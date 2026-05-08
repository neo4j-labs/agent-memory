"""Neo4j schema management for indexes and constraints."""

import re
from typing import TYPE_CHECKING

from neo4j_agent_memory.core.exceptions import SchemaError
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.schema.models import (
    AdoptionLabelReport,
    AdoptionReport,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


# A Cypher identifier we are willing to interpolate into a label position.
# Matches names like ``Person``, ``Person_v2``, ``Client``. We deliberately
# disallow backticks so the caller cannot break out of the quoted label.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# Default vector dimensions
DEFAULT_VECTOR_DIMENSIONS = 1536


class SchemaManager:
    """
    Manages Neo4j schema for agent memory.

    Handles creation of indexes and constraints for all memory types.
    """

    def __init__(
        self,
        client: "Neo4jClient",
        *,
        vector_dimensions: int = DEFAULT_VECTOR_DIMENSIONS,
    ):
        """
        Initialize schema manager.

        Args:
            client: Neo4j client
            vector_dimensions: Dimensions for vector indexes
        """
        self._client = client
        self._vector_dimensions = vector_dimensions

    async def setup_all(self) -> None:
        """Set up all indexes and constraints."""
        await self.setup_constraints()
        await self.setup_indexes()
        await self.setup_vector_indexes()
        await self.setup_point_indexes()

    async def setup_constraints(self) -> None:
        """Create unique constraints for all node types."""
        constraints = [
            # Short-term memory
            ("conversation_id", "Conversation", "id"),
            ("message_id", "Message", "id"),
            # Long-term memory
            ("entity_id", "Entity", "id"),
            ("preference_id", "Preference", "id"),
            ("fact_id", "Fact", "id"),
            # Reasoning memory
            ("reasoning_trace_id", "ReasoningTrace", "id"),
            ("reasoning_step_id", "ReasoningStep", "id"),
            ("tool_name", "Tool", "name"),
            ("tool_call_id", "ToolCall", "id"),
            # Multi-tenant (v0.4)
            ("user_identifier", "User", "identifier"),
            # Hygiene + privacy (v0.5)
            ("consolidation_run_id", "ConsolidationRun", "id"),
            ("memory_read_audit_id", "MemoryReadAudit", "id"),
        ]

        for constraint_name, label, property_name in constraints:
            await self._create_constraint(constraint_name, label, property_name)

    async def setup_indexes(self) -> None:
        """Create regular indexes for common queries."""
        indexes = [
            # Short-term memory
            ("conversation_session_idx", "Conversation", "session_id"),
            ("message_timestamp_idx", "Message", "timestamp"),
            ("message_role_idx", "Message", "role"),
            # Long-term memory
            ("entity_type_idx", "Entity", "type"),
            ("entity_name_idx", "Entity", "name"),
            ("entity_canonical_idx", "Entity", "canonical_name"),
            ("preference_category_idx", "Preference", "category"),
            # Reasoning memory
            ("trace_session_idx", "ReasoningTrace", "session_id"),
            ("trace_success_idx", "ReasoningTrace", "success"),
            ("trace_error_kind_idx", "ReasoningTrace", "error_kind"),
            ("tool_call_status_idx", "ToolCall", "status"),
            # Hygiene (v0.5)
            ("conversation_archived_idx", "Conversation", "archived"),
            ("consolidation_run_kind_idx", "ConsolidationRun", "kind"),
            ("memory_read_audit_kind_idx", "MemoryReadAudit", "kind"),
        ]

        for index_name, label, property_name in indexes:
            await self._create_index(index_name, label, property_name)

    async def setup_vector_indexes(self) -> None:
        """Create vector indexes for semantic search."""
        vector_indexes = [
            ("message_embedding_idx", "Message", "embedding"),
            ("entity_embedding_idx", "Entity", "embedding"),
            ("preference_embedding_idx", "Preference", "embedding"),
            ("fact_embedding_idx", "Fact", "embedding"),
            ("task_embedding_idx", "ReasoningTrace", "task_embedding"),
            ("step_embedding_idx", "ReasoningStep", "embedding"),
        ]

        for index_name, label, property_name in vector_indexes:
            await self._create_vector_index(index_name, label, property_name)

    async def setup_point_indexes(self) -> None:
        """Create point indexes for geospatial queries."""
        point_indexes = [
            # Location entities have a 'location' Point property for coordinates
            ("entity_location_idx", "Entity", "location"),
        ]

        for index_name, label, property_name in point_indexes:
            await self._create_point_index(index_name, label, property_name)

    async def _create_constraint(
        self,
        constraint_name: str,
        label: str,
        property_name: str,
    ) -> None:
        """Create a unique constraint if it doesn't exist."""
        try:
            exists = await self._client.check_constraint_exists(constraint_name)
            if exists:
                return

            query = queries.create_constraint_query(constraint_name, label, property_name)
            await self._client.execute_write(query)
        except Exception as e:
            raise SchemaError(f"Failed to create constraint {constraint_name}: {e}") from e

    async def _create_index(
        self,
        index_name: str,
        label: str,
        property_name: str,
    ) -> None:
        """Create a regular index if it doesn't exist."""
        try:
            exists = await self._client.check_index_exists(index_name)
            if exists:
                return

            query = queries.create_index_query(index_name, label, property_name)
            await self._client.execute_write(query)
        except Exception as e:
            raise SchemaError(f"Failed to create index {index_name}: {e}") from e

    async def _create_vector_index(
        self,
        index_name: str,
        label: str,
        property_name: str,
    ) -> None:
        """Create a vector index if it doesn't exist."""
        try:
            exists = await self._client.check_index_exists(index_name)
            if exists:
                return

            query = queries.create_vector_index_query(
                index_name, label, property_name, self._vector_dimensions
            )
            await self._client.execute_write(query)
        except Exception:
            # Vector indexes require Neo4j 5.11+, log warning but don't fail
            # as the package can still work without vector search
            pass

    async def _create_point_index(
        self,
        index_name: str,
        label: str,
        property_name: str,
    ) -> None:
        """Create a point index for geospatial queries if it doesn't exist."""
        try:
            exists = await self._client.check_index_exists(index_name)
            if exists:
                return

            query = queries.create_point_index_query(index_name, label, property_name)
            await self._client.execute_write(query)
        except Exception:
            # Point indexes require Neo4j 5.0+, log warning but don't fail
            pass

    async def drop_all(self) -> None:
        """Drop all memory-related indexes and constraints."""
        # Get all constraints
        constraints = await self._client.execute_read(queries.SHOW_CONSTRAINTS)
        for constraint in constraints:
            name = constraint["name"]
            if self._is_memory_schema(name):
                await self._client.execute_write(queries.drop_constraint_query(name))

        # Get all indexes
        indexes = await self._client.execute_read(queries.SHOW_INDEXES)
        for index in indexes:
            name = index["name"]
            if self._is_memory_schema(name):
                await self._client.execute_write(queries.drop_index_query(name))

    def _is_memory_schema(self, name: str) -> bool:
        """Check if a schema element belongs to agent memory."""
        memory_prefixes = [
            "conversation_",
            "message_",
            "entity_",
            "preference_",
            "fact_",
            "reasoning_",
            "trace_",
            "tool_",
            "task_",
            "step_",
            "user_",
            "consolidation_",
            "memory_read_",
        ]
        return any(name.startswith(prefix) for prefix in memory_prefixes)

    async def adopt_existing_graph(
        self,
        label_to_type: dict[str, str],
        *,
        name_property_per_label: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> AdoptionReport:
        """Adopt an existing domain graph as long-term memory entities.

        For each input label, attaches the ``:Entity`` super-label and the
        library's required ``id`` / ``type`` / ``name`` properties to nodes
        already present in the database. After adoption, library APIs that
        MERGE on ``(:Entity {name, type})`` (e.g. message-mention extraction,
        relation writes) link to the existing nodes instead of creating
        duplicates.

        The operation is idempotent — re-running on already-adopted nodes
        is a no-op. Nodes that lack the configured name property are
        skipped and reported.

        Args:
            label_to_type: Map from existing Neo4j label to the library
                entity type to assign, e.g.
                ``{"Person": "PERSON", "Movie": "OBJECT"}``.
            name_property_per_label: Optional per-label override for the
                property to use as the entity name. Defaults to ``"name"``
                for every label not in the map.
            dry_run: When True, do not mutate the graph — return a report
                describing what *would* happen.

        Returns:
            An :class:`AdoptionReport` with per-label counts.

        Raises:
            SchemaError: If a label or name property contains characters
                that cannot be safely interpolated as a Cypher identifier.
        """
        name_property_per_label = name_property_per_label or {}
        per_label: list[AdoptionLabelReport] = []

        for label, entity_type in label_to_type.items():
            name_property = name_property_per_label.get(label, "name")

            if not _SAFE_IDENTIFIER.match(label):
                raise SchemaError(
                    f"Refusing to adopt label {label!r}: must match [A-Za-z_][A-Za-z0-9_]*"
                )
            if not _SAFE_IDENTIFIER.match(name_property):
                raise SchemaError(
                    f"Refusing to use name property {name_property!r} for "
                    f"label {label!r}: must match [A-Za-z_][A-Za-z0-9_]*"
                )

            already_rows = await self._client.execute_read(
                queries.count_already_adopted_query(label)
            )
            already_adopted = int(already_rows[0]["already_adopted_count"]) if already_rows else 0

            skipped_rows = await self._client.execute_read(
                queries.count_adoption_skipped_query(label, name_property)
            )
            skipped = int(skipped_rows[0]["skipped_count"]) if skipped_rows else 0

            if dry_run:
                # Count what *would* migrate without mutating.
                projected_rows = await self._client.execute_read(
                    f"""
                    MATCH (n:`{label}`)
                    WHERE NOT n:Entity AND n.`{name_property}` IS NOT NULL
                    RETURN count(n) AS migrated_count
                    """
                )
                migrated = int(projected_rows[0]["migrated_count"]) if projected_rows else 0
            else:
                rows = await self._client.execute_write(
                    queries.adopt_label_to_entity_query(label, name_property),
                    {"label_lc": label.lower(), "type": entity_type},
                )
                # ``execute_write`` returns the records produced by the
                # query — it's a list of dicts here.
                migrated = int(rows[0]["migrated_count"]) if rows else 0

            per_label.append(
                AdoptionLabelReport(
                    label=label,
                    type=entity_type,
                    name_property=name_property,
                    migrated_count=migrated,
                    already_adopted_count=already_adopted,
                    skipped_count=skipped,
                )
            )

        return AdoptionReport(by_label=per_label, dry_run=dry_run)

    async def get_schema_info(self) -> dict:
        """Get information about the current schema."""
        constraints = await self._client.execute_read(queries.SHOW_CONSTRAINTS_DETAIL)
        indexes = await self._client.execute_read(queries.SHOW_INDEXES_DETAIL)

        return {
            "constraints": [
                {
                    "name": c["name"],
                    "type": c["type"],
                    "labels": c["labelsOrTypes"],
                    "properties": c["properties"],
                }
                for c in constraints
                if self._is_memory_schema(c["name"])
            ],
            "indexes": [
                {
                    "name": i["name"],
                    "type": i["type"],
                    "labels": i["labelsOrTypes"],
                    "properties": i["properties"],
                }
                for i in indexes
                if self._is_memory_schema(i["name"])
            ],
        }
