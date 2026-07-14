"""Schema persistence for storing and loading schemas from Neo4j.

This module provides the SchemaManager class for persisting EntitySchemaConfig
objects to Neo4j, enabling:
- No code changes required for new domains
- Schema versioning and audit trails
- Multi-tenant schema management
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.schema.models import EntitySchemaConfig

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


@dataclass
class StoredSchema:
    """Represents a schema stored in Neo4j."""

    id: UUID
    name: str
    version: str
    description: str | None
    config: EntitySchemaConfig
    is_active: bool
    created_at: datetime
    created_by: str | None

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> StoredSchema:
        """Create a StoredSchema from a Neo4j node."""
        config_data = json.loads(node["config"])
        return cls(
            id=UUID(node["id"]) if isinstance(node["id"], str) else node["id"],
            name=node["name"],
            version=node["version"],
            description=node.get("description"),
            config=EntitySchemaConfig(**config_data),
            is_active=node.get("is_active", True),
            created_at=node.get("created_at", datetime.now()),
            created_by=node.get("created_by"),
        )


@dataclass
class SchemaListItem:
    """Summary of a schema for listing."""

    name: str
    latest_version: str
    description: str | None
    version_count: int
    is_active: bool


class SchemaManager:
    """Manages schema persistence in Neo4j.

    Provides methods to save, load, list, and manage entity schemas
    stored in Neo4j, with support for versioning.

    Example:
        ```python
        from neo4j_agent_memory.graph.client import Neo4jClient
        from neo4j_agent_memory.config.settings import Neo4jConfig
        from neo4j_agent_memory.schema import EntitySchemaConfig, SchemaManager

        config = Neo4jConfig(uri="bolt://localhost:7687", password="password")
        client = Neo4jClient(config)

        async with client:
            manager = SchemaManager(client)

            # Create and save a custom schema
            schema = EntitySchemaConfig(
                name="medical",
                version="1.0",
                description="Medical records schema",
                entity_types=[...],
            )
            stored = await manager.save_schema(schema)

            # Load schema by name (gets latest active version)
            loaded = await manager.load_schema("medical")

            # List all schemas
            schemas = await manager.list_schemas()
        ```
    """

    def __init__(self, client: Neo4jClient):
        """Initialize the schema manager.

        Args:
            client: Connected Neo4jClient instance
        """
        self._client = client

    async def save_schema(
        self,
        schema: EntitySchemaConfig,
        *,
        created_by: str | None = None,
        set_active: bool = True,
    ) -> StoredSchema:
        """Save a schema to Neo4j.

        If a schema with the same name already exists, this creates a new
        version. If set_active is True (default), the new version becomes
        the active version.

        Args:
            schema: The EntitySchemaConfig to save
            created_by: Optional identifier for who created this schema
            set_active: Whether to set this as the active version (default True)

        Returns:
            StoredSchema with the saved schema details
        """
        schema_id = str(uuid4())

        # Serialize the schema config to JSON
        config_json = schema.model_dump_json()

        # Deactivate other versions if setting this as active
        if set_active:
            await self._client.execute_write(
                queries.DEACTIVATE_SCHEMA_VERSIONS,
                {"name": schema.name},
            )

        # Create the new schema node
        results = await self._client.execute_write(
            queries.CREATE_SCHEMA,
            {
                "id": schema_id,
                "name": schema.name,
                "version": schema.version,
                "description": schema.description,
                "config": config_json,
                "is_active": set_active,
                "created_by": created_by,
            },
        )

        if results and "s" in results[0]:
            return StoredSchema.from_node(results[0]["s"])

        # Fallback: construct from input
        return StoredSchema(
            id=UUID(schema_id),
            name=schema.name,
            version=schema.version,
            description=schema.description,
            config=schema,
            is_active=set_active,
            created_at=datetime.now(),
            created_by=created_by,
        )

    async def load_schema(self, name: str) -> EntitySchemaConfig | None:
        """Load the active schema by name.

        Args:
            name: Schema name to load

        Returns:
            EntitySchemaConfig if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_NAME,
            {"name": name},
        )

        if results and "s" in results[0]:
            stored = StoredSchema.from_node(results[0]["s"])
            return stored.config

        return None

    async def load_schema_version(self, name: str, version: str) -> EntitySchemaConfig | None:
        """Load a specific version of a schema.

        Args:
            name: Schema name
            version: Schema version to load

        Returns:
            EntitySchemaConfig if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_NAME_VERSION,
            {"name": name, "version": version},
        )

        if results and "s" in results[0]:
            stored = StoredSchema.from_node(results[0]["s"])
            return stored.config

        return None

    async def get_stored_schema(self, name: str) -> StoredSchema | None:
        """Get full stored schema details by name.

        Args:
            name: Schema name

        Returns:
            StoredSchema with full details if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_NAME,
            {"name": name},
        )

        if results and "s" in results[0]:
            return StoredSchema.from_node(results[0]["s"])

        return None

    async def get_stored_schema_by_id(self, schema_id: UUID | str) -> StoredSchema | None:
        """Get stored schema by ID.

        Args:
            schema_id: Schema UUID

        Returns:
            StoredSchema if found, None otherwise
        """
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_ID,
            {"id": str(schema_id)},
        )

        if results and "s" in results[0]:
            return StoredSchema.from_node(results[0]["s"])

        return None

    async def list_schemas(self, name: str | None = None) -> list[SchemaListItem]:
        """List all schemas or filter by name.

        Args:
            name: Optional schema name to filter by

        Returns:
            List of SchemaListItem with summary info
        """
        results = await self._client.execute_read(
            queries.LIST_SCHEMAS,
            {"name": name},
        )

        items = []
        for record in results:
            latest_node = record.get("latest")
            if latest_node:
                items.append(
                    SchemaListItem(
                        name=record["name"],
                        latest_version=latest_node.get("version", "unknown"),
                        description=latest_node.get("description"),
                        version_count=record.get("version_count", 1),
                        is_active=latest_node.get("is_active", True),
                    )
                )

        return items

    async def list_schema_versions(self, name: str) -> list[StoredSchema]:
        """List all versions of a schema.

        Args:
            name: Schema name

        Returns:
            List of StoredSchema for all versions, newest first
        """
        results = await self._client.execute_read(
            queries.LIST_SCHEMA_VERSIONS,
            {"name": name},
        )

        return [StoredSchema.from_node(record["s"]) for record in results if "s" in record]

    async def set_active_version(self, name: str, version: str) -> StoredSchema | None:
        """Set a specific version as the active schema.

        Args:
            name: Schema name
            version: Version to set as active

        Returns:
            The activated StoredSchema, or None if not found
        """
        # First find the schema to activate
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_NAME_VERSION,
            {"name": name, "version": version},
        )

        if not results or "s" not in results[0]:
            return None

        schema_id = results[0]["s"]["id"]

        # Update active status
        results = await self._client.execute_write(
            queries.UPDATE_SCHEMA_ACTIVE,
            {"name": name, "id": schema_id},
        )

        if results and "active" in results[0]:
            return StoredSchema.from_node(results[0]["active"])

        return None

    async def delete_schema(self, schema_id: UUID | str) -> bool:
        """Delete a specific schema version by ID.

        Args:
            schema_id: Schema UUID to delete

        Returns:
            True if deleted, False if not found
        """
        results = await self._client.execute_write(
            queries.DELETE_SCHEMA,
            {"id": str(schema_id)},
        )

        if results and results[0] is not None:
            return bool(results[0].get("deleted", False))
        return False

    async def delete_all_versions(self, name: str) -> int:
        """Delete all versions of a schema.

        Args:
            name: Schema name to delete

        Returns:
            Number of versions deleted
        """
        results = await self._client.execute_write(
            queries.DELETE_SCHEMA_BY_NAME,
            {"name": name},
        )

        return results[0].get("deleted_count", 0) if results else 0

    async def schema_exists(self, name: str) -> bool:
        """Check if a schema exists.

        Args:
            name: Schema name to check

        Returns:
            True if schema exists
        """
        results = await self._client.execute_read(
            queries.GET_SCHEMA_BY_NAME,
            {"name": name},
        )

        return bool(results and "s" in results[0])

    async def ensure_schema_index(self) -> None:
        """Create index on Schema nodes for efficient lookups."""
        # Create index on name for fast lookups
        await self._client.execute_write(queries.CREATE_SCHEMA_NAME_INDEX, {})

        # Create index on id for direct lookups
        await self._client.execute_write(queries.CREATE_SCHEMA_ID_INDEX, {})
