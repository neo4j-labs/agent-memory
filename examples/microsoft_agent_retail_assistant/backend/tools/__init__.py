"""Catalog tools for the retail assistant.

This package is the **single implementation** of every catalog operation.
``agent.py`` wraps these plain async functions as Agent Framework
``FunctionTool``s and ``main.py`` serves several of them over REST, so the
Cypher exists in exactly one place.

Every function takes a connected ``MemoryClient`` first and returns plain
dicts, which makes them directly testable without an agent or an HTTP client.
"""

from .cart import (
    add_to_cart,
    apply_coupon,
    clear_cart,
    get_cart,
    remove_from_cart,
    save_cart_for_later,
    update_cart_item,
)
from .inventory import (
    check_inventory,
    find_alternatives,
    get_low_stock_products,
    get_stock_status,
    notify_when_available,
)
from .product_search import (
    get_brands,
    get_categories,
    get_product_details,
    get_products_by_category,
    search_products,
)
from .recommendations import (
    explain_product_connection,
    get_bought_together,
    get_recommendations,
    get_related_products,
)

__all__ = [
    # product_search
    "search_products",
    "get_product_details",
    "get_products_by_category",
    "get_brands",
    "get_categories",
    # recommendations
    "get_recommendations",
    "get_related_products",
    "get_bought_together",
    "explain_product_connection",
    # inventory
    "check_inventory",
    "find_alternatives",
    "get_stock_status",
    "get_low_stock_products",
    "notify_when_available",
    # cart
    "get_cart",
    "add_to_cart",
    "update_cart_item",
    "remove_from_cart",
    "clear_cart",
    "apply_coupon",
    "save_cart_for_later",
]
