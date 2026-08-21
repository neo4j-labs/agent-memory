/**
 * Neo4jMemoryStore (TypeScript). NAMS-only, so search is entities-only.
 * Test names mirror tests/unit/integrations/test_strands_memory_store.py
 * wherever behaviour is shared across the two SDKs.
 */

import { describe, it, expect, vi } from "vitest";
import { MemoryClient } from "../../../src/client.js";
import { Neo4jMemoryStore } from "../../../src/integrations/strands/index.js";

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
