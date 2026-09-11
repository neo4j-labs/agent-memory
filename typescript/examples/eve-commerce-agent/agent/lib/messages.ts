/**
 * Flattening AI SDK `ModelMessage`s into the plain strings NAMS stores.
 *
 * eve hands authored memory handlers the conversation as `ModelMessage[]` (the
 * `ai` 7 shape), where `content` is either a string or an array of parts. NAMS
 * messages are `{ role, content: string }`, so tool calls and tool results are
 * dropped here — they are recorded separately as reasoning steps by
 * `agent/hooks/reasoning-trace.ts`.
 */

import type { ModelMessage } from "ai";

/** The text of one message, or `undefined` when it carries no text part. */
export function messageText(message: ModelMessage): string | undefined {
  if (typeof message.content === "string") {
    const trimmed = message.content.trim();
    return trimmed === "" ? undefined : trimmed;
  }
  const text = message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("")
    .trim();
  return text === "" ? undefined : text;
}

/** The text of every user message in `messages`. */
export function userTexts(messages: readonly ModelMessage[]): string[] {
  return messages
    .filter((message) => message.role === "user")
    .map(messageText)
    .filter((text): text is string => text !== undefined);
}

/**
 * The assistant text at the end of a settled history — the reply this turn
 * produced. Walks backwards and stops at the first non-assistant message, so an
 * earlier turn's reply is never re-captured.
 */
export function assistantTail(messages: readonly ModelMessage[]): string[] {
  const tail: string[] = [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message === undefined) break;
    if (message.role !== "assistant") break;
    const text = messageText(message);
    if (text !== undefined) tail.unshift(text);
  }
  return tail;
}

export function firstText(texts: readonly string[]): string | undefined {
  return texts.length > 0 ? texts[0] : undefined;
}
