/**
 * Runtime contexts eve would normally build.
 *
 * eve calls tools, hooks and memory handlers with real context objects; these
 * builders make the same shapes by hand so the suite can call each handler
 * directly. Everything is a genuine value of the published type — no casts — so
 * a context field that changes shape in a future eve release fails `typecheck`
 * here instead of at runtime in production.
 */

import type { MemoryClient } from "@neo4j-labs/agent-memory";
import type { ModelMessage } from "ai";
import type { SessionAuthContext, SessionContext } from "eve/context";
import type { HookContext } from "eve/hooks";
import type {
  MemoryScope,
  MemoryToolsContext,
  MemoryTurnCompletedContext,
  MemoryTurnStartedContext,
} from "eve/memory";
import type { ToolContext } from "eve/tools";
import { EMPTY_CART, type Cart, type CartStore } from "../agent/lib/cart.js";

export const SHOPPER = "shopper-ada";
export const OTHER_SHOPPER = "shopper-bo";

/** An in-memory cart, standing in for the durable session-state slot. */
export function fakeCart(initial: Cart = EMPTY_CART): CartStore {
  let cart = initial;
  return {
    get: () => cart,
    update: (fn) => {
      cart = fn(cart);
    },
  };
}

export function shopperPrincipal(shopperId = SHOPPER): SessionAuthContext {
  return {
    attributes: { source: "header" },
    authenticator: "demo-shopper-header",
    principalId: shopperId,
    principalType: "user",
  };
}

function unavailable(what: string): never {
  throw new Error(`${what} is not available in unit tests`);
}

function sessionContext(sessionId: string, shopperId: string): SessionContext {
  const principal = shopperPrincipal(shopperId);
  return {
    session: {
      id: sessionId,
      auth: { current: principal, initiator: principal },
      turn: { id: "turn_0", sequence: 0 },
    },
    getSandbox: () => unavailable("a sandbox"),
    getSkill: () => unavailable("a skill handle"),
  };
}

export function toolContext(
  options: { sessionId?: string; shopperId?: string; callId?: string; toolName?: string } = {},
): ToolContext {
  return {
    ...sessionContext(options.sessionId ?? "sess_1", options.shopperId ?? SHOPPER),
    abortSignal: new AbortController().signal,
    callId: options.callId ?? "call_0001ab",
    toolName: options.toolName ?? "tool",
    getToken: () => unavailable("outbound auth"),
    requireAuth: () => unavailable("outbound auth"),
  };
}

export function hookContext(
  options: { sessionId?: string; shopperId?: string } = {},
): HookContext {
  return {
    ...sessionContext(options.sessionId ?? "sess_1", options.shopperId ?? SHOPPER),
    agent: { name: "eve-commerce-agent" },
    channel: { kind: "http" },
  };
}

export function memoryScope(shopperId = SHOPPER): MemoryScope {
  return {
    key: `ns:shopper:${shopperId}:v1`,
    namespace: "eve-commerce-agent/shopper",
    value: shopperId,
  };
}

export function userMessage(text: string): ModelMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

export function assistantMessage(text: string): ModelMessage {
  return { role: "assistant", content: [{ type: "text", text }] };
}

interface MemoryContextOptions {
  sessionId?: string;
  shopperId?: string;
  turnId?: string;
  sequence?: number;
  operationId?: string;
  input?: readonly ModelMessage[];
  messages?: readonly ModelMessage[];
}

export function recallContext(options: MemoryContextOptions = {}): MemoryTurnStartedContext {
  const sessionId = options.sessionId ?? "sess_1";
  const shopperId = options.shopperId ?? SHOPPER;
  return {
    ...sessionContext(sessionId, shopperId),
    abortSignal: new AbortController().signal,
    messages: options.messages ?? [],
    operationId: options.operationId ?? "op-recall-1",
    memory: { scope: memoryScope(shopperId), slot: "shopper" },
    turn: {
      id: options.turnId ?? "turn_0",
      input: options.input ?? [userMessage("hello")],
      sequence: options.sequence ?? 0,
    },
  };
}

export function captureContext(options: MemoryContextOptions = {}): MemoryTurnCompletedContext {
  const recall = recallContext(options);
  return { ...recall, operationId: options.operationId ?? "op-capture-1" };
}

export function memoryToolsContext(
  options: { sessionId?: string; shopperId?: string; turnId?: string } = {},
): MemoryToolsContext {
  const shopperId = options.shopperId ?? SHOPPER;
  const principal = shopperPrincipal(shopperId);
  return {
    session: {
      id: options.sessionId ?? "sess_1",
      auth: { current: principal, initiator: principal },
    },
    channel: { kind: "http" },
    messages: [],
    memory: { scope: memoryScope(shopperId), slot: "shopper" },
    turn: { id: options.turnId ?? "turn_0", input: [], sequence: 0 },
  };
}

/**
 * Await a tool result.
 *
 * `defineTool` allows `execute` to be an async generator (for tools that stream
 * preliminary snapshots), so its declared return type is
 * `T | Promise<T> | AsyncIterable<T>`. None of this example's tools is a
 * generator, so this narrows that union back to `T` for the assertions.
 */
export async function runTool<T>(result: T | Promise<T> | AsyncIterable<T>): Promise<T> {
  return (await result) as T;
}

/** The same, for a memory-provider tool (whose input type is erased to `never`). */
export async function runMemoryTool(
  tool: { execute(input: never, ctx: ToolContext): unknown },
  input: unknown,
  ctx: ToolContext,
): Promise<unknown> {
  return await tool.execute(input as never, ctx);
}

export type { MemoryClient };
