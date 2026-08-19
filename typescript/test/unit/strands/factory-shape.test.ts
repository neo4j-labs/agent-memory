/**
 * Pure-logic tests for connectMemoryToAgent — confirms the factory returns
 * the right shape for spreading into `new Agent({ ... })` and that its
 * conversation manager registers BOTH the context injection hook and the
 * reasoning hooks when initAgent runs.
 */

import { describe, it, expect } from "vitest";
import {
  BeforeInvocationEvent,
  AfterInvocationEvent,
  BeforeToolCallEvent,
  AfterToolCallEvent,
  type LocalAgent,
} from "@strands-agents/sdk";
import { connectMemoryToAgent } from "../../../src/integrations/strands/index.js";

function makeMemoryStub() {
  return {
    transport: { async request() { return undefined; } },
    shortTerm: {
      async getContext() {
        return { reflections: [], observations: [], recentMessages: [] };
      },
      async getConversation() { return { id: "c", messages: [] }; },
      async getConversationMetadata() { return { id: "c", metadata: {} }; },
      async addMessage() { return { id: "m", role: "user", content: "" }; },
      async deleteConversation() { return undefined; },
    },
    reasoning: {
      async recordStep() { return { id: "s1" } as unknown as { id: string }; },
      async recordToolCall() { return { id: "t1" } as unknown as { id: string }; },
    },
  } as unknown as Parameters<typeof connectMemoryToAgent>[0];
}

function makeAgent() {
  const hooks = new Map<unknown, Array<(e: unknown) => Promise<void> | void>>();
  const agent = {
    id: "a",
    messages: [],
    addHook(eventClass: unknown, cb: (e: unknown) => Promise<void> | void) {
      const list = hooks.get(eventClass) ?? [];
      list.push(cb);
      hooks.set(eventClass, list);
      return () => {};
    },
  } as unknown as LocalAgent;
  return { agent, hooks };
}

describe("connectMemoryToAgent — factory shape", () => {
  it("returns sessionManager + conversationManager", async () => {
    const memory = makeMemoryStub();
    const result = await connectMemoryToAgent(memory, { conversationId: "c1" });
    expect(result).toHaveProperty("sessionManager");
    expect(result).toHaveProperty("conversationManager");
    expect(typeof result.conversationManager.initAgent).toBe("function");
  });

  it("conversationManager.initAgent registers all four reasoning hooks + context injection", async () => {
    const memory = makeMemoryStub();
    const { conversationManager } = await connectMemoryToAgent(memory, {
      conversationId: "c1",
    });
    const { agent, hooks } = makeAgent();
    await conversationManager.initAgent(agent);

    // Context-injection hook registers BeforeInvocationEvent;
    // reasoning hooks register all four — so the BeforeInvocation registry
    // should have at least 2 callbacks (context + reasoning).
    expect((hooks.get(BeforeInvocationEvent) ?? []).length).toBeGreaterThanOrEqual(2);
    expect(hooks.get(AfterInvocationEvent)?.length ?? 0).toBeGreaterThanOrEqual(1);
    expect(hooks.get(BeforeToolCallEvent)?.length ?? 0).toBeGreaterThanOrEqual(1);
    expect(hooks.get(AfterToolCallEvent)?.length ?? 0).toBeGreaterThanOrEqual(1);
  });

  it("conversationManager has a stable name", async () => {
    const memory = makeMemoryStub();
    const { conversationManager } = await connectMemoryToAgent(memory, {
      conversationId: "c1",
    });
    expect(conversationManager.name).toBe("neo4j:context-injection");
  });
});
