# Copied from microsoft/STATE-Bench @ 5644b1838d96, state_bench/domains/customer_support/environment.py
# (MIT License, Copyright (c) 2026 STATE-Bench contributors; see LICENSE in this
# directory). Only the imports were changed, to point at this package.
"""Stateful customer support environment with tool handlers.

The CustomerSupportEnvironment holds the in-memory database and provides tool handler
methods as bound functions. Each evaluation run gets a fresh deep copy.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.statebench.vendor import policies
from src.statebench.vendor.schemas import (
    CSEnvironmentData,
    Customer,
    Order,
    OrderItem,
    Product,
    Warranty,
)
from src.statebench.vendor.base import BaseEnvironment


class CustomerSupportEnvironment(BaseEnvironment):
    """Stateful environment wrapping products, orders, items, customers, warranties and policy engine."""

    def __init__(self, env_data: CSEnvironmentData, now: str):
        super().__init__(env_data, now)
        self.products: dict[str, Product] = {p.product_id: p for p in env_data.products}
        self.orders: dict[str, Order] = {o.order_id: o for o in env_data.orders}
        self.order_items: dict[str, OrderItem] = {i.item_id: i for i in env_data.order_items}
        self.customers: dict[str, Customer] = {c.customer_id: c for c in env_data.customers}
        self.warranties: dict[str, Warranty] = {w.warranty_id: w for w in env_data.warranties}

        # Policy gate: track which policy topics have been looked up
        self._policies_checked: set[str] = set()

        # Two-step enforcement: track previewed operations per tool
        self._previewed: dict[str, set[str]] = {
            "return": set(),
            "refund": set(),
            "cancel": set(),
            "exchange": set(),
            "warranty_claim": set(),
        }
        self._previewed_refunds: dict[str, set[tuple[str, int | float]]] = {}

    def get_orders_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot for assertion checking.

        Keys include order_ids, item_ids, AND warranty_ids so that
        DBAssertion.booking_id (used as generic entity_id) can reference
        any entity type.
        """
        snapshot: dict[str, dict[str, Any]] = {}
        for oid, order in self.orders.items():
            snapshot[oid] = order.to_dict()
        for iid, item in self.order_items.items():
            snapshot[iid] = item.to_dict()
        for wid, warranty in self.warranties.items():
            snapshot[wid] = warranty.to_dict()
        return snapshot

    def get_full_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return all mutable entities indexed by type and ID for StateDiff."""
        return {
            "orders": {oid: o.to_dict() for oid, o in self.orders.items()},
            "order_items": {iid: i.to_dict() for iid, i in self.order_items.items()},
            "customers": {cid: c.to_dict() for cid, c in self.customers.items()},
            "warranties": {wid: w.to_dict() for wid, w in self.warranties.items()},
        }

    def _get_items_for_order(self, order_id: str) -> list[OrderItem]:
        """Get all OrderItems belonging to an order."""
        return [item for item in self.order_items.values() if item.order_id == order_id]

    def _get_warranty_for_item(self, item_id: str) -> Warranty | None:
        """Get warranty for an item, if any."""
        for w in self.warranties.values():
            if w.item_id == item_id:
                return w
        return None

    # -------------------------------------------------------------------
    # READ tools
    # -------------------------------------------------------------------

    def get_order(self, params: dict[str, Any]) -> dict[str, Any]:
        """Retrieve full order details including all items and product info."""
        order_id = params.get("order_id", "")
        order = self.orders.get(order_id)
        if not order:
            return {"error": f"Order {order_id} not found."}

        items = self._get_items_for_order(order_id)
        items_data = []
        for item in items:
            product = self.products.get(item.product_id)
            item_dict = item.to_dict()
            if product:
                item_dict["product"] = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "category": product.category,
                    "price": product.price,
                    "current_price": product.current_price,
                    "return_window_days": product.return_window_days,
                    "warranty_months": product.warranty_months,
                    "in_stock": product.in_stock,
                }
            items_data.append(item_dict)

        result = order.to_dict()
        result["items"] = items_data
        return result

    def get_customer(self, params: dict[str, Any]) -> dict[str, Any]:
        """Retrieve customer profile."""
        customer_id = params.get("customer_id", "")
        customer = self.customers.get(customer_id)
        if not customer:
            return {"error": f"Customer {customer_id} not found."}
        result = customer.to_dict()
        # Add membership benefit summary
        tier = customer.membership_tier
        result["benefits"] = {
            "return_window_extension": f"+{policies.TIER_RETURN_EXTENSION.get(tier, 0)} days",
            "compensation_multiplier": f"{policies.TIER_COMPENSATION_MULTIPLIER.get(tier, 1.0)}x",
            "restocking_fee_waiver": tier == "platinum",
            "prime_shipping": customer.has_prime_shipping,
        }
        return result

    def search_products(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search task-local products by name, category, or subcategory."""
        query = str(params.get("query", "")).strip()
        if not query:
            return {"error": "query is required."}

        query_terms = [term for term in re.split(r"[^a-z0-9]+", query.lower()) if term]
        matches: list[tuple[int, str, str, Product]] = []
        for product in self.products.values():
            name = product.name.lower()
            category = product.category.lower()
            subcategory = product.subcategory.lower()
            score = 0
            matched_field = ""
            if query.lower() == name:
                score += 100
                matched_field = "name"
            elif query.lower() in name:
                score += 70
                matched_field = "name"
            for term in query_terms:
                if term in name:
                    score += 10
                    matched_field = matched_field or "name"
                if term == category or term in category:
                    score += 5
                    matched_field = matched_field or "category"
                if term == subcategory or term in subcategory:
                    score += 7
                    matched_field = matched_field or "subcategory"
            if score > 0:
                matches.append((score, product.name, matched_field, product))

        matches.sort(key=lambda row: (-row[0], row[1], row[3].product_id))
        return {
            "query": query,
            "results": [
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "matched_field": matched_field,
                    "category": product.category,
                    "subcategory": product.subcategory,
                    "in_stock": product.in_stock,
                }
                for _score, _name, matched_field, product in matches
            ],
        }

    def get_product_details(self, params: dict[str, Any]) -> dict[str, Any]:
        """Retrieve full product details by exact product_id."""
        product_id = params.get("product_id", "")
        product = self.products.get(product_id)
        if not product:
            return {"error": f"Product {product_id} not found."}
        return product.to_dict()

    def get_policies(self, params: dict[str, Any]) -> dict[str, Any]:
        """Look up company policies for a given topic. Must be called before any write tool."""
        topic = params.get("topic", "")
        self._policies_checked.add(topic)

        if topic == "return":
            return {
                "topic": "return",
                "rules": {
                    "agent_computes_amount": "process_return requires the agent to submit the net refund as `amount` on confirm. Compute it from the preview's component breakdown: base_item_price (after promo redistribution) minus restocking_fee plus restocking_discount minus shipping_clawback minus bulk_clawback minus repeat_surcharge minus paid_return_shipping_fee. The env writes the submitted amount verbatim — skipping a component (e.g. forgetting the Gold restocking discount) produces a wrong refund_amount and fails state scoring.",
                    "windows_by_category": {
                        "electronics": "15-day return window",
                        "clothing": "30-day return window",
                        "kitchen": "30-day return window",
                        "books": "14-day return window",
                        "accessories": "30-day return window",
                    },
                    "window_extensions": {
                        "membership": "Gold/Platinum members get +15 days",
                        "prime": "Prime shipping members get +15 additional days",
                        "seasonal": "Orders placed in November or December extend through January 31 of the following year. Seasonal extension applies if it gives more time than tier/prime extensions.",
                        "store_credit_grace": "Up to 15 days past the return window: store credit only.",
                    },
                    "eligibility": {
                        "defective_items": "No window restriction for defective, wrong item, or damaged in transit.",
                        "already_returned": "Items already returned/exchanged/cancelled are ineligible.",
                        "not_as_described": "Use not_as_described only for an unused/unworn item that materially differs from the listing. If the customer wore, washed, altered, or used the item and only dislikes fit, feel, texture, or preference-based qualities, classify the return as changed_mind instead.",
                    },
                    "restocking_fee": {
                        "base": "15% restocking fee for opened electronics returned for changed_mind.",
                        "tier_discount": {
                            "platinum": "Restocking fee fully waived.",
                            "gold": "50% off the restocking fee.",
                            "silver": "25% off the restocking fee.",
                            "standard": "No discount.",
                        },
                        "auto_applied": "Restocking fee and any tier discount are folded into the process_return refund amount automatically; the result reports the discount under 'restocking_discount' for transparency.",
                        "completed_returns": "Once a changed-mind return is completed with the applicable restocking fee, do not reverse that fee later as a courtesy or supplemental refund.",
                    },
                    "return_shipping_fee": {
                        "low_value": "Orders with subtotal < $50: customer pays $8 return shipping fee.",
                        "free_threshold": "Orders >= $50: free return label provided.",
                        "fault_exempt": "Defective, wrong item, or damaged in transit: always free return shipping.",
                        "deduction": "Return shipping fee, when charged, is deducted from the return refund.",
                    },
                    "bulk_purchase_clawback": {
                        "applies_when": "Order originally qualified for a bulk discount (3+ items with a discount code) AND the return drops remaining count below 3.",
                        "amount": "$5 per remaining item, deducted from the return refund.",
                        "no_discount_code": "If order had no discount code, no clawback applies.",
                    },
                    "free_shipping_clawback": {
                        "applies_when": "Order originally qualified for free shipping (subtotal >= $100) AND the return drops remaining subtotal below $100.",
                        "amount": "$8 standard shipping fee is deducted from the return refund. This is a flat policy charge, not the original shipping cost (which was $0 on free-shipping orders).",
                        "fault_exempt": "Defective, wrong item, or damaged in transit: no clawback (customer-fault returns only).",
                        "paid_shipping": "If the order did not qualify for free shipping originally (subtotal < $100), no clawback applies.",
                    },
                    "repeat_category_surcharge": {
                        "rule": "Returning 2+ items from the same product category in one order: $5 surcharge per additional return (first return in each category is free).",
                        "different_categories": "Returns from different categories do not trigger the surcharge.",
                        "deduction": "Surcharge is deducted from the return refund.",
                    },
                },
            }
        elif topic == "refund":
            return {
                "topic": "refund",
                "rules": {
                    "amount": {
                        "full_refund": "Full refund for defective, wrong item, or damaged in transit.",
                        "promo_redistribution": "If a promo/coupon was used: discount allocated proportionally by item price. Refund = item_price - (discount * item_price / subtotal).",
                        "outside_window": "Outside return window but within store-credit grace: store credit only.",
                        "shipping_refund": "When refunding shipping (defective/wrong/damaged returns, 6+-days-late compensation), refund the actual shipping cost the customer was charged on the order — read order.shipping_cost and refund that amount. Standard policy: $0 on free-shipping orders (subtotal >= $100), $8 otherwise. NOT for buyer's remorse.",
                    },
                    "method": {
                        "original_payment": "Default refund method when item was paid for and is being returned for fault.",
                        "store_credit": "Required for: gift returns (at current product price), outside-window grace returns, and exchange-cheaper differences.",
                        "store_credit_only_constraint": "When a return is issued under the store-credit-only rule (gift return OR outside-window grace), the refund method cannot be changed back to original_payment afterward. process_refund will reject any attempt to flip the method on those returns.",
                    },
                    "price_match": "If product price drops within 7 days of delivery, refund the difference (no return required).",
                    "goodwill_credit": "Goodwill credits (e.g., fragile-item damage bonus) are issued as a refund with the credit amount.",
                },
            }
        elif topic == "cancellation":
            return {
                "topic": "cancellation",
                "rules": {
                    "pre_shipment": "Free cancellation before shipment (pending/processing status).",
                    "in_transit": "$10 intercept fee per item for in-transit orders.",
                    "delivered": "Cannot cancel delivered orders — must use the return process instead.",
                    "partial": "Partial cancellation allowed if items not yet delivered.",
                    "split_payment": "Refund distributed proportionally to original payment methods.",
                    "already_cancelled": "Already cancelled orders cannot be cancelled again.",
                },
            }
        elif topic == "exchange":
            return {
                "topic": "exchange",
                "rules": {
                    "must_be_different_product": "Exchanges must specify a different product than the original item. Self-swap (exchanging an item for the same product) is not allowed — use process_return for a refund instead.",
                    "same_price": "Exchange for a same-price product: no charge, no refund.",
                    "more_expensive": "Exchange for more expensive item: customer pays price difference.",
                    "cheaper": "Exchange for cheaper item: difference refunded as store credit (not original payment).",
                    "out_of_stock": "If requested item is out of stock: issue store credit for the original item; do not complete the exchange.",
                    "return_window": "Must be within return window (same rules as returns).",
                    "no_price_protection": "No price protection on exchanges (item price at time of purchase applies).",
                },
            }
        elif topic == "warranty":
            return {
                "topic": "warranty",
                "rules": {
                    "active": "Active warranty: eligible for claim.",
                    "active_item_required": "Warranty claims require an active order item. Items already returned, exchanged, or cancelled are ineligible for a new warranty claim even if a warranty record still exists.",
                    "expired_recent": "Expired <30 days: 50% off repair.",
                    "expired_old": "Expired >30 days: full-price repair or 25% off replacement.",
                    "claim_limit": "Max claims reached: paid repair only (40% of item price).",
                    "repair_vs_replace": "Items <$100: replacement. Items >=$100: repair first.",
                    "recurring_defect": "2+ prior claims for same issue: automatic replacement.",
                    "manufacturer": "Manufacturer warranty covers first 12 months.",
                    "extended": "Extended warranty covers after manufacturer period.",
                    "void_exclusions": "Liquid, accidental, or user-caused physical damage (e.g. spills, drops, cracked screens from misuse) is NOT covered and voids the claim, even while the warranty is otherwise active. The eligibility check is date/claim-count based only and does not detect damage cause, so when the customer describes liquid/accidental/user damage, deny the claim on this exclusion and do not file it.",
                },
            }
        elif topic == "shipping":
            return {
                "topic": "shipping",
                "rules": {
                    "not_received": {
                        "under_500": "Delivered but not received (<$500): reship or refund.",
                        "over_500": "Delivered but not received (>=$500): mandatory investigation (3-5 business days).",
                        "signature_on_file": "Delivery with signature on file: claim denied.",
                    },
                    "lost_in_transit": {
                        "under_500": "Lost in transit (<$500): immediate reship or refund.",
                        "over_500": "Lost in transit (>=$500): carrier claim required first.",
                        "stuck_7_days": "No tracking update for 7+ days: treat as lost.",
                    },
                    "damaged": {
                        "rule": "Damaged in transit: full refund or replacement.",
                        "fragile_bonus": "Fragile items damaged in transit qualify for a separate $10 goodwill credit, issued as a refund in addition to the return refund.",
                    },
                    "late_delivery_compensation": {
                        "tiers": {
                            "1_to_2_days_late": "$5 credit",
                            "3_to_5_days_late": "$15 credit",
                            "6_plus_days_late": "Full shipping refund + $15 credit",
                        },
                        "shipping_refund_basis": "When refunding shipping, refund the actual shipping cost the customer was charged on the order — read order.shipping_cost. Standard policy charges $0 on free-shipping orders (subtotal >= $100) and $8 otherwise.",
                        "tier_multipliers": {
                            "gold": "1.5x multiplier on the base late-delivery credit only (NOT on shipping refund or goodwill). Rounded down (e.g., int(15*1.5) = 22).",
                            "platinum": "2x multiplier on the base late-delivery credit only. Rounded down.",
                        },
                        "repeated_issues": "3+ PRIOR issues in 6 months (not counting current incident): additional $25 goodwill credit.",
                        "loyalty_bonus": "Platinum members with 50+ total orders: one-time $50 loyalty bonus on next compensation claim. Confirm with customer if already redeemed.",
                        "max_cap": "Maximum compensation: 50% of order total (rounded down).",
                        "calculation_order": "1) Compute base credit by days-late tier. 2) Apply tier multiplier to base credit only. 3) Add shipping refund (if 6+ days late) using order.shipping_cost. 4) Add goodwill (if 3+ prior issues or fragile damage). 5) Apply loyalty bonus if eligible. 6) Apply 50%-of-order cap on the sum.",
                    },
                },
            }
        else:
            return {
                "error": f"Unknown policy topic: {topic}. Valid: return, refund, cancellation, exchange, warranty, shipping"
            }

    def get_warranty_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Check warranty status for an item."""
        item_id = params.get("item_id", "")
        item = self.order_items.get(item_id)
        if not item:
            return {"error": f"Item {item_id} not found."}

        warranty = self._get_warranty_for_item(item_id)
        if not warranty:
            return {"item_id": item_id, "has_warranty": False, "message": "No warranty found for this item."}

        now_dt = datetime.fromisoformat(self.now)
        end_dt = datetime.fromisoformat(warranty.end_date)
        is_active = now_dt <= end_dt

        result = warranty.to_dict()
        result["is_active"] = is_active
        result["days_until_expiry"] = (end_dt - now_dt).days if is_active else 0
        result["days_past_expiry"] = (now_dt - end_dt).days if not is_active else 0
        result["has_warranty"] = True
        return result

    # -------------------------------------------------------------------
    # WRITE tools (all two-step: preview → confirm)
    # -------------------------------------------------------------------

    def process_return(self, params: dict[str, Any]) -> dict[str, Any]:
        """Process a return for an order item. Two-step: preview then confirm.

        On preview (confirm=False), the env returns the component breakdown
        (restocking_fee, restocking_discount, shipping_clawback, bulk_clawback,
        repeat_surcharge, paid_return_shipping_fee) so the agent can compute the
        net refund. On confirm, the agent MUST submit `amount` — whatever they
        submit is written to item.refund_amount as-is. The env does not auto-
        compute the final amount; the agent owns that responsibility.
        """
        item_id = params.get("item_id", "")
        reason = params.get("reason", "changed_mind")
        confirm = self.parse_bool(params.get("confirm"))

        # Policy gate
        if "return" not in self._policies_checked:
            return {"error": "Policy review required. Call get_policies(topic='return') first."}

        # A return presupposes the customer physically has the item. An item that was
        # never received (not in the package / not delivered) cannot be "returned" —
        # resolve those via process_refund (or a reship), not process_return.
        if reason == "missing":
            return {
                "error": (
                    "process_return does not handle not-received/missing items — a return requires the "
                    "customer to have the item. For a missing or never-received item, issue a refund with "
                    "process_refund (or arrange a reship)."
                )
            }

        item = self.order_items.get(item_id)
        if not item:
            return {"error": f"Item {item_id} not found."}

        order = self.orders.get(item.order_id)
        if not order:
            return {"error": f"Order {item.order_id} not found."}

        product = self.products.get(item.product_id)
        if not product:
            return {"error": f"Product {item.product_id} not found."}

        customer = self.customers.get(order.customer_id)
        if not customer:
            return {"error": f"Customer {order.customer_id} not found."}

        # Check eligibility
        eligibility = policies.check_return_eligibility(
            category=product.category,
            delivery_date=order.delivery_date,
            now=self.now,
            item_status=item.item_status,
            return_reason=reason,
            membership_tier=customer.membership_tier,
            has_prime_shipping=customer.has_prime_shipping,
            order_date=order.order_date,
        )

        if not eligibility["eligible"]:
            return {"status": "rejected", "item_id": item_id, "reason": eligibility["reason"]}

        # Calculate refund
        store_credit_only = eligibility.get("store_credit_only", False)
        refund = policies.calculate_refund(
            item_price=item.unit_price,
            return_reason=reason,
            category=product.category,
            discount_code=order.discount_code,
            discount_amount=order.discount_amount,
            order_subtotal=order.subtotal,
            membership_tier=customer.membership_tier,
            is_gift_return=order.is_gift and reason == "changed_mind",
            current_product_price=product.current_price,
            store_credit_only=store_credit_only,
        )

        # Clawbacks: bulk applies universally (policy text: "deducted from return refund"
        # with no reason carveout). Shipping clawback skipped on product-fault returns
        # since those already get shipping refunded — clawing back simultaneously is
        # contradictory.
        is_product_fault = reason in ("defective", "damaged_in_transit", "wrong_item")

        # Free-shipping clawback: if this return drops remaining subtotal below the
        # free-shipping threshold, deduct the would-be standard shipping cost from
        # the refund. "Remaining" counts items not yet returned/cancelled/exchanged
        # EXCLUDING the current item.
        original_free_shipping = order.subtotal >= policies.FREE_SHIPPING_THRESHOLD
        all_items = self._get_items_for_order(order.order_id)
        remaining_items_after = [
            i for i in all_items if i.item_id != item_id and i.item_status not in ("returned", "cancelled", "exchanged")
        ]
        remaining_subtotal_after = sum(i.unit_price for i in remaining_items_after)
        clawback_amount = 0
        if (
            not is_product_fault
            and original_free_shipping
            and remaining_subtotal_after < policies.FREE_SHIPPING_THRESHOLD
        ):
            clawback_amount = policies.STANDARD_SHIPPING_COST

        # Bulk-discount clawback: if this return drops remaining item count
        # below the bulk threshold (3), claw back $5 per remaining item.
        bulk = policies.calculate_bulk_clawback(
            original_item_count=len(all_items),
            items_being_returned=1,
            remaining_item_count=len(remaining_items_after),
            discount_code=order.discount_code,
            discount_amount=order.discount_amount,
        )
        bulk_clawback_amount = bulk.get("clawback_amount", 0) if bulk.get("applies") else 0

        # Repeat-category surcharge: $5 on 2nd+ return in same category within this order.
        already_returned_categories: list[str] = []
        for i in all_items:
            if i.item_id == item_id:
                continue
            if i.item_status == "returned":
                p = self.products.get(i.product_id)
                if p:
                    already_returned_categories.append(p.category)
        repeat = policies.calculate_repeat_return_surcharge(
            return_category=product.category,
            already_returned_categories=already_returned_categories,
        )
        repeat_surcharge_amount = repeat.get("surcharge", 0) if repeat.get("applies") else 0

        # Paid return shipping: low-value orders (<$50) pay $8 return shipping
        # on customer-fault returns. Product-fault returns always get free shipping.
        paid_ship = policies.calculate_paid_return_shipping(
            order_subtotal=order.subtotal,
            return_reason=reason,
        )
        paid_return_shipping_fee = paid_ship.get("fee", 0) if paid_ship.get("applies") else 0

        # Tier-based restocking-fee discount (Gold 50%, Silver 25%). Added back
        # to the refund so agents don't need a separate process_refund call.
        restocking_discount = policies.calculate_restocking_discount(
            restocking_fee=refund["restocking_fee"],
            membership_tier=customer.membership_tier,
        )
        restocking_discount_amount = restocking_discount.get("discount", 0) if restocking_discount.get("applies") else 0

        refund_after_clawback = max(
            0,
            refund["refund_amount"]
            - clawback_amount
            - bulk_clawback_amount
            - repeat_surcharge_amount
            - paid_return_shipping_fee
            + restocking_discount_amount,
        )

        if not confirm:
            self._previewed["return"].add(item_id)
            return {
                "status": "preview",
                "item_id": item_id,
                "return_eligible": True,
                "reason": reason,
                "refund_amount": refund_after_clawback,
                "refund_method": refund["refund_method"],
                "restocking_fee": refund["restocking_fee"],
                "discount_adjustment": refund["discount_adjustment"],
                "free_return_shipping": eligibility.get("free_return_shipping", False),
                "shipping_refund": refund["shipping_refund"],
                "shipping_clawback": clawback_amount,
                "bulk_clawback": bulk_clawback_amount,
                "repeat_surcharge": repeat_surcharge_amount,
                "paid_return_shipping_fee": paid_return_shipping_fee,
                "restocking_discount": restocking_discount_amount,
            }

        # Enforce two-step
        if item_id not in self._previewed["return"]:
            return {"error": "Must preview return before confirming. Call without confirm first."}

        # Agent owns the refund math. They must submit the net `amount` they
        # computed from the preview's component breakdown. The env writes that
        # value verbatim — no auto-compute. Skipping a policy component (Gold
        # discount, shipping clawback, etc.) shows up as a wrong refund_amount
        # in state.
        amount = params.get("amount")
        if amount is None:
            return {
                "error": (
                    "Missing required parameter `amount` for confirm. "
                    "Use the preview response's component breakdown to compute the net refund "
                    "and pass it as `amount`."
                ),
            }
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return {"error": f"`amount` must be an integer, got {amount!r}."}
        if amount < 0:
            return {"error": f"`amount` must be non-negative, got {amount}."}

        # Execute return
        item.item_status = "returned"
        item.return_reason = reason
        item.refund_amount = amount
        item.refund_method = refund["refund_method"]
        item.restocking_fee = refund["restocking_fee"]
        item.return_label_issued = eligibility.get("free_return_shipping", False)
        # Persist the policy constraint when store credit was forced (outside-window
        # grace OR gift return). process_refund's method-update path checks this
        # to prevent the agent from silently flipping back to original_payment.
        item.store_credit_only = bool(store_credit_only) or (order.is_gift and reason == "changed_mind")
        self._previewed["return"].discard(item_id)

        # Update order status. Mixed return+cancellation outcomes should stay
        # partial rather than collapsing to fully_returned.
        order_items = self._get_items_for_order(order.order_id)
        all_terminal = all(i.item_status in ("returned", "cancelled", "exchanged") for i in order_items)
        if all_terminal:
            statuses = {i.item_status for i in order_items}
            if statuses == {"cancelled"}:
                order.status = "cancelled"
            elif statuses <= {"returned", "exchanged"}:
                order.status = "fully_returned"
            else:
                order.status = "partially_cancelled"
        else:
            any_terminal = any(i.item_status in ("returned", "cancelled", "exchanged") for i in order_items)
            any_cancelled = any(i.item_status == "cancelled" for i in order_items)
            if any_terminal:
                order.status = "partially_cancelled" if any_cancelled else "partially_returned"

        return {
            "status": "returned",
            "item_id": item_id,
            "refund_amount": amount,
            "policy_computed_amount": refund_after_clawback,
            "refund_method": refund["refund_method"],
            "restocking_fee": refund["restocking_fee"],
            "return_label_issued": item.return_label_issued,
            "shipping_clawback": clawback_amount,
            "bulk_clawback": bulk_clawback_amount,
            "repeat_surcharge": repeat_surcharge_amount,
            "paid_return_shipping_fee": paid_return_shipping_fee,
            "restocking_discount": restocking_discount_amount,
        }

    def process_refund(self, params: dict[str, Any]) -> dict[str, Any]:
        """Process a refund for an order item. Two-step: preview then confirm."""
        item_id = params.get("item_id", "")
        refund_method = params.get("refund_method", "original_payment")
        amount = params.get("amount", 0)
        confirm = self.parse_bool(params.get("confirm"))

        # Policy gate
        if "refund" not in self._policies_checked:
            return {"error": "Policy review required. Call get_policies(topic='refund') first."}

        item = self.order_items.get(item_id)
        if not item:
            return {"error": f"Item {item_id} not found."}

        if item.item_status == "cancelled":
            return {
                "error": "Refunds for cancelled items are issued automatically by cancel_order — do not call process_refund on this item."
            }

        order = self.orders.get(item.order_id)
        if not order:
            return {"error": f"Order {item.order_id} not found."}

        preview_key = (refund_method, amount)
        if not confirm:
            self._previewed_refunds.setdefault(item_id, set()).add(preview_key)
            self._previewed["refund"].add(item_id)
            return {
                "status": "preview",
                "item_id": item_id,
                "refund_amount": amount,
                "refund_method": refund_method,
            }

        if preview_key not in self._previewed_refunds.get(item_id, set()):
            return {"error": "Must preview refund before confirming. Call without confirm first."}

        if item.refund_amount is None and item.item_status not in ("returned", "exchanged"):
            try:
                refund_amount_int = int(amount)
            except (TypeError, ValueError):
                refund_amount_int = None
            if refund_method == "store_credit" and refund_amount_int == item.unit_price:
                return {
                    "error": (
                        "Cannot issue full-item store credit on an active item with process_refund. "
                        "Use the applicable return or exchange tool so item and order state are updated."
                    ),
                }

        # If a return/cancel already created the base refund, allow the agent to
        # either reissue that same refund to a different method or add a separate
        # supplemental credit/refund. Supplemental amounts must persist so replay
        # GT can verify every state-mutating monetary action.
        reverses_completed_restocking_fee = (
            item.item_status == "returned"
            and item.return_reason == "changed_mind"
            and item.restocking_fee is not None
            and amount == item.restocking_fee
        )
        if item.refund_amount is not None and amount == item.refund_amount:
            # Policy gate: store-credit-only returns (outside-window grace, gift returns)
            # cannot be silently flipped to original_payment. Reject the override.
            if item.store_credit_only and refund_method != "store_credit":
                return {
                    "error": (
                        "Cannot change refund method to original_payment: this return was issued "
                        "under store-credit-only policy (outside return window or gift return). "
                        "The store credit must remain. See get_policies(topic='refund')."
                    ),
                }
            item.refund_method = refund_method
            effective_amount = item.refund_amount
            mode = "refund_method_update"
        elif item.refund_amount is not None and refund_method == item.refund_method:
            if reverses_completed_restocking_fee:
                return {
                    "error": (
                        "Cannot issue a supplemental refund equal to the completed changed-mind "
                        "restocking fee. Completed changed-mind restocking fees are not reversed "
                        "later as courtesy or supplemental refunds."
                    )
                }
            item.goodwill_credit = (item.goodwill_credit or 0) + amount
            item.goodwill_credit_method = refund_method
            effective_amount = amount
            mode = "supplemental_refund"
        elif item.refund_amount is not None:
            if reverses_completed_restocking_fee:
                return {
                    "error": (
                        "Cannot issue a supplemental refund equal to the completed changed-mind "
                        "restocking fee. Completed changed-mind restocking fees are not reversed "
                        "later as courtesy or supplemental refunds."
                    )
                }
            item.goodwill_credit = (item.goodwill_credit or 0) + amount
            item.goodwill_credit_method = refund_method
            effective_amount = amount
            mode = "goodwill_credit"
        else:
            item.refund_amount = amount
            item.refund_method = refund_method
            effective_amount = amount
            mode = "refund"
        item_previews = self._previewed_refunds.get(item_id)
        if item_previews is not None:
            item_previews.discard(preview_key)
            if not item_previews:
                self._previewed_refunds.pop(item_id, None)
                self._previewed["refund"].discard(item_id)
        if order.payment_method == "split" and refund_method == "original_payment":
            split_refund = policies.calculate_split_refund(order.payment_details, effective_amount)
            return {
                "status": "refunded",
                "item_id": item_id,
                "mode": mode,
                "refund_amount": effective_amount,
                "refund_method": "split",
                "split_details": split_refund,
                "total_goodwill_credit": item.goodwill_credit,
            }

        return {
            "status": "refunded",
            "item_id": item_id,
            "mode": mode,
            "refund_amount": effective_amount,
            "refund_method": refund_method,
            "total_goodwill_credit": item.goodwill_credit,
        }

    def cancel_order(self, params: dict[str, Any]) -> dict[str, Any]:
        """Cancel an order or specific items. Two-step: preview then confirm."""
        order_id = params.get("order_id", "")
        item_ids = params.get("item_ids")
        confirm = self.parse_bool(params.get("confirm"))

        # Policy gate
        if "cancellation" not in self._policies_checked:
            return {"error": "Policy review required. Call get_policies(topic='cancellation') first."}

        order = self.orders.get(order_id)
        if not order:
            return {"error": f"Order {order_id} not found."}

        order_items = self._get_items_for_order(order_id)
        if not order_items:
            return {"error": f"No items found for order {order_id}."}

        # Determine which items to cancel
        if item_ids:
            target_items = [i for i in order_items if i.item_id in item_ids]
            if len(target_items) != len(item_ids):
                found = {i.item_id for i in target_items}
                missing = [iid for iid in item_ids if iid not in found]
                return {"error": f"Items not found in order: {missing}"}
            item_statuses = [i.item_status for i in target_items]
        else:
            target_items = order_items
            item_statuses = [i.item_status for i in order_items]
            item_ids = [i.item_id for i in order_items]

        eligibility = policies.check_cancellation_eligibility(
            order_status=order.status,
            shipping_status=order.shipping_status,
            item_statuses=item_statuses,
            item_ids_to_cancel=item_ids,
            total_items=len(order_items),
        )

        if not eligibility["eligible"]:
            return {"status": "rejected", "order_id": order_id, "reason": eligibility["reason"]}

        cancel_fee = eligibility["cancellation_fee"]
        # Compute refund: sum of item prices minus fee
        items_total = sum(i.unit_price for i in target_items)
        refund_amount = items_total - cancel_fee

        if not confirm:
            self._previewed["cancel"].add(order_id)
            return {
                "status": "preview",
                "order_id": order_id,
                "items_to_cancel": [i.item_id for i in target_items],
                "cancellation_fee": cancel_fee,
                "refund_amount": refund_amount,
                "reason": eligibility["reason"],
            }

        if order_id not in self._previewed["cancel"]:
            return {"error": "Must preview cancellation before confirming. Call without confirm first."}

        # Execute cancellation. Prorate the cancellation fee across items
        # so per-item refund_amount fields reflect the post-fee payout.
        self._previewed["cancel"].discard(order_id)
        n = len(target_items)
        per_item_fee = cancel_fee // n if n else 0
        fee_remainder = cancel_fee - per_item_fee * n
        # Cancellation refunds always go back to the order's payment method.
        # Split-payment orders prorate across methods (the response below carries
        # split_details); per-item state records "split" as a marker.
        cancel_refund_method = "split" if order.payment_method == "split" else order.payment_method
        for idx, item in enumerate(target_items):
            item.item_status = "cancelled"
            item_fee = per_item_fee + (1 if idx < fee_remainder else 0)
            item.refund_amount = item.unit_price - item_fee
            item.refund_method = cancel_refund_method

        # Update order status
        all_items = self._get_items_for_order(order_id)
        all_cancelled = all(i.item_status == "cancelled" for i in all_items)
        if all_cancelled:
            order.status = "cancelled"
        else:
            any_cancelled = any(i.item_status == "cancelled" for i in all_items)
            if any_cancelled:
                order.status = "partially_cancelled"

        # Handle split payment refund
        refund_details: dict[str, Any] = {
            "refund_amount": refund_amount,
            "refund_method": order.payment_method,
        }
        if order.payment_method == "split":
            refund_details["split_details"] = policies.calculate_split_refund(order.payment_details, refund_amount)

        return {
            "status": "cancelled",
            "order_id": order_id,
            "items_cancelled": [i.item_id for i in target_items],
            "cancellation_fee": cancel_fee,
            **refund_details,
        }

    def process_exchange(self, params: dict[str, Any]) -> dict[str, Any]:
        """Exchange an item for a different product/variant. Two-step: preview then confirm."""
        item_id = params.get("item_id", "")
        new_product_id = params.get("new_product_id", "")
        confirm = self.parse_bool(params.get("confirm"))

        # Policy gate
        if "exchange" not in self._policies_checked:
            return {"error": "Policy review required. Call get_policies(topic='exchange') first."}

        item = self.order_items.get(item_id)
        if not item:
            return {"error": f"Item {item_id} not found."}

        order = self.orders.get(item.order_id)
        if not order:
            return {"error": f"Order {item.order_id} not found."}

        old_product = self.products.get(item.product_id)
        new_product = self.products.get(new_product_id)
        if not new_product:
            # Try name-based lookup
            pid_lower = new_product_id.lower().strip()
            for p in self.products.values():
                if p.name.lower() == pid_lower or pid_lower in p.name.lower() or p.name.lower() in pid_lower:
                    new_product = p
                    new_product_id = p.product_id
                    break
        if not old_product:
            return {"error": f"Original product {item.product_id} not found."}
        if not new_product:
            return {"error": f"New product {new_product_id} not found."}

        if new_product.product_id == item.product_id:
            return {
                "status": "rejected",
                "item_id": item_id,
                "reason": (
                    "Cannot exchange an item for the same product. "
                    "Choose a different product to exchange to, or use process_return for a refund."
                ),
            }

        customer = self.customers.get(order.customer_id)
        if not customer:
            return {"error": f"Customer {order.customer_id} not found."}

        exchange = policies.calculate_exchange(
            original_item_price=item.unit_price,
            new_product_price=new_product.price,
            new_product_in_stock=new_product.in_stock,
            category=old_product.category,
            delivery_date=order.delivery_date,
            now=self.now,
            return_window_days=old_product.return_window_days,
            membership_tier=customer.membership_tier,
            has_prime_shipping=customer.has_prime_shipping,
        )

        if not exchange["eligible"]:
            return {"status": "rejected", "item_id": item_id, "reason": exchange["reason"]}

        if exchange.get("out_of_stock"):
            if not confirm:
                self._previewed["exchange"].add(item_id)
                return {
                    "status": "preview",
                    "item_id": item_id,
                    "new_product_id": new_product_id,
                    "out_of_stock": True,
                    "store_credit_amount": exchange["store_credit_amount"],
                    "refund_method": "store_credit",
                    "reason": exchange["reason"],
                }
            if item_id not in self._previewed["exchange"]:
                return {"error": "Must preview exchange before confirming. Call without confirm first."}
            self._previewed["exchange"].discard(item_id)
            item.item_status = "returned"
            item.return_reason = "changed_mind"
            item.refund_amount = exchange["store_credit_amount"]
            item.refund_method = "store_credit"
            item.restocking_fee = 0
            item.return_label_issued = False
            order_items = self._get_items_for_order(order.order_id)
            if all(i.item_status in ("returned", "cancelled", "exchanged") for i in order_items):
                order.status = "fully_returned"
            else:
                order.status = "partially_returned"
            return {
                "status": "store_credit_issued",
                "item_id": item_id,
                "new_product_id": new_product_id,
                "out_of_stock": True,
                "refund_amount": exchange["store_credit_amount"],
                "refund_method": "store_credit",
                "reason": exchange["reason"],
            }

        if not confirm:
            self._previewed["exchange"].add(item_id)
            return {
                "status": "preview",
                "item_id": item_id,
                "new_product_id": new_product_id,
                "price_difference": exchange["price_difference"],
                "customer_pays": exchange.get("customer_pays", 0),
                "store_credit_refund": exchange.get("store_credit_refund", 0),
                "reason": exchange["reason"],
            }

        if item_id not in self._previewed["exchange"]:
            return {"error": "Must preview exchange before confirming. Call without confirm first."}

        # Execute exchange
        self._previewed["exchange"].discard(item_id)
        item.item_status = "exchanged"
        store_credit_refund = exchange.get("store_credit_refund", 0)
        if store_credit_refund > 0:
            item.refund_amount = store_credit_refund
            item.refund_method = "store_credit"

        # Create replacement item
        new_item_id = _next_item_id(self.order_items)
        new_item = OrderItem(
            item_id=new_item_id,
            order_id=order.order_id,
            product_id=new_product_id,
            quantity=item.quantity,
            unit_price=new_product.price,
            item_status="confirmed",
        )
        self.order_items[new_item_id] = new_item
        item.replacement_item_id = new_item_id

        return {
            "status": "exchanged",
            "item_id": item_id,
            "new_item_id": new_item_id,
            "new_product_id": new_product_id,
            "price_difference": exchange["price_difference"],
            "customer_pays": exchange.get("customer_pays", 0),
            "store_credit_refund": exchange.get("store_credit_refund", 0),
        }

    def process_warranty_claim(self, params: dict[str, Any]) -> dict[str, Any]:
        """File a warranty claim. Two-step: preview then confirm."""
        warranty_id = params.get("warranty_id", "")
        item_id = params.get("item_id", "")
        _issue_desc = params.get("issue_description", "")  # logged but not used in policy logic
        confirm = self.parse_bool(params.get("confirm"))

        # Policy gate
        if "warranty" not in self._policies_checked:
            return {"error": "Policy review required. Call get_policies(topic='warranty') first."}

        warranty = self.warranties.get(warranty_id)
        if not warranty:
            return {"error": f"Warranty {warranty_id} not found."}

        item = self.order_items.get(item_id)
        if not item:
            return {"error": f"Item {item_id} not found."}

        if item.item_status in ("returned", "exchanged", "cancelled"):
            return {
                "error": (
                    f"Cannot file warranty claim on item {item_id} because item_status is "
                    f"{item.item_status!r}. Warranty claims require an active item."
                )
            }

        claim = policies.check_warranty_claim(
            warranty_type=warranty.warranty_type,
            warranty_start=warranty.start_date,
            warranty_end=warranty.end_date,
            now=self.now,
            claim_count=warranty.claim_count,
            max_claims=warranty.max_claims,
            item_price=item.unit_price,
        )

        if not confirm:
            self._previewed["warranty_claim"].add(warranty_id)
            return {
                "status": "preview",
                "warranty_id": warranty_id,
                "item_id": item_id,
                "eligible": claim["eligible"],
                "resolution": claim.get("resolution"),
                "cost": claim.get("cost", 0),
                "reason": claim["reason"],
            }

        if warranty_id not in self._previewed["warranty_claim"]:
            return {"error": "Must preview warranty claim before confirming. Call without confirm first."}

        if not claim["eligible"] and claim.get("resolution") != "paid_repair":
            return {"status": "rejected", "reason": claim["reason"]}

        # Execute claim
        self._previewed["warranty_claim"].discard(warranty_id)
        warranty.claim_count += 1
        warranty.status = "claimed"

        resolution = claim.get("resolution", "repair")
        warranty.resolution = resolution

        # Create replacement item if resolution is replacement
        replacement_id = None
        if "replacement" in resolution:
            new_item_id = _next_item_id(self.order_items)
            new_item = OrderItem(
                item_id=new_item_id,
                order_id=item.order_id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                item_status="confirmed",
            )
            self.order_items[new_item_id] = new_item
            item.replacement_item_id = new_item_id
            replacement_id = new_item_id

        return {
            "status": "claimed",
            "warranty_id": warranty_id,
            "item_id": item_id,
            "resolution": resolution,
            "cost": claim.get("cost", 0),
            "replacement_item_id": replacement_id,
            "claim_count": warranty.claim_count,
        }

    # -------------------------------------------------------------------
    # Tool handler registry
    # -------------------------------------------------------------------

    @property
    def tool_handlers(self) -> dict[str, Any]:
        return {
            "get_order": self.get_order,
            "get_customer": self.get_customer,
            "search_products": self.search_products,
            "get_product_details": self.get_product_details,
            "get_policies": self.get_policies,
            "get_warranty_status": self.get_warranty_status,
            "process_return": self.process_return,
            "process_refund": self.process_refund,
            "cancel_order": self.cancel_order,
            "process_exchange": self.process_exchange,
            "process_warranty_claim": self.process_warranty_claim,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_item_id(order_items: dict[str, OrderItem]) -> str:
    """Generate next sequential item ID."""
    existing_nums = []
    for iid in order_items:
        if iid.startswith("ITEM-"):
            try:
                existing_nums.append(int(iid.split("-")[1]))
            except (ValueError, IndexError):
                pass
    next_num = max(existing_nums, default=8000) + 1
    return f"ITEM-{next_num}"
