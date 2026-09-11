"""Product search tools.

Every function here takes a connected ``MemoryClient`` as its first argument
and returns plain dicts. This module is the **single implementation** of these
catalog operations: ``agent.py`` wraps them as Agent Framework tools and
``main.py`` serves them over REST, so there is no second copy of the Cypher to
drift.

Reads go through ``client.query.cypher()`` — the portable accessor that works
on both the bolt and the hosted (NAMS) backend — rather than the bolt-only
``client.graph.execute_read()``.

Exposed as agent tools: :func:`search_products`, :func:`get_product_details`.
:func:`get_products_by_category`, :func:`get_brands` and :func:`get_categories`
back the ``/products/categories`` and ``/products/brands`` REST endpoints.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from neo4j.exceptions import ClientError

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.embeddings.base import Embedder

logger = logging.getLogger(__name__)

#: Name of the vector index created by ``data/load_products.py``.
PRODUCT_VECTOR_INDEX = "product_embedding"


async def search_products(
    client: MemoryClient,
    query: str,
    category: str | None = None,
    brand: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    in_stock_only: bool = True,
    limit: int = 10,
    embedder: Embedder | None = None,
) -> dict:
    """
    Search products using vector similarity and filters.

    Args:
        client: MemoryClient instance.
        query: Search query.
        category: Optional category filter.
        brand: Optional brand filter.
        max_price: Optional maximum price.
        min_price: Optional minimum price.
        in_stock_only: Only return in-stock items.
        limit: Maximum results.
        embedder: Embedder for the vector branch. ``None`` (no API key
            configured) skips straight to text search. There is no
            ``client.embeddings`` accessor — the embedder is constructed
            explicitly in ``memory_config.create_embedder()``.

    Returns:
        Dict with ``products`` (each carrying ``relevance_score``), ``count``
        and ``search_mode`` ("vector" or "text") so callers can tell which
        branch ran.
    """
    # Build filter conditions
    conditions = ["p:Product"]
    params: dict[str, Any] = {"query": query, "limit": limit}

    if category:
        conditions.append("p.category = $category")
        params["category"] = category

    if brand:
        conditions.append("p.brand = $brand")
        params["brand"] = brand

    if max_price is not None:
        conditions.append("p.price <= $max_price")
        params["max_price"] = max_price

    if min_price is not None:
        conditions.append("p.price >= $min_price")
        params["min_price"] = min_price

    if in_stock_only:
        conditions.append("p.in_stock = true")

    where_clause = " AND ".join(conditions)
    # The text branch matches (p:Product) in the pattern, so the label
    # predicate is redundant there.
    text_conditions = [c for c in conditions if c != "p:Product"]

    projection = """
        p {
            .id, .name, .description, .price, .in_stock, .inventory,
            .image_url, .attributes,
            category: coalesce(c.name, p.category),
            brand: coalesce(b.name, p.brand)
        } as product
    """

    result: list[dict[str, Any]] | None = None
    search_mode = "text"

    if embedder is not None:
        vector_cypher = f"""
        CALL db.index.vector.queryNodes('{PRODUCT_VECTOR_INDEX}', $candidates, $embedding)
        YIELD node as p, score
        WHERE {where_clause}
        OPTIONAL MATCH (p)-[:IN_CATEGORY]->(c:Category)
        OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
        RETURN {projection}, score
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            params["embedding"] = await embedder.embed(query)
            # The index returns its nearest neighbours *before* the WHERE
            # clause filters them, so ask for more candidates when filters are
            # in play — otherwise a brand filter can empty the result set.
            params["candidates"] = limit * (10 if len(text_conditions) > 1 else 2)
            result = await client.query.cypher(vector_cypher, params)
            search_mode = "vector"
        except ClientError as exc:
            # Missing index or bad dimensions — visible, not silent.
            logger.warning(
                "Vector search on '%s' failed (%s); falling back to text search. "
                "Run `python -m data.load_products` with OPENAI_API_KEY set to "
                "populate product embeddings.",
                PRODUCT_VECTOR_INDEX,
                exc.code or exc,
            )
        except Exception as exc:  # embedding provider errors (auth, quota, network)
            logger.warning("Could not embed the query (%s); using text search.", exc)

    if result is None:
        text_cypher = f"""
        MATCH (p:Product)
        WHERE (toLower(p.name) CONTAINS toLower($query)
               OR toLower(coalesce(p.description, '')) CONTAINS toLower($query))
        {"AND " + " AND ".join(text_conditions) if text_conditions else ""}
        OPTIONAL MATCH (p)-[:IN_CATEGORY]->(c:Category)
        OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
        RETURN {projection}, 1.0 as score
        ORDER BY p.popularity DESC
        LIMIT $limit
        """
        params.pop("embedding", None)
        params.pop("candidates", None)
        result = await client.query.cypher(text_cypher, params)

    products = [{**record["product"], "relevance_score": record["score"]} for record in result]

    return {"products": products, "count": len(products), "search_mode": search_mode}


async def get_product_details(
    client: MemoryClient,
    product_id: str,
) -> dict | None:
    """
    Get detailed product information.

    Args:
        client: MemoryClient instance.
        product_id: Product ID or element ID.

    Returns:
        Product details dict or None if not found.
    """
    cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    OPTIONAL MATCH (p)-[:IN_CATEGORY]->(c:Category)
    OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
    OPTIONAL MATCH (p)-[:HAS_ATTRIBUTE]->(a:Attribute)
    OPTIONAL MATCH (p)-[:HAS_REVIEW]->(r:Review)
    WITH p, c, b, collect(DISTINCT a) as attributes, collect(DISTINCT r) as reviews
    RETURN p {
        .id, .name, .description, .price, .in_stock, .inventory,
        .image_url, .images, .specifications,
        category: c.name,
        brand: b.name,
        attributes: [attr in attributes | attr.name + ': ' + attr.value],
        // avg() is an aggregation function and cannot take a list, so the
        // mean is computed with reduce() over the collected reviews.
        rating: CASE WHEN size(reviews) > 0
                     THEN round(
                         reduce(total = 0.0, rv IN reviews | total + coalesce(rv.rating, 0))
                         / size(reviews) * 10
                     ) / 10
                     ELSE null END,
        review_count: size(reviews)
    } as product
    """

    result = await client.query.cypher(cypher, {"product_id": product_id})

    if result:
        return result[0]["product"]
    return None


async def get_products_by_category(
    client: MemoryClient,
    category: str,
    limit: int = 20,
    sort_by: str = "popularity",
) -> dict:
    """
    Get products in a specific category.

    Args:
        client: MemoryClient instance.
        category: Category name.
        limit: Maximum results.
        sort_by: Sort field (popularity, price_asc, price_desc, newest).

    Returns:
        Dict with products list.
    """
    order_clause = {
        "popularity": "p.popularity DESC NULLS LAST",
        "price_asc": "p.price ASC NULLS LAST",
        "price_desc": "p.price DESC NULLS LAST",
        "newest": "p.created_at DESC NULLS LAST",
    }.get(sort_by, "p.popularity DESC NULLS LAST")

    cypher = f"""
    MATCH (p:Product)-[:IN_CATEGORY]->(c:Category)
    WHERE toLower(c.name) = toLower($category)
    OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
    RETURN p {{
        .id, .name, .description, .price, .in_stock,
        .image_url,
        category: c.name,
        brand: b.name
    }} as product
    ORDER BY {order_clause}
    LIMIT $limit
    """

    result = await client.query.cypher(cypher, {"category": category, "limit": limit})

    return {"products": [r["product"] for r in result], "category": category}


async def get_brands(client: MemoryClient, category: str | None = None) -> list[str]:
    """Get all brands, optionally filtered by category."""
    if category:
        cypher = """
        MATCH (p:Product)-[:IN_CATEGORY]->(c:Category)
        WHERE toLower(c.name) = toLower($category)
        MATCH (p)-[:MADE_BY]->(b:Brand)
        RETURN DISTINCT b.name as brand
        ORDER BY brand
        """
        params = {"category": category}
    else:
        cypher = """
        MATCH (b:Brand)
        RETURN b.name as brand
        ORDER BY brand
        """
        params = {}

    result = await client.query.cypher(cypher, params)
    return [r["brand"] for r in result]


async def get_categories(client: MemoryClient) -> list[dict]:
    """Get all product categories with counts."""
    cypher = """
    MATCH (c:Category)<-[:IN_CATEGORY]-(p:Product)
    RETURN c.name as name, c.description as description, count(p) as product_count
    ORDER BY product_count DESC
    """

    result = await client.query.cypher(cypher, {})
    return [dict(r) for r in result]
