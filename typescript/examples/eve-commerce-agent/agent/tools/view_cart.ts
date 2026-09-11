/**
 * `view_cart` — read the session's cart.
 */

import { defineTool } from "eve/tools";
import { z } from "zod";
import { summarizeCart } from "../lib/cart.js";
import { cartStore } from "../lib/deps.js";

export default defineTool({
  description: "Show what is currently in the shopper's cart, with the running total.",
  inputSchema: z.object({}),
  label: {
    start: () => "View cart",
  },
  execute() {
    return summarizeCart(cartStore().get());
  },
});
