/**
 * `checkout` — the one tool a person has to approve.
 *
 * `approval: always()` is eve's human-in-the-loop gate: the turn parks, the
 * channel raises an Approve / Cancel prompt, and `execute` does not run until a
 * human answers. That is a stronger guarantee than an instruction asking the
 * model to confirm first, which is why the guardrail lives here rather than only
 * in `agent/instructions.md`.
 *
 * The order itself is mocked — this example is about memory, not payments — but
 * the order id is derived from `ctx.callId` so a replayed durable step produces
 * the same id instead of a second order.
 */

import { defineTool } from "eve/tools";
import { always } from "eve/tools/approval";
import { z } from "zod";
import { EMPTY_CART, summarizeCart } from "../lib/cart.js";
import { formatPrice } from "../lib/catalog.js";
import { cartStore, memoryClient } from "../lib/deps.js";
import { peekConversation } from "../lib/memory-session.js";

function orderId(callId: string): string {
  const suffix = callId.replace(/[^a-zA-Z0-9]/g, "").slice(-6).toUpperCase();
  return `ORD-${suffix.padStart(6, "0")}`;
}

export default defineTool({
  description:
    "Place the order for everything in the cart. Requires the shopper's explicit " +
    "approval, so tell them the items and the total before calling it.",
  inputSchema: z.object({
    confirmed: z
      .boolean()
      .describe("Set true only after the shopper has seen the cart total and agreed."),
  }),
  approval: always(),
  label: {
    start: () => "Place order",
  },
  async execute({ confirmed }, ctx) {
    const store = cartStore();
    const summary = summarizeCart(store.get());

    if (!confirmed) {
      return { placed: false as const, reason: "not-confirmed", ...summary };
    }
    if (summary.itemCount === 0) {
      return { placed: false as const, reason: "empty-cart", ...summary };
    }

    const id = orderId(ctx.callId);

    // Remember the order in long-term memory as a fact, so a later visit can
    // answer "what did I buy last time?". Skipped when the memory slot is not
    // active for this caller (see agent/memory/shopper.ts).
    if (peekConversation(ctx.session.id) !== undefined) {
      const client = memoryClient();
      for (const line of summary.lines) {
        await client.longTerm.addFact(id, "contains", `${line.quantity} × ${line.name}`);
      }
      await client.longTerm.addFact(id, "order_total", formatPrice(summary.total));
    }

    store.update(() => EMPTY_CART);

    return {
      placed: true as const,
      orderId: id,
      itemCount: summary.itemCount,
      total: summary.total,
      totalFormatted: summary.totalFormatted,
      lines: summary.lines,
    };
  },
});
