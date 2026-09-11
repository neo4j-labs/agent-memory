/**
 * Runs the whole example against a fake NAMS transport and a fake chat model —
 * no API key, no network, no Neo4j. This is the drift guard: if the SDK
 * adapters or the LangChain agent API change shape, this test fails before the
 * README does.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { AIMessage, fakeModel } from "langchain";
import { describe, expect, it } from "vitest";

import { main } from "../src/index.js";
import { entityName, fromLangChainMessage, toLangChainMessage } from "../src/nams-memory.js";
import { FakeNamsTransport } from "./fake-nams.js";

function buildModel() {
  return fakeModel()
    .respondWithTools([{ name: "search_memory_entities", args: { query: "graph database" } }])
    .respond(new AIMessage("Neo4j is a good fit for relationship-heavy recommendations."))
    .respond(new AIMessage("Benchmark Neo4j against your recommendation query patterns first."));
}

describe("LangChain v1 agent backed by NAMS", () => {
  it("runs two turns, persists them, and recalls entities from the graph", async () => {
    const transport = new FakeNamsTransport();
    const client = new MemoryClient(transport);
    const model = buildModel();
    const lines: string[] = [];

    const result = await main({
      client,
      model,
      log: (line) => lines.push(line),
      extractionTimeoutMs: 5_000,
      closeClient: true,
    });

    // Two answers, the tool-calling turn resolved into a text answer.
    expect(result.answers).toHaveLength(2);
    expect(result.answers[0]).toContain("Neo4j");
    expect(result.answers[1]).not.toBe("");

    // The middleware persisted the turn: two human + two AI messages, and no
    // tool message or empty tool-call turn leaked into the graph.
    expect(transport.messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(result.storedMessages).toBe(4);

    // Turn 2 is invoked with only the new user message, so anything the model
    // knows about turn 1 arrived through the injected memory block.
    const systemTexts = model.calls.flatMap((call) =>
      call.messages.filter((m) => m.getType() === "system").map((m) => m.text),
    );
    expect(systemTexts.some((text) => text.includes("## Memory"))).toBe(true);
    expect(systemTexts.some((text) => text.includes("Earlier in this conversation"))).toBe(true);
    expect(systemTexts.some((text) => text.includes("Known user preferences"))).toBe(true);

    // The agent called the retriever tool.
    expect(lines.some((line) => line.startsWith("tool search_memory_entities"))).toBe(true);

    // Extraction is awaited: the fake only returns entities from the second
    // poll onward, so an un-awaited search would come back empty.
    expect(result.extractionReady).toBe(true);
    expect(transport.entitySearches).toBeGreaterThanOrEqual(2);
    expect(result.entityNames).toContain("Neo4j");

    // One hop of the graph was expanded, and the client was closed.
    expect(transport.calls.some((c) => c.method === "expand_graph")).toBe(true);
    expect(transport.closed).toBe(true);
  });

  it("fails fast with a named error when no API key is configured", async () => {
    const saved = process.env.MEMORY_API_KEY;
    delete process.env.MEMORY_API_KEY;
    try {
      await expect(main({ model: buildModel() })).rejects.toThrow(/MEMORY_API_KEY/);
    } finally {
      if (saved !== undefined) process.env.MEMORY_API_KEY = saved;
    }
  });

  describe("adapter bridge", () => {
    it("maps stored message types onto @langchain/core classes and back", () => {
      const human = toLangChainMessage({ type: "human", content: "hi" });
      const ai = toLangChainMessage({ type: "ai", content: "hello" });
      expect([human.getType(), ai.getType()]).toEqual(["human", "ai"]);
      expect(fromLangChainMessage(human)).toEqual({ type: "human", content: "hi" });
      expect(fromLangChainMessage(ai)).toEqual({ type: "ai", content: "hello" });
    });

    it("skips messages that should not be persisted", () => {
      // A tool-call-only assistant turn has no text content.
      expect(fromLangChainMessage(new AIMessage({ content: "" }))).toBeNull();
      expect(fromLangChainMessage(toLangChainMessage({ type: "system", content: "x" }))).toBeNull();
    });

    it("names an entity document even though the adapter omits metadata.name", () => {
      // Once the SDK sets `metadata.name`, the first branch wins and this still
      // passes; until then the name is recovered from `pageContent`.
      expect(
        entityName({
          pageContent: "Neo4j — Graph database the user is evaluating",
          metadata: { id: "entity-neo4j", type: "concept" },
        }),
      ).toBe("Neo4j");
      expect(
        entityName({ pageContent: "whatever", metadata: { name: "Neo4j" } }),
      ).toBe("Neo4j");
    });
  });
});
