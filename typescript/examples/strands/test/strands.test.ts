/**
 * Runs the whole example against a fake NAMS transport and a scripted Strands
 * model — no API key, no network, no Neo4j.
 *
 * The assertions are the ones a broken integration would fail: the transcript
 * has to be in the graph (session storage), the reasoning trace has to carry a
 * `lookup_fact` tool call (reasoning hooks), the injected context has to reach
 * the model (conversation manager), and a second run has to resume the first
 * conversation (the recall the README promises).
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { isSyntheticStrandsMessage } from "@neo4j-labs/agent-memory/integrations/strands";
import { beforeEach, describe, expect, it } from "vitest";
import { main } from "../src/index.js";
import { FakeNamsTransport } from "./fake-nams.js";
import { StubModel } from "./stub-model.js";

const USER_ID = "strands-test-user";

async function run(options: { transport?: FakeNamsTransport; conversationId?: string } = {}) {
  const transport = options.transport ?? new FakeNamsTransport();
  const client = new MemoryClient(transport);
  const model = new StubModel();
  const lines: string[] = [];

  const result = await main({
    client,
    model,
    userId: USER_ID,
    ...(options.conversationId !== undefined && { conversationId: options.conversationId }),
    log: (line) => lines.push(line),
    closeClient: false,
  });

  return { result, transport, model, lines };
}

beforeEach(() => {
  // main() reads these when no option is passed; the tests pass options, but an
  // inherited value must not change what is asserted.
  delete process.env.CONVERSATION_ID;
  delete process.env.MODEL_PROVIDER;
});

describe("strands example", () => {
  it("creates a conversation on a first run and answers every turn", async () => {
    const { result, lines } = await run();

    expect(result.resumed).toBe(false);
    expect(result.answers).toHaveLength(3);
    expect(result.answers.every((answer) => answer.length > 0)).toBe(true);
    expect(lines[0]).toMatch(/^Created conversation conv-\d+ for the demo user$/);
  });

  it("persists the transcript as real NAMS messages via Neo4jSessionStorage", async () => {
    const { result, transport } = await run();

    // Session storage writes real messages plus synthetic state markers; the
    // script reports them separately.
    expect(result.storedMessages).toBeGreaterThan(0);
    expect(result.syntheticMessages).toBeGreaterThan(0);

    const stored = transport.messagesFor(result.conversationId);
    const real = stored.filter((message) => !isSyntheticStrandsMessage(message));
    expect(real.length).toBe(result.storedMessages);
    expect(real.map((m) => m.content).join("\n")).toContain("recommendation engine");
  });

  it("records a reasoning trace with at least one lookup_fact tool call", async () => {
    const { result } = await run();

    expect(result.trace.steps).toBeGreaterThan(0);
    expect(result.trace.toolCalls).toBeGreaterThan(0);
    expect(result.trace.toolNames).toContain("lookup_fact");
  });

  it("injects the conversation's three-tier context into the model prompt", async () => {
    // A seeded conversation with history is what gives getContext() something to
    // return on the very first turn.
    const transport = new FakeNamsTransport({
      conversations: [
        { id: "conv-seed", userId: USER_ID, metadata: { source: "strands-example" } },
      ],
    });
    for (let i = 0; i < 6; i += 1) {
      await transport.request("add_message", {
        conversation_id: "conv-seed",
        role: i % 2 === 0 ? "user" : "assistant",
        content: `earlier turn ${i}`,
      });
    }

    const { model, result } = await run({ transport, conversationId: "conv-seed" });

    expect(result.resumed).toBe(true);
    expect(result.context.reflections).toBeGreaterThan(0);
    // The reflection reached the model because the conversation manager put it
    // there — nothing in the script passes it to `agent.invoke()`.
    expect(model.promptText(0)).toContain("[reflection]");
    expect(model.promptText(0)).toContain("recommendation engine");
  });

  it("reads the entities extraction produced, after waiting for it", async () => {
    const { result, transport } = await run();

    expect(result.entities).toContain("Neo4j");
    // Two searches: waitForExtraction polled once (empty), then the read.
    const searches = transport.calls.filter((c) => c.method === "search_entities");
    expect(searches.length).toBeGreaterThanOrEqual(2);
  });

  it("resumes the previous conversation on a second run with the same user", async () => {
    const transport = new FakeNamsTransport();
    const first = await run({ transport });
    const second = await run({ transport });

    expect(second.result.resumed).toBe(true);
    expect(second.result.conversationId).toBe(first.result.conversationId);
    expect(second.lines[0]).toContain(`Resumed conversation ${first.result.conversationId}`);

    // The recall the README promises: the second run's very first model call
    // already contains the first run's transcript. Nothing in the script passes
    // it in — Neo4jSessionStorage restored it from the graph. (The message count
    // does not grow because the storage dedupes by role+content and this script
    // replays the same three turns.)
    expect(second.model.promptText(0)).toContain("Neo4j is top of my list");
    expect(second.result.storedMessages).toBeGreaterThanOrEqual(first.result.storedMessages);

    // The reasoning trace accumulates across runs on the same conversation.
    expect(second.result.trace.steps).toBeGreaterThan(first.result.trace.steps);
  });

  it("resumes the conversation named by CONVERSATION_ID", async () => {
    const transport = new FakeNamsTransport({
      conversations: [
        { id: "conv-explicit", userId: USER_ID, metadata: { source: "strands-example" } },
        { id: "conv-other", userId: USER_ID, metadata: { source: "strands-example" } },
      ],
    });

    const { result } = await run({ transport, conversationId: "conv-explicit" });
    expect(result.conversationId).toBe("conv-explicit");
  });

  it("ignores conversations this example did not create", async () => {
    // memory-store.ts creates its own sink conversation under the same user id,
    // so the resume scan has to filter on the metadata this script writes.
    const transport = new FakeNamsTransport({
      conversations: [
        {
          id: "conv-store-sink",
          userId: USER_ID,
          metadata: { strands_memory_store: `strands-memory-store/${USER_ID}/graph` },
        },
      ],
    });

    const { result } = await run({ transport });
    expect(result.resumed).toBe(false);
    expect(result.conversationId).not.toBe("conv-store-sink");
  });

  it("prints a re-run hint with no undefined in it", async () => {
    const { lines, result } = await run();
    const hint = lines.at(-1)!;

    expect(hint).toContain(`CONVERSATION_ID=${result.conversationId}`);
    expect(hint).toContain("DEMO_USER_ID");
    expect(hint).not.toContain(USER_ID);
    expect(lines.join("\n")).not.toContain("undefined");
  });

  it("fails by name when no API key and no client are available", async () => {
    const key = process.env.MEMORY_API_KEY;
    delete process.env.MEMORY_API_KEY;
    try {
      await expect(main({ model: new StubModel() })).rejects.toThrow(/MEMORY_API_KEY/);
    } finally {
      if (key !== undefined) process.env.MEMORY_API_KEY = key;
    }
  });

  it("rejects an unknown MODEL_PROVIDER by name", async () => {
    process.env.MODEL_PROVIDER = "vertex";
    await expect(main({ client: new MemoryClient(new FakeNamsTransport()) })).rejects.toThrow(
      /MODEL_PROVIDER must be "openai" or "bedrock"/,
    );
  });
});
