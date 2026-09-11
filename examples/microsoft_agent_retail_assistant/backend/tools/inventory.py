"""Inventory management tools.

Single implementation of the stock operations. Reads go through
``client.query.cypher()``; the one write (:func:`notify_when_available`) uses
``client.graph.execute_write()`` because ``client.query`` is read-only by
design.

Exposed as agent tools: :func:`check_inventory`, :func:`find_alternatives`.
:func:`get_stock_status`, :func:`notify_when_available` and
:func:`get_low_stock_products` are kept as reusable helpers — wire them into a
tool or a route if your app needs them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)


async def check_inventory(
    client: MemoryClient,
    product_id: str,
) -> dict:
    """
    Check inventory status for a product.

    Args:
        client: MemoryClient instance.
        product_id: Product ID to check.

    Returns:
        Dict with inventory status.
    """
    cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    RETURN p.name as name,
           p.in_stock as in_stock,
           p.inventory as quantity,
           p.restock_date as restock_date,
           p.low_stock_threshold as low_stock_threshold
    """

    result = await client.query.cypher(cypher, {"product_id": product_id})

    if not result:
        return {"error": "Product not found", "product_id": product_id}

    r = result[0]
    quantity = r.get("quantity") or 0
    threshold = r.get("low_stock_threshold") or 5

    # Determine status
    if not r["in_stock"] or quantity == 0:
        status = "out_of_stock"
        message = "Currently out of stock"
        if r.get("restock_date"):
            message += f". Expected restock: {r['restock_date']}"
    elif quantity <= threshold:
        status = "low_stock"
        message = f"Low stock - only {quantity} left"
    else:
        status = "in_stock"
        message = f"In stock - {quantity} available"

    return {
        "product_id": product_id,
        "name": r["name"],
        "status": status,
        "quantity": quantity,
        "in_stock": r["in_stock"],
        "message": message,
        "restock_date": r.get("restock_date"),
    }


async def get_stock_status(
    client: MemoryClient,
    product_ids: list[str],
) -> dict:
    """
    Get stock status for multiple products.

    Args:
        client: MemoryClient instance.
        product_ids: List of product IDs.

    Returns:
        Dict with status for each product.
    """
    cypher = """
    UNWIND $product_ids as pid
    MATCH (p:Product)
    WHERE p.id = pid OR elementId(p) = pid
    RETURN pid as requested_id,
           p.id as product_id,
           p.name as name,
           p.in_stock as in_stock,
           p.inventory as quantity
    """

    result = await client.query.cypher(cypher, {"product_ids": product_ids})

    statuses = {}
    for r in result:
        quantity = r.get("quantity") or 0
        statuses[r["requested_id"]] = {
            "product_id": r["product_id"],
            "name": r["name"],
            "in_stock": r["in_stock"],
            "quantity": quantity,
            "status": "in_stock" if r["in_stock"] and quantity > 0 else "out_of_stock",
        }

    # Mark missing products
    for pid in product_ids:
        if pid not in statuses:
            statuses[pid] = {
                "product_id": pid,
                "name": None,
                "in_stock": False,
                "quantity": 0,
                "status": "not_found",
            }

    return {"statuses": statuses, "all_in_stock": all(s["in_stock"] for s in statuses.values())}


async def find_alternatives(
    client: MemoryClient,
    product_id: str,
    limit: int = 3,
) -> dict:
    """
    Find in-stock alternatives to an out-of-stock product.

    Args:
        client: MemoryClient instance.
        product_id: Product ID to find alternatives for.
        limit: Maximum alternatives.

    Returns:
        Dict with alternative products.
    """
    cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id

    // Find alternatives through multiple paths
    CALL (p) {
        // Same category, similar price range
        MATCH (p)-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(alt:Product)
        WHERE alt <> p AND alt.in_stock = true
        AND alt.price BETWEEN p.price * 0.7 AND p.price * 1.3
        RETURN alt, 'Same category, similar price' as reason, 3 as score

        UNION

        // Same brand
        MATCH (p)-[:MADE_BY]->(b)<-[:MADE_BY]-(alt:Product)
        WHERE alt <> p AND alt.in_stock = true
        RETURN alt, 'Same brand' as reason, 2 as score

        UNION

        // Direct similarity relationship
        MATCH (p)-[:SIMILAR_TO]-(alt:Product)
        WHERE alt.in_stock = true
        RETURN alt, 'Similar product' as reason, 4 as score
    }

    WITH alt, reason, score
    ORDER BY score DESC

    RETURN DISTINCT alt {
        .id, .name, .description, .price, .category, .brand, .image_url, .inventory
    } as alternative,
    collect(DISTINCT reason)[0] as reason
    LIMIT $limit
    """

    result = await client.query.cypher(cypher, {"product_id": product_id, "limit": limit})

    return {
        "original_product_id": product_id,
        "alternatives": [{**r["alternative"], "reason": r["reason"]} for r in result],
        "found": len(result) > 0,
    }


async def notify_when_available(
    client: MemoryClient,
    product_id: str,
    user_id: str,
    session_id: str,
) -> dict:
    """
    Register interest in being notified when a product is back in stock.

    Args:
        client: MemoryClient instance.
        product_id: Product ID to watch.
        user_id: User ID requesting notification.
        session_id: Current session ID.

    Returns:
        Dict with notification status.
    """
    # Check if product exists and is actually out of stock
    check = await check_inventory(client, product_id)

    if check.get("error"):
        return {"success": False, "error": check["error"]}

    if check["in_stock"]:
        return {
            "success": False,
            "error": "Product is already in stock",
            "product": check["name"],
        }

    # Create notification request in graph. This is a write, so it cannot go
    # through the read-only client.query accessor.
    cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    MERGE (u:User {identifier: $user_id})
    MERGE (u)-[r:WANTS_NOTIFICATION]->(p)
    SET r.created_at = datetime(),
        r.session_id = $session_id
    RETURN p.name as product_name
    """

    result = await client.graph.execute_write(
        cypher,
        {"product_id": product_id, "user_id": user_id, "session_id": session_id},
    )

    if result:
        return {
            "success": True,
            "message": f"You'll be notified when {result[0]['product_name']} is back in stock",
            "product_id": product_id,
        }

    return {"success": False, "error": "Failed to register notification"}


async def get_low_stock_products(
    client: MemoryClient,
    category: str | None = None,
    threshold: int = 10,
    limit: int = 20,
) -> dict:
    """
    Get products with low stock levels.

    Args:
        client: MemoryClient instance.
        category: Optional category filter.
        threshold: Stock level threshold.
        limit: Maximum results.

    Returns:
        Dict with low stock products.
    """
    category_filter = "AND p.category = $category" if category else ""

    cypher = f"""
    MATCH (p:Product)
    WHERE p.inventory <= $threshold AND p.inventory > 0
    {category_filter}
    RETURN p {{
        .id, .name, .price, .category, .inventory
    }} as product
    ORDER BY p.inventory ASC
    LIMIT $limit
    """

    params: dict[str, Any] = {"threshold": threshold, "limit": limit}
    if category:
        params["category"] = category

    result = await client.query.cypher(cypher, params)

    return {
        "low_stock_products": [r["product"] for r in result],
        "count": len(result),
        "threshold": threshold,
    }
