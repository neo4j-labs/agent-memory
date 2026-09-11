/**
 * The route handlers, end to end, with no API key and no network.
 *
 * The NAMS side is a mock REST service (msw) driven through the real
 * `MemoryClient`/`RestTransport`; the model side is the AI SDK's own
 * `MockLanguageModelV4` from `ai/test`, which records every call it receives.
 *
 * The assertions are the ones that fail if memory stops working:
 *   - the chat route persists both sides of a turn,
 *   - the prompt the model sees carries history the request never sent,
 *   - `expandGraph` receives the ids already on the canvas,
 *   - the extraction probe waits for a *new* entity before the rail refetches,
 *   - the answered turn lands in the reasoning trace.
 */

import type {
  LanguageModelV4CallOptions,
  LanguageModelV4FinishReason,
  LanguageModelV4StreamPart,
  LanguageModelV4Usage,
} from "@ai-sdk/provider";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { MockLanguageModelV4 } from "ai/test";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import {
  handleChat,
  handleContext,
  handleExpandGraph,
  handleExtractionStatus,
  handleGraph,
  handleTrace,
} from "../lib/handlers.js";
import { API_KEY, ENDPOINT, NamsState, createNamsServer } from "./nams-server.js";

const ANSWER_DELTAS = ["Base yourself in Tokyo, ", "Kyoto and Kanazawa — ", "all reachable by train."];
const ANSWER = ANSWER_DELTAS.join("");

const FINISH: LanguageModelV4FinishReason = { unified: "stop", raw: "stop" };
const USAGE: LanguageModelV4Usage = {
  inputTokens: { total: 12, noCache: 12, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 12, text: 12, reasoning: 0 },
};

function mockModel(): MockLanguageModelV4 {
  return new MockLanguageModelV4({
    provider: "mock",
    modelId: "mock-model",
    doStream: async () => ({
      stream: new ReadableStream<LanguageModelV4StreamPart>({
        start(controller) {
          controller.enqueue({ type: "stream-start", warnings: [] });
          controller.enqueue({ type: "text-start", id: "0" });
          for (const delta of ANSWER_DELTAS) {
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

function post(url: string, body: unknown): Request {
  return new Request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Poll until `predicate` holds — the middleware's writes are fire-and-forget. */
async function eventually(predicate: () => boolean, timeoutMs = 3_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (predicate()) return;
    if (Date.now() > deadline) throw new Error("timed out waiting for a memory write");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

let state: NamsState;
let server: ReturnType<typeof createNamsServer>;
let client: MemoryClient;

function makeClient(): MemoryClient {
  return new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
}

beforeAll(() => {
  state = new NamsState();
  server = createNamsServer(state);
  server.listen({ onUnhandledRequest: "error" });
});

afterAll(() => server.close());

beforeEach(() => {
  // A fresh state object per test, mutated in place so the running msw handlers
  // (bound once in beforeAll) keep seeing it.
  const fresh = new NamsState();
  Object.assign(state, {
    entities: fresh.entities,
    pendingEntity: null,
    searches: 0,
    graph: fresh.graph,
    neighbours: new Map(),
  });
  state.conversations.clear();
  state.steps.length = 0;
  state.calls.length = 0;
  client = makeClient();
});

afterEach(() => server.resetHandlers());

const deps = () => ({ client, userId: "traveller@example.com" });

describe("POST /api/chat", () => {
  it("persists the user turn and the streamed assistant turn", async () => {
    const conversationId = state.seedConversation("traveller@example.com", []);
    const model = mockModel();

    const response = await handleChat(
      { client, model, userId: "traveller@example.com" },
      post("http://app.test/api/chat", {
        conversationId,
        messages: [
          { id: "m1", role: "user", parts: [{ type: "text", text: "Where should I base myself?" }] },
        ],
      }),
    );

    expect(response.status).toBe(200);
    const streamed = await response.text();
    expect(streamed).toContain("Kanazawa");

    await eventually(() => state.rolesOf(conversationId).length === 2);
    expect(state.rolesOf(conversationId)).toEqual(["user", "assistant"]);
    expect(state.contentsOf(conversationId)).toEqual([
      "Where should I base myself?",
      ANSWER,
    ]);
  });

  it("injects history the request never sent", async () => {
    const conversationId = state.seedConversation("traveller@example.com", [
      { role: "user", content: "I'm vegetarian and I can't do long bus rides." },
      { role: "assistant", content: "Noted — vegetarian, trains only." },
      { role: "user", content: "Budget is £3,000." },
      { role: "assistant", content: "That works for two weeks." },
    ]);
    const model = mockModel();

    const response = await handleChat(
      { client, model, userId: "traveller@example.com" },
      post("http://app.test/api/chat", {
        conversationId,
        messages: [
          { id: "m5", role: "user", parts: [{ type: "text", text: "What did I say about transport?" }] },
        ],
      }),
    );
    await response.text();

    expect(model.doStreamCalls).toHaveLength(1);
    const prompt = promptText(model.doStreamCalls[0]!);

    // None of this was in the request body — the middleware read it back out of
    // the graph in `transformParams`.
    expect(prompt).toContain("can't do long bus rides");
    expect(prompt).toContain("Budget is £3,000");
    // The observation tier is injected as a system note.
    expect(prompt).toContain("avoids buses");
    // Replayed turns are part-array shaped, which is what spec v4 requires.
    expect(prompt).toContain('"type":"text"');
  });

  it("records the answered turn as a reasoning step", async () => {
    const conversationId = state.seedConversation("traveller@example.com", []);
    const response = await handleChat(
      { client, model: mockModel(), userId: "traveller@example.com" },
      post("http://app.test/api/chat", {
        conversationId,
        messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "Plan day one." }] }],
      }),
    );
    await response.text();

    await eventually(() => state.steps.length === 1);
    expect(state.steps[0]!.conversation_id).toBe(conversationId);
    expect(state.steps[0]!.action_taken).toBe("generate_answer");
    expect(state.steps[0]!.reasoning).toContain("Plan day one.");

    const trace = await handleTrace(deps(), new Request(
      `http://app.test/api/memory/trace?conversationId=${conversationId}`,
    ));
    const payload = (await trace.json()) as { steps: Array<{ actionTaken: string }> };
    expect(payload.steps).toHaveLength(1);
    expect(payload.steps[0]!.actionTaken).toBe("generate_answer");
  });

  it("rejects a request with no conversation id", async () => {
    const response = await handleChat(
      { client, model: mockModel(), userId: "traveller@example.com" },
      post("http://app.test/api/chat", {
        messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "hi" }] }],
      }),
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "conversationId is required." });
  });

  it("rejects a request with no user message", async () => {
    const conversationId = state.seedConversation("traveller@example.com", []);
    const response = await handleChat(
      { client, model: mockModel(), userId: "traveller@example.com" },
      post("http://app.test/api/chat", { conversationId, messages: [] }),
    );
    expect(response.status).toBe(400);
  });
});

describe("GET /api/memory/context", () => {
  it("returns the three tiers the middleware will inject", async () => {
    const conversationId = state.seedConversation("traveller@example.com", [
      { role: "user", content: "one" },
      { role: "assistant", content: "two" },
      { role: "user", content: "three" },
      { role: "assistant", content: "four" },
    ]);

    const response = await handleContext(
      deps(),
      new Request(`http://app.test/api/memory/context?conversationId=${conversationId}`),
    );
    const payload = (await response.json()) as {
      reflections: unknown[];
      observations: unknown[];
      recentMessages: unknown[];
    };
    expect(payload.recentMessages).toHaveLength(4);
    expect(payload.observations).toHaveLength(1);
    expect(payload.reflections).toHaveLength(0);
  });

  it("requires a conversation id", async () => {
    const response = await handleContext(deps(), new Request("http://app.test/api/memory/context"));
    expect(response.status).toBe(400);
  });
});

describe("/api/memory/graph", () => {
  it("returns the full entity graph", async () => {
    state.graph = {
      nodes: [
        { id: "e1", name: "Kyoto", type: "LOCATION" },
        { id: "e2", name: "Japan Railways", type: "ORGANIZATION" },
      ],
      edges: [{ id: "r1", source: "e1", target: "e2", type: "SERVED_BY" }],
    };

    const response = await handleGraph(deps());
    const payload = (await response.json()) as { nodes: unknown[]; edges: unknown[] };
    expect(payload.nodes).toHaveLength(2);
    expect(payload.edges).toHaveLength(1);
  });

  it("expands one hop and sends the ids already on the canvas", async () => {
    state.neighbours.set("e1", {
      nodes: [
        { id: "e2", name: "Japan Railways", type: "ORGANIZATION" },
        { id: "e3", name: "Kanazawa", type: "LOCATION" },
      ],
      edges: [{ id: "r2", source: "e1", target: "e3", type: "REACHABLE_FROM" }],
    });

    const response = await handleExpandGraph(
      deps(),
      post("http://app.test/api/memory/graph", { nodeId: "e1", loadedIds: ["e1", "e2"] }),
    );
    const payload = (await response.json()) as {
      nodes: Array<{ id: string; name: string; type: string }>;
    };

    // `loadedIds` reached the service, so e2 (already on screen) is not resent.
    const call = state.callsTo("/graph/expand")[0];
    expect(call?.body).toEqual({ nodeId: "e1", loadedIds: ["e1", "e2"] });
    expect(payload.nodes.map((n) => n.id)).toEqual(["e3"]);
    // Expansion nodes arrive as labels+properties and are flattened for the view.
    expect(payload.nodes[0]).toEqual({ id: "e3", name: "Kanazawa", type: "LOCATION" });
  });

  it("requires a node id to expand", async () => {
    const response = await handleExpandGraph(
      deps(),
      post("http://app.test/api/memory/graph", { loadedIds: [] }),
    );
    expect(response.status).toBe(400);
  });
});

describe("POST /api/memory/extraction", () => {
  it("waits for an entity it has not seen before reporting settled", async () => {
    state.entities = [{ id: "e1", name: "Kyoto", type: "LOCATION" }];
    // The new entity only shows up on the third search — a background pipeline
    // that has not caught up.
    state.pendingEntity = {
      afterSearches: 3,
      entity: { id: "e9", name: "Kanazawa", type: "LOCATION" },
    };

    const response = await handleExtractionStatus(
      deps(),
      post("http://app.test/api/memory/extraction", {
        query: "Japan trip",
        knownIds: ["e1"],
        timeoutMs: 5_000,
      }),
    );
    const payload = (await response.json()) as {
      settled: boolean;
      entities: Array<{ name: string }>;
    };

    expect(payload.settled).toBe(true);
    expect(state.searches).toBeGreaterThanOrEqual(3);
    expect(payload.entities.map((e) => e.name)).toContain("Kanazawa");
  });

  it("reports 'nothing new' instead of throwing when the turn added no entity", async () => {
    state.entities = [{ id: "e1", name: "Kyoto", type: "LOCATION" }];

    const response = await handleExtractionStatus(
      deps(),
      post("http://app.test/api/memory/extraction", {
        query: "Japan trip",
        knownIds: ["e1"],
        timeoutMs: 0,
      }),
    );
    const payload = (await response.json()) as { settled: boolean };
    expect(response.status).toBe(200);
    expect(payload.settled).toBe(false);
  });

  it("requires a query", async () => {
    const response = await handleExtractionStatus(
      deps(),
      post("http://app.test/api/memory/extraction", { knownIds: [] }),
    );
    expect(response.status).toBe(400);
  });
});

describe("edge safety", () => {
  // The route handlers only use fetch, Request and Response, so the app can be
  // deployed to an edge runtime. A stray `node:` import would break that
  // silently, so it is asserted at the source level.
  it("ships no node: imports in app, components or lib", () => {
    const roots = ["app", "components", "lib"];
    const offenders: string[] = [];

    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(path);
        } else if (/\.tsx?$/.test(entry.name)) {
          if (/from "node:/.test(readFileSync(path, "utf8"))) offenders.push(path);
        }
      }
    };

    for (const root of roots) walk(new URL(`../${root}`, import.meta.url).pathname);
    expect(offenders).toEqual([]);
  });
});
