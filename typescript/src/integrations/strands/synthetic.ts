/**
 * Synthetic marker messages used to stash Strands snapshot/manifest state
 * inline in NAMS conversation messages (see {@link session-storage.ts} for
 * why: NAMS exposes no conversation-metadata-update endpoint, so state rides
 * along on the only write surface that works robustly).
 */

import type { MessageRole } from "../../types.js";

/**
 * Strands snapshot state is persisted as synthetic `role: "user"`
 * messages on the NAMS conversation. NAMS exposes no conversation-
 * metadata-update endpoint, so we use the only write surface that
 * works robustly: a message whose `content` carries both the marker
 * prefix AND the JSON-serialized blob, base64-encoded for safety.
 *
 * The historical choice of `role: "system"` + per-message metadata was
 * abandoned after live-service testing showed that NAMS' GET
 * /conversations/{id}/messages either filters out `system`-role
 * messages or doesn't surface per-message metadata on read (or both).
 * `role: "user"` is universally preserved, and stuffing the blob inline
 * in `content` removes the dependency on metadata round-tripping.
 *
 * Each distinct snapshot state writes ONE synthetic message:
 *
 *   { role: "user", content: "__strands_state__:{base64(JSON.stringify(blob))}" }
 *
 * Manifests use a parallel prefix `__strands_manifest__:`. Consumers
 * walking the message list MUST filter these out — see
 * {@link isSyntheticStrandsMessage}. Strands' agent loop never sees
 * them because `Neo4jSessionStorage.loadSnapshot` strips them
 * before returning the reconstructed Snapshot.
 *
 * Per-snapshot synthetic messages mean `listSnapshotIds` is O(n) over
 * the message list, but repeated idempotent saves short-circuit when the
 * latest stored blob already matches. In practice snapshots are small
 * JSON deltas and the conversation's message count is bounded — fine for v0.x.
 */
export const STATE_PREFIX = "__strands_state__:";
export const MANIFEST_PREFIX = "__strands_manifest__:";

/** Role used for synthetic state messages. */
export const SYNTHETIC_ROLE: MessageRole = "user";

export function encodeBlob(blob: unknown): string {
  return base64Encode(JSON.stringify(blob));
}

export function decodeBlob<T>(content: string, prefix: string): T | null {
  if (!content.startsWith(prefix)) return null;
  const payload = content.slice(prefix.length);
  try {
    return JSON.parse(base64Decode(payload)) as T;
  } catch {
    return null;
  }
}

function base64Encode(s: string): string {
  // Use Buffer when available (Node, Bun, edge runtimes with shims),
  // else fall back to a btoa-on-UTF8 path for purer browser-like runtimes.
  if (typeof Buffer !== "undefined") {
    return Buffer.from(s, "utf8").toString("base64");
  }
  // eslint-disable-next-line no-restricted-globals
  const g = globalThis as { btoa?: (s: string) => string };
  if (typeof g.btoa === "function") {
    // btoa requires Latin-1; wrap UTF-8 bytes first.
    const bytes = new TextEncoder().encode(s);
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return g.btoa(bin);
  }
  throw new Error("No base64 encoder available in this runtime");
}

function base64Decode(b64: string): string {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(b64, "base64").toString("utf8");
  }
  // eslint-disable-next-line no-restricted-globals
  const g = globalThis as { atob?: (s: string) => string };
  if (typeof g.atob === "function") {
    const bin = g.atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  throw new Error("No base64 decoder available in this runtime");
}

/**
 * Returns true if a message is one of our synthetic state/manifest
 * markers. Exported so consumers walking the conversation can filter
 * them out of UI rendering. See `SYNTHETIC_MESSAGE_PREFIXES` for the
 * canonical prefix list.
 *
 * Recognizes ANY role — the storage role used by the integration is
 * `"user"`, but older saves may have used `"system"`. We match on the
 * content prefix alone for resilience.
 */
export function isSyntheticStrandsMessage(
  message: { role: string; content: string },
): boolean {
  return (
    message.content.startsWith(STATE_PREFIX) ||
    message.content.startsWith(MANIFEST_PREFIX)
  );
}

/**
 * Canonical content prefixes used for synthetic messages. Consumers
 * (chat UIs, message-list renderers, Cypher queries) can filter on
 * these to skip the Strands-internal state messages.
 */
export const SYNTHETIC_MESSAGE_PREFIXES = [STATE_PREFIX, MANIFEST_PREFIX] as const;
