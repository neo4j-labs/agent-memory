"""Graph Data Science (GDS) integration for Microsoft Agent Framework.

Provides graph algorithms for enhanced context ranking and entity analysis.
Supports fallback to basic Cypher when GDS library is not installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import AsyncSession

    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)


class GDSAlgorithm(str, Enum):
    """Available GDS algorithms."""

    PAGERANK = "pagerank"
    COMMUNITY_DETECTION = "louvain"
    SHORTEST_PATH = "shortest_path"
    NODE_SIMILARITY = "node_similarity"
    BETWEENNESS = "betweenness"
    CLOSENESS = "closeness"


@dataclass
class GDSConfig:
    """
    Configuration for GDS algorithm integration.

    Controls which algorithms are enabled, whether they're exposed as
    agent tools, and fallback behavior when GDS is not installed.

    Example:
        config = GDSConfig(
            enabled=True,
            use_pagerank_for_ranking=True,
            expose_as_tools=[GDSAlgorithm.SHORTEST_PATH, GDSAlgorithm.NODE_SIMILARITY],
            fallback_to_basic=True,
        )
    """

    # Master enable/disable
    enabled: bool = False

    # Automatic ranking settings
    use_pagerank_for_ranking: bool = True
    pagerank_weight: float = 0.3  # Weight in combined relevance score
    pagerank_damping_factor: float = 0.85

    # Community detection for context grouping
    use_community_grouping: bool = False

    # Which algorithms to expose as agent tools
    expose_as_tools: list[GDSAlgorithm] = field(default_factory=list)

    # Fallback behavior when GDS not installed
    fallback_to_basic: bool = True

    # Warn when falling back (only once per session)
    warn_on_fallback: bool = True


class GDSIntegration:
    """
    Neo4j Graph Data Science integration for context enhancement.

    Provides graph algorithms for:
    - PageRank: Rank entities by importance/centrality
    - Community Detection: Group related entities together
    - Shortest Path: Find connections between entities
    - Node Similarity: Find similar entities based on relationships

    When the GDS library is not installed in Neo4j, this class provides
    fallback implementations using basic Cypher queries with a warning.

    Example:
        from neo4j_agent_memory.integrations.microsoft_agent import GDSIntegration, GDSConfig

        config = GDSConfig(enabled=True, fallback_to_basic=True)
        gds = GDSIntegration(memory_client, config)

        # Check GDS availability
        if await gds.is_gds_available():
            print("GDS library is installed")
        else:
            print("Using fallback Cypher queries")

        # Get PageRank scores
        scores = await gds.get_pagerank_scores(entity_ids)

        # Find shortest path
        path = await gds.find_shortest_path("Alice", "Bob")
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        config: GDSConfig | None = None,
    ):
        """
        Initialize GDS integration.

        Args:
            memory_client: Connected MemoryClient instance.
            config: GDS configuration options.
        """
        self._client = memory_client
        self._config = config or GDSConfig()
        self._gds_available: bool | None = None
        self._fallback_warned: bool = False

    def _session(self) -> AsyncSession:
        """Return a raw Neo4j session, narrowing the client's connected state."""
        neo4j_client = self._client._client
        if neo4j_client is None:
            raise RuntimeError("MemoryClient is not connected; call connect() first.")
        return neo4j_client.session()

    @property
    def config(self) -> GDSConfig:
        """Get the GDS configuration."""
        return self._config

    async def is_gds_available(self) -> bool:
        """
        Check if GDS library is installed in Neo4j.

        Returns:
            True if GDS is available, False otherwise.
        """
        if self._gds_available is not None:
            return self._gds_available

        try:
            # Try to call gds.version()
            async with self._session() as session:
                result = await session.run("RETURN gds.version() AS version")
                record = await result.single()
                if record:
                    version = record["version"]
                    logger.info(f"GDS library version {version} detected")
                    self._gds_available = True
                    return True
        except Exception as e:
            logger.debug(f"GDS not available: {e}")
            self._gds_available = False

            if self._config.warn_on_fallback and not self._fallback_warned:
                logger.warning(
                    "Neo4j Graph Data Science (GDS) library is not installed. "
                    "Graph algorithms will use basic Cypher fallbacks with reduced "
                    "functionality. Install GDS for full algorithm support: "
                    "https://neo4j.com/docs/graph-data-science/current/installation/"
                )
                self._fallback_warned = True

        return False

    async def get_pagerank_scores(
        self,
        entity_ids: list[str],
        damping_factor: float | None = None,
    ) -> list[tuple[str, float]]:
        """
        Calculate PageRank scores for entities.

        Args:
            entity_ids: List of entity IDs to rank.
            damping_factor: PageRank damping factor (default from config).

        Returns:
            List of (entity_id, score) tuples sorted by score descending.
        """
        if not entity_ids:
            return []

        damping = damping_factor or self._config.pagerank_damping_factor

        if await self.is_gds_available():
            return await self._pagerank_gds(entity_ids, damping)
        elif self._config.fallback_to_basic:
            return await self._pagerank_fallback(entity_ids)
        else:
            return [(eid, 1.0) for eid in entity_ids]

    async def _pagerank_gds(
        self,
        entity_ids: list[str],
        damping_factor: float,
    ) -> list[tuple[str, float]]:
        """PageRank using GDS library."""
        query = """
        CALL gds.pageRank.stream({
            nodeQuery: 'MATCH (e:Entity) WHERE e.id IN $entity_ids RETURN elementId(e) AS id',
            relationshipQuery: '
                MATCH (e1:Entity)-[r]-(e2:Entity)
                WHERE e1.id IN $entity_ids AND e2.id IN $entity_ids
                RETURN elementId(e1) AS source, elementId(e2) AS target
            ',
            dampingFactor: $damping_factor
        })
        YIELD nodeId, score
        MATCH (e:Entity) WHERE elementId(e) = nodeId
        RETURN e.id AS entity_id, score
        ORDER BY score DESC
        """
        try:
            async with self._session() as session:
                result = await session.run(
                    query,
                    entity_ids=entity_ids,
                    damping_factor=damping_factor,
                )
                records = await result.data()
                return [(r["entity_id"], r["score"]) for r in records]
        except Exception as e:
            logger.warning(f"GDS PageRank failed, using fallback: {e}")
            return await self._pagerank_fallback(entity_ids)

    async def _pagerank_fallback(
        self,
        entity_ids: list[str],
    ) -> list[tuple[str, float]]:
        """
        PageRank fallback using degree centrality approximation.

        This provides a rough approximation of importance based on
        the number of relationships each entity has.
        """
        # Simplified fallback that works across Neo4j versions
        simple_query = """
        MATCH (e:Entity)
        WHERE e.id IN $entity_ids
        OPTIONAL MATCH (e)-[r]-()
        WITH e.id AS entity_id, count(r) AS degree
        WITH collect({entity_id: entity_id, degree: degree}) AS entities,
             max(degree) AS max_degree
        UNWIND entities AS ent
        RETURN ent.entity_id AS entity_id,
               CASE WHEN max_degree > 0
                    THEN toFloat(ent.degree) / max_degree
                    ELSE 0.1
               END AS score
        ORDER BY score DESC
        """
        try:
            async with self._session() as session:
                result = await session.run(simple_query, entity_ids=entity_ids)
                records = await result.data()
                return [(r["entity_id"], r["score"]) for r in records]
        except Exception as e:
            logger.debug(f"PageRank fallback failed: {e}")
            # Ultimate fallback: equal scores
            return [(eid, 1.0) for eid in entity_ids]

    async def detect_communities(
        self,
        entity_ids: list[str],
    ) -> dict[str, int]:
        """
        Detect communities among entities.

        Args:
            entity_ids: List of entity IDs to analyze.

        Returns:
            Dict mapping entity_id to community_id.
        """
        if not entity_ids:
            return {}

        if await self.is_gds_available():
            return await self._communities_gds(entity_ids)
        elif self._config.fallback_to_basic:
            return await self._communities_fallback(entity_ids)
        else:
            return dict.fromkeys(entity_ids, 0)

    async def _communities_gds(self, entity_ids: list[str]) -> dict[str, int]:
        """Community detection using GDS Louvain algorithm."""
        query = """
        CALL gds.louvain.stream({
            nodeQuery: 'MATCH (e:Entity) WHERE e.id IN $entity_ids RETURN elementId(e) AS id',
            relationshipQuery: '
                MATCH (e1:Entity)-[r]-(e2:Entity)
                WHERE e1.id IN $entity_ids AND e2.id IN $entity_ids
                RETURN elementId(e1) AS source, elementId(e2) AS target
            '
        })
        YIELD nodeId, communityId
        MATCH (e:Entity) WHERE elementId(e) = nodeId
        RETURN e.id AS entity_id, communityId AS community_id
        """
        try:
            async with self._session() as session:
                result = await session.run(query, entity_ids=entity_ids)
                records = await result.data()
                return {r["entity_id"]: r["community_id"] for r in records}
        except Exception as e:
            logger.warning(f"GDS community detection failed, using fallback: {e}")
            return await self._communities_fallback(entity_ids)

    async def _communities_fallback(self, entity_ids: list[str]) -> dict[str, int]:
        """
        Community detection fallback using connected components.

        Groups entities that are directly connected.
        """
        # Simple connected component approximation
        query = """
        MATCH (e:Entity)
        WHERE e.id IN $entity_ids
        OPTIONAL MATCH (e)-[r]-(other:Entity)
        WHERE other.id IN $entity_ids
        WITH e.id AS entity_id,
             CASE WHEN other IS NOT NULL
                  THEN min(other.id)
                  ELSE e.id
             END AS group_id
        RETURN entity_id, group_id
        """
        try:
            async with self._session() as session:
                result = await session.run(query, entity_ids=entity_ids)
                records = await result.data()

                # Convert group IDs to integer community IDs
                group_to_community: dict[str, int] = {}
                community_counter = 0
                communities: dict[str, int] = {}

                for r in records:
                    group_id = r["group_id"]
                    if group_id not in group_to_community:
                        group_to_community[group_id] = community_counter
                        community_counter += 1
                    communities[r["entity_id"]] = group_to_community[group_id]

                return communities
        except Exception as e:
            logger.debug(f"Community fallback failed: {e}")
            return dict.fromkeys(entity_ids, 0)

    async def find_shortest_path(
        self,
        source_entity: str,
        target_entity: str,
        max_hops: int = 5,
    ) -> dict[str, Any] | None:
        """
        Find shortest path between two entities.

        Args:
            source_entity: Name or ID of the source entity.
            target_entity: Name or ID of the target entity.
            max_hops: Maximum path length to search.

        Returns:
            A dict with ``"nodes"`` (the entities along the path) and
            ``"relationships"`` (their connecting relationship types), or
            ``None`` if no path exists.
        """
        # This doesn't require GDS - use built-in shortestPath.
        # Cypher can't parameterize a variable-length path bound, so max_hops
        # is interpolated; coerce it to int to keep the query injection-safe.
        safe_hops = int(max_hops)
        query = f"""
        MATCH (source:Entity)
        WHERE source.name = $source OR source.id = $source
        MATCH (target:Entity)
        WHERE target.name = $target OR target.id = $target
        MATCH path = shortestPath((source)-[*..{safe_hops}]-(target))
        RETURN [n IN nodes(path) | {{
            id: n.id,
            name: n.name,
            type: CASE WHEN n.type IS NOT NULL THEN n.type ELSE 'Entity' END
        }}] AS nodes,
        [r IN relationships(path) | {{type: type(r)}}] AS relationships
        """
        try:
            async with self._session() as session:
                result = await session.run(
                    query,  # ty: ignore[invalid-argument-type]  # neo4j stubs type the query as LiteralString; ours is a runtime str built from a fixed template + an int bound (safe_hops)
                    source=source_entity,
                    target=target_entity,
                )
                record = await result.single()
                if record:
                    return {
                        "nodes": record["nodes"],
                        "relationships": record["relationships"],
                    }
                return None
        except Exception as e:
            logger.debug(f"Shortest path query failed: {e}")
            return None

    async def find_similar_entities(
        self,
        entity_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find entities similar to the given entity.

        Uses relationship overlap (Jaccard similarity) to find similar entities.

        Args:
            entity_id: ID or name of the entity to find similar items for.
            limit: Maximum number of results.

        Returns:
            List of similar entities with similarity scores.
        """
        if await self.is_gds_available():
            return await self._similarity_gds(entity_id, limit)
        elif self._config.fallback_to_basic:
            return await self._similarity_fallback(entity_id, limit)
        else:
            return []

    async def _similarity_gds(
        self,
        entity_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Node similarity using GDS."""
        # GDS node similarity requires more setup; use fallback for now
        return await self._similarity_fallback(entity_id, limit)

    async def _similarity_fallback(
        self,
        entity_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Find similar entities using relationship overlap (Jaccard-like).
        """
        query = """
        MATCH (e:Entity)
        WHERE e.id = $entity_id OR e.name = $entity_id
        MATCH (e)-[r1]-(shared)-[r2]-(similar:Entity)
        WHERE similar <> e
        WITH similar, count(DISTINCT shared) AS shared_count
        MATCH (similar)-[r]-()
        WITH similar, shared_count, count(r) AS similar_degree
        RETURN similar.id AS id,
               similar.name AS name,
               similar.type AS type,
               similar.description AS description,
               toFloat(shared_count) / (similar_degree + 1) AS similarity
        ORDER BY similarity DESC
        LIMIT $limit
        """
        try:
            async with self._session() as session:
                result = await session.run(query, entity_id=entity_id, limit=limit)
                records = await result.data()
                return [dict(r) for r in records]
        except Exception as e:
            logger.debug(f"Similarity query failed: {e}")
            return []

    async def get_central_entities(
        self,
        entity_ids: list[str] | None = None,
        algorithm: GDSAlgorithm = GDSAlgorithm.PAGERANK,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Find the most central/important entities.

        Args:
            entity_ids: Optional list of entity IDs to consider.
            algorithm: Centrality algorithm to use.
            limit: Maximum number of results.

        Returns:
            List of central entities with scores.
        """
        if algorithm == GDSAlgorithm.PAGERANK:
            if entity_ids:
                scores = await self.get_pagerank_scores(entity_ids)
            else:
                # Get all entities
                query = "MATCH (e:Entity) RETURN e.id AS id LIMIT 1000"
                async with self._session() as session:
                    result = await session.run(query)
                    records = await result.data()
                    all_ids = [r["id"] for r in records]
                    scores = await self.get_pagerank_scores(all_ids)

            # Get entity details
            top_ids = [s[0] for s in scores[:limit]]
            score_map = dict(scores[:limit])

            query = """
            MATCH (e:Entity)
            WHERE e.id IN $ids
            RETURN e.id AS id, e.name AS name, e.type AS type, e.description AS description
            """
            async with self._session() as session:
                result = await session.run(query, ids=top_ids)
                records = await result.data()
                return [{**dict(r), "score": score_map.get(r["id"], 0)} for r in records]

        # Fallback for other algorithms
        return []
