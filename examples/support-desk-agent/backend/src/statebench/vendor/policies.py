# Copied from microsoft/STATE-Bench @ 5644b1838d96, state_bench/domains/customer_support/policies.py
# (MIT License, Copyright (c) 2026 STATE-Bench contributors; see LICENSE in this
# directory). Only the imports were changed, to point at this package.
"""Deterministic policy engine for the customer support domain.

All functions are pure — no randomness, no side effects.
Given the same inputs, they always produce the same outputs.
These same functions define the policy math used by the runtime environment
and checked-in task expectations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Return windows by category (days)
RETURN_WINDOWS: dict[str, int] = {
    "electronics": 15,
    "clothing": 30,
    "kitchen": 30,
    "books": 14,
    "accessories": 30,
}

# Extended return windows by membership tier (additional days)
TIER_RETURN_EXTENSION: dict[str, int] = {
    "standard": 0,
    "silver": 0,
    "gold": 15,
    "platinum": 15,
}

# Prime shipping members get extra days
PRIME_RETURN_EXTENSION: int = 15

# Restocking fee percentage for opened electronics
RESTOCKING_FEE_PCT: int = 15

# Cancellation intercept fee per item (post-shipment)
INTERCEPT_FEE_PER_ITEM: int = 10

# Warranty manufacturer default (months)
DEFAULT_WARRANTY_MONTHS: dict[str, int] = {
    "electronics": 12,
    "kitchen": 6,
    "clothing": 0,
    "books": 0,
    "accessories": 3,
}

# Compensation tiers for late delivery
LATE_DELIVERY_COMPENSATION: list[tuple[int, int, int]] = [
    # (min_days_late, max_days_late, credit_amount)
    (1, 2, 5),
    (3, 5, 15),
    (6, 999, 15),  # 6+ days: shipping refund + $15 (shipping refund handled separately)
]

# Compensation tier multipliers
TIER_COMPENSATION_MULTIPLIER: dict[str, float] = {
    "standard": 1.0,
    "silver": 1.0,
    "gold": 1.5,
    "platinum": 2.0,
}

# High-value threshold for shipping investigations
HIGH_VALUE_THRESHOLD: int = 500

# Warranty repair vs replace price threshold
REPAIR_VS_REPLACE_THRESHOLD: int = 100

# Recurring defect auto-replace after N claims
RECURRING_DEFECT_THRESHOLD: int = 2

# Warranty discount for recently expired (within 30 days)
EXPIRED_RECENT_REPAIR_DISCOUNT: float = 0.50  # 50% off repair

# Warranty discount for long-expired replacement
EXPIRED_OLD_REPLACEMENT_DISCOUNT: float = 0.25  # 25% off replacement


# ---------------------------------------------------------------------------
# 1. Return Eligibility
# ---------------------------------------------------------------------------


def check_return_eligibility(
    category: str,
    delivery_date: str | None,
    now: str,
    item_status: str,
    return_reason: str,
    membership_tier: str,
    has_prime_shipping: bool,
    order_date: str | None = None,
) -> dict[str, Any]:
    """Check whether an item is eligible for return.

    Rules:
    - Category-specific return windows (electronics 15d, clothing 30d, etc.)
    - Gold/platinum members: +15 days
    - Prime shipping members: +15 days (stacks with tier)
    - Defective/wrong_item/damaged_in_transit: no window restriction (within warranty)
    - Already returned/exchanged: ineligible
    - Not yet delivered: ineligible for return (use cancellation)
    - Holiday seasonal extension: Nov/Dec orders get window extended to Jan 31
      (only applied when order_date is provided, and only if normal window has expired)
    """
    # Already processed
    if item_status in ("returned", "exchanged", "cancelled"):
        return {"eligible": False, "reason": f"Item already {item_status}"}

    # Not delivered yet
    if delivery_date is None:
        return {"eligible": False, "reason": "Item not yet delivered — use cancellation instead"}

    # Defective/wrong/damaged — no window restriction
    if return_reason in ("defective", "wrong_item", "damaged_in_transit"):
        return {
            "eligible": True,
            "reason": f"Return eligible: {return_reason} (no window restriction)",
            "free_return_shipping": True,
            "restocking_fee_applies": False,
        }

    # Calculate effective return window
    base_window = RETURN_WINDOWS.get(category, 30)
    tier_extension = TIER_RETURN_EXTENSION.get(membership_tier, 0)
    prime_extension = PRIME_RETURN_EXTENSION if has_prime_shipping else 0
    effective_window = base_window + tier_extension + prime_extension

    # Calculate days since delivery
    delivery_dt = datetime.fromisoformat(delivery_date)
    now_dt = datetime.fromisoformat(now)
    days_since_delivery = (now_dt - delivery_dt).days

    if days_since_delivery > effective_window:
        # Seasonal extension (Nov/Dec orders → Jan 31) takes precedence over
        # the store-credit-only window when it applies.
        if order_date is not None:
            base_window_end = (delivery_dt + timedelta(days=effective_window)).isoformat()
            seasonal = check_seasonal_return_extension(
                order_date=order_date,
                now=now,
                base_window_end=base_window_end,
            )
            if seasonal["applies"]:
                return {
                    "eligible": True,
                    "reason": seasonal["reason"],
                    "free_return_shipping": False,
                    "restocking_fee_applies": category == "electronics" and return_reason == "changed_mind",
                    "store_credit_only": False,
                    "seasonal_extension_applied": True,
                }

        # Check if within extended store-credit-only window (effective_window + 15 days)
        store_credit_window = effective_window + 15
        if days_since_delivery <= store_credit_window:
            return {
                "eligible": True,
                "reason": f"Outside {effective_window}-day return window but within {store_credit_window}-day store credit window",
                "free_return_shipping": False,
                "restocking_fee_applies": category == "electronics",
                "store_credit_only": True,
            }
        return {
            "eligible": False,
            "reason": f"Outside {store_credit_window}-day return window ({days_since_delivery} days since delivery)",
        }

    return {
        "eligible": True,
        "reason": f"Within {effective_window}-day return window ({days_since_delivery} days since delivery)",
        "free_return_shipping": False,
        "restocking_fee_applies": category == "electronics" and return_reason == "changed_mind",
    }


# ---------------------------------------------------------------------------
# 2. Refund Calculation
# ---------------------------------------------------------------------------


def calculate_refund(
    item_price: int,
    return_reason: str,
    category: str,
    discount_code: str | None,
    discount_amount: int,
    order_subtotal: int,
    membership_tier: str,
    is_gift_return: bool,
    current_product_price: int | None,
    store_credit_only: bool = False,
) -> dict[str, Any]:
    """Calculate the refund amount for a return.

    Rules:
    - Full refund: defective, wrong_item, damaged_in_transit
    - Restocking fee: 15% for opened electronics (changed_mind), waived for platinum
    - Promo redistribution: discount allocated proportionally by item price
      refund = item_price - (discount_amount * item_price / order_subtotal)
    - Gift return (no receipt): store credit at current product price
    - Outside window: store credit only (if eligible at all)
    - Shipping: refunded for defective/wrong_item, NOT for changed_mind
    """
    # Gift return — store credit at current (possibly lower) price
    if is_gift_return:
        credit_price = current_product_price if current_product_price is not None else item_price
        return {
            "refund_amount": credit_price,
            "refund_method": "store_credit",
            "restocking_fee": 0,
            "discount_adjustment": 0,
            "shipping_refund": False,
        }

    # Calculate restocking fee
    restocking_fee = 0
    if category == "electronics" and return_reason == "changed_mind":
        restocking_fee = int(item_price * RESTOCKING_FEE_PCT / 100)
        # Platinum waiver
        if membership_tier == "platinum":
            restocking_fee = 0

    # Calculate discount adjustment (promo redistribution)
    discount_adjustment = 0
    if discount_code and discount_amount > 0 and order_subtotal > 0:
        # Proportional discount allocation to this item
        discount_adjustment = int(discount_amount * item_price / order_subtotal)

    # Base refund
    refund_amount = item_price - restocking_fee - discount_adjustment

    # Determine refund method
    if store_credit_only:
        refund_method = "store_credit"
    elif return_reason in ("defective", "wrong_item", "damaged_in_transit"):
        refund_method = "original_payment"
    else:
        refund_method = "original_payment"  # default, customer can choose

    # Shipping refund for defective/wrong item
    shipping_refund = return_reason in ("defective", "wrong_item", "damaged_in_transit")

    return {
        "refund_amount": max(0, refund_amount),
        "refund_method": refund_method,
        "restocking_fee": restocking_fee,
        "discount_adjustment": discount_adjustment,
        "shipping_refund": shipping_refund,
    }


# ---------------------------------------------------------------------------
# 3. Cancellation Eligibility
# ---------------------------------------------------------------------------


def check_cancellation_eligibility(
    order_status: str,
    shipping_status: str,
    item_statuses: list[str],
    item_ids_to_cancel: list[str] | None,
    total_items: int,
) -> dict[str, Any]:
    """Check whether an order (or specific items) can be cancelled.

    Rules:
    - Pre-shipment (pending/processing): free cancellation, full refund
    - In-transit: $10 intercept fee per item
    - Delivered: cannot cancel — must use return
    - Partial cancel: allowed if items not yet delivered
    - Already cancelled: ineligible
    """
    if order_status == "cancelled":
        return {"eligible": False, "reason": "Order already cancelled"}

    if shipping_status == "delivered":
        return {"eligible": False, "reason": "Order already delivered — please use the return process"}

    # Count items to cancel
    cancel_count = len(item_ids_to_cancel) if item_ids_to_cancel else total_items

    # Check if any target items are already cancelled/returned
    already_done = [s for s in item_statuses if s in ("cancelled", "returned", "exchanged")]
    if already_done and not item_ids_to_cancel:
        return {"eligible": False, "reason": f"Some items already processed ({len(already_done)} of {total_items})"}

    if shipping_status in ("pending", "processing"):
        return {
            "eligible": True,
            "reason": "Pre-shipment cancellation — no fee",
            "cancellation_fee": 0,
            "items_to_cancel": cancel_count,
        }

    if shipping_status == "in_transit":
        fee = INTERCEPT_FEE_PER_ITEM * cancel_count
        return {
            "eligible": True,
            "reason": f"In-transit cancellation — ${INTERCEPT_FEE_PER_ITEM} intercept fee per item",
            "cancellation_fee": fee,
            "items_to_cancel": cancel_count,
        }

    if shipping_status == "lost":
        return {
            "eligible": True,
            "reason": "Package lost — free cancellation with full refund",
            "cancellation_fee": 0,
            "items_to_cancel": cancel_count,
        }

    return {"eligible": False, "reason": f"Cannot cancel with shipping status: {shipping_status}"}


# ---------------------------------------------------------------------------
# 4. Exchange Calculation
# ---------------------------------------------------------------------------


def calculate_exchange(
    original_item_price: int,
    new_product_price: int,
    new_product_in_stock: bool,
    category: str,
    delivery_date: str | None,
    now: str,
    return_window_days: int,
    membership_tier: str,
    has_prime_shipping: bool,
) -> dict[str, Any]:
    """Calculate exchange terms.

    Rules:
    - Different product, more expensive: customer pays difference
    - Different product, cheaper or same price: difference (or $0) to store credit
    - Out of stock: issue store credit for the original item; do not complete the exchange
    - Must be within return window (same as return eligibility)
    - No price protection on exchanges
    - Self-swap (new_product_id == old_product_id) is rejected upstream in env.process_exchange,
      not here — this function only sees prices.
    """
    if delivery_date is None:
        return {"eligible": False, "reason": "Item not yet delivered"}

    # Check return window for exchange eligibility
    tier_extension = TIER_RETURN_EXTENSION.get(membership_tier, 0)
    prime_extension = PRIME_RETURN_EXTENSION if has_prime_shipping else 0
    effective_window = return_window_days + tier_extension + prime_extension

    delivery_dt = datetime.fromisoformat(delivery_date)
    now_dt = datetime.fromisoformat(now)
    days_since_delivery = (now_dt - delivery_dt).days

    if days_since_delivery > effective_window:
        return {
            "eligible": False,
            "reason": f"Outside {effective_window}-day exchange window ({days_since_delivery} days since delivery)",
        }

    if not new_product_in_stock:
        return {
            "eligible": True,
            "reason": "Requested item out of stock — store credit is available for the original item",
            "out_of_stock": True,
            "store_credit_amount": original_item_price,
        }

    price_difference = new_product_price - original_item_price

    if price_difference > 0:
        return {
            "eligible": True,
            "reason": f"Exchange for more expensive item — customer pays ${price_difference}",
            "price_difference": price_difference,
            "customer_pays": price_difference,
            "store_credit_refund": 0,
            "restocking_fee": 0,
        }

    if price_difference == 0:
        return {
            "eligible": True,
            "reason": "Exchange for same-price item — no charge, no refund",
            "price_difference": 0,
            "customer_pays": 0,
            "store_credit_refund": 0,
            "restocking_fee": 0,
        }

    # Cheaper product — refund difference to store credit
    return {
        "eligible": True,
        "reason": f"Exchange for cheaper item — ${abs(price_difference)} to store credit",
        "price_difference": price_difference,
        "customer_pays": 0,
        "store_credit_refund": abs(price_difference),
        "restocking_fee": 0,
    }


# ---------------------------------------------------------------------------
# 5. Warranty Claim
# ---------------------------------------------------------------------------


def check_warranty_claim(
    warranty_type: str,
    warranty_start: str,
    warranty_end: str,
    now: str,
    claim_count: int,
    max_claims: int,
    item_price: int,
) -> dict[str, Any]:
    """Check warranty claim eligibility and determine resolution.

    Rules:
    - Active warranty: eligible
    - Expired < 30 days: discounted repair (50% off)
    - Expired > 30 days: full-price repair or 25% off replacement
    - Claim limit reached: paid repair only
    - Item < $100: replace. Item >= $100: repair first
    - Recurring defect (2+ prior claims): auto-replacement
    """
    now_dt = datetime.fromisoformat(now)
    end_dt = datetime.fromisoformat(warranty_end)
    days_past_expiry = (now_dt - end_dt).days

    # Check claim limits
    if claim_count >= max_claims:
        return {
            "eligible": False,
            "reason": f"Maximum claims ({max_claims}) reached — paid repair only",
            "resolution": "paid_repair",
            "cost": int(item_price * 0.4),  # 40% of item price for repair
        }

    # Recurring defect — auto-replace
    if claim_count >= RECURRING_DEFECT_THRESHOLD:
        if days_past_expiry <= 0:
            return {
                "eligible": True,
                "reason": f"Recurring defect ({claim_count} prior claims) — automatic replacement",
                "resolution": "full_replacement",
                "cost": 0,
            }

    # Active warranty
    if days_past_expiry <= 0:
        if item_price < REPAIR_VS_REPLACE_THRESHOLD:
            resolution = "full_replacement"
        else:
            resolution = "repair"
        return {
            "eligible": True,
            "reason": f"Active {warranty_type} warranty — {resolution}",
            "resolution": resolution,
            "cost": 0,
        }

    # Recently expired (within 30 days)
    if days_past_expiry <= 30:
        repair_cost = int(item_price * 0.4 * (1 - EXPIRED_RECENT_REPAIR_DISCOUNT))
        return {
            "eligible": True,
            "reason": f"Warranty expired {days_past_expiry} days ago — discounted repair available",
            "resolution": "discounted_repair",
            "cost": repair_cost,
            "discount": f"{int(EXPIRED_RECENT_REPAIR_DISCOUNT * 100)}%",
        }

    # Long expired
    repair_cost = int(item_price * 0.4)
    replacement_cost = int(item_price * (1 - EXPIRED_OLD_REPLACEMENT_DISCOUNT))
    return {
        "eligible": True,
        "reason": f"Warranty expired {days_past_expiry} days ago — paid repair or discounted replacement",
        "resolution": "paid_repair_or_discounted_replacement",
        "repair_cost": repair_cost,
        "replacement_cost": replacement_cost,
        "replacement_discount": f"{int(EXPIRED_OLD_REPLACEMENT_DISCOUNT * 100)}%",
    }


# ---------------------------------------------------------------------------
# 6. Shipping Claim
# ---------------------------------------------------------------------------


def check_shipping_claim(
    shipping_status: str,
    order_total: int,
    delivery_date: str | None,
    delivery_promised_date: str,
    now: str,
    signature_required: bool,
    signature_on_file: str | None,
    is_fragile: bool,
    tracking_number: str,
) -> dict[str, Any]:
    """Evaluate a shipping-related claim.

    Rules:
    - Delivered but not received:
      - Signature on file: deny claim
      - Order < $500: reship or refund (customer choice)
      - Order >= $500: mandatory investigation (3-5 business days)
    - Lost in transit (no tracking update 7+ days):
      - < $500: immediate reship or refund
      - >= $500: carrier claim first
    - Damaged in transit:
      - Full refund or replacement
      - Fragile item: +$10 goodwill credit
    - Late delivery: see calculate_compensation
    """
    now_dt = datetime.fromisoformat(now)

    # Damaged
    if shipping_status == "damaged":
        goodwill = 10 if is_fragile else 0
        return {
            "claim_type": "damaged",
            "resolution": "refund_or_replacement",
            "eligible": True,
            "goodwill_credit": goodwill,
            "reason": "Item damaged in transit" + (" — fragile item goodwill credit applied" if goodwill else ""),
        }

    # Lost in transit
    if shipping_status == "lost":
        if order_total >= HIGH_VALUE_THRESHOLD:
            return {
                "claim_type": "lost",
                "resolution": "investigation_required",
                "eligible": True,
                "investigation_days": 5,
                "reason": f"High-value order (${order_total}) lost in transit — carrier investigation required",
            }
        return {
            "claim_type": "lost",
            "resolution": "reship_or_refund",
            "eligible": True,
            "reason": "Package lost in transit — reship or refund available",
        }

    # Delivered but customer says not received
    if shipping_status == "delivered":
        if signature_on_file:
            return {
                "claim_type": "not_received",
                "resolution": "denied",
                "eligible": False,
                "reason": f"Delivery confirmed with signature from '{signature_on_file}' — claim denied",
            }
        if order_total >= HIGH_VALUE_THRESHOLD:
            return {
                "claim_type": "not_received",
                "resolution": "investigation_required",
                "eligible": True,
                "investigation_days": 5,
                "reason": f"High-value order (${order_total}) — investigation required before resolution",
            }
        return {
            "claim_type": "not_received",
            "resolution": "reship_or_refund",
            "eligible": True,
            "reason": "Package marked delivered but not received — reship or refund available",
        }

    # In transit too long (potential lost)
    if shipping_status == "in_transit" and delivery_promised_date:
        promised_dt = datetime.fromisoformat(delivery_promised_date)
        days_overdue = (now_dt - promised_dt).days
        if days_overdue >= 7:
            return {
                "claim_type": "potentially_lost",
                "resolution": "reship_or_refund",
                "eligible": True,
                "reason": f"No delivery update for {days_overdue} days — likely lost",
            }

    return {"eligible": False, "reason": f"No actionable shipping claim for status: {shipping_status}"}


# ---------------------------------------------------------------------------
# 7. Compensation
# ---------------------------------------------------------------------------


def calculate_compensation(
    delivery_date: str | None,
    delivery_promised_date: str,
    now: str,
    order_total: int,
    shipping_cost: int,
    membership_tier: str,
    previous_issues_count: int,
) -> dict[str, Any]:
    """Calculate compensation for late delivery or repeated issues.

    Rules:
    - Late 1-2 days: $5 credit
    - Late 3-5 days: $15 credit
    - Late 6+ days: full shipping refund + $15 credit
    - Repeated issues (3+ in 6 months): +$25 goodwill
    - Gold: 1.5x, Platinum: 2x compensation
    - Max: 50% of order total
    """
    if delivery_date is None:
        return {"eligible": False, "reason": "Order not yet delivered"}

    delivery_dt = datetime.fromisoformat(delivery_date)
    promised_dt = datetime.fromisoformat(delivery_promised_date)
    days_late = (delivery_dt - promised_dt).days

    if days_late <= 0:
        return {"eligible": False, "reason": "Order delivered on time or early"}

    # Base credit from tiers
    base_credit = 0
    shipping_refund = 0
    for min_days, max_days, credit in LATE_DELIVERY_COMPENSATION:
        if min_days <= days_late <= max_days:
            base_credit = credit
            if days_late >= 6:
                shipping_refund = shipping_cost
            break

    # Tier multiplier
    multiplier = TIER_COMPENSATION_MULTIPLIER.get(membership_tier, 1.0)
    credit = int(base_credit * multiplier)

    # Repeated issues bonus
    goodwill = 25 if previous_issues_count >= 3 else 0

    # Total compensation
    total = credit + shipping_refund + goodwill

    # Cap at 50% of order total
    max_comp = int(order_total * 0.50)
    total = min(total, max_comp)

    return {
        "eligible": True,
        "days_late": days_late,
        "base_credit": base_credit,
        "tier_multiplier": multiplier,
        "adjusted_credit": credit,
        "shipping_refund": shipping_refund,
        "goodwill_credit": goodwill,
        "total_compensation": total,
        "reason": f"Delivered {days_late} days late — ${total} total compensation",
    }


# ---------------------------------------------------------------------------
# Split payment refund helper
# ---------------------------------------------------------------------------


def calculate_split_refund(
    payment_details: dict[str, int],
    refund_amount: int,
) -> dict[str, int]:
    """Distribute a refund across split payment methods proportionally.

    E.g., paid 75% credit card + 25% gift card → refund split same way.
    """
    total_paid = sum(payment_details.values())
    if total_paid == 0:
        return {}

    refunds: dict[str, int] = {}
    allocated = 0
    methods = sorted(payment_details.keys())

    for i, method in enumerate(methods):
        if i == len(methods) - 1:
            # Last method gets remainder to avoid rounding issues
            refunds[method] = refund_amount - allocated
        else:
            share = int(refund_amount * payment_details[method] / total_paid)
            refunds[method] = share
            allocated += share

    return refunds


# ---------------------------------------------------------------------------
# 8. Loyalty Bonus
# ---------------------------------------------------------------------------

# Platinum customers with 50+ total orders get a one-time $50 loyalty bonus
# applied to their next compensation or refund claim.
LOYALTY_BONUS_TIER: str = "platinum"
LOYALTY_BONUS_MIN_ORDERS: int = 50
LOYALTY_BONUS_AMOUNT: int = 50


def check_loyalty_bonus(
    membership_tier: str,
    total_orders: int,
    bonus_already_used: bool = False,
) -> dict[str, Any]:
    """Check if customer qualifies for the one-time loyalty bonus.

    Rules:
    - Platinum tier only
    - 50+ total orders
    - One-time: if already used, not eligible
    - Applied as additional credit on next compensation/refund claim
    """
    if bonus_already_used:
        return {"eligible": False, "reason": "Loyalty bonus already used"}
    if membership_tier != LOYALTY_BONUS_TIER:
        return {"eligible": False, "reason": f"Loyalty bonus requires {LOYALTY_BONUS_TIER} tier"}
    if total_orders < LOYALTY_BONUS_MIN_ORDERS:
        return {
            "eligible": False,
            "reason": f"Loyalty bonus requires {LOYALTY_BONUS_MIN_ORDERS}+ orders (currently {total_orders})",
        }
    return {
        "eligible": True,
        "bonus_amount": LOYALTY_BONUS_AMOUNT,
        "reason": f"Platinum loyalty bonus: ${LOYALTY_BONUS_AMOUNT} (one-time)",
    }


# ---------------------------------------------------------------------------
# 9. Bulk Order Discount Clawback
# ---------------------------------------------------------------------------

# If a customer returns items from an order that had a bulk discount (3+ items),
# and the return brings the remaining item count below the bulk threshold,
# the discount is clawed back from the remaining items.
BULK_DISCOUNT_THRESHOLD: int = 3  # minimum items for bulk discount
BULK_CLAWBACK_PER_ITEM: int = 5  # dollars clawed back per remaining item


def calculate_bulk_clawback(
    original_item_count: int,
    items_being_returned: int,
    remaining_item_count: int,
    discount_code: str | None,
    discount_amount: int,
) -> dict[str, Any]:
    """Calculate whether returning items triggers bulk discount clawback.

    Rules:
    - Only applies if order had a discount code AND 3+ items originally
    - If remaining items < 3 after return, clawback = $5 per remaining item
    - Clawback is deducted from the return refund
    - If no discount code, no clawback
    """
    if not discount_code or discount_amount == 0:
        return {"applies": False, "clawback_amount": 0, "reason": "No discount code on order"}

    if original_item_count < BULK_DISCOUNT_THRESHOLD:
        return {"applies": False, "clawback_amount": 0, "reason": "Order was below bulk threshold"}

    if remaining_item_count >= BULK_DISCOUNT_THRESHOLD:
        return {"applies": False, "clawback_amount": 0, "reason": "Still meets bulk threshold after return"}

    # Clawback applies
    clawback = BULK_CLAWBACK_PER_ITEM * remaining_item_count
    return {
        "applies": True,
        "clawback_amount": clawback,
        "reason": f"Return drops order below bulk threshold — ${clawback} clawback on remaining {remaining_item_count} items",
    }


# ---------------------------------------------------------------------------
# 10. Seasonal Return Extension
# ---------------------------------------------------------------------------

# Orders placed in November or December get extended return windows
# until January 31 of the following year (holiday gift returns).
SEASONAL_MONTHS: list[int] = [11, 12]  # November, December
SEASONAL_EXTENSION_DAY: int = 31  # January 31


def check_seasonal_return_extension(
    order_date: str,
    now: str,
    base_window_end: str,
) -> dict[str, Any]:
    """Check if a holiday seasonal return extension applies.

    Rules:
    - Orders placed in November or December qualify
    - Return window extends to January 31 of the year after the order
    - Only applies if the seasonal deadline is later than the normal window end
    - This is IN ADDITION to tier/prime extensions (whichever is later wins)
    """
    order_dt = datetime.fromisoformat(order_date)
    now_dt = datetime.fromisoformat(now)
    base_end_dt = datetime.fromisoformat(base_window_end)

    if order_dt.month not in SEASONAL_MONTHS:
        return {"applies": False, "reason": "Order not placed in holiday season (Nov-Dec)"}

    # January 31 of the year after the order
    seasonal_deadline = datetime(order_dt.year + 1, 1, SEASONAL_EXTENSION_DAY)

    if seasonal_deadline <= base_end_dt:
        return {"applies": False, "reason": "Normal return window already extends past seasonal deadline"}

    if now_dt > seasonal_deadline:
        return {"applies": False, "reason": "Seasonal deadline (Jan 31) has passed"}

    return {
        "applies": True,
        "seasonal_deadline": seasonal_deadline.strftime("%Y-%m-%d"),
        "reason": f"Holiday order — return window extended to {seasonal_deadline.strftime('%B %d, %Y')}",
    }


# ---------------------------------------------------------------------------
# 11. Free Shipping Threshold Clawback
# ---------------------------------------------------------------------------

# Orders with subtotal >= FREE_SHIPPING_THRESHOLD qualified for free shipping.
# If a return drops the remaining items below this threshold, the original
# shipping cost is deducted from the return refund.
FREE_SHIPPING_THRESHOLD: int = 100  # minimum subtotal for free shipping
STANDARD_SHIPPING_COST: int = (
    8  # flat shipping fee charged when subtotal < FREE_SHIPPING_THRESHOLD; also the retroactive clawback amount
)


def calculate_shipping_clawback(
    order_subtotal: int,
    return_item_price: int,
    shipping_cost: int,
    original_free_shipping: bool,
) -> dict[str, Any]:
    """Calculate whether a return triggers free shipping clawback.

    Rules:
    - Only applies if the order originally qualified for free shipping (subtotal >= $100)
    - If remaining subtotal after return drops below $100, deduct original shipping cost from refund
    - The shipping cost is deducted from the return refund, NOT charged separately
    - If order didn't qualify for free shipping originally, no clawback
    """
    if not original_free_shipping:
        return {"applies": False, "clawback_amount": 0, "reason": "Order did not qualify for free shipping"}

    remaining_subtotal = order_subtotal - return_item_price
    if remaining_subtotal >= FREE_SHIPPING_THRESHOLD:
        return {"applies": False, "clawback_amount": 0, "reason": "Remaining items still qualify for free shipping"}

    return {
        "applies": True,
        "clawback_amount": shipping_cost,
        "remaining_subtotal": remaining_subtotal,
        "reason": f"Return drops remaining subtotal to ${remaining_subtotal} (below ${FREE_SHIPPING_THRESHOLD} threshold) — ${shipping_cost} shipping clawback",
    }


# ---------------------------------------------------------------------------
# 12. Same-Category Repeat Return Surcharge
# ---------------------------------------------------------------------------

# If customer returns 2+ items from the same product category within a single order,
# a per-item surcharge of $5 applies on the second and subsequent returns.
# This discourages "try everything, return most" behavior.
REPEAT_CATEGORY_SURCHARGE: int = 5


def calculate_repeat_return_surcharge(
    return_category: str,
    already_returned_categories: list[str],
) -> dict[str, Any]:
    """Calculate surcharge for repeat same-category returns within an order.

    Rules:
    - If the customer is returning an item whose category already has a return from this order, $5 surcharge
    - Applies per additional return in the same category
    - First return in each category is free of surcharge
    - Surcharge is deducted from the return refund
    """
    same_cat_count = sum(1 for c in already_returned_categories if c == return_category)

    if same_cat_count == 0:
        return {"applies": False, "surcharge": 0, "reason": "First return in this category — no surcharge"}

    return {
        "applies": True,
        "surcharge": REPEAT_CATEGORY_SURCHARGE,
        "same_category_returns": same_cat_count,
        "reason": f"${REPEAT_CATEGORY_SURCHARGE} surcharge: {same_cat_count} prior return(s) in '{return_category}' category",
    }


# ---------------------------------------------------------------------------
# 13. Tiered Restocking Fee Discount
# ---------------------------------------------------------------------------

# Gold/Silver members get a partial discount on the 15% restocking fee
# (Platinum already gets full waiver via calculate_refund).
# This is checked separately and applied as a credit via process_refund.
RESTOCKING_DISCOUNT_BY_TIER: dict[str, float] = {
    "gold": 0.50,  # 50% off restocking fee
    "silver": 0.25,  # 25% off restocking fee
}


def calculate_restocking_discount(
    restocking_fee: int,
    membership_tier: str,
) -> dict[str, Any]:
    """Calculate tier-based discount on restocking fee.

    Rules:
    - Gold members: 50% off the restocking fee
    - Silver members: 25% off the restocking fee
    - Standard members: no discount
    - Platinum members: fee already waived in calculate_refund (no discount needed)
    - Applied as a credit via process_refund after the return
    """
    if membership_tier == "platinum":
        return {"applies": False, "discount": 0, "reason": "Platinum: restocking fee already waived"}

    discount_pct = RESTOCKING_DISCOUNT_BY_TIER.get(membership_tier, 0.0)
    if discount_pct == 0:
        return {"applies": False, "discount": 0, "reason": "No restocking fee discount for this tier"}

    discount_amount = int(restocking_fee * discount_pct)
    return {
        "applies": True,
        "discount": discount_amount,
        "discount_pct": int(discount_pct * 100),
        "reason": f"{membership_tier.title()} member: {int(discount_pct * 100)}% off restocking fee = ${discount_amount} credit",
    }


# ---------------------------------------------------------------------------
# 14. Paid Return Shipping on Low-Value Orders
# ---------------------------------------------------------------------------

# Orders with subtotal under this threshold don't qualify for free return labels.
# Customer must pay a flat return shipping fee.
PAID_RETURN_SHIPPING_THRESHOLD: int = 50
PAID_RETURN_SHIPPING_FEE: int = 8


def calculate_paid_return_shipping(
    order_subtotal: int,
    return_reason: str,
) -> dict[str, Any]:
    """Determine if the customer must pay for return shipping.

    Rules:
    - Orders with subtotal < $50: customer pays $8 return shipping
    - Orders >= $50: free return label
    - Product-fault returns (defective/wrong_item/damaged_in_transit): always free
      regardless of order value — customer was not at fault, so they are not charged for
      shipping back an item the company shipped wrong.
    - Fee is deducted from the return refund
    """
    if return_reason in ("defective", "wrong_item", "damaged_in_transit"):
        return {
            "applies": False,
            "fee": 0,
            "reason": "Free return shipping for product-fault returns (defective/wrong/damaged)",
        }

    if order_subtotal >= PAID_RETURN_SHIPPING_THRESHOLD:
        return {
            "applies": False,
            "fee": 0,
            "reason": f"Order subtotal ${order_subtotal} qualifies for free return shipping",
        }

    return {
        "applies": True,
        "fee": PAID_RETURN_SHIPPING_FEE,
        "reason": f"Order subtotal ${order_subtotal} < ${PAID_RETURN_SHIPPING_THRESHOLD} threshold — ${PAID_RETURN_SHIPPING_FEE} return shipping fee",
    }
