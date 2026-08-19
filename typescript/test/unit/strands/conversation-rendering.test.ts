/**
 * Pure-logic tests for context injection: given a synthetic
 * `BeforeInvocationEvent`, verify the Neo4jConversationManager prepends
 * reflections + observations to `agent.messages` in the right order, and
 * falls back gracefully when context fetch fails or returns empty.
 */

import { describe, it, expect } from "vitest";
import { BeforeInvocationEvent } from "@strands-agents/sdk";
import { Neo4jConversationManager } from "../../../src/integrations/strands/index.js";

function makeMemoryStub(ctx: unknown | (() => Promise<unknown>)) {
  return {
    shortTerm: {
      getContext: async () =>
        typeof ctx === "function" ? await (ctx as () => Promise<unknown>)() : ctx,
    },
  } as unknown as ConstructorParameters<typeof Neo4jConversationManager>[0];
}

function makeAgent(messages: Array<Record<string, unknown>>) {
  let hookCb:
    | ((event: BeforeInvocationEvent) => Promise<void> | void)
    | null = null;
  const agent = {
    id: "a1",
    messages,
    addHook(_eventClass: unknown, cb: (event: BeforeInvocationEvent) => Promise<void> | void) {
      hookCb = cb;
      return () => {};
    },
  } as unknown as Parameters<Neo4jConversationManager["initAgent"]>[0];
  return { agent, fire: () => hookCb };
}

async function runHook(
  cm: Neo4jConversationManager,
  messages: Array<Record<string, unknown>>,
): Promise<Array<Record<string, unknown>>> {
  const { agent, fire } = makeAgent(messages);
  await cm.initAgent(agent);
  const cb = fire();
  if (!cb) throw new Error("hook was not registered");
  const event = new BeforeInvocationEvent({
    agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
    invocationState: {},
  });
  await cb(event);
  // Read the mutated messages off the agent stub.
  return (agent as unknown as { messages: Array<Record<string, unknown>> }).messages;
}

describe("Neo4jConversationManager — context injection", () => {
  it("prepends reflections + observations in the canonical order", async () => {
    const memory = makeMemoryStub({
      reflections: [{ id: "r1", content: "user prefers concise replies" }],
      observations: [{ id: "o1", content: "asks about graphs" }],
      recentMessages: [],
    });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "now" }] }]);
    expect(result).toHaveLength(3);
    expect(result[0]).toMatchObject({
      content: [{ text: "[reflection] user prefers concise replies" }],
    });
    expect(result[1]).toMatchObject({
      content: [{ text: "[observation] asks about graphs" }],
    });
    expect(result[2]).toMatchObject({ role: "user" });
  });

  it("empty context leaves messages untouched", async () => {
    const memory = makeMemoryStub({ reflections: [], observations: [], recentMessages: [] });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "now" }] }]);
    expect(result).toHaveLength(1);
  });

  it("includeReflections=false skips reflections, keeps observations", async () => {
    const memory = makeMemoryStub({
      reflections: [{ id: "r1", content: "skip me" }],
      observations: [{ id: "o1", content: "keep me" }],
      recentMessages: [],
    });
    const cm = new Neo4jConversationManager(memory, {
      conversationId: "c1",
      includeReflections: false,
    });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "now" }] }]);
    const texts = result.map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(texts.some((t) => t.startsWith("[reflection]"))).toBe(false);
    expect(texts.some((t) => t.startsWith("[observation] keep me"))).toBe(true);
  });

  it("includeObservations=false skips observations, keeps reflections", async () => {
    const memory = makeMemoryStub({
      reflections: [{ id: "r1", content: "keep me" }],
      observations: [{ id: "o1", content: "skip me" }],
      recentMessages: [],
    });
    const cm = new Neo4jConversationManager(memory, {
      conversationId: "c1",
      includeObservations: false,
    });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "now" }] }]);
    const texts = result.map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(texts.some((t) => t.startsWith("[reflection] keep me"))).toBe(true);
    expect(texts.some((t) => t.startsWith("[observation]"))).toBe(false);
  });

  it("getContext failure is swallowed silently", async () => {
    const memory = makeMemoryStub(() => Promise.reject(new Error("transient")));
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "x" }] }]);
    expect(result).toHaveLength(1);
  });

  it("preserves stable order across multiple reflections + observations", async () => {
    const memory = makeMemoryStub({
      reflections: [
        { id: "r1", content: "first" },
        { id: "r2", content: "second" },
      ],
      observations: [
        { id: "o1", content: "alpha" },
        { id: "o2", content: "beta" },
      ],
      recentMessages: [],
    });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });
    const result = await runHook(cm, [{ role: "user", content: [{ text: "go" }] }]);
    const texts = result.map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(texts).toEqual([
      "[reflection] first",
      "[reflection] second",
      "[observation] alpha",
      "[observation] beta",
      "go",
    ]);
  });
});
