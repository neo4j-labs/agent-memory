"""Async Neo4j client wrapper."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from neo4j import (
    AsyncDriver,
    AsyncGraphDatabase,
    AsyncManagedTransaction,
    AsyncSession,
    unit_of_work,
)
from neo4j.exceptions import AuthError, ServiceUnavailable

from neo4j_agent_memory.config.settings import Neo4jConfig
from neo4j_agent_memory.core.exceptions import ConnectionError


class Neo4jClient:
    """
    Async Neo4j client wrapper for memory operations.

    Provides connection management and query execution with proper
    error handling and resource cleanup.
    """

    def __init__(self, config: Neo4jConfig):
        """
        Initialize Neo4j client.

        Args:
            config: Neo4j connection configuration
        """
        self._config = config
        self._driver: AsyncDriver | None = None
        try:
            self._package_version = version("neo4j-agent-memory")
        except PackageNotFoundError:
            self._package_version = "0.0.0"

    async def connect(self) -> None:
        """
        Establish connection to Neo4j.

        Raises:
            ConnectionError: If connection fails
        """
        if self._driver is not None:
            return

        try:
            self._driver = AsyncGraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password.get_secret_value()),
                max_connection_pool_size=self._config.max_connection_pool_size,
                connection_timeout=self._config.connection_timeout,
                max_transaction_retry_time=self._config.max_transaction_retry_time,
                max_connection_lifetime=self._config.max_connection_lifetime,
                liveness_check_timeout=self._config.liveness_check_timeout,
                keep_alive=self._config.keep_alive,
            )
            # Verify connectivity
            await self._driver.verify_connectivity()
        except AuthError as e:
            raise ConnectionError(f"Authentication failed: {e}") from e
        except ServiceUnavailable as e:
            raise ConnectionError(f"Neo4j service unavailable: {e}") from e
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Neo4j: {e}") from e

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def __aenter__(self) -> Neo4jClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._driver is not None

    def _ensure_connected(self) -> AsyncDriver:
        """Ensure client is connected and return driver."""
        if self._driver is None:
            raise ConnectionError("Not connected to Neo4j. Call connect() first.")
        return self._driver

    def _get_session(self) -> AsyncSession:
        """Get a new session."""
        driver = self._ensure_connected()
        return driver.session(database=self._config.database)

    async def execute_read(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a read query.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dictionaries
        """
        async with self._get_session() as session:

            @unit_of_work(metadata={"app": f"neo4j-agent-memory_v{self._package_version}"})
            async def execute_read_tx(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
                result = await tx.run(query, parameters or {})  # ty: ignore[invalid-argument-type]  # neo4j stubs type the query as LiteralString; ours is a runtime str (pre-built Cypher, never raw user input)
                data = await result.data()
                return data

            records = await session.execute_read(execute_read_tx)
            return records

    async def execute_write(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a write query.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dictionaries
        """
        async with self._get_session() as session:

            @unit_of_work(metadata={"app": f"neo4j-agent-memory_v{self._package_version}"})
            async def execute_write_tx(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
                result = await tx.run(query, parameters or {})  # ty: ignore[invalid-argument-type]  # neo4j stubs type the query as LiteralString; ours is a runtime str (pre-built Cypher, never raw user input)
                data = await result.data()
                return data

            records = await session.execute_write(execute_write_tx)
            return records

    async def vector_search(
        self,
        index_name: str,
        query_embedding: list[float],
        *,
        limit: int = 10,
        threshold: float = 0.0,
        return_properties: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Perform vector similarity search.

        Args:
            index_name: Name of the vector index
            query_embedding: Query embedding vector
            limit: Maximum number of results
            threshold: Minimum similarity threshold
            return_properties: Properties to return (None for all)

        Returns:
            List of matching nodes with similarity scores
        """
        # Build return clause
        if return_properties:
            return_clause = ", ".join([f"node.{p} AS {p}" for p in return_properties])
        else:
            return_clause = "node"

        query = f"""
        CALL db.index.vector.queryNodes($index_name, $limit, $embedding)
        YIELD node, score
        WHERE score >= $threshold
        RETURN {return_clause}, score
        ORDER BY score DESC
        """

        return await self.execute_read(
            query,
            {
                "index_name": index_name,
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
            },
        )

    async def get_node_by_id(
        self,
        label: str,
        node_id: str,
        id_property: str = "id",
    ) -> dict[str, Any] | None:
        """
        Get a node by its ID.

        Args:
            label: Node label
            node_id: Node ID value
            id_property: Name of the ID property

        Returns:
            Node properties or None if not found
        """
        query = f"""
        MATCH (n:{label} {{{id_property}: $id}})
        RETURN n
        """
        results = await self.execute_read(query, {"id": node_id})
        if results:
            return dict(results[0]["n"])
        return None

    async def delete_node_by_id(
        self,
        label: str,
        node_id: str,
        id_property: str = "id",
    ) -> bool:
        """
        Delete a node by its ID.

        Args:
            label: Node label
            node_id: Node ID value
            id_property: Name of the ID property

        Returns:
            True if node was deleted
        """
        query = f"""
        MATCH (n:{label} {{{id_property}: $id}})
        DETACH DELETE n
        RETURN count(n) AS deleted
        """
        results = await self.execute_write(query, {"id": node_id})
        return results[0]["deleted"] > 0 if results else False

    async def check_index_exists(self, index_name: str) -> bool:
        """Check if an index exists."""
        query = "SHOW INDEXES YIELD name WHERE name = $name RETURN count(*) AS count"
        results = await self.execute_read(query, {"name": index_name})
        return results[0]["count"] > 0 if results else False

    async def check_constraint_exists(self, constraint_name: str) -> bool:
        """Check if a constraint exists."""
        query = "SHOW CONSTRAINTS YIELD name WHERE name = $name RETURN count(*) AS count"
        results = await self.execute_read(query, {"name": constraint_name})
        return results[0]["count"] > 0 if results else False

    async def get_database_info(self) -> dict[str, Any]:
        """Get database information."""
        query = "CALL dbms.components() YIELD name, versions, edition RETURN *"
        results = await self.execute_read(query)
        if results:
            return results[0]
        return {}
