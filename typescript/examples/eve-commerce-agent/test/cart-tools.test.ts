/**
 * The cart tools, driven through a fake session-state slot.
 *
 * `deps.cart` is the seam: in production the tools read eve's durable
 * `defineState` slot, which throws outside an eve runtime context.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { deps, resetDeps } from "../agent/lib/deps.js";
import addToCart from "../agent/tools/add_to_cart.js";
import removeFromCart from "../agent/tools/remove_from_cart.js";
import viewCart from "../agent/tools/view_cart.js";
import { fakeCart, runTool, toolContext } from "./fixtures.js";

const ctx = toolContext();

// `async` on purpose: these tools are synchronous, so without it a thrown error
// would escape before `expect(...).rejects` could see a rejected promise.
const add = async (productId: string, quantity = 1) =>
  await runTool(addToCart.execute({ productId, quantity }, ctx));
const remove = async (productId: string, quantity?: number) =>
  await runTool(removeFromCart.execute({ productId, quantity }, ctx));

beforeEach(() => {
  deps.cart = fakeCart();
});

afterEach(resetDeps);

describe("view_cart", () => {
  it("starts empty", async () => {
    expect(await runTool(viewCart.execute({}, ctx))).toEqual({
      lines: [],
      itemCount: 0,
      total: 0,
      totalFormatted: "$0.00",
    });
  });
});

describe("add_to_cart", () => {
  it("adds a line and returns the running total", async () => {
    const result = await add("pv-5005");
    expect(result.added).toEqual({
      productId: "pv-5005",
      name: "Pavia Cashmere Scarf",
      brand: "Pavia",
      quantity: 1,
    });
    expect(result.cart.totalFormatted).toBe("$156.00");
  });

  it("merges a repeat id into one line instead of duplicating it", async () => {
    await add("td-6005", 1);
    const result = await add("td-6005", 2);
    expect(result.cart.lines).toHaveLength(1);
    expect(result.cart.itemCount).toBe(3);
    expect(result.cart.total).toBe(102);
  });

  it("refuses an unknown product id", async () => {
    await expect(add("ghost-1")).rejects.toThrow(/No product with id "ghost-1"/);
  });
});

describe("remove_from_cart", () => {
  beforeEach(async () => {
    await add("kt-4001", 3);
  });

  it("reduces the quantity when one is given", async () => {
    const result = await remove("kt-4001", 1);
    expect(result.removed).toBe(true);
    expect(result.cart.itemCount).toBe(2);
  });

  it("drops the whole line when no quantity is given", async () => {
    const result = await remove("kt-4001");
    expect(result.cart.lines).toEqual([]);
  });

  it("reports a miss for something not in the cart", async () => {
    const result = await remove("pv-5005");
    expect(result.removed).toBe(false);
    expect(result.cart.itemCount).toBe(3);
  });
});
