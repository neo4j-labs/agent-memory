/**
 * Runs `src/memory-store.ts` against the fake NAMS transport and the scripted
 * model — no API key, no network, no Neo4j.
 *
 * The assertions track the round trip a `MemoryStore` exists for: the turns are
 * written to the store's sink conversation, extraction is awaited rather than
 * slept through, and the recalled entity reaches the model's prompt because
 * `MemoryManager` injected it — not because the script put it there.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { describe, expect, it } from "vitest";
import { main } from "../src/memory-store.js";
import { FakeNamsTransport } from "./fake-nams.js";
import { StubModel } from "./stub-model.js";

const USER_ID = "store-test-user";

async function run() {
  const transport = new FakeNamsTransport({
    entities: [{ name: "Acme Corp", type: "organization" }],
  });
  const client = new MemoryClient(transport);
  const model = new StubModel();
  const lines: string[] = [];

  const result = await main({
    client,
    model,
    userId: USER_ID,
    log: (line) => lines.push(line),
    closeClient: false,
  });

  return { result, transport, model, lines };
}

describe("strands memory-store example", () => {
  it("answers both turns and waits for extraction between them", async () => {
    const { result } = await run();

    expect(result.answers).toHaveLength(2);
    expect(result.extracted).toBe(true);
  });

  it("writes the turns into a sink conversation the store owns", async () => {
    const { transport } = await run();

    const sinks = [...transport.conversations.values()].filter(
      (conv) => conv.metadata["strands_memory_store"] !== undefined,
    );
    expect(sinks).toHaveLength(1);
    expect(sinks[0]!.user_id).toBe(USER_ID);
    expect(sinks[0]!.metadata["strands_memory_store"]).toBe(`strands-memory-store/${USER_ID}/graph`);

    // Written through bulkAddMessages, which NAMS extracts server-side.
    expect(transport.calls.some((call) => call.method === "bulk_add_messages")).toBe(true);
    expect(sinks[0]!.messages.map((m) => m.content).join("\n")).toContain("Acme Corp");
  });

  it("recalls the extracted entity out of the graph", async () => {
    const { result } = await run();

    expect(result.recalled).toContain("[entity] Acme Corp (organization)");
  });

  it("injects the recalled entity into the next model call", async () => {
    const { model } = await run();

    // Turn one had nothing to recall; the last turn sees the graph hit because
    // MemoryManager's injection put it in the prompt.
    expect(model.promptText(0)).not.toContain("Acme Corp (organization)");
    expect(model.promptText(model.prompts.length - 1)).toContain("Acme Corp");
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
});
