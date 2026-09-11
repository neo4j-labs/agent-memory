"""Shopping cart tools.

Cart state lives in the graph as ``(:Cart {session_id})-[:CONTAINS]->(:Product)``
so it is scoped to the same session id the memory layers use. Reads go through
``client.query.cypher()``; writes use ``client.graph.execute_write()``.

Exposed as agent tools: :func:`get_cart`, :func:`add_to_cart`,
:func:`remove_from_cart`. :func:`update_cart_item` and :func:`clear_cart` are
reusable helpers. :func:`apply_coupon` and :func:`save_cart_for_later` need
``:Coupon`` / ``:SavedCart`` data that ``data/load_products.py`` does not
create — treat them as sketches, not working features.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)


async def get_cart(
    client: MemoryClient,
    session_id: str,
) -> dict:
    """
    Get the current shopping cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.

    Returns:
        Dict with cart contents and totals.
    """
    cypher = """
    MATCH (c:Cart {session_id: $session_id})-[contains:CONTAINS]->(p:Product)
    RETURN p {
        .id, .name, .price, .image_url, .in_stock
    } as product,
    contains.quantity as quantity,
    p.price * contains.quantity as line_total
    ORDER BY contains.added_at DESC
    """

    result = await client.query.cypher(cypher, {"session_id": session_id})

    items = []
    subtotal = 0

    for r in result:
        item = {
            "product": r["product"],
            "quantity": r["quantity"],
            "line_total": r["line_total"],
        }
        items.append(item)
        subtotal += r["line_total"]

    return {
        "session_id": session_id,
        "items": items,
        "item_count": sum(item["quantity"] for item in items),
        "subtotal": subtotal,
        "estimated_tax": round(subtotal * 0.08, 2),  # Example 8% tax
        "total": round(subtotal * 1.08, 2),
    }


async def add_to_cart(
    client: MemoryClient,
    session_id: str,
    product_id: str,
    quantity: int = 1,
) -> dict:
    """
    Add a product to the cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.
        product_id: Product to add.
        quantity: Quantity to add.

    Returns:
        Dict with updated cart info.
    """
    # First check if product exists and is in stock
    check_cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    RETURN p.name as name, p.in_stock as in_stock, p.inventory as inventory, p.price as price
    """

    check_result = await client.query.cypher(check_cypher, {"product_id": product_id})

    if not check_result:
        return {"success": False, "error": "Product not found"}

    product = check_result[0]

    if not product["in_stock"]:
        return {
            "success": False,
            "error": f"{product['name']} is currently out of stock",
        }

    if product["inventory"] is not None and product["inventory"] < quantity:
        return {
            "success": False,
            "error": f"Only {product['inventory']} units available",
        }

    # Add to cart (merge to handle existing items)
    cypher = """
    MERGE (c:Cart {session_id: $session_id})
    ON CREATE SET c.created_at = datetime()

    WITH c
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id

    MERGE (c)-[contains:CONTAINS]->(p)
    ON CREATE SET
        contains.quantity = $quantity,
        contains.added_at = datetime()
    ON MATCH SET
        contains.quantity = contains.quantity + $quantity

    RETURN p.name as product_name, contains.quantity as new_quantity, p.price as price
    """

    result = await client.graph.execute_write(
        cypher,
        {"session_id": session_id, "product_id": product_id, "quantity": quantity},
    )

    if result:
        r = result[0]
        return {
            "success": True,
            "message": f"Added {quantity} x {r['product_name']} to cart",
            "product_name": r["product_name"],
            "quantity_in_cart": r["new_quantity"],
            "line_total": r["new_quantity"] * r["price"],
        }

    return {"success": False, "error": "Failed to add to cart"}


async def update_cart_item(
    client: MemoryClient,
    session_id: str,
    product_id: str,
    quantity: int,
) -> dict:
    """
    Update quantity of an item in the cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.
        product_id: Product to update.
        quantity: New quantity (0 to remove).

    Returns:
        Dict with update status.
    """
    if quantity <= 0:
        return await remove_from_cart(client, session_id, product_id)

    # Check inventory
    check_cypher = """
    MATCH (p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    RETURN p.inventory as inventory, p.name as name
    """

    check_result = await client.query.cypher(check_cypher, {"product_id": product_id})

    if not check_result:
        return {"success": False, "error": "Product not found"}

    inventory = check_result[0].get("inventory")
    if inventory is not None and inventory < quantity:
        return {
            "success": False,
            "error": f"Only {inventory} units available",
        }

    cypher = """
    MATCH (c:Cart {session_id: $session_id})-[contains:CONTAINS]->(p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    SET contains.quantity = $quantity
    RETURN p.name as product_name, contains.quantity as new_quantity, p.price as price
    """

    result = await client.graph.execute_write(
        cypher,
        {"session_id": session_id, "product_id": product_id, "quantity": quantity},
    )

    if result:
        r = result[0]
        return {
            "success": True,
            "message": f"Updated {r['product_name']} quantity to {quantity}",
            "product_name": r["product_name"],
            "quantity": r["new_quantity"],
            "line_total": r["new_quantity"] * r["price"],
        }

    return {"success": False, "error": "Item not found in cart"}


async def remove_from_cart(
    client: MemoryClient,
    session_id: str,
    product_id: str,
) -> dict:
    """
    Remove an item from the cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.
        product_id: Product to remove.

    Returns:
        Dict with removal status.
    """
    cypher = """
    MATCH (c:Cart {session_id: $session_id})-[contains:CONTAINS]->(p:Product)
    WHERE p.id = $product_id OR elementId(p) = $product_id
    WITH c, contains, p.name as product_name
    DELETE contains
    RETURN product_name
    """

    result = await client.graph.execute_write(
        cypher, {"session_id": session_id, "product_id": product_id}
    )

    if result:
        return {
            "success": True,
            "message": f"Removed {result[0]['product_name']} from cart",
            "product_name": result[0]["product_name"],
        }

    return {"success": False, "error": "Item not found in cart"}


async def clear_cart(
    client: MemoryClient,
    session_id: str,
) -> dict:
    """
    Clear all items from the cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.

    Returns:
        Dict with clear status.
    """
    cypher = """
    MATCH (c:Cart {session_id: $session_id})-[contains:CONTAINS]->()
    DELETE contains
    RETURN count(*) as items_removed
    """

    result = await client.graph.execute_write(cypher, {"session_id": session_id})

    items_removed = result[0]["items_removed"] if result else 0

    return {
        "success": True,
        "message": f"Cleared {items_removed} items from cart",
        "items_removed": items_removed,
    }


async def apply_coupon(
    client: MemoryClient,
    session_id: str,
    coupon_code: str,
) -> dict:
    """
    Apply a coupon code to the cart.

    Args:
        client: MemoryClient instance.
        session_id: Session ID.
        coupon_code: Coupon code to apply.

    Returns:
        Dict with coupon application status.
    """
    # Check if coupon exists and is valid
    check_cypher = """
    MATCH (coupon:Coupon {code: $code})
    WHERE coupon.valid_from <= datetime() AND coupon.valid_until >= datetime()
    AND (coupon.uses_remaining IS NULL OR coupon.uses_remaining > 0)
    RETURN coupon.discount_type as type,
           coupon.discount_value as value,
           coupon.minimum_purchase as minimum,
           coupon.description as description
    """

    result = await client.query.cypher(check_cypher, {"code": coupon_code.upper()})

    if not result:
        return {"success": False, "error": "Invalid or expired coupon code"}

    coupon = result[0]

    # Get current cart total
    cart = await get_cart(client, session_id)

    if coupon["minimum"] and cart["subtotal"] < coupon["minimum"]:
        return {
            "success": False,
            "error": f"Minimum purchase of ${coupon['minimum']} required for this coupon",
        }

    # Calculate discount
    if coupon["type"] == "percentage":
        discount = cart["subtotal"] * (coupon["value"] / 100)
    else:  # fixed amount
        discount = coupon["value"]

    # Apply coupon to cart
    apply_cypher = """
    MATCH (c:Cart {session_id: $session_id})
    SET c.coupon_code = $code,
        c.discount = $discount
    RETURN c.coupon_code as code
    """

    await client.graph.execute_write(
        apply_cypher,
        {"session_id": session_id, "code": coupon_code.upper(), "discount": discount},
    )

    return {
        "success": True,
        "message": f"Coupon applied: {coupon['description']}",
        "discount": round(discount, 2),
        "new_total": round(cart["subtotal"] - discount, 2),
    }


async def save_cart_for_later(
    client: MemoryClient,
    session_id: str,
    user_id: str,
) -> dict:
    """
    Save the current cart for the user to retrieve later.

    Args:
        client: MemoryClient instance.
        session_id: Current session ID.
        user_id: User ID.

    Returns:
        Dict with save status.
    """
    cypher = """
    MATCH (c:Cart {session_id: $session_id})-[contains:CONTAINS]->(p:Product)
    MERGE (u:User {id: $user_id})
    MERGE (saved:SavedCart {user_id: $user_id})
    SET saved.updated_at = datetime()

    // Copy items to saved cart
    MERGE (saved)-[s:SAVED_ITEM]->(p)
    SET s.quantity = contains.quantity,
        s.saved_at = datetime()

    RETURN count(p) as items_saved
    """

    result = await client.graph.execute_write(
        cypher, {"session_id": session_id, "user_id": user_id}
    )

    items_saved = result[0]["items_saved"] if result else 0

    return {
        "success": True,
        "message": f"Saved {items_saved} items for later",
        "items_saved": items_saved,
    }
