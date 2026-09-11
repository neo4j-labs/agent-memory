/**
 * The shopping cart — eve session state, not memory.
 *
 * A cart belongs to one conversation: it should die with the session, which is
 * exactly what `defineState` gives you (durable for the life of the session,
 * replayed across crashes and redeploys, never shared with a subagent). What
 * belongs in NAMS instead is what outlives the cart — the shopper's sizes,
 * brands and budget. See `agent/memory/shopper.ts`.
 *
 * The cart arithmetic below is pure so it can be unit-tested without an eve
 * runtime context; the tools reach the live slot through `cartStore()`.
 */

import { defineState } from "eve/context";
import { formatPrice, type Product } from "./catalog.js";

export interface CartLine {
  readonly productId: string;
  readonly name: string;
  readonly brand: string;
  readonly size: string;
  readonly unitPrice: number;
  readonly quantity: number;
}

export interface Cart {
  readonly lines: readonly CartLine[];
}

export const EMPTY_CART: Cart = { lines: [] };

const cartState = defineState<Cart>("eve-commerce-agent.cart", () => EMPTY_CART);

/** The narrow surface the cart tools need, so tests can substitute a fake. */
export interface CartStore {
  get(): Cart;
  update(fn: (cart: Cart) => Cart): void;
}

/** The real store: one cart per durable eve session. */
export const sessionCart: CartStore = {
  get: () => cartState.get(),
  update: (fn) => cartState.update(fn),
};

/** Add `quantity` of `product`, merging into an existing line for the same id. */
export function addLine(cart: Cart, product: Product, quantity: number): Cart {
  const existing = cart.lines.find((line) => line.productId === product.id);
  if (existing !== undefined) {
    return {
      lines: cart.lines.map((line) =>
        line.productId === product.id
          ? { ...line, quantity: line.quantity + quantity }
          : line,
      ),
    };
  }
  return {
    lines: [
      ...cart.lines,
      {
        productId: product.id,
        name: product.name,
        brand: product.brand,
        size: product.size,
        unitPrice: product.price,
        quantity,
      },
    ],
  };
}

/**
 * Remove `quantity` of `productId` (the whole line when `quantity` is omitted).
 * Removing more than the line holds drops the line rather than going negative.
 */
export function removeLine(cart: Cart, productId: string, quantity?: number): Cart {
  const existing = cart.lines.find((line) => line.productId === productId);
  if (existing === undefined) return cart;
  if (quantity === undefined || quantity >= existing.quantity) {
    return { lines: cart.lines.filter((line) => line.productId !== productId) };
  }
  return {
    lines: cart.lines.map((line) =>
      line.productId === productId ? { ...line, quantity: line.quantity - quantity } : line,
    ),
  };
}

export interface CartSummary {
  readonly lines: readonly (CartLine & { readonly lineTotal: number })[];
  readonly itemCount: number;
  readonly total: number;
  readonly totalFormatted: string;
}

export function summarizeCart(cart: Cart): CartSummary {
  const lines = cart.lines.map((line) => ({
    ...line,
    lineTotal: Number((line.unitPrice * line.quantity).toFixed(2)),
  }));
  const total = Number(lines.reduce((sum, line) => sum + line.lineTotal, 0).toFixed(2));
  return {
    lines,
    itemCount: cart.lines.reduce((sum, line) => sum + line.quantity, 0),
    total,
    totalFormatted: formatPrice(total),
  };
}
