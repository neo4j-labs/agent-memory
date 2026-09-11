/**
 * Runs the whole example against a fake NAMS transport and the AI SDK's own
 * mock model (`MockLanguageModelV4` from `ai/test`) — no API key, no network,
 * no Neo4j.
 *
 * The assertions are the ones that fail if memory stops working:
 *   - turn two's prompt carries turn one, and the example never passed it in —
 *     it came back out of the graph through `transformParams`,
 *   - the streamed turn is persisted, which only `wrapStream` can do,
 *   - a second run resumes the first conversation and recalls its preference.
 */

import type {
  LanguageModelV4CallOptions,
  LanguageModelV4FinishReason,
  LanguageModelV4GenerateResult,
  LanguageModelV4StreamPart,
  LanguageModelV4Usage,
} from "@ai-sdk/provider";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { MockLanguageModelV4 } from "ai/test";
import { describe, expect, it } from "vitest";
import { main } from "../src/index.js";
import { FakeNamsTransport } from "./fake-nams.js";

const ANSWER = "Start with a BoardGameGeek ratings dump and a simple item-item recommender.";
const STREAM_DELTAS = ["Build ", "a euro-style ", "shortlist first."];
const STREAM_ANSWER = STREAM_DELTAS.join("");

const FINISH: LanguageModelV4FinishReason = { unified: "stop", raw: "stop" };

const USAGE: LanguageModelV4Usage = {
  inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 10, text: 10, reasoning: 0 },
};

function generateResult(): LanguageModelV4GenerateResult {
  return {
    content: [{ type: "text", text: ANSWER }],
    finishReason: FINISH,
    usage: USAGE,
    warnings: [],
  };
}

function streamParts(): LanguageModelV4StreamPart[] {
  return [
    { type: "stream-start", warnings: [] },
    { type: "text-start", id: "0" },
    ...STREAM_DELTAS.map((delta): LanguageModelV4StreamPart => ({
      type: "text-delta",
      id: "0",
      delta,
    })),
    { type: "text-end", id: "0" },
    { type: "finish", finishReason: FINISH, usage: USAGE },
  ];
}

/** The AI SDK's own mock model; it records every call it receives. */
function mockModel(): MockLanguageModelV4 {
  return new MockLanguageModelV4({
    provider: "mock",
    modelId: "mock-model",
    doGenerate: async () => generateResult(),
    doStream: async () => ({
      stream: new ReadableStream<LanguageModelV4StreamPart>({
        start(controller) {
          for (const part of streamParts()) controller.enqueue(part);
          controller.close();
        },
      }),
    }),
  });
}

function promptText(call: LanguageModelV4CallOptions): string {
  return JSON.stringify(call.prompt);
}

async function run(transport = new FakeNamsTransport()) {
  const client = new MemoryClient(transport);
  const model = mockModel();
  const lines: string[] = [];
  const chunks: string[] = [];

  const result = await main({
    client,
    model,
    userId: "test-user",
    log: (line) => lines.push(line),
    write: (chunk) => chunks.push(chunk),
    extractionTimeoutMs: 0,
    closeClient: false,
  });

  return { result, transport, model, lines, chunks };
}

describe("vercel-ai example", () => {
  it("persists both sides of all three turns", async () => {
    const { result, transport } = await run();

    expect(transport.rolesOf(result.conversationId)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(result.answers).toEqual([ANSWER, ANSWER]);
  });

  it("replays the stored history into the next turn", async () => {
    const { result, model } = await run();

    expect(model.doGenerateCalls).toHaveLength(2);

    // Turn one: only the new user message — nothing is stored yet.
    const first = promptText(model.doGenerateCalls[0]!);
    expect(first).toContain("recommendation engine for board games");
    expect(first).not.toContain(ANSWER);

    // Turn two: turn one's user message *and* its assistant reply are in the
    // prompt. The example passed neither — the middleware read them back out
    // of the graph.
    const second = promptText(model.doGenerateCalls[1]!);
    expect(second).toContain("recommendation engine for board games");
    expect(second).toContain(ANSWER);
    expect(second).toContain("What kind of dataset");

    // The injected history is part-array shaped, not string-content shaped.
    expect(second).toContain('"type":"text"');
    expect(result.context.recentMessages).toBe(6);
  });

  it("persists the streamed turn via wrapStream", async () => {
    const { result, transport, model, chunks } = await run();

    expect(model.doStreamCalls).toHaveLength(1);
    // The streamed turn saw both earlier turns.
    expect(promptText(model.doStreamCalls[0]!)).toContain("What kind of dataset");

    expect(result.streamedAnswer).toBe(STREAM_ANSWER);
    expect(chunks.join("")).toContain(STREAM_ANSWER);

    // The assembled answer — not the deltas — is the stored assistant message.
    const stored = transport.contentsOf(result.conversationId);
    expect(stored.at(-1)).toBe(STREAM_ANSWER);
  });

  it("resumes its own conversation on a second run instead of creating a new one", async () => {
    const transport = new FakeNamsTransport();

    const first = await run(transport);
    expect(first.result.resumed).toBe(false);

    const second = await run(transport);
    expect(second.result.resumed).toBe(true);
    expect(second.result.conversationId).toBe(first.result.conversationId);
    expect(transport.conversations.size).toBe(1);
    expect(second.lines[0]).toContain("Resumed conversation");

    // The second run's first prompt already carries the first run's history.
    expect(promptText(second.model.doGenerateCalls[0]!)).toContain(ANSWER);
  });

  it("writes the preference once and recalls it on every run", async () => {
    const transport = new FakeNamsTransport();

    const first = await run(transport);
    expect(first.result.recalledPreferences).toEqual([
      "Prefers euro-style board games with low randomness",
    ]);

    const second = await run(transport);
    expect(transport.preferences).toHaveLength(1);
    expect(second.result.recalledPreferences).toEqual(first.result.recalledPreferences);
  });

  it("reports the extracted entities", async () => {
    const { result, lines } = await run();

    expect(result.extractedEntities).toEqual(["Euro-style board games"]);
    expect(lines.join("\n")).toContain("Entities extracted from the conversation: 1");
  });

  it("fails with a named error when MEMORY_API_KEY is missing", async () => {
    const saved = process.env.MEMORY_API_KEY;
    delete process.env.MEMORY_API_KEY;
    try {
      await expect(main()).rejects.toThrow(/Set MEMORY_API_KEY/);
    } finally {
      if (saved !== undefined) process.env.MEMORY_API_KEY = saved;
    }
  });
});
