/**
 * The canary suite. Every test in this file runs inside `workerd` — if the SDK,
 * the AI SDK, or anything they pull in needed a Node built-in, the file would
 * fail to load at all and nothing below would run.
 *
 * Two levels are exercised:
 *  - the handlers, against the real `MemoryClient` and its REST transport over a
 *    stubbed `fetch` (`test/fake-nams.ts`) plus the AI SDK's own mock model, and
 *  - the Worker's own `fetch` export, for routing and binding resolution, on the
 *    routes that need no model.
 */

import type {
  LanguageModelV4CallOptions,
  LanguageModelV4FinishReason,
  LanguageModelV4StreamPart,
  LanguageModelV4Usage,
} from "@ai-sdk/provider";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { MockLanguageModelV4 } from "ai/test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Env } from "../src/env.js";
import { NamsTimings, handleChat, handleGraph, handleMemory, type MemoryDeps } from "../src/handlers.js";
import worker from "../src/index.js";
import { FAKE_ENDPOINT, FakeNams } from "./fake-nams.js";

const API_KEY = "nams_test";
const DELTAS = ["Stay ", "in Gion ", "for the first two nights."];
const ANSWER = DELTAS.join("");

const FINISH: LanguageModelV4FinishReason = { unified: "stop", raw: "stop" };
const USAGE: LanguageModelV4Usage = {
  inputTokens: { total: 12, noCache: 12, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 9, text: 9, reasoning: 0 },
};

/** The AI SDK's own mock model; it records every call it receives. */
function mockModel(): MockLanguageModelV4 {
  return new MockLanguageModelV4({
    provider: "mock",
    modelId: "mock-model",
    doStream: async () => ({
      stream: new ReadableStream<LanguageModelV4StreamPart>({
        start(controller) {
          controller.enqueue({ type: "stream-start", warnings: [] });
          controller.enqueue({ type: "text-start", id: "0" });
          for (const delta of DELTAS) {
            controller.enqueue({ type: "text-delta", id: "0", delta });
          }
          controller.enqueue({ type: "text-end", id: "0" });
          controller.enqueue({ type: "finish", finishReason: FINISH, usage: USAGE });
          controller.close();
        },
      }),
    }),
  });
}

function promptText(call: LanguageModelV4CallOptions): string {
  return JSON.stringify(call.prompt);
}

function env(overrides: Partial<Env> = {}): Env {
  return {
    MEMORY_API_KEY: API_KEY,
    OPENAI_API_KEY: "sk-test",
    MEMORY_ENDPOINT: FAKE_ENDPOINT,
    ...overrides,
  };
}

/**
 * A test harness that mirrors `deps()` in `src/index.ts`, except the model is
 * mocked and `waitUntil` is collected so the assertions can decide when the
 * post-response work has landed.
 */
function harness(nams: FakeNams, model = mockModel()) {
  const pending: Promise<unknown>[] = [];
  const deps: MemoryDeps = {
    client: new MemoryClient({ apiKey: API_KEY, endpoint: FAKE_ENDPOINT }),
    model,
    userId: "edge-test-user",
    waitUntil: (promise) => pending.push(promise),
    nams: new NamsTimings(),
  };
  return {
    deps,
    model,
    nams,
    /** How many promises the handler handed to `waitUntil`. */
    deferred: () => pending.length,
    /** Await everything the handler deferred — what `waitUntil` does for real. */
    settle: async () => {
      await Promise.all(pending);
    },
  };
}

function chatRequest(message: string, conversationId?: string): Request {
  return new Request("https://worker.test/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, ...(conversationId ? { conversationId } : {}) }),
  });
}

let nams: FakeNams;

beforeEach(() => {
  nams = new FakeNams();
  vi.stubGlobal("fetch", nams.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("POST /chat", () => {
  it("streams the answer and persists both sides of the turn", async () => {
    const h = harness(nams);
    const response = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));

    expect(response.status).toBe(200);
    const conversationId = response.headers.get("X-Conversation-Id");
    expect(conversationId).toBeTruthy();
    expect(await response.text()).toBe(ANSWER);

    await h.settle();
    expect(nams.rolesOf(conversationId!)).toEqual(["user", "assistant"]);
    expect(nams.contentsOf(conversationId!)).toEqual([
      "Where should I stay in Kyoto?",
      ANSWER,
    ]);
  });

  it("defers the assistant write to waitUntil rather than firing and forgetting", async () => {
    const h = harness(nams);
    const response = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    const conversationId = response.headers.get("X-Conversation-Id")!;

    // The handler has returned a Response, but `streamText` is lazy: nothing
    // has been generated and *nothing has been written to memory* yet — not
    // even the user turn. Every memory write for this request happens after the
    // handler returned, which is precisely why it has to be held open.
    expect(nams.rolesOf(conversationId)).toEqual([]);
    // Exactly one promise was handed to the runtime to keep the isolate alive.
    expect(h.deferred()).toBe(1);

    await response.text(); // the client drains the stream
    await h.settle(); // what the runtime does with a `waitUntil` promise

    expect(nams.rolesOf(conversationId)).toEqual(["user", "assistant"]);
  });

  it("records a reasoning step and its tool call", async () => {
    const h = harness(nams);
    const response = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    const conversationId = response.headers.get("X-Conversation-Id")!;
    await response.text();
    await h.settle();

    expect(nams.steps).toHaveLength(1);
    expect(nams.steps[0]).toMatchObject({ conversationId, actionTaken: "generate_reply" });

    expect(nams.toolCalls).toHaveLength(1);
    expect(nams.toolCalls[0]).toMatchObject({
      stepId: nams.steps[0]!.id,
      toolName: "generate_reply",
      status: "success",
    });
    expect(nams.toolCalls[0]!.durationMs).toBeTypeOf("number");
  });

  it("replays stored context into the next turn, which the client never re-sent", async () => {
    const h = harness(nams);
    const first = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    const conversationId = first.headers.get("X-Conversation-Id")!;
    await first.text();
    await h.settle();

    const second = await handleChat(h.deps, chatRequest("For how long?", conversationId));
    await second.text();
    await h.settle();

    expect(h.model.doStreamCalls).toHaveLength(2);

    // Turn one: nothing stored yet, so only the new question.
    const one = promptText(h.model.doStreamCalls[0]!);
    expect(one).toContain("Where should I stay in Kyoto?");
    expect(one).not.toContain(ANSWER);

    // Turn two: the earlier question *and* answer are in the prompt, plus the
    // observation NAMS derived. The request body carried none of it.
    const two = promptText(h.model.doStreamCalls[1]!);
    expect(two).toContain("Where should I stay in Kyoto?");
    expect(two).toContain(ANSWER);
    expect(two).toContain("planning a trip to Kyoto");
    expect(two).toContain("For how long?");

    // One conversation, not two — the id round-trips through the client.
    expect(nams.conversations.size).toBe(1);
  });

  it("reaches NAMS as plain authenticated HTTPS and nothing else", async () => {
    const h = harness(nams);
    const response = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    const conversationId = response.headers.get("X-Conversation-Id")!;
    await response.text();
    await h.settle();

    expect(nams.trace()).toEqual([
      "POST /conversations",
      // Three-tier context first. On a brand-new conversation it comes back
      // empty, so the middleware falls back to flat history — hence the second
      // read. From turn two onwards only the context call is made.
      `GET /conversations/${conversationId}/context`,
      `GET /conversations/${conversationId}/messages`,
      `POST /conversations/${conversationId}/messages`, // user turn
      `POST /conversations/${conversationId}/messages`, // assistant turn
      "POST /reasoning/steps",
      "POST /reasoning/tool-calls",
    ]);
    for (const call of nams.calls) {
      expect(call.authorization).toBe(`Bearer ${API_KEY}`);
    }
  });

  it("reports what memory cost in Server-Timing", async () => {
    const h = harness(nams);
    const response = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    expect(response.headers.get("Server-Timing")).toMatch(/^nams;dur=\d+;desc="\d+ requests"$/);
  });

  it("rejects a body with no message", async () => {
    const h = harness(nams);
    const response = await handleChat(
      h.deps,
      new Request("https://worker.test/chat", { method: "POST", body: "{}" }),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: expect.stringContaining("message") });
  });
});

describe("GET /memory", () => {
  it("returns the injectable context and the reasoning behind the last turn", async () => {
    const h = harness(nams);
    const chat = await handleChat(h.deps, chatRequest("Where should I stay in Kyoto?"));
    const conversationId = chat.headers.get("X-Conversation-Id")!;
    await chat.text();
    await h.settle();

    const response = await handleMemory(
      h.deps,
      new URL(`https://worker.test/memory?conversationId=${conversationId}`),
    );
    const payload = (await response.json()) as {
      willBeInjectedNextTurn: { observations: string[]; recentMessages: unknown[] };
      reasoning: { steps: unknown[]; toolCalls: Array<{ toolName: string }> };
    };

    expect(payload.willBeInjectedNextTurn.recentMessages).toHaveLength(2);
    expect(payload.willBeInjectedNextTurn.observations[0]).toContain("Kyoto");
    expect(payload.reasoning.steps).toHaveLength(1);
    expect(payload.reasoning.toolCalls[0]?.toolName).toBe("generate_reply");
  });

  it("requires a conversationId", async () => {
    const h = harness(nams);
    const response = await handleMemory(h.deps, new URL("https://worker.test/memory"));
    expect(response.status).toBe(400);
  });
});

describe("GET /graph", () => {
  it("returns the workspace graph by default", async () => {
    const h = harness(nams);
    const response = await handleGraph(h.deps, new URL("https://worker.test/graph"));
    const payload = (await response.json()) as {
      scope: string;
      nodes: Array<{ name: string }>;
      edges: unknown[];
    };
    expect(payload.scope).toBe("workspace");
    expect(payload.nodes.map((n) => n.name)).toContain("Kyoto");
    expect(payload.edges).toHaveLength(1);
  });

  it("expands one hop around a named entity", async () => {
    const h = harness(nams);
    const response = await handleGraph(h.deps, new URL("https://worker.test/graph?entity=Kyoto"));
    const payload = (await response.json()) as { scope: string; nodes: unknown[] };
    expect(payload.scope).toBe("one-hop");
    expect(payload.nodes).toHaveLength(1);
    expect(nams.trace()).toEqual(["POST /entities/search", "POST /graph/expand"]);
  });

  it("404s on an entity the graph has never seen", async () => {
    const h = harness(nams);
    nams.entities = [];
    const response = await handleGraph(h.deps, new URL("https://worker.test/graph?entity=Nowhere"));
    expect(response.status).toBe(404);
  });
});

describe("the Worker's fetch export", () => {
  it("serves the usage note at /", async () => {
    const ctx = createExecutionContext();
    const response = await worker.fetch(new Request("https://worker.test/"), env(), ctx);
    await waitOnExecutionContext(ctx);
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("POST /chat");
  });

  it("builds the client from env bindings, not module scope", async () => {
    const ctx = createExecutionContext();
    const response = await worker.fetch(new Request("https://worker.test/graph"), env(), ctx);
    await waitOnExecutionContext(ctx);

    expect(response.status).toBe(200);
    expect(nams.calls[0]?.authorization).toBe(`Bearer ${API_KEY}`);
  });

  it("404s an unknown route", async () => {
    const ctx = createExecutionContext();
    const response = await worker.fetch(new Request("https://worker.test/nope"), env(), ctx);
    await waitOnExecutionContext(ctx);
    expect(response.status).toBe(404);
  });

  it("names the missing secret in the log, not in the response body", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const ctx = createExecutionContext();
      const response = await worker.fetch(
        new Request("https://worker.test/graph"),
        env({ MEMORY_API_KEY: "" }),
        ctx,
      );
      await waitOnExecutionContext(ctx);

      // The client sees a generic 500; the diagnosis (which secret to set)
      // goes to the console, where `wrangler tail` shows it.
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({ error: "request failed" });
      const logged = consoleError.mock.calls.map((call) => call.map(String).join(" ")).join("\n");
      expect(logged).toContain("wrangler secret put MEMORY_API_KEY");
      expect(nams.calls).toHaveLength(0);
    } finally {
      consoleError.mockRestore();
    }
  });
});

describe("edge safety", () => {
  // Vite inlines these at build time, so the scan itself needs no `node:fs`.
  const raw = import.meta.glob<string>("../src/**/*.ts", {
    query: "?raw",
    import: "default",
    eager: true,
  });

  /**
   * Strip comments, so the prose in this example (which talks about `node:`
   * imports and `process.env` constantly) cannot trip its own canary.
   */
  const sources = Object.fromEntries(
    Object.entries(raw).map(([path, source]) => [
      path,
      source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .split("\n")
        .filter((line) => !line.trim().startsWith("//"))
        .join("\n"),
    ]),
  );

  it("has sources to scan", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(0);
  });

  it("imports no Node built-ins anywhere in src/", () => {
    const offenders = Object.entries(sources).filter(([, source]) =>
      /from\s+["']node:|require\(["']node:/.test(source),
    );
    expect(offenders.map(([path]) => path)).toEqual([]);
  });

  it("touches no Node globals anywhere in src/", () => {
    const offenders = Object.entries(sources).filter(([, source]) =>
      /\bprocess\.env\b|\b__dirname\b|\bBuffer\./.test(source),
    );
    expect(offenders.map(([path]) => path)).toEqual([]);
  });
});
