/**
 * The `shopper` memory slot.
 *
 * One file is the whole declaration: who the memory belongs to (`scope`) and
 * where it lives (`provider`). eve resolves and locks the scope before any
 * recall runs, passes the locked scope to every provider call, and exposes the
 * provider's tools to the model as `shopper__remember_preference` and
 * `shopper__recall_shopper`.
 *
 * The scope is the shopper id that the channel authenticated — never anything
 * the model said. Returning `null` disables the slot for that caller: eve skips
 * recall, capture and the memory tools entirely and never falls back to a shared
 * scope, so an unidentified visitor gets a working storefront with no memory
 * rather than someone else's profile.
 */

import { defineMemory } from "eve/memory";
import { namsMemory } from "../lib/nams-memory.js";

export default defineMemory({
  description:
    "Durable facts about this shopper — sizes, brands, budget and style — plus " +
    "summaries of their earlier visits. Stored in the Neo4j Agent Memory Service.",
  provider: namsMemory(),
  scope(ctx) {
    const caller = ctx.session.auth.current;
    if (caller === null || caller.principalType !== "user") return null;
    return caller.principalId;
  },
  // Default, stated explicitly because it is the tenant-isolation decision:
  // if the caller changes mid-session, records recalled for the earlier shopper
  // stop being visible to the model.
  visibility: "scope",
});
