/**
 * Neo4jMemoryStore (TypeScript). NAMS-only, so search is entities-only.
 * Test names mirror tests/unit/integrations/test_strands_memory_store.py
 * wherever behaviour is shared across the two SDKs.
 */

import { describe, it, expect, vi } from "vitest";
import { MemoryClient } from "../../../src/client.js";
import { Neo4jMemoryStore } from "../../../src/integrations/strands/index.js";
import { NotSupportedError } from "../../../src/errors.js";

/** Records every client call the store makes, and lets a test override any of them. */
export function makeClient(overrides: Record<string, unknown> = {}) {
  const calls = {
    connect: 0,
    close: 0,
    searchEntities: [] as Array<{ query: string; options?: { limit?: number } }>,
    addEntity: [] as unknown[],
    addPreference: [] as unknown[],
    addFact: [] as unknown[],
    addMessage: [] as Array<{ sink: string; role: string; content: string }>,
    bulk: [] as Array<{ sink: string; messages: Array<{ role: string; content: string }> }>,
    listConversations: 0,
    created: [] as Array<{ userId: string; metadata?: Record<string, unknown> }>,
    expandGraph: [] as string[],
  };
  const conversations: Array<{ id: string; metadata?: Record<string, unknown> }> = [];

  const client = {
    async connect() {
      calls.connect += 1;
    },
    async close() {
      calls.close += 1;
    },
    longTerm: {
      async searchEntities(query: string, options?: { limit?: number }) {
        calls.searchEntities.push({ query, options });
        return [
          {
            id: "e1",
            name: "Acme Corp",
            type: "ORGANIZATION",
            createdAt: "2026-01-01T00:00:00Z",
          },
        ];
      },
      async addEntity(name: string, entityType: string) {
        calls.addEntity.push({ name, entityType });
        return { id: "ent-1", name, type: entityType, createdAt: "" };
      },
      async addPreference(category: string, preference: string) {
        calls.addPreference.push({ category, preference });
        return { id: "pref-1", category, preference, createdAt: "" };
      },
      async addFact(subject: string, predicate: string, object: string) {
        calls.addFact.push({ subject, predicate, object });
        return { id: "fact-1", subject, predicate, object, createdAt: "" };
      },
      async expandGraph(nodeId: string) {
        calls.expandGraph.push(nodeId);
        return { nodes: [], edges: [] };
      },
      ...(overrides.longTerm as object | undefined),
    },
    shortTerm: {
      async listConversations() {
        calls.listConversations += 1;
        return [...conversations];
      },
      async createConversation(input: { userId: string; metadata?: Record<string, unknown> }) {
        calls.created.push(input);
        const conversation = { id: `c${calls.created.length}`, metadata: input.metadata };
        conversations.push(conversation);
        return conversation;
      },
      async addMessage(sink: string, role: string, content: string) {
        calls.addMessage.push({ sink, role, content });
        return { id: `m${calls.addMessage.length}` };
      },
      async bulkAddMessages(sink: string, messages: Array<{ role: string; content: string }>) {
        if (messages.length > 100) throw new Error("bulkAddMessages accepts a maximum of 100");
        calls.bulk.push({ sink, messages });
        return messages.map((_, index) => ({ id: `m${index}` }));
      },
      ...(overrides.shortTerm as object | undefined),
    },
  };
  return { client: client as unknown as MemoryClient, calls, conversations };
}

export function makeStore(options: Record<string, unknown> = {}) {
  const { client, calls, conversations } = makeClient(
    (options.clientOverrides as Record<string, unknown>) ?? {},
  );
  delete options.clientOverrides;
  const store = new Neo4jMemoryStore({ name: "graph", client, ...options } as never);
  return { store, calls, client, conversations };
}

describe("Neo4jMemoryStore construction", () => {
  it("requires a name", () => {
    const { client } = makeClient();
    expect(() => new Neo4jMemoryStore({ client } as never)).toThrow(/name/);
  });

  it("requires exactly one of client or clientOptions", () => {
    expect(() => new Neo4jMemoryStore({ name: "graph" } as never)).toThrow(
      /exactly one of/,
    );
    const { client } = makeClient();
    expect(
      () => new Neo4jMemoryStore({ name: "graph", client, clientOptions: {} } as never),
    ).toThrow(/exactly one of/);
  });

  it("assigns the protocol attributes with their defaults", () => {
    const { store } = makeStore();
    expect(store.name).toBe("graph");
    expect(store.description).toContain("graph");
    expect(store.maxSearchResults).toBeUndefined();
    expect(store.writable).toBe(true);
    expect(store.extraction).toBe(false);
    expect(store.graphTools).toBe(true);
    expect(store.userId).toBeUndefined();
  });

  it("keeps an explicit description, limit, and extraction config", () => {
    const { store } = makeStore({
      description: "team knowledge",
      maxSearchResults: 7,
      extraction: true,
      writable: false,
      userId: "alice",
    });
    expect(store.description).toBe("team knowledge");
    expect(store.maxSearchResults).toBe(7);
    expect(store.extraction).toBe(true);
    expect(store.writable).toBe(false);
    expect(store.userId).toBe("alice");
  });

  it("builds and owns a client from clientOptions", () => {
    const store = new Neo4jMemoryStore({
      name: "graph",
      clientOptions: { endpoint: "https://memory.test/v1", apiKey: "k" },
    });
    expect(store.client).toBeInstanceOf(MemoryClient);
  });
});

describe("Neo4jMemoryStore lifecycle", () => {
  it("connects once, however many times initialize is called", async () => {
    const { store, calls } = makeStore();
    await store.initialize();
    await store.initialize();
    expect(calls.connect).toBe(1);
  });

  it("never closes a borrowed client", async () => {
    const { store, calls } = makeStore();
    await store.initialize();
    await store.close();
    expect(calls.close).toBe(0);
  });

  it("closes a client it built itself", async () => {
    const store = new Neo4jMemoryStore({
      name: "graph",
      clientOptions: { endpoint: "https://memory.test/v1", apiKey: "k" },
    });
    const close = vi.spyOn(store.client, "close").mockResolvedValue(undefined);
    await store.close();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("reconnects after close", async () => {
    const { store, calls } = makeStore();
    await store.initialize();
    await store.close();
    await store.initialize();
    expect(calls.connect).toBe(2);
  });
});

describe("Neo4jMemoryStore.forNams", () => {
  it("reads MEMORY_ENDPOINT and MEMORY_API_KEY from the environment", () => {
    vi.stubEnv("MEMORY_ENDPOINT", "https://staging.memory.test/v1");
    vi.stubEnv("MEMORY_API_KEY", "env-key");
    try {
      const store = Neo4jMemoryStore.forNams({ name: "graph" });
      expect(store.client).toBeInstanceOf(MemoryClient);
      expect(store.name).toBe("graph");
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("prefers explicit connection values over the environment", () => {
    vi.stubEnv("MEMORY_ENDPOINT", "https://staging.memory.test/v1");
    try {
      const store = Neo4jMemoryStore.forNams(
        { name: "graph" },
        { endpoint: "https://explicit.memory.test/v1", apiKey: "explicit" },
      );
      expect(store.client).toBeInstanceOf(MemoryClient);
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("does not mutate the caller's options", () => {
    const options = { name: "graph" as const };
    Neo4jMemoryStore.forNams(options);
    expect(Object.keys(options)).toEqual(["name"]);
  });
});

/** Exposes the protected sink resolver so it can be tested before add() exists. */
class ProbeStore extends Neo4jMemoryStore {
  sink(): Promise<string> {
    return this.resolveSink();
  }
}

function makeProbe(options: Record<string, unknown> = {}) {
  const { client, calls } = makeClient();
  return { probe: new ProbeStore({ name: "graph", client, ...options } as never), calls, client };
}

describe("Neo4jMemoryStore sink resolution", () => {
  it("creates a sink tagged with its deterministic name", async () => {
    const { probe, calls } = makeProbe();
    expect(await probe.sink()).toBe("c1");

    expect(calls.created).toHaveLength(1);
    expect(calls.created[0]!.metadata).toMatchObject({
      strands_memory_store: "strands-memory-store/_/graph",
      sessionType: "MEMORY_STORE",
    });
  });

  it("records the configured userId on the sink", async () => {
    const { probe, calls } = makeProbe({ userId: "alice" });
    await probe.sink();

    expect(calls.created[0]!.userId).toBe("alice");
    expect(calls.created[0]!.metadata).toMatchObject({
      strands_memory_store: "strands-memory-store/alice/graph",
    });
  });

  it("labels an unscoped sink rather than inventing a tenant", async () => {
    const { probe, calls } = makeProbe();
    await probe.sink();
    expect(calls.created[0]!.userId).toBe("strands-memory-store");
  });

  it("reuses an existing sink across store instances", async () => {
    const { client, calls } = makeClient();
    const first = await new ProbeStore({ name: "graph", client } as never).sink();
    const second = await new ProbeStore({ name: "graph", client } as never).sink();

    expect([first, second]).toEqual(["c1", "c1"]);
    expect(calls.created).toHaveLength(1);
  });

  it("scans conversations once per store instance", async () => {
    const { probe, calls } = makeProbe();
    await probe.sink();
    await probe.sink();
    expect(calls.listConversations).toBe(1);
  });

  it("uses an explicit conversationId verbatim and never scans", async () => {
    const { probe, calls } = makeProbe({ conversationId: "conv-42" });
    expect(await probe.sink()).toBe("conv-42");

    expect(calls.listConversations).toBe(0);
    expect(calls.created).toEqual([]);
  });

  it("gives two differently-named stores different sinks", async () => {
    const { client, calls } = makeClient();
    const personal = await new ProbeStore({ name: "personal", client } as never).sink();
    const team = await new ProbeStore({ name: "team", client } as never).sink();

    expect([personal, team]).toEqual(["c1", "c2"]);
    expect(calls.created).toHaveLength(2);
  });
});

describe("Neo4jMemoryStore.search", () => {
  it("returns memory entries with metadata", async () => {
    const { store } = makeStore();
    const entries = await store.search("acme");

    expect(entries).toHaveLength(1);
    expect(entries[0]!.content).toBe("[entity] Acme Corp (ORGANIZATION)");
    expect(entries[0]!.metadata).toEqual({
      kind: "entity",
      id: "e1",
      type: "ORGANIZATION",
    });
  });

  it("prefers the canonical name and appends a description", async () => {
    const { store } = makeStore({
      clientOverrides: {
        longTerm: {
          async searchEntities() {
            return [
              {
                id: "e2",
                name: "acme",
                canonicalName: "Acme Corporation",
                type: "ORGANIZATION",
                subtype: "COMPANY",
                description: "A manufacturer",
                createdAt: "",
              },
            ];
          },
        },
      },
    });

    const entries = await store.search("acme");
    expect(entries[0]!.content).toBe(
      "[entity] Acme Corporation (ORGANIZATION:COMPANY) — A manufacturer",
    );
    expect(entries[0]!.metadata).toMatchObject({ type: "ORGANIZATION:COMPANY" });
  });

  it("omits a score, which NAMS does not return", async () => {
    const { store } = makeStore();
    const entries = await store.search("acme");
    expect(entries[0]!.metadata).not.toHaveProperty("score");
  });

  it("applies limit precedence: per-call, then store, then 3", async () => {
    const { store, calls } = makeStore();
    await store.search("q");
    expect(calls.searchEntities[0]!.options?.limit).toBe(3);

    await store.search("q", { maxSearchResults: 2 });
    expect(calls.searchEntities[1]!.options?.limit).toBe(2);

    const withStoreLimit = makeStore({ maxSearchResults: 7 });
    await withStoreLimit.store.search("q");
    expect(withStoreLimit.calls.searchEntities[0]!.options?.limit).toBe(7);
  });

  it("returns nothing when entities are switched off", async () => {
    const { store, calls } = makeStore({ includeEntities: false });
    expect(await store.search("q")).toEqual([]);
    expect(calls.searchEntities).toEqual([]);
  });

  it("does not mint a sink", async () => {
    const { store, calls } = makeStore();
    await store.search("q");
    expect(calls.created).toEqual([]);
    expect(calls.listConversations).toBe(0);
  });

  it("initializes without a prior initialize call", async () => {
    const { store, calls } = makeStore();
    await store.search("q");
    expect(calls.connect).toBe(1);
  });

  it("propagates a search failure so the manager can log a dead store", async () => {
    const { store } = makeStore({
      clientOverrides: {
        longTerm: {
          async searchEntities() {
            throw new Error("index offline");
          },
        },
      },
    });
    await expect(store.search("q")).rejects.toThrow(/index offline/);
  });
});

describe("Neo4jMemoryStore.add", () => {
  it("writes a message into the sink by default", async () => {
    const { store, calls } = makeStore();
    const result = await store.add("prefers dark mode");

    expect(calls.addMessage).toHaveLength(1);
    expect(calls.addMessage[0]).toMatchObject({
      role: "user",
      content: "prefers dark mode",
    });
    expect(result).toEqual({ kind: "message", id: "m1" });
  });

  it("routes kind=entity to addEntity", async () => {
    const { store, calls } = makeStore();
    const result = await store.add("Acme Corp", {
      kind: "entity",
      name: "Acme Corp",
      type: "ORGANIZATION",
    });

    expect(calls.addEntity).toEqual([{ name: "Acme Corp", entityType: "ORGANIZATION" }]);
    expect(calls.addMessage).toEqual([]);
    expect(result).toEqual({ kind: "entity", id: "ent-1" });
  });

  it("defaults an entity's name and type", async () => {
    const { store, calls } = makeStore();
    await store.add("Acme Corp", { kind: "entity" });
    expect(calls.addEntity).toEqual([{ name: "Acme Corp", entityType: "OBJECT" }]);
  });

  it("requires a full triple for kind=fact", async () => {
    const { store } = makeStore();
    await expect(
      store.add("acme makes widgets", { kind: "fact", subject: "Acme" }),
    ).rejects.toThrow(/subject, predicate and object/);
  });

  it("falls back to the sink when a kind is unsupported on this backend", async () => {
    const { store, calls } = makeStore({
      clientOverrides: {
        longTerm: {
          async addPreference() {
            throw new NotSupportedError("add_preference has no REST equivalent");
          },
        },
      },
    });

    const result = await store.add("dark mode", { kind: "preference", category: "ui" });
    expect(result).toMatchObject({ kind: "message" });
    expect(calls.addMessage).toHaveLength(1);
  });

  it("warns once per store for an unsupported kind", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const { store } = makeStore({
        clientOverrides: {
          longTerm: {
            async addPreference() {
              throw new NotSupportedError("unsupported");
            },
          },
        },
      });
      await store.add("a", { kind: "preference" });
      await store.add("b", { kind: "preference" });

      const hits = warn.mock.calls.filter((call) => String(call[0]).includes("preference"));
      expect(hits).toHaveLength(1);
    } finally {
      warn.mockRestore();
    }
  });

  it("lets a non-NotSupportedError failure propagate", async () => {
    const { store } = makeStore({
      clientOverrides: {
        longTerm: {
          async addPreference() {
            throw new Error("service unavailable");
          },
        },
      },
    });
    await expect(store.add("dark mode", { kind: "preference" })).rejects.toThrow(
      /service unavailable/,
    );
  });

  it("ignores an unknown kind and writes to the sink", async () => {
    const { store, calls } = makeStore();
    const result = await store.add("something", { kind: "sonnet" });
    expect(result).toMatchObject({ kind: "message" });
    expect(calls.addMessage).toHaveLength(1);
  });

  it("refuses writes when not writable", async () => {
    const { store } = makeStore({ writable: false });
    await expect(store.add("x")).rejects.toThrow(/not writable/);
  });

  it("refuses empty content", async () => {
    const { store } = makeStore();
    await expect(store.add("   ")).rejects.toThrow(/empty/);
  });
});

describe("Neo4jMemoryStore.addMessages", () => {
  const turn = (text: string, role = "user") => ({ role, content: [{ text }] });

  it("writes the batch to the sink", async () => {
    const { store, calls } = makeStore();
    const result = await store.addMessages(
      [turn("I prefer dark mode"), turn("Noted", "assistant")] as never,
      { sequenceNumbers: [0, 1] },
    );

    expect(result).toEqual({ written: 2, skipped: 0 });
    expect(calls.bulk).toHaveLength(1);
    expect(calls.bulk[0]!.messages).toEqual([
      { role: "user", content: "I prefer dark mode" },
      { role: "assistant", content: "Noted" },
    ]);
  });

  it("does not write a retried batch twice", async () => {
    const { store, calls } = makeStore();
    const batch = [turn("ok")] as never;

    expect(await store.addMessages(batch, { sequenceNumbers: [0] })).toEqual({
      written: 1,
      skipped: 0,
    });
    expect(await store.addMessages(batch, { sequenceNumbers: [0] })).toEqual({
      written: 0,
      skipped: 1,
    });
    expect(calls.bulk).toHaveLength(1);
  });

  it("keeps identical text carrying distinct sequence numbers", async () => {
    const { store, calls } = makeStore();
    await store.addMessages([turn("ok")] as never, { sequenceNumbers: [0] });
    await store.addMessages([turn("ok")] as never, { sequenceNumbers: [1] });
    expect(calls.bulk).toHaveLength(2);
  });

  it("writes everything when no sequence numbers arrive", async () => {
    const { store, calls } = makeStore();
    await store.addMessages([turn("ok")] as never);
    await store.addMessages([turn("ok")] as never);
    expect(calls.bulk).toHaveLength(2);
  });

  it("drops messages with no text", async () => {
    const { store, calls } = makeStore();
    const result = await store.addMessages(
      [{ role: "assistant", content: [{ toolUse: { name: "x", input: {} } }] }] as never,
      { sequenceNumbers: [0] },
    );

    expect(result).toEqual({ written: 0, skipped: 1 });
    expect(calls.bulk).toEqual([]);
    expect(calls.created).toEqual([]);
  });

  it("chunks batches larger than 100", async () => {
    const { store, calls } = makeStore();
    const messages = Array.from({ length: 250 }, (_, i) => turn(`m${i}`)) as never;

    expect(await store.addMessages(messages)).toEqual({ written: 250, skipped: 0 });
    expect(calls.bulk.map((c) => c.messages.length)).toEqual([100, 100, 50]);
  });

  it("does not re-send a chunk that landed before a later chunk failed", async () => {
    let call = 0;
    const { store, calls } = makeStore({
      clientOverrides: {
        shortTerm: {
          async listConversations() {
            return [];
          },
          async createConversation() {
            return { id: "c1" };
          },
          async bulkAddMessages(sink: string, messages: Array<{ role: string; content: string }>) {
            call += 1;
            if (call === 2) throw new Error("transient");
            return messages.map((_, index) => ({ id: `m${index}` }));
          },
        },
      },
    });
    const messages = Array.from({ length: 150 }, (_, i) => turn(`m${i}`)) as never;
    const sequenceNumbers = Array.from({ length: 150 }, (_, i) => i);

    await expect(store.addMessages(messages, { sequenceNumbers })).rejects.toThrow(
      /transient/,
    );
    // Strands rolls its high-water mark back and retries the whole batch, so
    // the first 100 must already be banked as written.
    const retry = await store.addMessages(messages, { sequenceNumbers });
    expect(retry).toEqual({ written: 50, skipped: 100 });
    void calls;
  });

  it("refuses writes when not writable", async () => {
    const { store } = makeStore({ writable: false });
    await expect(store.addMessages([turn("x")] as never)).rejects.toThrow(/not writable/);
  });

  it("reuses one sink across instances", async () => {
    const { client, calls } = makeClient();
    const batch = [turn("ok")] as never;
    await new Neo4jMemoryStore({ name: "graph", client }).addMessages(batch);
    await new Neo4jMemoryStore({ name: "graph", client }).addMessages(batch);
    expect(calls.created).toHaveLength(1);
  });
});

// Deferred from Task 5: MemoryManager's constructor rejects a `writable` store
// implementing neither sink, and the store defaults to writable. Both sinks
// exist as of this task, so a real manager can finally be constructed.
describe("MemoryManager integration", () => {
  it("passes its own default limit down to the store", async () => {
    // The manager resolves the limit itself and always passes it explicitly
    // (memory-manager.js: options?.maxSearchResults ?? store.maxSearchResults ??
    // DEFAULT_MAX_SEARCH_RESULTS). Pins that default at 3.
    const { MemoryManager } = await import("@strands-agents/sdk");
    const { store, calls } = makeStore();
    const manager = new MemoryManager({ stores: [store] });

    await manager.search("acme");
    expect(calls.searchEntities[0]!.options?.limit).toBe(3);
  });

  it("tags each entry with the store name", async () => {
    const { MemoryManager } = await import("@strands-agents/sdk");
    const { store } = makeStore();
    const manager = new MemoryManager({ stores: [store] });

    const entries = await manager.search("acme");
    expect(entries[0]!.storeName).toBe("graph");
  });
});

describe("Neo4jMemoryStore.getTools", () => {
  async function runTool(tool: { stream: (context: never) => AsyncGenerator<unknown, unknown> }) {
    const generator = tool.stream({
      toolUse: { name: "t", toolUseId: "u1", input: { entityName: "Acme Corp" } },
    } as never);
    let next = await generator.next();
    while (!next.done) next = await generator.next();
    return next.value as { status: string; content: Array<{ json?: unknown }> };
  }

  it("exposes exactly one graph tool, prefixed with the store name", () => {
    const { store } = makeStore();
    const tools = store.getTools();
    expect(tools.map((t) => t.name)).toEqual(["graph_get_entity_graph"]);
    expect(tools[0]!.toolSpec.name).toBe("graph_get_entity_graph");
    expect(tools[0]!.description.length).toBeGreaterThan(0);
  });

  it("omits the preferences tool, which NAMS has no endpoint for", () => {
    const { store } = makeStore({ userId: "alice" });
    expect(store.getTools().map((t) => t.name)).toEqual(["graph_get_entity_graph"]);
  });

  it("exposes nothing when graphTools is false", () => {
    const { store } = makeStore({ graphTools: false });
    expect(store.getTools()).toEqual([]);
  });

  it("is synchronous, because the plugin registry calls it before initAgent", () => {
    const { store } = makeStore();
    // PluginRegistry._addAndInit does `const tools = plugin.getTools?.() ?? []`
    // then `if (tools.length > 0)`. A promise has no length and the tools would
    // be dropped without a word.
    expect(Array.isArray(store.getTools())).toBe(true);
  });

  it("resolves the entity by name, then expands one hop", async () => {
    const { store, calls } = makeStore({
      clientOverrides: {
        longTerm: {
          async searchEntities() {
            return [{ id: "e1", name: "Acme Corp", type: "ORGANIZATION", createdAt: "" }];
          },
          async expandGraph(nodeId: string) {
            calls.expandGraph.push(nodeId);
            return {
              nodes: [{ id: "n2", labels: ["Entity"], properties: { name: "Ada" } }],
              edges: [{ id: "r1", source: "e1", target: "n2", type: "KNOWS" }],
            };
          },
        },
      },
    });

    const result = await runTool(store.getTools()[0] as never);
    expect(result.status).toBe("success");
    expect(calls.expandGraph).toEqual(["e1"]);
    expect(result.content[0]!.json).toEqual({
      center: "Acme Corp",
      depth: 1,
      nodes: [{ id: "n2", name: "Ada", labels: ["Entity"] }],
      edges: [{ from: "e1", relationship: "KNOWS", to: "n2" }],
    });
  });

  it("reports an unknown entity instead of throwing", async () => {
    const { store } = makeStore({
      clientOverrides: {
        longTerm: {
          async searchEntities() {
            return [];
          },
        },
      },
    });

    const result = await runTool(store.getTools()[0] as never);
    expect(result.content[0]!.json).toEqual({ error: "entity not found: Acme Corp" });
  });

  it("caps nodes and edges at 50", async () => {
    const wide = (n: number) =>
      Array.from({ length: n }, (_, i) => ({ id: `n${i}`, properties: { name: `e${i}` } }));
    const { store } = makeStore({
      clientOverrides: {
        longTerm: {
          async searchEntities() {
            return [{ id: "e1", name: "Acme Corp", type: "ORGANIZATION", createdAt: "" }];
          },
          async expandGraph() {
            return {
              nodes: wide(80),
              edges: Array.from({ length: 80 }, (_, i) => ({ source: "e1", target: `n${i}`, type: "R" })),
            };
          },
        },
      },
    });

    const result = await runTool(store.getTools()[0] as never);
    const json = result.content[0]!.json as { nodes: unknown[]; edges: unknown[] };
    expect(json.nodes).toHaveLength(50);
    expect(json.edges).toHaveLength(50);
  });

  it("initializes the store before touching the client", async () => {
    const { store, calls } = makeStore();
    await runTool(store.getTools()[0] as never);
    expect(calls.connect).toBe(1);
  });
});

describe("store tool namespacing", () => {
  it("gives two stores on one manager distinct tool names", async () => {
    const { MemoryManager } = await import("@strands-agents/sdk");
    const personal = makeStore({ name: "personal" }).store;
    const team = makeStore({ name: "team" }).store;
    const manager = new MemoryManager({ stores: [personal, team] });

    const names = manager.getTools().map((t) => t.name);
    expect(names).toContain("personal_get_entity_graph");
    expect(names).toContain("team_get_entity_graph");
  });

  it("keeps a legal prefix for a name needing sanitising", () => {
    const { store } = makeStore({ name: "Team Graph/EU" });
    const name = store.getTools()[0]!.name;
    expect(name).toMatch(/^[a-zA-Z0-9_-]+$/);
    expect(name.length).toBeLessThanOrEqual(64);
    expect(name.endsWith("_get_entity_graph")).toBe(true);
  });

  it("keeps names that sanitise alike distinct", () => {
    const a = makeStore({ name: "team/graph" }).store.getTools()[0]!.name;
    const b = makeStore({ name: "team graph" }).store.getTools()[0]!.name;
    expect(a).not.toBe(b);
  });

  it("keeps an already-legal name digest-free", () => {
    expect(makeStore({ name: "graph" }).store.getTools()[0]!.name).toBe(
      "graph_get_entity_graph",
    );
  });
});
