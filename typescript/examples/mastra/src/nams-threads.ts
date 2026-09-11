/**
 * The whole bridge between NAMS history and Mastra message input — one type and
 * one function. Copy this file into your own app.
 */

import type { MastraMemoryMessage } from "@neo4j-labs/agent-memory/integrations/mastra";

/**
 * One message as `agent.generate()` takes it. Mastra accepts AI SDK
 * `ModelMessage`s, which are role-discriminated — hence the union rather than a
 * single interface with a union-typed `role`. Mapping inline without this
 * annotation (`history.map((m) => ({ role: m.role, content: m.content }))`)
 * does not type-check against Mastra's `MessageListItem`.
 */
export type MastraInputMessage =
  | { role: "system"; content: string }
  | { role: "user"; content: string }
  | { role: "assistant"; content: string };

/** NAMS thread history -> Mastra message input. */
export function toMastraMessages(history: MastraMemoryMessage[]): MastraInputMessage[] {
  return history.map((m) => ({ role: m.role, content: m.content }));
}

/** A system message carrying a recalled preference into a brand-new thread. */
export function preferenceReminder(category: string, preference: string): MastraInputMessage {
  return { role: "system", content: `Known preference (${category}): ${preference}` };
}
