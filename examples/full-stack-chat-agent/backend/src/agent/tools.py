"""News graph tools for the chat agent.

Every tool in this module reads the *news* graph — a separate, read-only Neo4j
database from the memory graph. Two rules hold throughout:

1. **Reads go through a managed read transaction** (``session.execute_read``).
   That is a server-enforced boundary: Neo4j answers a write inside a read
   transaction with ``Neo.ClientError.Statement.AccessMode``. String scanning
   for ``CREATE``/``SET`` is only a friendly pre-check, never the boundary.
2. **No provider credential ever travels to the database.** Embeddings are
   computed in the application and only the resulting vector is passed as a
   Cypher parameter.
"""

import asyncio
import functools
import logging
from typing import Any

from neo4j import AsyncManagedTransaction
from pydantic_ai import RunContext

from neo4j_agent_memory.core.query import is_read_only_query
from neo4j_agent_memory.llm import from_provider
from neo4j_agent_memory.llm.protocol import EmbeddingProvider
from src.agent.dependencies import AgentDeps
from src.config import get_settings

logger = logging.getLogger(__name__)

# Hard ceiling on rows returned to the model, whatever a tool's `limit` says.
# Keeps a runaway agent query out of the context window.
MAX_ROWS = 200


async def _run_query(
    ctx: RunContext[AgentDeps],
    query: str,
    params: dict[str, Any] | None = None,
    *,
    max_rows: int = MAX_ROWS,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Execute a read-only Cypher query against the news graph.

    Runs inside ``session.execute_read`` so the server rejects any write, caps
    the rows fetched, and bounds the wall-clock time.
    """
    if ctx.deps.news_driver is None:
        return [{"error": "News graph driver not configured (check NEWS_GRAPH_URI)"}]

    settings = get_settings()
    limit = min(max_rows, MAX_ROWS)

    async def read(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
        result = await tx.run(query, params or {})
        records = await result.fetch(limit)
        await result.consume()  # discard anything beyond the cap
        return [record.data() for record in records]

    try:
        async with ctx.deps.news_driver.session(database=ctx.deps.news_database) as session:
            return await asyncio.wait_for(
                session.execute_read(read),
                timeout=timeout if timeout is not None else settings.cypher_timeout_seconds,
            )
    except asyncio.TimeoutError:
        return [{"error": "Query timed out. Narrow the query or lower the limit."}]
    except Exception as e:  # surfaced to the model so it can correct itself
        logger.warning("News graph query failed: %s", e)
        return [{"error": f"{type(e).__name__}: {e}"}]


@functools.lru_cache(maxsize=1)
def _news_embedder() -> EmbeddingProvider:
    """The embedding provider used for news vector search (cached).

    Resolved from ``NEWS_EMBEDDING_MODEL`` through the library's provider
    factory — the same code path the memory graph uses — so there is no second
    OpenAI client and no private attribute access.
    """
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.openai_api_key.get_secret_value():
        kwargs["api_key"] = settings.openai_api_key.get_secret_value()
    return from_provider(settings.news_embedding_model, kind="embedding", **kwargs)


async def embed_query(text: str) -> list[float]:
    """Embed a search string application-side. Never sends a key to Neo4j."""
    return await _news_embedder().embed_one(text)


async def search_news(
    ctx: RunContext[AgentDeps],
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search news articles using text matching on title and abstract.

    Args:
        ctx: The agent run context.
        query: The search query string.
        limit: Maximum number of results to return.

    Returns:
        List of matching articles with title, abstract, and published date.
    """
    # CONTAINS rather than a fulltext index, so the tool works against a graph
    # that has no `article_fulltext` index provisioned.
    cypher = """
    MATCH (a:Article)
    WHERE toLower(a.title) CONTAINS toLower($query)
       OR toLower(a.abstract) CONTAINS toLower($query)
    RETURN a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(ctx, cypher, {"query": query, "limit": limit}, max_rows=limit)


async def vector_search_news(
    ctx: RunContext[AgentDeps],
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search news articles using semantic vector similarity.

    The query embedding is computed in the application; only the vector
    reaches Neo4j. The model must match the one the ``article_embeddings``
    index was built with — see ``NEWS_EMBEDDING_MODEL``.

    Args:
        ctx: The agent run context.
        query: The search query for semantic matching.
        limit: Maximum number of results to return.

    Returns:
        List of semantically similar articles.
    """
    try:
        query_vector = await embed_query(query)
    except Exception as e:
        logger.warning("Embedding the news query failed: %s", e)
        return [
            {
                "error": (
                    f"Could not embed the query ({type(e).__name__}). "
                    "Check OPENAI_API_KEY / NEWS_EMBEDDING_MODEL, or use search_news."
                )
            }
        ]

    cypher = """
    CALL db.index.vector.queryNodes("article_embeddings", $limit, $queryVector)
    YIELD node, score
    RETURN node.title AS title,
           node.abstract AS abstract,
           node.published AS published,
           node.url AS url,
           score
    """
    return await _run_query(
        ctx,
        cypher,
        {"limit": limit, "queryVector": query_vector},
        max_rows=limit,
    )


async def get_recent_news(
    ctx: RunContext[AgentDeps],
    limit: int = 10,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Get the most recent news articles.

    Args:
        ctx: The agent run context.
        limit: Maximum number of results to return.
        days: Number of days to look back.

    Returns:
        List of recent articles ordered by publication date.
    """
    cypher = """
    MATCH (a:Article)
    WHERE a.published >= datetime() - duration({days: $days})
    RETURN a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(ctx, cypher, {"limit": limit, "days": days}, max_rows=limit)


async def get_news_by_topic(
    ctx: RunContext[AgentDeps],
    topic: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get news articles by topic.

    Args:
        ctx: The agent run context.
        topic: The topic to filter by.
        limit: Maximum number of results to return.

    Returns:
        List of articles matching the topic.
    """
    cypher = """
    MATCH (a:Article)-[:HAS_TOPIC]->(t:Topic)
    WHERE toLower(t.name) CONTAINS toLower($topic)
    RETURN a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url,
           t.name AS topic
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(ctx, cypher, {"topic": topic, "limit": limit}, max_rows=limit)


async def get_topics(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """Get all available news topics with article counts.

    Args:
        ctx: The agent run context.

    Returns:
        List of topics with their article counts.
    """
    cypher = """
    MATCH (t:Topic)<-[:HAS_TOPIC]-(a:Article)
    RETURN t.name AS topic, count(a) AS article_count
    ORDER BY article_count DESC
    LIMIT 50
    """
    return await _run_query(ctx, cypher, {}, max_rows=50)


async def search_news_by_location(
    ctx: RunContext[AgentDeps],
    location: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search news articles by geographic location.

    Args:
        ctx: The agent run context.
        location: The location to search for.
        limit: Maximum number of results to return.

    Returns:
        List of articles related to the location.
    """
    cypher = """
    MATCH (a:Article)-[:ABOUT_GEO]->(g:Geo)
    WHERE toLower(g.name) CONTAINS toLower($location)
    RETURN a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url,
           g.name AS location
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(ctx, cypher, {"location": location, "limit": limit}, max_rows=limit)


async def search_news_multi_topic(
    ctx: RunContext[AgentDeps],
    topics: list[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search news articles that match any of the given topics or keywords.

    Use this for broad research queries that span multiple subjects.

    Args:
        ctx: The agent run context.
        topics: List of topics/keywords to search for.
        limit: Maximum number of results to return.

    Returns:
        List of articles matching any of the topics.
    """
    # Matches the title/abstract text *and* every neighbour label, so the tool
    # does what its docstring promises ("subjects, people, or events").
    cypher = """
    MATCH (a:Article)
    OPTIONAL MATCH (a)-[:HAS_TOPIC]->(t:Topic)
    OPTIONAL MATCH (a)-[:ABOUT_PERSON]->(p:Person)
    OPTIONAL MATCH (a)-[:ABOUT_ORGANIZATION]->(o:Organization)
    OPTIONAL MATCH (a)-[:ABOUT_GEO]->(g:Geo)
    WITH a,
         collect(DISTINCT t.name) AS topics,
         collect(DISTINCT p.name) AS people,
         collect(DISTINCT o.name) AS organizations,
         collect(DISTINCT g.name) AS locations
    WHERE any(topic IN $topics WHERE
        toLower(a.title) CONTAINS toLower(topic) OR
        toLower(a.abstract) CONTAINS toLower(topic) OR
        any(name IN topics + people + organizations + locations
            WHERE toLower(toString(name)) CONTAINS toLower(topic))
    )
    RETURN DISTINCT a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url,
           topics,
           people,
           organizations,
           locations
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(ctx, cypher, {"topics": topics, "limit": limit}, max_rows=limit)


async def search_news_by_date_range(
    ctx: RunContext[AgentDeps],
    start_date: str,
    end_date: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search news articles within a date range.

    Args:
        ctx: The agent run context.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        limit: Maximum number of results to return.

    Returns:
        List of articles published within the date range.
    """
    cypher = """
    MATCH (a:Article)
    WHERE date(a.published) >= date($start_date)
      AND date(a.published) <= date($end_date)
    RETURN a.title AS title,
           a.abstract AS abstract,
           a.published AS published,
           a.url AS url
    ORDER BY a.published DESC
    LIMIT $limit
    """
    return await _run_query(
        ctx,
        cypher,
        {"start_date": start_date, "end_date": end_date, "limit": limit},
        max_rows=limit,
    )


async def get_database_schema(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
    """Get the news graph database schema.

    Args:
        ctx: The agent run context.

    Returns:
        Dictionary describing the database schema including node labels,
        relationship types, and their properties.
    """
    # apoc.meta.* is optional: fall back to plain db.labels() when the plugin
    # is not installed on the news graph.
    labels_query = """
    CALL db.labels() YIELD label
    CALL apoc.meta.nodeTypeProperties({labels: [label]}) YIELD propertyName, propertyTypes
    RETURN label, collect({property: propertyName, types: propertyTypes}) AS properties
    """
    fallback_labels_query = "CALL db.labels() YIELD label RETURN label, [] AS properties"
    rels_query = """
    CALL db.relationshipTypes() YIELD relationshipType
    RETURN collect(relationshipType) AS relationships
    """

    labels_result = await _run_query(ctx, labels_query, {}, max_rows=50)
    if labels_result and "error" in labels_result[0]:
        labels_result = await _run_query(ctx, fallback_labels_query, {}, max_rows=50)
    rels_result = await _run_query(ctx, rels_query, {}, max_rows=1)

    return {
        "node_labels": labels_result,
        "relationships": (
            rels_result[0]["relationships"]
            if rels_result and "relationships" in rels_result[0]
            else []
        ),
        "description": """
News Graph Schema:
- Article: News articles with title, abstract, published date, url, embedding
- Topic: Categories/topics that articles are about
- Person: People mentioned in articles
- Organization: Companies and organizations mentioned
- Geo: Geographic locations mentioned
- Photo: Images associated with articles

Relationships:
- (Article)-[:HAS_TOPIC]->(Topic)
- (Article)-[:ABOUT_PERSON]->(Person)
- (Article)-[:ABOUT_ORGANIZATION]->(Organization)
- (Article)-[:ABOUT_GEO]->(Geo)
- (Article)-[:HAS_PHOTO]->(Photo)
        """,
    }


async def execute_cypher(
    ctx: RunContext[AgentDeps],
    query: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a read-only Cypher query against the news graph.

    Two layers, in order:

    1. :func:`neo4j_agent_memory.core.query.is_read_only_query` — the library's
       shared validator. A *friendly* pre-check that gives the model a usable
       error message instead of a driver exception. It is word-boundary aware,
       so ``RETURN a.created`` and ``MATCH (n:Asset)`` are not false positives
       the way a plain substring scan was.
    2. ``session.execute_read`` in :func:`_run_query` — the actual boundary.
       Anything that slips past step 1 is rejected by the **server** with
       ``Neo.ClientError.Statement.AccessMode``.

    Args:
        ctx: The agent run context.
        query: The Cypher query to execute.
        params: Optional parameters for the query.

    Returns:
        Query results as a list of dictionaries.
    """
    if not is_read_only_query(query):
        return [
            {
                "error": (
                    "Only read-only Cypher is allowed on the news graph "
                    "(no CREATE/MERGE/DELETE/SET/REMOVE/DROP/LOAD CSV/CALL {...})."
                )
            }
        ]

    settings = get_settings()
    return await _run_query(ctx, query, params, max_rows=settings.cypher_max_rows)
