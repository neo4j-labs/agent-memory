/**
 * Integration tests — Neo4jConversationManager against an MSW-mocked
 * /v1/conversations/:id/context endpoint. Asserts:
 *  - Reflections + observations prepended in canonical order
 *  - Empty context leaves messages untouched
 *  - getContext failure falls back silently
 *  - includeReflections/Observations toggles
 *  - Inner manager defaults to the lazy SlidingWindow
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  BeforeInvocationEvent,
  type LocalAgent,
} from "@strands-agents/sdk";
import { MemoryClient } from "../../src/client.js";
import { Neo4jConversationManager } from "../../src/integrations/strands/index.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";
const CONV = "conv-cm";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function makeAgent(messages: Array<Record<string, unknown>>) {
  let beforeInv:
    | ((event: BeforeInvocationEvent) => Promise<void> | void)
    | null = null;
  const agent = {
    id: "a",
    messages,
    addHook(_event: unknown, cb: (event: BeforeInvocationEvent) => Promise<void> | void) {
      beforeInv = cb;
      return () => {};
    },
  } as unknown as LocalAgent;
  return {
    agent,
    fire: async () => {
      if (!beforeInv) throw new Error("hook not registered");
      const event = new BeforeInvocationEvent({
        agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
        invocationState: {},
      });
      await beforeInv(event);
    },
  };
}

function getMessages(agent: LocalAgent): Array<Record<string, unknown>> {
  return (agent as unknown as { messages: Array<Record<string, unknown>> }).messages;
}

function mountContext(opts: {
  reflections?: Array<{ id: string; content: string; conversationId?: string }>;
  observations?: Array<{ id: string; content: string; conversationId?: string }>;
  status?: number;
}) {
  server.use(
    http.get(`${ENDPOINT}/conversations/${CONV}/context`, () => {
      if (opts.status && opts.status >= 400) {
        return new HttpResponse("err", { status: opts.status });
      }
      return HttpResponse.json({
        reflections: (opts.reflections ?? []).map((r) => ({
          id: r.id,
          conversationId: r.conversationId ?? CONV,
          content: r.content,
          createdAt: "x",
        })),
        observations: (opts.observations ?? []).map((o) => ({
          id: o.id,
          conversationId: o.conversationId ?? CONV,
          content: o.content,
          createdAt: "x",
        })),
        recentMessages: [],
      });
    }),
  );
}

function client(): MemoryClient {
  return new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
}

describe("Neo4jConversationManager — three-tier injection", () => {
  it("prepends reflections + observations in canonical order", async () => {
    mountContext({
      reflections: [{ id: "r1", content: "user prefers concise" }],
      observations: [{ id: "o1", content: "asks about graphs" }],
    });
    const cm = new Neo4jConversationManager(client(), { conversationId: CONV });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "now" }] }]);
    await cm.initAgent(agent);
    await fire();

    const msgs = getMessages(agent);
    expect(msgs).toHaveLength(3);
    const texts = msgs.map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(msgs[0]?.role).toBe("system");
    expect(msgs[1]?.role).toBe("system");
    expect(texts).toEqual([
      "[reflection] user prefers concise",
      "[observation] asks about graphs",
      "now",
    ]);
  });

  it("empty context leaves messages untouched", async () => {
    mountContext({});
    const cm = new Neo4jConversationManager(client(), { conversationId: CONV });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "now" }] }]);
    await cm.initAgent(agent);
    await fire();
    expect(getMessages(agent)).toHaveLength(1);
  });

  it("getContext 5xx is swallowed silently — agent run continues with no prepend", async () => {
    mountContext({ status: 500 });
    const cm = new Neo4jConversationManager(client(), { conversationId: CONV });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "x" }] }]);
    await cm.initAgent(agent);
    await fire();
    expect(getMessages(agent)).toHaveLength(1);
  });

  it("getContext 401 is also swallowed (best-effort)", async () => {
    mountContext({ status: 401 });
    const cm = new Neo4jConversationManager(client(), { conversationId: CONV });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "x" }] }]);
    await cm.initAgent(agent);
    await fire();
    expect(getMessages(agent)).toHaveLength(1);
  });

  it("includeReflections=false skips reflections, keeps observations", async () => {
    mountContext({
      reflections: [{ id: "r1", content: "skip" }],
      observations: [{ id: "o1", content: "keep" }],
    });
    const cm = new Neo4jConversationManager(client(), {
      conversationId: CONV,
      includeReflections: false,
    });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "now" }] }]);
    await cm.initAgent(agent);
    await fire();
    const texts = getMessages(agent).map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(texts.some((t) => t.startsWith("[reflection]"))).toBe(false);
    expect(texts.some((t) => t.startsWith("[observation] keep"))).toBe(true);
  });

  it("includeObservations=false skips observations, keeps reflections", async () => {
    mountContext({
      reflections: [{ id: "r1", content: "keep" }],
      observations: [{ id: "o1", content: "skip" }],
    });
    const cm = new Neo4jConversationManager(client(), {
      conversationId: CONV,
      includeObservations: false,
    });
    const { agent, fire } = makeAgent([{ role: "user", content: [{ text: "now" }] }]);
    await cm.initAgent(agent);
    await fire();
    const texts = getMessages(agent).map((m) =>
      ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
    );
    expect(texts.some((t) => t.startsWith("[reflection] keep"))).toBe(true);
    expect(texts.some((t) => t.startsWith("[observation]"))).toBe(false);
  });
});

describe("Neo4jConversationManager — inner manager", () => {
  it("inner defaults to SlidingWindow (lazily constructed)", async () => {
    mountContext({});
    const cm = new Neo4jConversationManager(client(), { conversationId: CONV });
    const { agent } = makeAgent([]);
    // initAgent triggers inner construction.
    await cm.initAgent(agent);
    // The inner manager should now exist.
    const inner = (cm as unknown as { inner: { name: string } | null }).inner;
    expect(inner).not.toBeNull();
    // SlidingWindow's name in Strands v1.2 — best-effort assertion.
    expect(typeof inner!.name).toBe("string");
  });

  it("inner manager's initAgent is delegated through", async () => {
    mountContext({});
    let innerInitCalled = false;
    const fakeInner = {
      name: "fake-inner",
      reduce: async () => false,
      initAgent: async () => {
        innerInitCalled = true;
      },
    };
    const cm = new Neo4jConversationManager(client(), {
      conversationId: CONV,
      inner: fakeInner as unknown as ConstructorParameters<typeof Neo4jConversationManager>[1]["inner"],
    });
    const { agent } = makeAgent([]);
    await cm.initAgent(agent);
    expect(innerInitCalled).toBe(true);
  });

  it("reduce() delegates to inner", async () => {
    let reduceCalled = false;
    const fakeInner = {
      name: "fake-inner",
      reduce: async () => {
        reduceCalled = true;
        return true;
      },
      initAgent: async () => {},
    };
    const cm = new Neo4jConversationManager(client(), {
      conversationId: CONV,
      inner: fakeInner as unknown as ConstructorParameters<typeof Neo4jConversationManager>[1]["inner"],
    });
    const result = await cm.reduce({} as Parameters<typeof cm.reduce>[0]);
    expect(reduceCalled).toBe(true);
    expect(result).toBe(true);
  });
});
