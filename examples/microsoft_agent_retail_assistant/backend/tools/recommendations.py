"""Product recommendation tools using graph traversals.

Single implementation of the recommendation operations: ``agent.py`` wraps
these as Agent Framework tools and ``main.py`` serves
:func:`get_related_products` over REST. Reads use ``client.query.cypher()``.

Exposed as agent tools: all four functions in this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

#: The relationship kinds :func:`get_related_products` can traverse, mapped to
#: the query fragment that traverses them. Relationship types are not
#: parameterisable in Cypher, so a closed allow-list like this — never a
#: caller-supplied string — is the only safe way to accept one as input.
RELATED_BRANCHES: dict[str, str] = {
    "IN_CATEGORY": """MATCH (source)-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(related:Product)
        WHERE related <> source AND related.in_stock = true
        RETURN related, 'Same category: ' + c.name as relationship, 2 as weight""",
    "MADE_BY": """MATCH (source)-[:MADE_BY]->(b)<-[:MADE_BY]-(related:Product)
        WHERE related <> source AND related.in_stock = true
        RETURN related, 'Same brand: ' + b.name as relationship, 2 as weight""",
    "SIMILAR_TO": """MATCH (source)-[:SIMILAR_TO]-(related:Product)
        WHERE related.in_stock = true
        RETURN related, 'Similar product' as relationship, 3 as weight""",
    "BOUGHT_TOGETHER": """MATCH (source)-[:BOUGHT_TOGETHER]-(related:Product)
        WHERE related.in_stock = true
        RETURN related, 'Frequently bought together' as relationship, 4 as weight""",
    "HAS_ATTRIBUTE": """MATCH (source)-[:HAS_ATTRIBUTE]->(a)<-[:HAS_ATTRIBUTE]-(related:Product)
        WHERE related <> source AND related.in_stock = true
        RETURN related, 'Shared attribute: ' + a.name as relationship, 1 as weight""",
}


async def get_recommendations(
    client: MemoryClient,
    user_id: str | None = None,
    session_id: str | None = None,
    category: str | None = None,
    limit: int = 5,
) -> dict:
    """
    Get personalized product recommendations.

    Uses a combination of:
    - User preferences from long-term memory
    - Products viewed/discussed in session
    - Graph-based collaborative filtering
    - GDS PageRank for popular items (if available)

    Args:
        client: MemoryClient instance.
        user_id: Optional user ID for personalization.
        session_id: Optional session ID for context.
        category: Optional category filter.
        limit: Maximum recommendations.

    Returns:
        Dict with recommendations and reasoning.
    """
    recommendations = []
    reasons = []

    # 1. Get user preferences
    preferences = []
    try:
        prefs = await client.long_term.search_preferences(query="shopping preferences", limit=10)
        preferences = [{"category": p.category, "value": p.preference} for p in prefs]
    except Exception as e:
        logger.debug(f"Could not get preferences: {e}")

    # 2. Get products from session context.
    #
    # Two things matter in this query and both were wrong before:
    #   * :Message nodes carry no session_id — the session lives on the
    #     :Conversation the message hangs off (see graph/queries.py).
    #   * extracted entities use the POLE+O model, so a product is
    #     type 'OBJECT' with subtype 'PRODUCT' (and labels :Object:Product),
    #     never type 'Product'.
    session_products = []
    if session_id:
        try:
            cypher = """
            MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
            MATCH (m)-[:MENTIONS]->(e:Entity)
            WHERE e.type = 'OBJECT'
            RETURN DISTINCT e.name as product_name
            LIMIT 10
            """
            result = await client.query.cypher(cypher, {"session_id": session_id})
            session_products = [r["product_name"] for r in result]
        except Exception as e:
            logger.debug(f"Could not get session products: {e}")

    # 3. Build recommendation query based on available context
    if preferences or session_products:
        # Personalized recommendations
        pref_brands = [p["value"] for p in preferences if p["category"] == "brand"]
        pref_categories = [p["value"] for p in preferences if p["category"] == "category"]
        pref_styles = [p["value"] for p in preferences if p["category"] == "style"]

        cypher = """
        // Start with products matching preferences
        MATCH (p:Product)
        WHERE p.in_stock = true
        AND (
            p.brand IN $brands
            OR p.category IN $categories
            OR p.style IN $styles
            OR p.name IN $session_products
        )

        // Calculate relevance score
        WITH p,
             CASE WHEN p.brand IN $brands THEN 2 ELSE 0 END +
             CASE WHEN p.category IN $categories THEN 2 ELSE 0 END +
             CASE WHEN p.style IN $styles THEN 1 ELSE 0 END +
             CASE WHEN p.name IN $session_products THEN 3 ELSE 0 END as preference_score

        // Get related products through graph
        OPTIONAL MATCH (p)-[:SIMILAR_TO]-(similar:Product)
        WHERE similar.in_stock = true

        WITH p, preference_score, collect(DISTINCT similar)[0..3] as similar_products

        RETURN p {
            .id, .name, .description, .price, .category, .brand, .image_url
        } as product,
        preference_score,
        'Based on your preferences' as reason
        ORDER BY preference_score DESC, p.popularity DESC NULLS LAST
        LIMIT $limit
        """

        params: dict[str, Any] = {
            "brands": pref_brands,
            "categories": (pref_categories + [category]) if category else pref_categories,
            "styles": pref_styles,
            "session_products": session_products,
            "limit": limit,
        }

        try:
            result = await client.query.cypher(cypher, params)
            for r in result:
                recommendations.append(r["product"])
                reasons.append(r["reason"])
        except Exception as e:
            logger.warning(f"Personalized recommendation query failed: {e}")

    # 4. Fill remaining slots with popular items
    if len(recommendations) < limit:
        remaining = limit - len(recommendations)
        existing_ids = [p.get("id") for p in recommendations]

        # Try degree-based importance ranking (GDS-free fallback)
        try:
            cypher = """
            MATCH (p:Product)
            WHERE p.in_stock = true AND NOT p.id IN $existing
            OPTIONAL MATCH (p)-[r:SIMILAR_TO|BOUGHT_TOGETHER]-()
            WITH p, count(r) AS degree
            WITH collect({product: p, degree: degree}) AS products,
                 max(degree) AS max_degree
            UNWIND products AS item
            WITH item.product AS p,
                 CASE WHEN max_degree > 0
                      THEN toFloat(item.degree) / max_degree
                      ELSE 0.1
                 END AS score
            RETURN p {
                .id, .name, .description, .price, .category, .brand, .image_url
            } as product, score, 'Popular product' as reason
            ORDER BY score DESC
            LIMIT $limit
            """
            result = await client.query.cypher(
                cypher, {"existing": existing_ids, "limit": remaining}
            )
            for r in result:
                recommendations.append(r["product"])
                reasons.append(r["reason"])

        except Exception as e:
            logger.debug(f"Degree-based ranking not available, using popularity: {e}")

            # Fallback to simple popularity
            category_filter = "AND p.category = $category" if category else ""
            cypher = f"""
            MATCH (p:Product)
            WHERE p.in_stock = true AND NOT p.id IN $existing
            {category_filter}
            RETURN p {{
                .id, .name, .description, .price, .category, .brand, .image_url
            }} as product, 'Popular product' as reason
            ORDER BY p.popularity DESC NULLS LAST
            LIMIT $limit
            """
            params: dict[str, Any] = {"existing": existing_ids, "limit": remaining}
            if category:
                params["category"] = category

            result = await client.query.cypher(cypher, params)
            for r in result:
                recommendations.append(r["product"])
                reasons.append(r["reason"])

    return {
        "recommendations": recommendations,
        "reasons": reasons,
        "personalized": bool(preferences or session_products),
    }


async def get_related_products(
    client: MemoryClient,
    product_id: str,
    relationship_types: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """
    Find products related to a given product through graph relationships.

    Args:
        client: MemoryClient instance.
        product_id: Source product ID.
        relationship_types: Types of relationships to consider.
        limit: Maximum results.

    Returns:
        Dict with related products and relationship info.
    """
    types = list(relationship_types) if relationship_types else list(RELATED_BRANCHES)
    unknown = [t for t in types if t not in RELATED_BRANCHES]
    if unknown:
        # Relationship types cannot be parameterised in Cypher, so anything
        # that reaches the query body must come from this closed allow-list.
        raise ValueError(
            f"Unsupported relationship type(s): {unknown}. Allowed: {sorted(RELATED_BRANCHES)}"
        )

    branches = "\n\n        UNION\n\n".join(RELATED_BRANCHES[t] for t in types)

    cypher = f"""
    MATCH (source:Product)
    WHERE source.id = $product_id OR elementId(source) = $product_id

    // Find related through the requested paths
    CALL (source) {{
        {branches}
    }}

    WITH related, relationship, weight
    ORDER BY weight DESC

    RETURN related {{
        .id, .name, .description, .price, .category, .brand, .image_url
    }} as product,
    collect(DISTINCT relationship)[0..3] as relationships,
    sum(weight) as relevance_score
    ORDER BY relevance_score DESC
    LIMIT $limit
    """

    result = await client.query.cypher(cypher, {"product_id": product_id, "limit": limit})

    return {
        "source_product_id": product_id,
        "related_products": [
            {
                **r["product"],
                "relationships": r["relationships"],
                "relevance_score": r["relevance_score"],
            }
            for r in result
        ],
    }


async def get_bought_together(
    client: MemoryClient,
    product_id: str,
    limit: int = 3,
) -> dict:
    """
    Get products frequently bought together with the given product.

    Args:
        client: MemoryClient instance.
        product_id: Source product ID.
        limit: Maximum results.

    Returns:
        Dict with frequently bought together products.
    """
    cypher = """
    MATCH (source:Product)-[r:BOUGHT_TOGETHER]-(related:Product)
    WHERE (source.id = $product_id OR elementId(source) = $product_id)
    AND related.in_stock = true
    RETURN related {
        .id, .name, .price, .image_url
    } as product,
    r.frequency as purchase_frequency,
    r.confidence as confidence
    ORDER BY r.frequency DESC, r.confidence DESC
    LIMIT $limit
    """

    result = await client.query.cypher(cypher, {"product_id": product_id, "limit": limit})

    return {
        "source_product_id": product_id,
        "frequently_bought_together": [
            {
                **r["product"],
                "purchase_frequency": r.get("purchase_frequency", 0),
                "confidence": r.get("confidence", 0),
            }
            for r in result
        ],
    }


async def explain_product_connection(
    client: MemoryClient,
    product_id_1: str,
    product_id_2: str,
    max_hops: int = 4,
) -> dict:
    """
    Explain how two products are connected in the graph.

    Uses shortest path to find the connection and explains
    the relationship in natural language.

    Args:
        client: MemoryClient instance.
        product_id_1: First product ID.
        product_id_2: Second product ID.
        max_hops: Maximum path length to search.

    Returns:
        Dict with path explanation.
    """
    cypher = """
    MATCH (p1:Product), (p2:Product)
    WHERE (p1.id = $id1 OR elementId(p1) = $id1)
    AND (p2.id = $id2 OR elementId(p2) = $id2)

    CALL apoc.path.expandConfig(p1, {
        terminatorNodes: [p2],
        maxLevel: $max_hops,
        relationshipFilter: 'IN_CATEGORY|MADE_BY|SIMILAR_TO|BOUGHT_TOGETHER|HAS_ATTRIBUTE'
    })
    YIELD path

    WITH path, length(path) as pathLength
    ORDER BY pathLength
    LIMIT 1

    UNWIND range(0, size(nodes(path))-1) as idx
    WITH nodes(path)[idx] as node,
         CASE WHEN idx < size(relationships(path))
              THEN relationships(path)[idx]
              ELSE null END as rel

    RETURN collect({
        node: labels(node)[0] + ': ' + coalesce(node.name, 'unknown'),
        relationship: CASE WHEN rel IS NOT NULL THEN type(rel) ELSE null END
    }) as path_steps
    """

    try:
        result = await client.query.cypher(
            cypher,
            {"id1": product_id_1, "id2": product_id_2, "max_hops": max_hops},
        )

        if result and result[0]["path_steps"]:
            steps = result[0]["path_steps"]
            explanation_parts = []

            for i, step in enumerate(steps):
                if i == 0:
                    explanation_parts.append(step["node"])
                elif step["relationship"]:
                    rel_name = step["relationship"].replace("_", " ").lower()
                    explanation_parts.append(f"is {rel_name}")
                    explanation_parts.append(step["node"])

            return {
                "connected": True,
                "path_length": len(steps) - 1,
                "path": steps,
                "explanation": " → ".join(explanation_parts),
            }

    except Exception as e:
        logger.warning(f"Path finding failed: {e}")

    # Fallback: check for direct common attributes
    cypher = """
    MATCH (p1:Product), (p2:Product)
    WHERE (p1.id = $id1 OR elementId(p1) = $id1)
    AND (p2.id = $id2 OR elementId(p2) = $id2)

    OPTIONAL MATCH (p1)-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(p2)
    OPTIONAL MATCH (p1)-[:MADE_BY]->(b)<-[:MADE_BY]-(p2)

    RETURN p1.name as product1, p2.name as product2,
           collect(DISTINCT c.name) as shared_categories,
           collect(DISTINCT b.name) as shared_brands
    """

    result = await client.query.cypher(cypher, {"id1": product_id_1, "id2": product_id_2})

    if result:
        r = result[0]
        shared = []
        if r["shared_categories"]:
            shared.append(f"same category ({', '.join(r['shared_categories'])})")
        if r["shared_brands"]:
            shared.append(f"same brand ({', '.join(r['shared_brands'])})")

        if shared:
            return {
                "connected": True,
                "path_length": 2,
                "explanation": f"{r['product1']} and {r['product2']} share the {' and '.join(shared)}",
            }

    return {
        "connected": False,
        "explanation": "No direct connection found between these products",
    }
