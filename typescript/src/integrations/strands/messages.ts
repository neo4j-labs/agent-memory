/**
 * Strands ↔ NAMS message mapping. Shared by session-storage (snapshot
 * round-trip) and memory-store (batch ingestion) so both flatten turns the
 * same way.
 */

import type { Message as StrandsMessage } from "@strands-agents/sdk";

/**
 * Flatten a Strands message's content blocks into one string.
 *
 * Text blocks concatenate. A block that declares a `type` but no text is
 * reduced to its tag, which keeps a tool turn visible without inventing
 * content. Data-form blocks (`{ toolUse: … }` off `MessageData`) have neither,
 * so they contribute nothing and an all-tool turn flattens to `""`.
 *
 * Typed loosely on purpose: `Message`, `MessageData` and a raw snapshot record
 * all arrive here with the same `content` array.
 */
export function strandsMessageToText(message: { content?: unknown }): string {
  const blocks = message.content;
  if (!Array.isArray(blocks)) return "";
  const parts: string[] = [];
  for (const candidate of blocks) {
    if (candidate && typeof candidate === "object") {
      const block = candidate as { text?: unknown; type?: string };
      if (typeof block.text === "string") {
        parts.push(block.text);
      } else if (block.type) {
        parts.push(`[${block.type}]`);
      }
    }
  }
  return parts.join("\n");
}

/** Wrap a stored message as a single-text-block Strands message. */
export function toStrandsMessage(m: { role: string; content: string }): StrandsMessage {
  return {
    role: m.role as StrandsMessage["role"],
    content: [{ text: m.content }] as unknown as StrandsMessage["content"],
  } as StrandsMessage;
}
