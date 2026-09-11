/**
 * The shop's catalog: 30 products loaded from `data/catalog.json`.
 *
 * Pure, synchronous, and dependency-free on purpose — the interesting part of
 * this example is the memory wiring, not the storefront. Swap these functions
 * for your own product service and nothing else in the agent changes.
 */

// Both bundlers that ever see this file (eve's build and Vitest's) resolve a
// plain JSON import, and the example's tsconfig models a bundler — so no
// `with { type: "json" }` attribute is needed here.
import catalogData from "../../data/catalog.json";

export interface Product {
  readonly id: string;
  readonly name: string;
  readonly brand: string;
  readonly category: string;
  /** Apparel size (`S`/`M`/`L`), a waist or shoe number, or `one size`. */
  readonly size: string;
  readonly price: number;
  readonly color: string;
  readonly description: string;
}

export const CATALOG: readonly Product[] = catalogData as readonly Product[];

/** Every brand in the catalog, sorted — used in the tool descriptions. */
export const BRANDS: readonly string[] = [...new Set(CATALOG.map((p) => p.brand))].sort();

/** Every category in the catalog, sorted. */
export const CATEGORIES: readonly string[] = [...new Set(CATALOG.map((p) => p.category))].sort();

export interface CatalogQuery {
  /** Free text matched against name, brand, category, colour and description. */
  readonly query?: string;
  readonly brand?: string;
  readonly category?: string;
  readonly size?: string;
  readonly maxPrice?: number;
  readonly limit?: number;
}

function norm(value: string): string {
  return value.trim().toLowerCase();
}

function haystack(product: Product): string {
  return norm(
    [
      product.name,
      product.brand,
      product.category,
      product.color,
      product.description,
      product.size,
    ].join(" "),
  );
}

/**
 * Filter the catalog. Every field is optional and ANDed together; free text is
 * split on whitespace and every term must appear somewhere in the product.
 */
export function searchCatalog(query: CatalogQuery = {}): Product[] {
  const terms = query.query === undefined ? [] : norm(query.query).split(/\s+/).filter(Boolean);
  const brand = query.brand === undefined ? undefined : norm(query.brand);
  const category = query.category === undefined ? undefined : norm(query.category);
  const size = query.size === undefined ? undefined : norm(query.size);

  const matches = CATALOG.filter((product) => {
    if (brand !== undefined && norm(product.brand) !== brand) return false;
    if (category !== undefined && norm(product.category) !== category) return false;
    if (size !== undefined && norm(product.size) !== size) return false;
    if (query.maxPrice !== undefined && product.price > query.maxPrice) return false;
    if (terms.length === 0) return true;
    const text = haystack(product);
    return terms.every((term) => text.includes(term));
  });

  return matches.slice(0, query.limit ?? 10);
}

export function getProduct(productId: string): Product | undefined {
  return CATALOG.find((product) => product.id === norm(productId));
}

/** `$138.00` — tool output is read by a model, so keep it unambiguous. */
export function formatPrice(amount: number): string {
  return `$${amount.toFixed(2)}`;
}
