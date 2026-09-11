---
description: Use when the shopper is buying for someone else — a gift, a present, or a surprise — and needs help choosing.
---

# Choosing a gift

A gift is not a purchase for the shopper, so the usual profile does not apply.
Work in this order.

## 1. Get four facts before recommending anything

Ask in one message, not four:

1. Who it is for, and the occasion.
2. A budget ceiling.
3. Whether you should avoid sizing — if you are not sure of the recipient's size,
   prefer `one size` categories (`bags`, `hats`, `accessories`) or ask for a
   size explicitly.
4. Anything to avoid (a colour, a material, something they already own).

## 2. Shortlist three, not ten

Search with `search_catalog` using the budget as `maxPrice`, then offer exactly
three options that differ from each other:

- a safe one — `one size`, broadly liked (a scarf, a beanie, a dopp kit);
- a considered one — matches a detail the shopper mentioned about the recipient;
- a stretch one — the best thing under the ceiling, named as the splurge.

Give each as one line: name, catalog id, price, and the single reason it fits.

## 3. Keep the gift out of the shopper's profile

Remember facts about the **shopper** (their own size, their budget habits,
brands they like). Never store the recipient's size, name or taste as a
preference — the next visit would recommend the wrong things to the wrong
person. It is fine to record a durable shopper-level fact such as
`budget: keeps gifts under $75`.

## 4. Finish the job

Confirm the choice, `add_to_cart`, then follow the normal checkout flow: show
the cart and the total, ask for the go-ahead, and call `checkout` only after
they agree.
