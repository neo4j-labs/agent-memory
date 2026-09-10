/**
 * Guards for the one overlap a paired `Neo4jMemoryStore` creates.
 *
 * The pairing itself is supported and intended: `Neo4jSessionStorage` persists
 * and restores the transcript, the store feeds the agent loop. Not supported:
 * both sides ingesting the same turns.
 *
 * Only this file reads a private `MemoryManager` field, and
 * test/unit/strands/coexistence.test.ts pins the read so an SDK rename fails
 * loudly rather than silently disabling the guard.
 */

import { Neo4jMemoryStore } from "./memory-store.js";

/** Our stores registered on the agent's `MemoryManager`, if any. */
export function ourStores(agent: unknown): Neo4jMemoryStore[] {
  const manager = (agent as { memoryManager?: unknown } | null)?.memoryManager;
  if (!manager || typeof manager !== "object") return [];
  const stores = (manager as { _config?: { stores?: unknown } })._config?.stores;
  if (!Array.isArray(stores)) return [];
  return stores.filter((store): store is Neo4jMemoryStore => store instanceof Neo4jMemoryStore);
}

/**
 * Refuse a configuration where both sides ingest the same turns.
 *
 * NAMS extracts every message write server-side, so a store whose `extraction`
 * is on writes the turns this conversation already carries into its own sink
 * and has them extracted a second time. There is no flag that averts it — the
 * fix is a configuration change, which the message names.
 */
export function assertNoDoubleExtraction(agent: unknown): void {
  const extracting = ourStores(agent).find((store) => Boolean(store.extraction));
  if (!extracting) return;
  throw new Error(
    `Neo4jConversationManager and Neo4jMemoryStore '${extracting.name}' would ` +
      `write and extract the same turns twice: the hosted service extracts every ` +
      `message write server-side. Set extraction: false on the store (recall ` +
      `only, recommended, and the session integration keeps persisting the ` +
      `transcript), or drop the Neo4j session integration and let the store own ` +
      `ingestion.`,
  );
}
