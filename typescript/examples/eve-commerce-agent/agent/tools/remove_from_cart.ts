/**
 * `remove_from_cart` — take something back out of the cart.
 */

import { defineTool } from "eve/tools";
import { z } from "zod";
import { removeLine, summarizeCart } from "../lib/cart.js";
import { cartStore } from "../lib/deps.js";

export default defineTool({
  description:
    "Remove a product from the cart by catalog id. Omit quantity to drop the " +
    "whole line; pass one to reduce it.",
  inputSchema: z.object({
    productId: z.string().min(1).max(40),
    quantity: z.number().int().min(1).max(10).optional(),
  }),
  label: {
    start: ({ productId }) => `Remove ${productId} from cart`,
  },
  execute({ productId, quantity }) {
    const store = cartStore();
    const before = store.get();
    const held = before.lines.some((line) => line.productId === productId);
    store.update((cart) => removeLine(cart, productId, quantity));
    return {
      removed: held,
      productId,
      cart: summarizeCart(store.get()),
    };
  },
});
