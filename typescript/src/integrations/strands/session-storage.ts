/**
 * {@link Neo4jSessionStorage} — implements `SnapshotStorage` so Strands'
 * `SessionManager` persists session state into a NAMS conversation. Hybrid
 * mapping: messages from each snapshot land as real `Message` graph nodes
 * via `addMessage`; the rest of the framework's per-snapshot state is
 * stashed losslessly in synthetic Strands marker messages on that same
 * conversation.
 */

import type {
  Message as StrandsMessage,
  Snapshot,
  SnapshotManifest,
  SnapshotStorage,
  SnapshotLocation,
} from "@strands-agents/sdk";

import type { MemoryClient } from "../../client.js";
import type { MessageRole } from "../../types.js";
import {
  STATE_PREFIX,
  MANIFEST_PREFIX,
  SYNTHETIC_ROLE,
  encodeBlob,
  decodeBlob,
  isSyntheticStrandsMessage,
} from "./synthetic.js";

interface StrandsStateBlob {
  /** snapshotId associated with this synthetic message. */
  snapshotId: string;
  /** Whether this save asserted itself as the latest. */
  isLatest: boolean;
  /** Snapshot data with messages stripped. */
  snapshot: Snapshot;
  /** Wall-clock save time (ISO 8601). Tie-breaker for "latest" elections. */
  savedAt: string;
}

interface StrandsManifestBlob {
  /** SnapshotLocation.scopeId so multiple agents per session can co-exist. */
  scopeId: string;
  manifest: SnapshotManifest;
  savedAt: string;
}

/**
 * Implements Strands' `SnapshotStorage` against a NAMS `MemoryClient`.
 *
 * One Strands session = one NAMS Conversation (keyed by `location.sessionId`).
 * Snapshots are versions within that conversation:
 *
 * - Real conversation messages from `snapshot.data.messages` land as real
 *   `Message` graph nodes via `addMessage` (so entity extraction, search,
 *   and the graph view all work on them).
 * - Non-message snapshot state (Strands' `data` minus `messages`, plus
 *   `appData`, plus the manifest) is persisted as synthetic `role: "user"`
 *   messages whose content carries both a marker prefix and a
 *   base64-encoded JSON blob. NAMS exposes `POST /conversations/{id}/messages`
 *   as the only documented conversation-scoped write, so this approach
 *   stays within the documented API surface.
 *
 * Consumers walking the message list (chat UIs, Cypher queries) MUST
 * filter synthetic messages with {@link isSyntheticStrandsMessage}.
 * Strands itself never sees them: {@link Neo4jSessionStorage.loadSnapshot}
 * strips them from the reconstructed Snapshot before handing back to
 * `SessionManager`.
 *
 * Auth errors propagate — Strands needs to know if the backing store is
 * unreachable. Transient errors propagate too; Strands' own retry
 * semantics (in `SessionManager`) apply.
 */
export class Neo4jSessionStorage implements SnapshotStorage {
  constructor(private readonly memory: MemoryClient) {}

  async saveSnapshot(params: {
    location: SnapshotLocation;
    snapshotId: string;
    isLatest: boolean;
    snapshot: Snapshot;
  }): Promise<void> {
    const { location, snapshotId, isLatest, snapshot } = params;
    const conversationId = location.sessionId;
    const existingConversation = await this.memory.shortTerm.getConversation(conversationId);

    // 1. Extract conversation messages out of snapshot.data.messages and
    //    persist any new ones as real Message nodes. Dedupe by role+content
    //    so re-saving the same snapshot doesn't grow the message list.
    await this.extractAndPersistMessages(conversationId, snapshot, existingConversation.messages);

    // 2. Write a synthetic user message whose content carries the full
    //    state blob (base64-encoded JSON after the marker prefix).
    const strippedSnapshot = stripMessagesFromSnapshot(snapshot);
    const blob: StrandsStateBlob = {
      snapshotId,
      isLatest,
      snapshot: strippedSnapshot,
      savedAt: new Date().toISOString(),
    };
    const previous = findLastStateBlobForSnapshotId(
      this.readStateBlobs(existingConversation.messages),
      snapshotId,
    );
    if (previous && sameStateBlob(previous, blob)) return;
    await this.memory.shortTerm.addMessage(
      conversationId,
      SYNTHETIC_ROLE,
      `${STATE_PREFIX}${encodeBlob(blob)}`,
    );
  }

  async loadSnapshot(params: {
    location: SnapshotLocation;
    snapshotId?: string;
  }): Promise<Snapshot | null> {
    const conversationId = params.location.sessionId;
    const conv = await this.memory.shortTerm.getConversation(conversationId);

    const stateBlobs = this.readStateBlobs(conv.messages);
    if (stateBlobs.length === 0) return null;

    // If a snapshotId was requested, find that specific save.
    // Otherwise fall back to the most recent save where isLatest=true,
    // or the latest save overall if none asserted "latest".
    let blob: StrandsStateBlob | undefined;
    if (params.snapshotId) {
      blob = findLastStateBlobForSnapshotId(stateBlobs, params.snapshotId);
    } else {
      blob = [...stateBlobs].reverse().find((b) => b.isLatest) ??
        stateBlobs[stateBlobs.length - 1];
    }
    if (!blob) return null;

    // Re-hydrate the snapshot: combine the stored data/appData with the
    // current conversation messages (filtered to drop our synthetic
    // markers so Strands doesn't replay them).
    const realMessages = conv.messages
      .filter((m) => !isSyntheticStrandsMessage(m))
      .map(toStrandsMessage);
    return mergeMessagesIntoSnapshot(blob.snapshot, realMessages);
  }

  async listSnapshotIds(params: {
    location: SnapshotLocation;
    limit?: number;
    startAfter?: string;
  }): Promise<string[]> {
    const conv = await this.memory.shortTerm.getConversation(params.location.sessionId);
    const stateBlobs = this.readStateBlobs(conv.messages);
    // Preserve save order. Dedupe per snapshotId in case a snapshotId is
    // saved more than once (Strands' contract permits re-saves).
    const seen = new Set<string>();
    const ids: string[] = [];
    for (const blob of stateBlobs) {
      if (seen.has(blob.snapshotId)) continue;
      seen.add(blob.snapshotId);
      ids.push(blob.snapshotId);
    }
    let start = 0;
    if (params.startAfter) {
      const idx = ids.indexOf(params.startAfter);
      start = idx >= 0 ? idx + 1 : 0;
    }
    return ids.slice(start, params.limit ? start + params.limit : undefined);
  }

  async deleteSession(params: { sessionId: string }): Promise<void> {
    await this.memory.shortTerm.deleteConversation(params.sessionId);
  }

  async loadManifest(params: { location: SnapshotLocation }): Promise<SnapshotManifest> {
    const conv = await this.memory.shortTerm.getConversation(params.location.sessionId);
    const blobs = this.readManifestBlobs(conv.messages);
    // Last write wins per scopeId — Strands writes manifests rarely so the
    // O(n) scan is fine.
    const matching = blobs.filter((b) => b.scopeId === params.location.scopeId);
    return matching[matching.length - 1]?.manifest ?? defaultManifest();
  }

  async saveManifest(params: {
    location: SnapshotLocation;
    manifest: SnapshotManifest;
  }): Promise<void> {
    const blob: StrandsManifestBlob = {
      scopeId: params.location.scopeId,
      manifest: params.manifest,
      savedAt: new Date().toISOString(),
    };
    await this.memory.shortTerm.addMessage(
      params.location.sessionId,
      SYNTHETIC_ROLE,
      `${MANIFEST_PREFIX}${encodeBlob(blob)}`,
    );
  }

  // --- Internals ------------------------------------------------------------

  /**
   * Scan a conversation's message list and parse any state markers into
   * blobs, in original order. Matches on the content prefix alone for
   * resilience against role normalization on the service side.
   */
  private readStateBlobs(
    messages: Array<{ role: string; content: string }>,
  ): StrandsStateBlob[] {
    const blobs: StrandsStateBlob[] = [];
    for (const msg of messages) {
      const blob = decodeBlob<StrandsStateBlob>(msg.content, STATE_PREFIX);
      if (blob) blobs.push(blob);
    }
    return blobs;
  }

  /** Same idea, for manifest markers. */
  private readManifestBlobs(
    messages: Array<{ role: string; content: string }>,
  ): StrandsManifestBlob[] {
    const blobs: StrandsManifestBlob[] = [];
    for (const msg of messages) {
      const blob = decodeBlob<StrandsManifestBlob>(msg.content, MANIFEST_PREFIX);
      if (blob) blobs.push(blob);
    }
    return blobs;
  }

  /**
   * Pull the message list out of `snapshot.data.messages` (the canonical
   * Strands layout), find ones not yet present on the conversation
   * (excluding our synthetic markers), and persist them via `addMessage`.
   * Returns the number of new messages written.
   */
  private async extractAndPersistMessages(
    conversationId: string,
    snapshot: Snapshot,
    existingMessages?: Array<{ role: string; content: string }>,
  ): Promise<number> {
    const messages = pickStrandsMessages(snapshot);
    if (messages.length === 0) return 0;

    const seen = new Set(
      (existingMessages ??
        (await this.memory.shortTerm.getConversation(conversationId)).messages)
        .filter((m) => !isSyntheticStrandsMessage(m))
        .map((m) => `${m.role}::${m.content}`),
    );

    let writes = 0;
    for (const msg of messages) {
      const text = strandsMessageToText(msg);
      const key = `${msg.role}::${text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      await this.memory.shortTerm.addMessage(conversationId, msg.role as MessageRole, text);
      writes++;
    }
    return writes;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultManifest(): SnapshotManifest {
  return {
    schemaVersion: "1.0",
    updatedAt: new Date().toISOString(),
  };
}

function pickStrandsMessages(snapshot: Snapshot): StrandsMessage[] {
  const data = snapshot.data as { messages?: unknown } | undefined;
  if (!data || !Array.isArray(data.messages)) return [];
  return data.messages as StrandsMessage[];
}

function stripMessagesFromSnapshot(snapshot: Snapshot): Snapshot {
  // Defensive shallow copy; messages live in the graph from here on.
  const nextData = { ...(snapshot.data ?? {}) };
  delete (nextData as Record<string, unknown>).messages;
  return { ...snapshot, data: nextData };
}

function mergeMessagesIntoSnapshot(
  blob: Snapshot,
  messages: StrandsMessage[],
): Snapshot {
  // Cast through unknown — Snapshot.data is typed as Record<string, JSONValue>
  // but Strands itself stores messages there, so the runtime shape matches.
  return {
    ...blob,
    data: { ...(blob.data ?? {}), messages: messages as unknown as never },
  };
}

function strandsMessageToText(msg: StrandsMessage): string {
  // Strands messages carry ContentBlock[]. Flatten plain-text blocks into a
  // single string; non-text blocks (images, tool uses) are described by tag.
  const blocks = (msg as unknown as { content: unknown[] }).content ?? [];
  if (!Array.isArray(blocks)) return "";
  const parts: string[] = [];
  for (const b of blocks) {
    if (b && typeof b === "object") {
      const block = b as { text?: unknown; type?: string };
      if (typeof block.text === "string") {
        parts.push(block.text);
      } else if (block.type) {
        parts.push(`[${block.type}]`);
      }
    }
  }
  return parts.join("\n");
}

function toStrandsMessage(m: { role: string; content: string }): StrandsMessage {
  return {
    role: m.role as StrandsMessage["role"],
    content: [{ text: m.content }] as unknown as StrandsMessage["content"],
  } as StrandsMessage;
}

function sameStateBlob(a: StrandsStateBlob, b: StrandsStateBlob): boolean {
  return (
    a.snapshotId === b.snapshotId &&
    a.isLatest === b.isLatest &&
    jsonLikeEqual(a.snapshot, b.snapshot)
  );
}

function findLastStateBlobForSnapshotId(
  blobs: StrandsStateBlob[],
  snapshotId: string,
): StrandsStateBlob | undefined {
  for (let i = blobs.length - 1; i >= 0; i--) {
    if (blobs[i]?.snapshotId === snapshotId) return blobs[i];
  }
  return undefined;
}

function jsonLikeEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((value, index) => jsonLikeEqual(value, b[index]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const aRecord = a as Record<string, unknown>;
    const bRecord = b as Record<string, unknown>;
    const aKeys = Object.keys(aRecord);
    const bKeys = Object.keys(bRecord);
    if (aKeys.length !== bKeys.length) return false;
    return aKeys.every((key) => key in bRecord && jsonLikeEqual(aRecord[key], bRecord[key]));
  }
  return false;
}
