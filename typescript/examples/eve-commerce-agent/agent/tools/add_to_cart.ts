/**
 * `add_to_cart` — add a catalog product to the session's cart.
 */

import { defineTool } from "eve/tools";
import { z } from "zod";
import { addLine, summarizeCart } from "../lib/cart.js";
import { getProduct } from "../lib/catalog.js";
import { cartStore } from "../lib/deps.js";

export default defineTool({
  description:
    "Add a product to the cart by catalog id. Adding an id already in the cart " +
    "increases its quantity. Adding to the cart is not an order — checkout is.",
  inputSchema: z.object({
    productId: z.string().min(1).max(40),
    quantity: z.number().int().min(1).max(10).default(1),
  }),
  label: {
    start: ({ productId, quantity }) => `Add ${quantity} × ${productId} to cart`,
  },
  execute({ productId, quantity }) {
    const product = getProduct(productId);
    if (product === undefined) {
      throw new Error(`No product with id "${productId}". Use search_catalog to find one.`);
    }
    const store = cartStore();
    store.update((cart) => addLine(cart, product, quantity));
    return {
      added: { productId: product.id, name: product.name, brand: product.brand, quantity },
      cart: summarizeCart(store.get()),
    };
  },
});
