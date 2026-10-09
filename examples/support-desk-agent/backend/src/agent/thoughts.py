"""What each tool call is recorded as having been *for* (the step's ``thought``).

Shared by the chat route, which records live turns, and the seed, which records
the STATE-Bench trajectories, so earlier and new traces read the same way.
"""

from __future__ import annotations

TOOL_THOUGHTS: dict[str, str] = {
    "recall_similar_tasks": "Recall earlier agent work on a similar task",
    "find_customer": "Identify the customer from what they said",
    "search_support_history": "Search earlier support conversations",
    "get_ontology": "Check which ontology revision is active",
    "get_customer": "Read the customer's profile and membership benefits",
    "get_order": "Look up the order, its items and their products",
    "search_products": "Search the catalogue",
    "get_product_details": "Read the product's details",
    "get_policies": "Check the policy before acting",
    "get_warranty_status": "Check the item's warranty",
    "process_return": "Return an item (preview, then confirm)",
    "process_refund": "Refund an item (preview, then confirm)",
    "cancel_order": "Cancel an order or some of its items (preview, then confirm)",
    "process_exchange": "Exchange an item for another product (preview, then confirm)",
    "process_warranty_claim": "File a warranty claim (preview, then confirm)",
}


def thought_for(tool_name: str, arguments: dict[str, object] | None = None) -> str:
    """The step's thought; a write tool's preview and confirmation read differently."""
    thought = TOOL_THOUGHTS.get(tool_name, f"Call {tool_name}")
    if (
        arguments is not None
        and "confirm" in arguments
        and tool_name.startswith(("process_", "cancel_"))
    ):
        verb = "Confirm" if arguments.get("confirm") in (True, "true", "True") else "Preview"
        thought = f"{verb}: " + thought.split(" (", 1)[0].lower()
    return thought
