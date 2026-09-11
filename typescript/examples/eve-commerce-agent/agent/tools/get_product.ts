/**
 * `get_product` — one product by id.
 */

import { defineTool } from "eve/tools";
import { z } from "zod";
import { formatPrice, getProduct } from "../lib/catalog.js";

export default defineTool({
  description:
    "Get the full record for one product by its catalog id (e.g. 'nm-2001'). " +
    "Use search_catalog first if you only have a description.",
  inputSchema: z.object({
    productId: z.string().min(1).max(40),
  }),
  label: {
    start: ({ productId }) => `Look up ${productId}`,
  },
  execute({ productId }) {
    const product = getProduct(productId);
    if (product === undefined) {
      return { found: false as const, productId };
    }
    return {
      found: true as const,
      ...product,
      priceFormatted: formatPrice(product.price),
    };
  },
});
