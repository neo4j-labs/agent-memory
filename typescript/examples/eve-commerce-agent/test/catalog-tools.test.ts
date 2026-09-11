/**
 * The two read-only catalog tools. No memory, no eve runtime — a tool's
 * `execute` is an ordinary function, so these call it directly.
 */

import { describe, expect, it } from "vitest";
import { CATALOG } from "../agent/lib/catalog.js";
import getProduct from "../agent/tools/get_product.js";
import searchCatalog from "../agent/tools/search_catalog.js";
import { runTool, toolContext } from "./fixtures.js";

const ctx = toolContext();

describe("the catalog", () => {
  it("has 30 products with unique ids", () => {
    expect(CATALOG).toHaveLength(30);
    expect(new Set(CATALOG.map((product) => product.id)).size).toBe(30);
  });
});

describe("search_catalog", () => {
  it("matches free text across name, brand, colour and description", async () => {
    const { products, matchCount } = await runTool(
      searchCatalog.execute({ query: "waterproof shell", limit: 5 }, ctx),
    );
    expect(matchCount).toBeGreaterThan(0);
    expect(products.every((product) => product.brand === "Northmoor")).toBe(true);
  });

  it("ANDs the structured filters together", async () => {
    const { products } = await runTool(
      searchCatalog.execute({ brand: "Loomfield", category: "jeans", size: "34", limit: 10 }, ctx),
    );
    expect(products).toHaveLength(1);
    expect(products[0]?.id).toBe("lf-3002");
  });

  it("respects maxPrice and the limit", async () => {
    const { products } = await runTool(searchCatalog.execute({ maxPrice: 40, limit: 3 }, ctx));
    expect(products.length).toBeLessThanOrEqual(3);
    expect(products.every((product) => product.price <= 40)).toBe(true);
  });

  it("formats prices for the model", async () => {
    const { products } = await runTool(searchCatalog.execute({ query: "beanie", limit: 1 }, ctx));
    expect(products[0]?.priceFormatted).toBe("$34.00");
  });

  it("returns an empty result rather than throwing on no match", async () => {
    const result = await runTool(
      searchCatalog.execute({ query: "snowboard bindings", limit: 5 }, ctx),
    );
    expect(result).toEqual({ matchCount: 0, products: [] });
  });
});

describe("get_product", () => {
  it("returns the full record for a known id", async () => {
    const result = await runTool(getProduct.execute({ productId: "nm-2001" }, ctx));
    expect(result.found).toBe(true);
    expect(result).toMatchObject({
      name: "Northmoor Fellside Rain Shell",
      brand: "Northmoor",
      size: "M",
      priceFormatted: "$245.00",
    });
  });

  it("reports a miss instead of throwing", async () => {
    const result = await runTool(getProduct.execute({ productId: "nope-9999" }, ctx));
    expect(result).toEqual({ found: false, productId: "nope-9999" });
  });
});
