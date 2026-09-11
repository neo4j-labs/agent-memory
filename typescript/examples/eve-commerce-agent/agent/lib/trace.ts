/**
 * Reasoning memory: one NAMS step per tool call, plus the products and brands
 * that call touched.
 *
 * This is the third memory type. Short-term holds the conversation, long-term
 * holds the shopper's preferences — and reasoning memory answers "why did the
 * assistant do that?". After a session you can ask the graph which step put a
 * product in front of this shopper, and `reasoning.getEntityProvenance(entityId)`
 * walks it back the other way: which steps influenced this entity.
 *
 * `agent/hooks/reasoning-trace.ts` calls into here from the `action.result`
 * stream event; everything below is ordinary async code so the test suite can
 * exercise it directly.
 */

import type { MemoryClient } from "@neo4j-labs/agent-memory";
import { memoryClient } from "./deps.js";
import { peekConversation } from "./memory-session.js";

/** One product or brand a tool result put in front of the shopper. */
export interface TouchedEntity {
  readonly name: string;
  /** POLE+O type: products are objects, brands are organizations. */
  readonly type: "OBJECT" | "ORGANIZATION";
  readonly description?: string;
}

export interface ToolCallRecord {
  readonly toolName: string;
  readonly input: unknown;
  readonly output: unknown;
}

export interface RecordedStep {
  readonly stepId: string;
  readonly conversationId: string;
  readonly touched: readonly TouchedEntity[];
}

/** `type:name` pairs already written this process, so a replay does not re-add. */
const registeredEntities = new Set<string>();

/** Test helper: forget which entities have been registered. */
export function forgetRegisteredEntities(): void {
  registeredEntities.clear();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

interface ProductLike {
  name: string;
  brand: string;
  description?: string;
}

function productLike(value: unknown): ProductLike | undefined {
  if (!isRecord(value)) return undefined;
  const name = value["name"];
  const brand = value["brand"];
  if (typeof name !== "string" || typeof brand !== "string") return undefined;
  const description = value["description"];
  return { name, brand, description: typeof description === "string" ? description : undefined };
}

/**
 * Pull the products and brands out of a tool result.
 *
 * Walks the output one and two levels deep (a tool returns either a product or
 * `{ products: [...] }` / `{ lines: [...] }`) and keeps anything with both a
 * `name` and a `brand` — which in this catalog is exactly a product.
 */
export function touchedEntities(output: unknown): TouchedEntity[] {
  const candidates: unknown[] = [output];
  if (isRecord(output)) {
    for (const value of Object.values(output)) {
      if (Array.isArray(value)) candidates.push(...value);
      else candidates.push(value);
    }
  } else if (Array.isArray(output)) {
    candidates.push(...output);
  }

  const touched = new Map<string, TouchedEntity>();
  for (const candidate of candidates) {
    const product = productLike(candidate);
    if (product === undefined) continue;
    touched.set(`OBJECT:${product.name}`, {
      name: product.name,
      type: "OBJECT",
      ...(product.description === undefined ? {} : { description: product.description }),
    });
    touched.set(`ORGANIZATION:${product.brand}`, { name: product.brand, type: "ORGANIZATION" });
  }
  return [...touched.values()];
}

function summarize(value: unknown, max = 400): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? null);
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/**
 * Record one tool call as a reasoning step and register what it touched.
 *
 * Returns `undefined` when no NAMS conversation is open for this eve session —
 * which is the case when the memory slot is disabled for an unscoped caller.
 * Shoppers without memory still get a working storefront.
 */
export async function recordToolCall(
  eveSessionId: string,
  record: ToolCallRecord,
  client: MemoryClient = memoryClient(),
): Promise<RecordedStep | undefined> {
  const conversationId = peekConversation(eveSessionId);
  if (conversationId === undefined) return undefined;

  const touched = touchedEntities(record.output);
  const step = await client.reasoning.recordStep({
    conversationId,
    reasoning: `Called ${record.toolName} with ${summarize(record.input, 200)}`,
    actionTaken: record.toolName,
    result: touched.length === 0
      ? summarize(record.output)
      : `${summarize(record.output)} | touched: ${touched.map((t) => t.name).join(", ")}`,
  });

  for (const entity of touched) {
    const key = `${entity.type}:${entity.name}`;
    if (registeredEntities.has(key)) continue;
    registeredEntities.add(key);
    await client.longTerm.addEntity(entity.name, entity.type, {
      ...(entity.description === undefined ? {} : { description: entity.description }),
    });
  }

  return { stepId: step.id, conversationId, touched };
}
