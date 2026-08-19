/**
 * Integration tests — connectMemoryToAgent + Neo4jConversationManager
 * against MSW. Drives the integration end-to-end with all three surfaces
 * (SessionStorage, ConversationManager, reasoning hooks) reaching the
 * mocked NAMS endpoint.
 *
 * The second test uses Neo4jConversationManager directly with a
 * NullConversationManager inner so SlidingWindow's own hooks (which need
 * agent internals our stub doesn't model) don't fire during the assertions.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  BeforeInvocationEvent,
  BeforeToolCallEvent,
  AfterToolCallEvent,
  AfterInvocationEvent,
  NullConversationManager,
  type LocalAgent,
} from "@strands-agents/sdk";
import { MemoryClient } from "../../src/client.js";
import {
  Neo4jConversationManager,
  connectMemoryToAgent,
  registerReasoningHooks,
} from "../../src/integrations/strands/index.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";
const CONV = "conv-factory";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function makeAgentStub() {
  const hooks = new Map<unknown, Array<(e: unknown) => Promise<void> | void>>();
  const messages: Array<Record<string, unknown>> = [
    { role: "user", content: [{ text: "Hello" }] },
  ];
  const agent = {
    id: "a",
    messages,
    addHook(eventClass: unknown, cb: (e: unknown) => Promise<void> | void) {
      const list = hooks.get(eventClass) ?? [];
      list.push(cb);
      hooks.set(eventClass, list);
      return () => {};
    },
  } as unknown as LocalAgent;
  return { agent, hooks };
}

describe("connectMemoryToAgent — return shape", () => {
  it("returns sessionManager + conversationManager ready to spread into new Agent", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV}/context`, () =>
        HttpResponse.json({ reflections: [], observations: [], recentMessages: [] }),
      ),
    );
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { sessionManager, conversationManager } = await connectMemoryToAgent(memory, {
      conversationId: CONV,
    });

    expect(sessionManager).toBeDefined();
    expect(conversationManager).toBeDefined();
    expect(conversationManager.name).toBe("neo4j:context-injection");
    expect(typeof conversationManager.initAgent).toBe("function");
    expect(typeof conversationManager.reduce).toBe("function");
  });
});

describe("Neo4jConversationManager + reasoning hooks — end-to-end against MSW", () => {
  it("context injection, reasoning step, and tool call all wire through to NAMS", async () => {
    const steps: Array<Record<string, unknown>> = [];
    const toolCalls: Array<Record<string, unknown>> = [];

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV}/context`, () =>
        HttpResponse.json({
          reflections: [{ id: "r1", conversationId: CONV, content: "be concise", createdAt: "x" }],
          observations: [],
          recentMessages: [],
        }),
      ),
      http.post(`${ENDPOINT}/reasoning/steps`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        steps.push(body);
        return HttpResponse.json({ id: `step-${steps.length}`, created_at: "x" });
      }),
      http.post(`${ENDPOINT}/reasoning/tool-calls`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        toolCalls.push(body);
        return HttpResponse.json({ id: `tc-${toolCalls.length}`, created_at: "x" });
      }),
    );

    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    // Build the conversation manager directly with a Null inner so the
    // assertions below don't trip over SlidingWindow's internal hooks.
    const cm = new Neo4jConversationManager(memory, {
      conversationId: CONV,
      inner: new NullConversationManager(),
    });
    const { agent, hooks } = makeAgentStub();
    await cm.initAgent(agent);
    // Reasoning hooks would normally be attached by the factory's
    // initAgent wrapper. Attach them explicitly to match the same
    // composition.
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    // Fire BeforeInvocation — context injection AND reasoning step.
    const inv: Record<string, unknown> = {};
    const event = new BeforeInvocationEvent({
      agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
      invocationState: inv,
    });
    const beforeInvCallbacks = hooks.get(BeforeInvocationEvent) ?? [];
    expect(beforeInvCallbacks.length).toBeGreaterThanOrEqual(2);
    for (const cb of beforeInvCallbacks) await cb(event);

    // Assertion 1 — context injection.
    const texts = (agent as unknown as { messages: Array<Record<string, unknown>> })
      .messages.map((m) => ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string);
    expect(texts[0]).toBe("[reflection] be concise");
    expect(texts[texts.length - 1]).toBe("Hello");

    // Assertion 2 — reasoning step recorded.
    expect(steps).toHaveLength(1);
    expect(inv["__neo4jReasoningStepId"]).toBe("step-1");

    // Fire BeforeToolCall + AfterToolCall.
    const beforeToolCb = (hooks.get(BeforeToolCallEvent) ?? [])[0]!;
    const afterToolCb = (hooks.get(AfterToolCallEvent) ?? [])[0]!;
    await beforeToolCb(
      new BeforeToolCallEvent({
        agent: {} as ConstructorParameters<typeof BeforeToolCallEvent>[0]["agent"],
        toolUse: { toolUseId: "tu", name: "search", input: { q: "neo4j" } },
        tool: undefined,
        invocationState: inv,
      } as unknown as ConstructorParameters<typeof BeforeToolCallEvent>[0]),
    );
    await afterToolCb(
      new AfterToolCallEvent({
        agent: {} as ConstructorParameters<typeof AfterToolCallEvent>[0]["agent"],
        toolUse: { toolUseId: "tu", name: "search", input: { q: "neo4j" } },
        tool: undefined,
        result: {
          toolUseId: "tu",
          content: [],
          status: "success",
        } as ConstructorParameters<typeof AfterToolCallEvent>[0]["result"],
        error: undefined,
        invocationState: inv,
      } as unknown as ConstructorParameters<typeof AfterToolCallEvent>[0]),
    );
    expect(toolCalls).toHaveLength(2);
    expect(toolCalls[0]!.status).toBe("pending");
    expect(toolCalls[1]!.status).toBe("success");

    // Fire AfterInvocation — closing step recorded.
    const afterInvCb = (hooks.get(AfterInvocationEvent) ?? [])[0]!;
    await afterInvCb(
      new AfterInvocationEvent({
        agent: {} as ConstructorParameters<typeof AfterInvocationEvent>[0]["agent"],
        invocationState: inv,
      }),
    );
    expect(steps).toHaveLength(2);
  });
});
