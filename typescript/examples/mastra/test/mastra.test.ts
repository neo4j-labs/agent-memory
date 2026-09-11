/**
 * Runs the whole example against a fake NAMS transport and a stub model — no
 * API key, no network, no Neo4j.
 *
 * The interesting assertions are the ones a thread-scoped store would fail:
 * turn two sees turn one only because the history came back out of the graph,
 * and thread two recalls a preference written in thread one.
 */

import { createMockModel } from "@mastra/core/test-utils/llm-mock";
import type { MemoryStorage } from "@mastra/core/storage";
import type { MastraModelConfig } from "@mastra/core/llm";
import type { Memory } from "@mastra/memory";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jMastraMemory } from "@neo4j-labs/agent-memory/integrations/mastra";
import { describe, expect, it } from "vitest";
import { main } from "../src/index.js";
import { toMastraMessages } from "../src/nams-threads.js";
import { FakeNamsTransport } from "./fake-nams.js";

const ANSWER = "Lisbon is a great pick — book the flights and an Alfama food tour first.";

/** A stub model that records every prompt Mastra sends it. */
function stubModel(): { model: MastraModelConfig; prompts: string[] } {
  const prompts: string[] = [];
  const record = (props: unknown): void => {
    prompts.push(JSON.stringify(props));
  };
  const model = createMockModel({
    mockText: ANSWER,
    version: "v2",
    spyGenerate: record,
    spyStream: record,
  });
  return { model: model as MastraModelConfig, prompts };
}

async function run(options: { cleanup?: boolean } = {}) {
  const transport = new FakeNamsTransport();
  const client = new MemoryClient(transport);
  const { model, prompts } = stubModel();
  const lines: string[] = [];

  const result = await main({
    client,
    model,
    log: (line) => lines.push(line),
    cleanup: options.cleanup ?? false,
    closeClient: false,
  });

  return { result, transport, prompts, lines, client };
}

describe("mastra example", () => {
  it("persists both sides of every turn as NAMS messages", async () => {
    const { result, transport } = await run();

    expect(result.storedRoles).toEqual(["user", "assistant", "user", "assistant"]);
    // Two turns in thread one, one turn in thread two.
    expect(transport.calls.filter((c) => c.method === "add_message")).toHaveLength(6);
    expect(result.answers).toEqual([ANSWER, ANSWER, ANSWER]);
  });

  it("replays the stored history into the next turn", async () => {
    const { prompts } = await run();

    // Turn one saw only the new user message — no prior turn exists yet.
    expect(prompts[0]).toContain("7-day trip to Lisbon");
    expect(prompts[0]).not.toContain(ANSWER);

    // Turn two saw turn one's user message *and* the assistant reply. Neither
    // was passed in by the caller: both came back out of the graph.
    expect(prompts[1]).toContain("7-day trip to Lisbon");
    expect(prompts[1]).toContain(ANSWER);
    expect(prompts[1]).toContain("what should I book first");
  });

  it("reads the thread title back out of conversation metadata", async () => {
    const { result } = await run();
    expect(result.threadTitle).toBe("Lisbon trip planning");
  });

  it("searches inside the thread and reads three-tier context", async () => {
    const { result, transport } = await run();
    expect(result.searchHits).toBeGreaterThan(0);
    expect(transport.calls.some((c) => c.method === "get_context")).toBe(true);
  });

  it("gives the second thread a distinct id and recalls the first thread's preference", async () => {
    const { result } = await run();

    expect(result.secondThreadId).not.toBe(result.threadId);
    expect(result.threadsForResource).toBe(2);
    expect(result.recalledPreferences).toContain("Prefers food and history trips");
  });

  it("primes the new thread from long-term memory, not from its own history", async () => {
    const { prompts } = await run();

    const followUp = prompts.at(-1)!;
    expect(followUp).toContain("Prefers food and history trips");
    expect(followUp).not.toContain("what should I book first");
  });

  it("deletes both threads when cleanup is requested", async () => {
    const { result, transport } = await run({ cleanup: true });

    expect(result.deleted).toBe(true);
    expect(transport.conversations.size).toBe(0);
  });

  it("fails by name when no API key and no client are available", async () => {
    const key = process.env.MEMORY_API_KEY;
    delete process.env.MEMORY_API_KEY;
    try {
      await expect(main({ model: stubModel().model })).rejects.toThrow(/MEMORY_API_KEY/);
    } finally {
      if (key !== undefined) process.env.MEMORY_API_KEY = key;
    }
  });

  it("maps NAMS history onto Mastra message input", () => {
    expect(
      toMastraMessages([
        { id: "m1", threadId: "t", role: "user", content: "Hi", createdAt: "t1" },
        { id: "m2", threadId: "t", role: "assistant", content: "Hello", createdAt: "t2" },
      ]),
    ).toEqual([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
    ]);
  });
});

describe("what this adapter is not", () => {
  /** What `new Memory({ storage })` in `@mastra/memory` actually takes. */
  type MemoryConfig = NonNullable<ConstructorParameters<typeof Memory>[0]>;
  type MemoryStorageSlot = NonNullable<MemoryConfig["storage"]>;

  /**
   * Compile-time assertion: the adapter is not assignable to that slot. If a
   * future Mastra release widened it to accept this shape, `true` would stop
   * being assignable and this line would fail to compile.
   */
  const NOT_A_MASTRA_STORAGE: Neo4jMastraMemory extends MemoryStorageSlot ? never : true = true;

  it("cannot be handed to `new Memory({ storage })`", () => {
    expect(NOT_A_MASTRA_STORAGE).toBe(true);
  });

  /**
   * A Mastra *storage* adapter has to implement the `memory` storage domain.
   * Typing the list as `keyof MemoryStorage` means a rename in `@mastra/core`
   * breaks this test rather than quietly making the README wrong.
   */
  const STORAGE_DOMAIN: ReadonlyArray<keyof MemoryStorage> = [
    "getThreadById",
    "saveThread",
    "updateThread",
    "deleteThread",
    "listThreads",
    "listMessages",
    "listMessagesById",
    "saveMessages",
    "updateMessages",
  ];

  it("implements only the thread/message-history slice, so it is never the `memory` slot", () => {
    const memory = new Neo4jMastraMemory(new MemoryClient(new FakeNamsTransport()));
    const missing = STORAGE_DOMAIN.filter(
      (name) => typeof (memory as unknown as Record<string, unknown>)[name] !== "function",
    );

    expect(missing).toEqual([
      "saveThread",
      "updateThread",
      "listThreads",
      "listMessages",
      "listMessagesById",
      "saveMessages",
      "updateMessages",
    ]);
  });
});
