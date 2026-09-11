/**
 * Reasoning memory, written from the runtime event stream.
 *
 * A hook is eve's observe-only subscriber: it runs after each event is durably
 * recorded and cannot change the turn. That is exactly right for an audit trail
 * — every tool call the model makes becomes one NAMS reasoning step, and the
 * products and brands in the result become entities that step touched.
 *
 * Two events are needed, because no single one carries both halves of a call:
 * `actions.requested` carries the validated input, `action.result` carries the
 * output. They are correlated by `callId`, as the protocol asks consumers to do.
 *
 * Results are matched on the public protocol shape (`kind: "tool-result"`).
 * `toolResultFrom(event.data.result, someTool)` from `eve/tools` is the typed
 * alternative; it matches on the compile-time identity eve stamps onto an
 * authored tool, so it only narrows inside a compiled agent.
 */

import { defineHook } from "eve/hooks";
import { recordToolCall } from "../lib/trace.js";

/** Tool names whose results are worth an audit step. */
const TRACKED_TOOLS = new Set([
  "search_catalog",
  "get_product",
  "add_to_cart",
  "remove_from_cart",
  "view_cart",
  "checkout",
  "shopper__remember_preference",
  "shopper__recall_shopper",
]);

/** callId → validated tool input, filled by `actions.requested`. */
const pendingInputs = new Map<string, unknown>();

/** Belt and braces: a dropped `action.result` must not grow the map forever. */
const MAX_PENDING = 100;

export default defineHook({
  events: {
    "actions.requested"(event) {
      for (const action of event.data.actions) {
        if (action.kind !== "tool-call" && action.kind !== "workflow-tool-call") continue;
        if (!TRACKED_TOOLS.has(action.toolName)) continue;
        if (pendingInputs.size >= MAX_PENDING) pendingInputs.clear();
        pendingInputs.set(action.callId, action.input);
      }
    },

    async "action.result"(event, ctx) {
      const result = event.data.result;
      if (result.kind !== "tool-result" || result.isError === true) return;
      if (!TRACKED_TOOLS.has(result.toolName)) return;

      const input = pendingInputs.get(result.callId) ?? null;
      pendingInputs.delete(result.callId);

      // A hook that throws fails the turn, and a missing audit row must never
      // cost the shopper their answer — so memory errors are logged, not raised.
      try {
        await recordToolCall(ctx.session.id, {
          toolName: result.toolName,
          input,
          output: result.output,
        });
      } catch (error) {
        console.warn("[reasoning-trace] could not record step", {
          toolName: result.toolName,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
  },
});
