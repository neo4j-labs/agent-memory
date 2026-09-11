/**
 * `search_catalog` — the tool name is this file's slug.
 */

import { defineTool } from "eve/tools";
import { z } from "zod";
import { BRANDS, CATEGORIES, formatPrice, searchCatalog } from "../lib/catalog.js";

export default defineTool({
  description:
    "Search the shop's catalog. Combine any of the filters; they are ANDed. " +
    `Brands: ${BRANDS.join(", ")}. Categories: ${CATEGORIES.join(", ")}.`,
  inputSchema: z.object({
    query: z
      .string()
      .max(120)
      .optional()
      .describe("Free text matched against name, brand, category, colour and description."),
    brand: z.string().max(60).optional(),
    category: z.string().max(60).optional(),
    size: z.string().max(20).optional().describe("'M', a waist or shoe number, or 'one size'."),
    maxPrice: z.number().positive().optional().describe("Ceiling in US dollars."),
    limit: z.number().int().min(1).max(20).default(5),
  }),
  label: {
    start: ({ query, brand, category }) =>
      `Search catalog: ${[query, brand, category].filter(Boolean).join(" ") || "everything"}`,
  },
  execute({ query, brand, category, size, maxPrice, limit }) {
    const products = searchCatalog({ query, brand, category, size, maxPrice, limit });
    return {
      matchCount: products.length,
      products: products.map((product) => ({
        ...product,
        priceFormatted: formatPrice(product.price),
      })),
    };
  },
});
