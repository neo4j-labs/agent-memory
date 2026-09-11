/**
 * Mapping between an eve session and a NAMS conversation, plus the shopper
 * identity helpers every memory call needs.
 *
 * An eve session is durable and can outlive the process that started it, so the
 * mapping cannot live only in process memory: the lookup below is memoised per
 * process but falls back to `listConversations({ userId })` and matches on the
 * `eveSessionId` we stamped into the conversation's metadata. A redeployed
 * runtime therefore re-attaches to the same NAMS conversation instead of
 * forking a new one.
 */

import type { MemoryClient } from "@neo4j-labs/agent-memory";
import type { MemoryScope } from "eve/memory";

/** Stamped on every conversation this example creates. */
export const CONVERSATION_SOURCE = "eve-commerce-agent";

/** eve session id → NAMS conversation id. */
const conversations = new Map<string, string>();

/**
 * The shopper id for a locked memory scope.
 *
 * `scope.value` is what the slot's resolver returned (see
 * `agent/memory/shopper.ts`) — trusted channel identity, never model input. A
 * tuple scope is joined so a multi-part scope still yields one stable id.
 */
export function shopperIdFromScope(scope: MemoryScope): string {
  return typeof scope.value === "string" ? scope.value : scope.value.join("/");
}

/** Marker written into a preference's `context` so reads can filter by shopper. */
export function shopperTag(shopperId: string): string {
  return `shopper:${shopperId}`;
}

export function belongsToShopper(
  preference: { readonly context?: string },
  shopperId: string,
): boolean {
  return (preference.context ?? "").includes(shopperTag(shopperId));
}

/**
 * The NAMS conversation for this eve session, creating it on first use.
 *
 * Safe to call on every turn: the first call per process costs one list (and
 * possibly one create), every later call is a map hit.
 */
export async function resolveConversation(
  client: MemoryClient,
  eveSessionId: string,
  shopperId: string,
): Promise<string> {
  const memoised = conversations.get(eveSessionId);
  if (memoised !== undefined) return memoised;

  const existing = await client.shortTerm.listConversations({ userId: shopperId, limit: 50 });
  const match = existing.find(
    (conversation) =>
      conversation.metadata?.["source"] === CONVERSATION_SOURCE &&
      conversation.metadata?.["eveSessionId"] === eveSessionId,
  );
  if (match !== undefined) {
    conversations.set(eveSessionId, match.id);
    return match.id;
  }

  const created = await client.shortTerm.createConversation({
    userId: shopperId,
    metadata: { source: CONVERSATION_SOURCE, eveSessionId },
  });
  conversations.set(eveSessionId, created.id);
  return created.id;
}

/**
 * The conversation for an eve session *if one has already been resolved this
 * process*. The reasoning hook uses this: recall runs at `turn.started`, so by
 * the time a tool result arrives the conversation exists — and when the memory
 * slot is disabled (an unscoped caller) it correctly returns `undefined` and
 * the hook stays quiet.
 */
export function peekConversation(eveSessionId: string): string | undefined {
  return conversations.get(eveSessionId);
}

/** Test helper: drop the per-process memo. */
export function forgetConversations(): void {
  conversations.clear();
}
