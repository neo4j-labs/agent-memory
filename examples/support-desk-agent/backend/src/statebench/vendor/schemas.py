# Copied from microsoft/STATE-Bench @ 5644b1838d96, state_bench/domains/customer_support/schemas.py
# (MIT License, Copyright (c) 2026 STATE-Bench contributors; see LICENSE in this
# directory). Only the imports were changed, to point at this package.
"""Core data models for the customer support e-commerce benchmark."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.statebench.vendor.base import DictMixin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database records
# ---------------------------------------------------------------------------


@dataclass
class Product(DictMixin):
    product_id: str  # e.g. "PROD-1001"
    name: str
    category: str  # electronics | clothing | kitchen | books | accessories
    subcategory: str  # laptop | phone | headphones | blender | shirt | novel
    price: int  # current price in dollars
    warranty_months: int  # manufacturer warranty duration
    return_window_days: int  # category-specific return window
    restocking_fee_pct: int  # 0 or 15 (for opened electronics)
    weight_lbs: float
    is_fragile: bool
    in_stock: bool = True
    current_price: int | None = None  # sale price if different from price


@dataclass
class Customer(DictMixin):
    customer_id: str  # e.g. "cust_001"
    name: str
    email: str
    membership_tier: str = "standard"  # standard | silver | gold | platinum
    account_created: str = ""  # ISO date
    total_orders: int = 0
    preferred_refund_method: str = "original_payment"  # original_payment | store_credit
    store_credit_balance: int = 0
    has_prime_shipping: bool = False  # free returns, extended windows


@dataclass
class Order(DictMixin):
    order_id: str  # e.g. "ORD-5001"
    customer_id: str
    order_date: str  # ISO datetime
    status: str = "confirmed"  # confirmed | processing | shipped | delivered | partially_returned | fully_returned | partially_cancelled | cancelled
    shipping_status: str = "pending"  # pending | in_transit | delivered | lost | damaged
    shipping_method: str = "standard"  # standard | express | overnight
    shipping_cost: int = 0
    tracking_number: str = ""
    delivery_date: str | None = None  # ISO datetime, None if not delivered
    delivery_promised_date: str = ""  # ISO datetime
    signature_required: bool = False
    signature_on_file: str | None = None  # signer name if signed
    payment_method: str = "credit_card"  # credit_card | debit_card | gift_card | split
    payment_details: dict[str, int] = field(default_factory=dict)  # e.g. {"credit_card": 150, "gift_card": 50}
    subtotal: int = 0  # sum of item prices before discount
    discount_code: str | None = None
    discount_amount: int = 0  # dollar amount of discount
    total_paid: int = 0  # subtotal - discount + shipping
    is_gift: bool = False
    gift_sender: str | None = None


@dataclass
class OrderItem(DictMixin):
    item_id: str  # e.g. "ITEM-8001"
    order_id: str  # FK to Order
    product_id: str  # FK to Product
    quantity: int = 1
    unit_price: int = 0  # price at time of purchase
    item_status: str = "confirmed"  # confirmed | shipped | delivered | return_requested | returned | exchange_requested | exchanged | cancelled
    return_reason: str | None = (
        None  # defective | wrong_item | not_as_described | changed_mind | damaged_in_transit | missing
    )
    refund_amount: int | None = None
    refund_method: str | None = None  # original_payment | store_credit
    restocking_fee: int | None = None
    return_label_issued: bool = False
    replacement_item_id: str | None = None  # links to replacement if exchanged/replaced
    goodwill_credit: int = 0  # additive credits applied via process_refund after a return
    goodwill_credit_method: str | None = None  # original_payment | store_credit
    store_credit_only: bool = False  # True when policy forced store_credit at return time
    # (outside-window grace, gift returns) — process_refund must not flip the method.


@dataclass
class Warranty(DictMixin):
    warranty_id: str  # e.g. "WRT-3001"
    order_id: str
    item_id: str
    product_id: str
    warranty_type: str = "manufacturer"  # manufacturer | extended | store
    start_date: str = ""  # ISO date
    end_date: str = ""  # ISO date
    status: str = "active"  # active | expired | claimed | voided
    claim_count: int = 0
    max_claims: int = 3
    coverage: str = "repair_or_replace"  # full_replacement | repair | repair_or_replace
    resolution: str | None = None  # set when claimed: repair | full_replacement | discounted_repair | paid_repair


# ---------------------------------------------------------------------------
# Environment snapshot
# ---------------------------------------------------------------------------


@dataclass
class CSEnvironmentData:
    """Full environment state for customer support domain."""

    products: list[Product]
    orders: list[Order]
    order_items: list[OrderItem]
    customers: list[Customer]
    warranties: list[Warranty]

    def to_dict(self) -> dict[str, Any]:
        return {
            "products": [p.to_dict() for p in self.products],
            "orders": [o.to_dict() for o in self.orders],
            "order_items": [i.to_dict() for i in self.order_items],
            "customers": [c.to_dict() for c in self.customers],
            "warranties": [w.to_dict() for w in self.warranties],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CSEnvironmentData:
        return cls(
            products=[Product.from_dict(p) for p in data["products"]],
            orders=[Order.from_dict(o) for o in data["orders"]],
            order_items=[OrderItem.from_dict(i) for i in data["order_items"]],
            customers=[Customer.from_dict(c) for c in data["customers"]],
            warranties=[Warranty.from_dict(w) for w in data.get("warranties", [])],
        )

    def deep_copy(self) -> CSEnvironmentData:
        return CSEnvironmentData.from_dict(copy.deepcopy(self.to_dict()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(
            "Saved environment: %s products, %s orders, %s items, %s customers, %s warranties -> %s",
            len(self.products),
            len(self.orders),
            len(self.order_items),
            len(self.customers),
            len(self.warranties),
            path,
        )

    @classmethod
    def load(cls, path: Path) -> CSEnvironmentData:
        with open(path) as f:
            return cls.from_dict(json.load(f))
