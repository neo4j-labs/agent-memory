/**
 * Smoke test — Vercel AI middleware drives transformParams / wrapGenerate /
 * wrapStream end-to-end against an MSW-mocked hosted service.
 *
 * The middleware implements AI SDK specification v4 (`@ai-sdk/provider` 4.x,
 * shipped with `ai` 7.x), so every prompt here uses the real
 * `LanguageModelV4Prompt` shape: `system` content is a string, `user` and
 * `assistant` content are arrays of content parts.
 *
 * Asserts the contract points that adopters depend on:
 *  - user input is persisted before generation, flattened out of its text parts
 *  - context (or flat history) is injected into the prompt in part form
 *  - assistant output is persisted after generation
 *  - streamed assistant output is persisted once the stream finishes
 *  - wrapGenerate forwards the doGenerate result unchanged
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type {
  LanguageModelV4,
  LanguageModelV4CallOptions,
  LanguageModelV4GenerateResult,
  LanguageModelV4Prompt,
  LanguageModelV4StreamPart,
  LanguageModelV4StreamResult,
} from "@ai-sdk/provider";
import { MemoryClient } from "../../src/client.js";
import { agentMemoryMiddleware } from "../../src/middleware/vercel-ai.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";
const CONV_ID = "conv-vercel-ai";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

interface CapturedMessage {
  role: string;
  content: string;
}

function callOptions(prompt: LanguageModelV4Prompt): LanguageModelV4CallOptions {
  return { prompt };
}

/** Flatten a transformed prompt back to text so assertions stay readable. */
function promptText(prompt: LanguageModelV4Prompt): string {
  return prompt
    .map((m) =>
      typeof m.content === "string"
        ? m.content
        : m.content.map((p) => ("text" in p && p.type === "text" ? p.text : "")).join(""),
    )
    .join("\n");
}

const FINISH = { unified: "stop", raw: "stop" } as const;
const USAGE = {
  inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 1, text: 1, reasoning: 0 },
} as const;

function generateResult(text: string): LanguageModelV4GenerateResult {
  return {
    content: text ? [{ type: "text", text }] : [],
    finishReason: FINISH,
    usage: USAGE,
    warnings: [],
  };
}

function streamResult(deltas: string[]): LanguageModelV4StreamResult {
  return {
    stream: new ReadableStream<LanguageModelV4StreamPart>({
      start(controller) {
        controller.enqueue({ type: "text-start", id: "t1" });
        for (const delta of deltas) {
          controller.enqueue({ type: "text-delta", id: "t1", delta });
        }
        controller.enqueue({ type: "text-end", id: "t1" });
        controller.enqueue({ type: "finish", finishReason: FINISH, usage: USAGE });
        controller.close();
      },
    }),
  };
}

/** Minimal specification-v4 model, enough to satisfy the middleware contract. */
const stubModel: LanguageModelV4 = {
  specificationVersion: "v4",
  provider: "test",
  modelId: "stub",
  supportedUrls: {},
  doGenerate: async () => generateResult(""),
  doStream: async () => ({
    stream: new ReadableStream<LanguageModelV4StreamPart>({
      start(controller) {
        controller.close();
      },
    }),
  }),
};

function mockAddMessage(sink: CapturedMessage[]): ReturnType<typeof http.post> {
  return http.post(`${ENDPOINT}/conversations/${CONV_ID}/messages`, async ({ request }) => {
    const body = (await request.json()) as CapturedMessage;
    sink.push(body);
    return HttpResponse.json({ id: `m-${sink.length}`, role: body.role, content: body.content });
  });
}

describe("Vercel AI middleware — smoke test", () => {
  it("declares the specification version the AI SDK discriminates on", () => {
    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    expect(agentMemoryMiddleware(client).specificationVersion).toBe("v4");
  });

  it("injects context, persists user input, and persists assistant output", async () => {
    const persistedMessages: CapturedMessage[] = [];

    server.use(
      // Three-tier context endpoint
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () =>
        HttpResponse.json({
          reflections: [
            {
              id: "r1",
              conversationId: CONV_ID,
              content: "user values concise replies",
              createdAt: "x",
            },
          ],
          observations: [
            { id: "o1", conversationId: CONV_ID, content: "asks about Neo4j", createdAt: "x" },
          ],
          recentMessages: [{ id: "m1", role: "user", content: "previously: hello" }],
        }),
      ),
      mockAddMessage(persistedMessages),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, {
      conversationId: CONV_ID,
      includeContext: true,
    });

    const incomingPrompt: LanguageModelV4Prompt = [
      { role: "user", content: [{ type: "text", text: "Tell me about graphs." }] },
    ];
    const transformed = await middleware.transformParams!({
      type: "generate",
      params: callOptions(incomingPrompt),
      model: stubModel,
    });

    // 1. Context was prepended — a system message carrying reflections and
    //    observations, then the recent message, then the incoming user turn.
    expect(transformed.prompt.length).toBeGreaterThan(incomingPrompt.length);
    const allContent = promptText(transformed.prompt);
    expect(allContent).toContain("[reflection] user values concise replies");
    expect(allContent).toContain("[observation] asks about Neo4j");
    expect(allContent).toContain("previously: hello");
    expect(allContent).toContain("Tell me about graphs.");

    // 1b. Injected history is emitted in part form, not as raw strings.
    const first = transformed.prompt[0]!;
    expect(first.role).toBe("system");
    expect(typeof first.content).toBe("string");
    const replayed = transformed.prompt.find(
      (m) => m.role === "user" && promptText([m]).includes("previously: hello"),
    )!;
    expect(replayed.content).toEqual([{ type: "text", text: "previously: hello" }]);

    // 1c. The incoming turn is still last and untouched.
    expect(transformed.prompt.at(-1)).toEqual(incomingPrompt[0]);

    // 2. User input was persisted, flattened out of its text parts.
    expect(
      persistedMessages.find(
        (m) => m.role === "user" && m.content === "Tell me about graphs.",
      ),
    ).toBeDefined();

    // 3. wrapGenerate forwards the model result and persists assistant text.
    const fakeResult = generateResult("Graphs model relationships.");
    const wrapped = await middleware.wrapGenerate!({
      doGenerate: async () => fakeResult,
      doStream: async () => streamResult([]),
      params: callOptions(incomingPrompt),
      model: stubModel,
    });
    expect(wrapped).toEqual(fakeResult);

    // 4. Assistant message was persisted.
    expect(
      persistedMessages.find(
        (m) => m.role === "assistant" && m.content === "Graphs model relationships.",
      ),
    ).toBeDefined();
  });

  it("round-trips a multi-part user message as joined text", async () => {
    const persistedMessages: CapturedMessage[] = [];

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () =>
        HttpResponse.json({ reflections: [], observations: [], recentMessages: [] }),
      ),
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      mockAddMessage(persistedMessages),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, { conversationId: CONV_ID });

    await middleware.transformParams!({
      type: "generate",
      params: callOptions([
        { role: "system", content: "be concise" },
        {
          role: "user",
          content: [
            { type: "text", text: "Compare Neo4j " },
            {
              type: "file",
              mediaType: "image/png",
              data: { type: "data", data: "aGVsbG8=" },
            },
            { type: "text", text: "and Postgres." },
          ],
        },
      ]),
      model: stubModel,
    });

    expect(persistedMessages).toEqual([
      { role: "user", content: "Compare Neo4j and Postgres." },
    ]);
  });

  it("persists streamed assistant text once the stream finishes", async () => {
    const persistedMessages: CapturedMessage[] = [];

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () =>
        HttpResponse.json({ reflections: [], observations: [], recentMessages: [] }),
      ),
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      mockAddMessage(persistedMessages),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, { conversationId: CONV_ID });

    const { stream } = await middleware.wrapStream!({
      doStream: async () => streamResult(["Graphs ", "model ", "relationships."]),
      doGenerate: async () => generateResult(""),
      params: callOptions([{ role: "user", content: [{ type: "text", text: "go" }] }]),
      model: stubModel,
    });

    // The consumer still sees every part, in order.
    const seen: LanguageModelV4StreamPart[] = [];
    for await (const part of stream as unknown as AsyncIterable<LanguageModelV4StreamPart>) {
      seen.push(part);
    }
    expect(seen.filter((p) => p.type === "text-delta")).toHaveLength(3);

    // The accumulated text is persisted (fire-and-forget — let it settle).
    await new Promise((r) => setTimeout(r, 50));
    expect(
      persistedMessages.find(
        (m) => m.role === "assistant" && m.content === "Graphs model relationships.",
      ),
    ).toBeDefined();
  });

  it("does not re-persist the same user turn across a tool loop", async () => {
    const persistedMessages: CapturedMessage[] = [];

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () =>
        HttpResponse.json({ reflections: [], observations: [], recentMessages: [] }),
      ),
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      mockAddMessage(persistedMessages),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, { conversationId: CONV_ID });

    // Step 1: the user turn is last in the prompt.
    const userTurn: LanguageModelV4Prompt[number] = {
      role: "user",
      content: [{ type: "text", text: "What is the weather?" }],
    };
    await middleware.transformParams!({
      type: "generate",
      params: callOptions([userTurn]),
      model: stubModel,
    });

    // Step 2: the model called a tool, so the prompt grew — but the same user
    // turn is still the most recent `user` message.
    await middleware.transformParams!({
      type: "generate",
      params: callOptions([
        userTurn,
        { role: "assistant", content: [{ type: "text", text: "checking" }] },
        {
          role: "tool",
          content: [
            {
              type: "tool-result",
              toolCallId: "c1",
              toolName: "weather",
              output: { type: "text", value: "sunny" },
            },
          ],
        },
      ]),
      model: stubModel,
    });

    expect(persistedMessages.filter((m) => m.role === "user")).toHaveLength(1);

    // A genuinely new turn is still persisted.
    await middleware.transformParams!({
      type: "generate",
      params: callOptions([
        { role: "user", content: [{ type: "text", text: "And tomorrow?" }] },
      ]),
      model: stubModel,
    });
    expect(persistedMessages.filter((m) => m.role === "user")).toHaveLength(2);
  });

  it("falls back to flat history when context fetch fails", async () => {
    let contextCalled = false;
    let historyCalled = false;

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () => {
        contextCalled = true;
        return new HttpResponse("nope", { status: 500 });
      }),
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () => {
        historyCalled = true;
        return HttpResponse.json({
          messages: [{ id: "m1", role: "user", content: "earlier flat message" }],
        });
      }),
      http.post(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () =>
        HttpResponse.json({ id: "x", role: "user", content: "" }),
      ),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, {
      conversationId: CONV_ID,
      includeContext: true,
    });

    const transformed = await middleware.transformParams!({
      type: "generate",
      params: callOptions([{ role: "user", content: [{ type: "text", text: "now" }] }]),
      model: stubModel,
    });

    expect(contextCalled).toBe(true);
    expect(historyCalled).toBe(true);
    expect(promptText(transformed.prompt)).toContain("earlier flat message");
  });

  it("skips persistence when persistInput=false and persistResponses=false", async () => {
    const persistedMessages: CapturedMessage[] = [];

    server.use(
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/context`, () =>
        HttpResponse.json({ reflections: [], observations: [], recentMessages: [] }),
      ),
      http.get(`${ENDPOINT}/conversations/${CONV_ID}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      mockAddMessage(persistedMessages),
    );

    const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const middleware = agentMemoryMiddleware(client, {
      conversationId: CONV_ID,
      includeContext: true,
      persistInput: false,
      persistResponses: false,
    });

    await middleware.transformParams!({
      type: "generate",
      params: callOptions([{ role: "user", content: [{ type: "text", text: "x" }] }]),
      model: stubModel,
    });
    await middleware.wrapGenerate!({
      doGenerate: async () => generateResult("y"),
      doStream: async () => streamResult([]),
      params: callOptions([]),
      model: stubModel,
    });

    expect(persistedMessages).toHaveLength(0);
  });
});
