/**
 * Checkout: the approval gate, the idempotent order id, and the order facts it
 * writes to long-term memory.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { deps, resetDeps } from "../agent/lib/deps.js";
import { forgetConversations, resolveConversation } from "../agent/lib/memory-session.js";
import addToCart from "../agent/tools/add_to_cart.js";
import checkout from "../agent/tools/checkout.js";
import { FakeNamsTransport } from "./fake-nams.js";
import { fakeCart, runTool, SHOPPER, toolContext } from "./fixtures.js";

let transport: FakeNamsTransport;

beforeEach(async () => {
  forgetConversations();
  transport = new FakeNamsTransport();
  deps.client = new MemoryClient(transport);
  deps.cart = fakeCart();
});

afterEach(resetDeps);

describe("checkout", () => {
  it("is gated on human approval, not just on an instruction", () => {
    // `always()` makes eve park the turn and ask a person before `execute` runs.
    expect(checkout.approval).toBeDefined();
  });

  it("will not place an order the shopper has not confirmed", async () => {
    const ctx = toolContext();
    await runTool(addToCart.execute({ productId: "pv-5003", quantity: 1 }, ctx));

    const result = await runTool(checkout.execute({ confirmed: false }, ctx));
    expect(result).toMatchObject({ placed: false, reason: "not-confirmed", itemCount: 1 });
    expect(transport.facts).toEqual([]);
  });

  it("will not place an order for an empty cart", async () => {
    const result = await runTool(checkout.execute({ confirmed: true }, toolContext()));
    expect(result).toMatchObject({ placed: false, reason: "empty-cart" });
  });

  it("places the order, clears the cart and records the order in memory", async () => {
    const ctx = toolContext({ callId: "call_7f3a9c" });
    // Memory is live for this session, so the order facts get written.
    await resolveConversation(deps.client!, ctx.session.id, SHOPPER);

    await runTool(addToCart.execute({ productId: "nm-2003", quantity: 2 }, ctx));
    await runTool(addToCart.execute({ productId: "td-6005", quantity: 1 }, ctx));

    const result = await runTool(checkout.execute({ confirmed: true }, ctx));
    if (!result.placed) throw new Error("expected the order to be placed");

    expect(result.orderId).toBe("ORD-7F3A9C");
    expect(result.itemCount).toBe(3);
    expect(result.total).toBe(370);

    // Cart emptied — the next turn starts clean.
    expect(deps.cart!.get().lines).toEqual([]);

    const subjects = new Set(transport.facts.map((fact) => fact.subject));
    expect(subjects).toEqual(new Set(["ORD-7F3A9C"]));
    expect(transport.facts.map((fact) => fact.predicate)).toEqual([
      "contains",
      "contains",
      "order_total",
    ]);
    expect(transport.facts.at(-1)?.object).toBe("$370.00");
  });

  it("derives the order id from the tool call, so a replay cannot double-order", async () => {
    const ctx = toolContext({ callId: "call_7f3a9c" });
    await runTool(addToCart.execute({ productId: "td-6005", quantity: 1 }, ctx));
    const first = await runTool(checkout.execute({ confirmed: true }, ctx));

    await runTool(addToCart.execute({ productId: "td-6005", quantity: 1 }, ctx));
    const replay = await runTool(checkout.execute({ confirmed: true }, ctx));

    expect(first.placed && replay.placed).toBe(true);
    expect(first.placed ? first.orderId : "").toBe(replay.placed ? replay.orderId : "x");
  });

  it("still sells when memory is unavailable for the caller", async () => {
    // No conversation resolved for this session: the `shopper` slot was disabled
    // (an unidentified visitor), so checkout skips the memory write.
    const ctx = toolContext({ sessionId: "sess_anonymous" });
    await runTool(addToCart.execute({ productId: "kt-4003", quantity: 1 }, ctx));

    const result = await runTool(checkout.execute({ confirmed: true }, ctx));
    expect(result.placed).toBe(true);
    expect(transport.facts).toEqual([]);
  });
});
