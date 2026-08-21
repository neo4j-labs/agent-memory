/**
 * The double-extraction guard. The agent reference arrives in
 * Neo4jConversationManager.initAgent, so the guard lives there — a
 * Neo4jSessionStorage used without the conversation manager gets none.
 */

import { describe, it, expect } from "vitest";
import {
  Neo4jConversationManager,
  Neo4jMemoryStore,
} from "../../../src/integrations/strands/index.js";
import { ourStores } from "../../../src/integrations/strands/coexistence.js";

function memoryStub() {
  return {
    async connect() {},
    async close() {},
    shortTerm: {
      async getContext() {
        return { reflections: [], observations: [], recentMessages: [] };
      },
    },
    longTerm: {
      async searchEntities() {
        return [];
      },
    },
  } as never;
}

function agentWith(memoryManager: unknown) {
  return {
    id: "a",
    messages: [],
    memoryManager,
    addHook() {
      return () => {};
    },
  } as never;
}

function fakeManager(stores: unknown[]) {
  // Shaped like the SDK's MemoryManager: the store list lives on _config.stores.
  return { _config: { stores } };
}

describe("double-extraction guard", () => {
  it("throws when a paired store extracts", async () => {
    const memory = memoryStub();
    const store = new Neo4jMemoryStore({ name: "graph", client: memory, extraction: true });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });

    await expect(cm.initAgent(agentWith(fakeManager([store])))).rejects.toThrow(
      /would write and extract the same turns twice/,
    );
  });

  it("names the store and both fixes", async () => {
    const memory = memoryStub();
    const store = new Neo4jMemoryStore({ name: "graph", client: memory, extraction: true });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });

    await expect(cm.initAgent(agentWith(fakeManager([store])))).rejects.toThrow(
      /'graph'[\s\S]*extraction: false/,
    );
  });

  it("allows the recommended recall-only pairing", async () => {
    const memory = memoryStub();
    const store = new Neo4jMemoryStore({ name: "graph", client: memory });
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });

    await expect(cm.initAgent(agentWith(fakeManager([store])))).resolves.toBeUndefined();
  });

  it("ignores a manager holding only foreign stores", async () => {
    const memory = memoryStub();
    const foreign = { name: "other", writable: true, extraction: true, search: async () => [] };
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });

    await expect(
      cm.initAgent(agentWith(fakeManager([foreign]))),
    ).resolves.toBeUndefined();
  });

  it("ignores an agent with no memory manager", async () => {
    const memory = memoryStub();
    const cm = new Neo4jConversationManager(memory, { conversationId: "c1" });
    await expect(cm.initAgent(agentWith(undefined))).resolves.toBeUndefined();
  });
});

describe("private attribute coupling", () => {
  it("MemoryManager still keeps its stores where the guard reads them", async () => {
    // Fails loudly if an SDK upgrade moves the field. Verified on 1.13.0:
    // `this._config = config` in the constructor, and `_searchStores` is the
    // same array. Note the TS class has no `_stores` — Python's does.
    const { MemoryManager } = await import("@strands-agents/sdk");
    const { TestMemoryStore } = await import(
      "@strands-agents/sdk/vended-memory-stores/test-memory-store"
    );
    const store = new Neo4jMemoryStore({ name: "graph", client: memoryStub() });
    const manager = new MemoryManager({
      // persist defaults to true and would write to ~/.strands — keep it in memory.
      stores: [store, new TestMemoryStore({ name: "t", persist: false })],
    });

    expect(ourStores({ memoryManager: manager })).toEqual([store]);
  });
});
