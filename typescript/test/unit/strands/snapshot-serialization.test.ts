/**
 * Pure-logic tests for Snapshot serialization. We feed synthetic Snapshot
 * objects through Neo4jSessionStorage with a mocked MemoryClient that
 * records every addMessage call, and assert the message extraction +
 * synthetic-state-message behaviour. No HTTP involved.
 */

import { describe, it, expect, vi } from "vitest";
import type { Snapshot } from "@strands-agents/sdk";
import {
  Neo4jSessionStorage,
  isSyntheticStrandsMessage,
} from "../../../src/integrations/strands/index.js";

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  metadata?: Record<string, unknown>;
}

function makeFakeClient(opts: {
  initialMessages?: StoredMessage[];
} = {}) {
  const messages: StoredMessage[] = (opts.initialMessages ?? []).map((m) => ({ ...m }));
  const addCalls: Array<{
    convId: string;
    role: string;
    content: string;
    metadata?: Record<string, unknown>;
  }> = [];

  const fake = {
    shortTerm: {
      async getConversation(_id: string) {
        return { id: _id, messages: messages.map((m) => ({ ...m })) };
      },
      async addMessage(
        convId: string,
        role: string,
        content: string,
        addOpts?: { metadata?: Record<string, unknown> },
      ) {
        const id = `m${messages.length + 1}`;
        const m: StoredMessage = { id, role, content, metadata: addOpts?.metadata };
        messages.push(m);
        addCalls.push({ convId, role, content, metadata: addOpts?.metadata });
        return m;
      },
      async deleteConversation(_convId: string) {
        return undefined;
      },
    },
  };

  return {
    client: fake as unknown as ConstructorParameters<typeof Neo4jSessionStorage>[0],
    addCalls,
    getMessages: () => messages,
  };
}

function snapshotWith(opts: {
  messages?: Array<{ role: string; content: Array<{ text?: string; type?: string }> }>;
  data?: Record<string, unknown>;
  appData?: Record<string, unknown>;
}): Snapshot {
  return {
    scope: "agent",
    schemaVersion: "1.0",
    createdAt: new Date().toISOString(),
    data: {
      ...(opts.data ?? {}),
      messages: opts.messages ?? [],
    } as unknown as Snapshot["data"],
    appData: (opts.appData ?? {}) as Snapshot["appData"],
  };
}

const LOCATION = { sessionId: "conv-1", scope: "agent" as const, scopeId: "agent-1" };

/**
 * Decode the JSON blob from a synthetic state message's content.
 * Mirrors the integration's `decodeBlob` for prefix `__strands_state__:`.
 */
function decodeStateContent(msg: StoredMessage): {
  snapshotId: string;
  isLatest: boolean;
  snapshot: Snapshot;
  savedAt: string;
} {
  const prefix = "__strands_state__:";
  if (!msg.content.startsWith(prefix)) {
    throw new Error("expected __strands_state__ content prefix");
  }
  const b64 = msg.content.slice(prefix.length);
  return JSON.parse(Buffer.from(b64, "base64").toString("utf8"));
}

/** Encode a state blob into the synthetic content format the integration uses. */
function encodeStateContent(blob: object): string {
  return `__strands_state__:${Buffer.from(JSON.stringify(blob), "utf8").toString("base64")}`;
}

describe("Neo4jSessionStorage — message extraction", () => {
  it("extracts text-block messages and persists each via addMessage", async () => {
    const { client, addCalls } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const snap = snapshotWith({
      messages: [
        { role: "user", content: [{ text: "hello" }] },
        { role: "assistant", content: [{ text: "hi there" }] },
      ],
    });

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snap,
    });

    // Two real messages + one synthetic state message.
    const realAddCalls = addCalls.filter(
      (c) =>
        (c.role === "user" || c.role === "assistant") &&
        !c.content.startsWith("__strands_state__:") &&
        !c.content.startsWith("__strands_manifest__:"),
    );
    expect(realAddCalls).toHaveLength(2);
    expect(realAddCalls[0]).toMatchObject({ role: "user", content: "hello" });
    expect(realAddCalls[1]).toMatchObject({ role: "assistant", content: "hi there" });
  });

  it("dedupes against existing conversation messages (ignoring synthetic state markers)", async () => {
    const { client, addCalls } = makeFakeClient({
      initialMessages: [
        { id: "m0", role: "user", content: "hello" },
        // A prior state marker should NOT count as a real message for dedup.
        {
          id: "m-state",
          role: "user",
          content: "__strands_state__:prior",
          metadata: { strands_state: "{}" },
        },
      ],
    });
    const storage = new Neo4jSessionStorage(client);
    const snap = snapshotWith({
      messages: [
        { role: "user", content: [{ text: "hello" }] },
        { role: "assistant", content: [{ text: "new" }] },
      ],
    });

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snap,
    });

    const realAddCalls = addCalls.filter(
      (c) =>
        (c.role === "user" || c.role === "assistant") &&
        !c.content.startsWith("__strands_state__:") &&
        !c.content.startsWith("__strands_manifest__:"),
    );
    expect(realAddCalls).toHaveLength(1);
    expect(realAddCalls[0]).toMatchObject({ role: "assistant", content: "new" });
  });

  it("handles snapshots with zero messages — still writes a state marker", async () => {
    const { client, addCalls } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snapshotWith({ messages: [] }),
    });
    const real = addCalls.filter((c) => c.role !== "user" && c.content.startsWith("__strands_state__:") === false);
    expect(real).toHaveLength(0);
    // The synthetic state marker still got written.
    const synthetic = addCalls.filter(
      (c) => c.content.startsWith("__strands_state__:"),
    );
    expect(synthetic).toHaveLength(1);
  });

  it("handles malformed snapshots (missing messages field) without throwing", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const malformed = {
      scope: "agent",
      schemaVersion: "1.0",
      createdAt: "x",
      data: {} as Snapshot["data"],
      appData: {} as Snapshot["appData"],
    } as Snapshot;
    await expect(
      storage.saveSnapshot({
        location: LOCATION,
        snapshotId: "s1",
        isLatest: true,
        snapshot: malformed,
      }),
    ).resolves.toBeUndefined();
  });

  it("preserves non-message snapshot state in the synthetic message metadata", async () => {
    const { client, getMessages } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snapshotWith({
        messages: [{ role: "user", content: [{ text: "hi" }] }],
        data: { agentState: { foo: 1 } },
        appData: { userCounter: 42 },
      }),
    });

    const synthetic = getMessages().find(
      (m) => m.content.startsWith("__strands_state__:"),
    );
    expect(synthetic).toBeDefined();
    const blob = decodeStateContent(synthetic!);
    expect(blob.snapshotId).toBe("s1");
    expect(blob.isLatest).toBe(true);
    expect(blob.snapshot.appData).toMatchObject({ userCounter: 42 });
    expect(blob.snapshot.data).toMatchObject({ agentState: { foo: 1 } });
    // messages field stripped from the persisted snapshot
    expect((blob.snapshot.data as Record<string, unknown>).messages).toBeUndefined();
  });

  it("each distinct snapshotId writes a synthetic state message in order", async () => {
    const { client, getMessages } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: false,
      snapshot: snapshotWith({ messages: [] }),
    });
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s2",
      isLatest: true,
      snapshot: snapshotWith({ messages: [] }),
    });

    const states = getMessages().filter(
      (m) => m.content.startsWith("__strands_state__:"),
    );
    expect(states).toHaveLength(2);
    expect(decodeStateContent(states[0]!).snapshotId).toBe("s1");
    expect(decodeStateContent(states[0]!).isLatest).toBe(false);
    expect(decodeStateContent(states[1]!).snapshotId).toBe("s2");
    expect(decodeStateContent(states[1]!).isLatest).toBe(true);
  });

  it("re-saving the same snapshotId with the same state is a no-op for synthetic markers", async () => {
    const { client, getMessages } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const snapshot = snapshotWith({ messages: [{ role: "user", content: [{ text: "hi" }] }] });

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot,
    });
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot,
    });

    const states = getMessages().filter((m) => m.content.startsWith("__strands_state__:"));
    expect(states).toHaveLength(1);
  });
});

describe("Neo4jSessionStorage — load + list + delete", () => {
  function seedConversationWithSnapshot(
    snapshotId: string,
    isLatest: boolean,
    snapshot: Snapshot,
  ): StoredMessage {
    return {
      id: `state-${snapshotId}`,
      role: "user",
      content: encodeStateContent({
        snapshotId,
        isLatest,
        snapshot,
        savedAt: new Date().toISOString(),
      }),
    };
  }

  it("loadSnapshot returns the stashed blob + current messages merged in", async () => {
    const seededSnap: Snapshot = {
      scope: "agent",
      schemaVersion: "1.0",
      createdAt: "x",
      data: { agentState: { foo: 1 } } as unknown as Snapshot["data"],
      appData: { stored: true } as unknown as Snapshot["appData"],
    };
    const { client } = makeFakeClient({
      initialMessages: [
        { id: "m1", role: "user", content: "hi" },
        seedConversationWithSnapshot("s1", true, seededSnap),
      ],
    });
    const storage = new Neo4jSessionStorage(client);
    const snap = await storage.loadSnapshot({ location: LOCATION });
    expect(snap).not.toBeNull();
    expect(snap!.data).toMatchObject({ agentState: { foo: 1 } });
    const messages = (snap!.data as { messages?: unknown[] }).messages;
    expect(Array.isArray(messages)).toBe(true);
    expect(messages).toHaveLength(1); // only the real message; synthetic marker filtered out
  });

  it("loadSnapshot returns null when no snapshots exist", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const snap = await storage.loadSnapshot({ location: LOCATION });
    expect(snap).toBeNull();
  });

  it("loadSnapshot prefers the latest stored blob for an explicit snapshotId", async () => {
    const baseSnapshot: Snapshot = {
      scope: "agent",
      schemaVersion: "1.0",
      createdAt: "x",
      data: {} as Snapshot["data"],
      appData: {} as Snapshot["appData"],
    };
    const { client } = makeFakeClient({
      initialMessages: [
        seedConversationWithSnapshot("s1", false, {
          ...baseSnapshot,
          data: { version: 1 } as Snapshot["data"],
        }),
        seedConversationWithSnapshot("s1", true, {
          ...baseSnapshot,
          data: { version: 2 } as Snapshot["data"],
        }),
      ],
    });
    const storage = new Neo4jSessionStorage(client);

    const snap = await storage.loadSnapshot({ location: LOCATION, snapshotId: "s1" });
    expect((snap!.data as Record<string, unknown>).version).toBe(2);
  });

  it("listSnapshotIds respects limit + startAfter", async () => {
    const { client } = makeFakeClient({
      initialMessages: ["a", "b", "c", "d", "e"].map((id) =>
        seedConversationWithSnapshot(id, id === "e", {
          scope: "agent",
          schemaVersion: "1.0",
          createdAt: "x",
          data: {} as Snapshot["data"],
          appData: {} as Snapshot["appData"],
        }),
      ),
    });
    const storage = new Neo4jSessionStorage(client);
    expect(await storage.listSnapshotIds({ location: LOCATION })).toEqual([
      "a", "b", "c", "d", "e",
    ]);
    expect(await storage.listSnapshotIds({ location: LOCATION, limit: 2 })).toEqual(["a", "b"]);
    expect(
      await storage.listSnapshotIds({ location: LOCATION, startAfter: "b" }),
    ).toEqual(["c", "d", "e"]);
    expect(
      await storage.listSnapshotIds({ location: LOCATION, startAfter: "b", limit: 2 }),
    ).toEqual(["c", "d"]);
  });

  it("deleteSession calls deleteConversation", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    // Wire a spy by re-binding deleteConversation:
    const calls: string[] = [];
    (client as unknown as { shortTerm: { deleteConversation: (id: string) => Promise<void> } })
      .shortTerm.deleteConversation = async (id: string) => {
      calls.push(id);
    };
    await storage.deleteSession({ sessionId: "conv-1" });
    expect(calls).toEqual(["conv-1"]);
  });
});

describe("Neo4jSessionStorage — manifest", () => {
  it("manifest round-trip via a synthetic manifest message", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const manifest = { schemaVersion: "1.0", updatedAt: "2026-05-16T00:00:00Z" };
    await storage.saveManifest({ location: LOCATION, manifest });
    const loaded = await storage.loadManifest({ location: LOCATION });
    expect(loaded).toEqual(manifest);
  });

  it("loadManifest returns a default when none stored", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const loaded = await storage.loadManifest({ location: LOCATION });
    expect(loaded.schemaVersion).toBe("1.0");
    expect(typeof loaded.updatedAt).toBe("string");
  });

  it("last-write-wins per scopeId", async () => {
    const { client } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    await storage.saveManifest({
      location: LOCATION,
      manifest: { schemaVersion: "1.0", updatedAt: "first" },
    });
    await storage.saveManifest({
      location: LOCATION,
      manifest: { schemaVersion: "1.0", updatedAt: "second" },
    });
    const loaded = await storage.loadManifest({ location: LOCATION });
    expect(loaded.updatedAt).toBe("second");
  });
});

describe("Neo4jSessionStorage — unicode + long content", () => {
  it("preserves unicode through extraction + persistence", async () => {
    const { client, addCalls } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const content = "你好 🚀 émoji ñ ç ø";
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snapshotWith({
        messages: [{ role: "user", content: [{ text: content }] }],
      }),
    });
    const real = addCalls.filter((c) => c.role === "user");
    expect(real[0]).toMatchObject({ content });
  });

  it("preserves a thousand messages without loss", async () => {
    const { client, addCalls } = makeFakeClient();
    const storage = new Neo4jSessionStorage(client);
    const big = Array.from({ length: 1000 }, (_, i) => ({
      role: i % 2 === 0 ? "user" : "assistant",
      content: [{ text: `msg-${i}` }],
    }));
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snapshotWith({ messages: big }),
    });
    const real = addCalls.filter(
      (c) =>
        (c.role === "user" || c.role === "assistant") &&
        !c.content.startsWith("__strands_state__:") &&
        !c.content.startsWith("__strands_manifest__:"),
    );
    expect(real).toHaveLength(1000);
  });
});

describe("isSyntheticStrandsMessage", () => {
  it("recognizes state and manifest markers on any role", () => {
    // The integration writes role=user; we match on content prefix alone
    // for resilience against role normalization on the service side.
    expect(
      isSyntheticStrandsMessage({ role: "user", content: "__strands_state__:s1" }),
    ).toBe(true);
    expect(
      isSyntheticStrandsMessage({ role: "system", content: "__strands_state__:s1" }),
    ).toBe(true);
    expect(
      isSyntheticStrandsMessage({ role: "user", content: "__strands_manifest__:agent-1" }),
    ).toBe(true);
  });

  it("rejects messages without the prefix", () => {
    expect(
      isSyntheticStrandsMessage({ role: "user", content: "real user message" }),
    ).toBe(false);
    expect(
      isSyntheticStrandsMessage({ role: "system", content: "real system message" }),
    ).toBe(false);
  });
});

// Silence unused warning on vi when unused in this file.
void vi;
