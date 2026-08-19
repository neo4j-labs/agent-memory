/**
 * Integration tests — Neo4jSessionStorage drives the real RestTransport
 * against an MSW-mocked /v1 server.
 *
 * Storage shape post-pivot: state is carried in synthetic `role: "user"`
 * messages whose content has the form
 * `__strands_state__:{base64(JSON.stringify(blob))}`. No conversation-
 * level metadata is written (NAMS doesn't expose that endpoint) and no
 * per-message metadata is used (the live service doesn't reliably
 * round-trip it).
 *
 * Exercises:
 *   - Save → read back of synthetic messages
 *   - listSnapshotIds order + paging
 *   - loadSnapshot by explicit id + latest fallback
 *   - Manifest round-trip
 *   - Auth + transport error propagation
 *   - deleteSession via DELETE /conversations/:id
 *   - Idempotency: re-saving doesn't duplicate real messages
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { Snapshot } from "@strands-agents/sdk";
import { MemoryClient } from "../../src/client.js";
import {
  AuthenticationError,
  TransportError,
} from "../../src/errors.js";
import { Neo4jSessionStorage } from "../../src/integrations/strands/index.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";
const SESSION_ID = "conv-strands-session";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function newStorage() {
  const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
  return new Neo4jSessionStorage(client);
}

function snap(opts: {
  messages?: Array<{ role: string; content: Array<{ text: string }> }>;
  agentState?: Record<string, unknown>;
} = {}): Snapshot {
  return {
    scope: "agent",
    schemaVersion: "1.0",
    createdAt: new Date().toISOString(),
    data: {
      ...(opts.agentState ?? {}),
      messages: opts.messages ?? [],
    } as unknown as Snapshot["data"],
    appData: {} as Snapshot["appData"],
  };
}

const LOCATION = { sessionId: SESSION_ID, scope: "agent" as const, scopeId: "a1" };

interface ServerMessage {
  id: string;
  role: string;
  content: string;
  metadata?: Record<string, unknown>;
}

interface ServerState {
  messages: ServerMessage[];
}

/**
 * Mount handlers that act like the hosted service's
 * GET /conversations/:id/messages + POST /conversations/:id/messages,
 * plus DELETE. No metadata routes — those don't exist on NAMS.
 */
function mountConversationHandlers(state: ServerState) {
  server.use(
    http.get(`${ENDPOINT}/conversations/${SESSION_ID}/messages`, () =>
      HttpResponse.json({ messages: state.messages.map((m) => ({ ...m })) }),
    ),
    http.post(`${ENDPOINT}/conversations/${SESSION_ID}/messages`, async ({ request }) => {
      const body = (await request.json()) as {
        role: string;
        content: string;
        metadata?: Record<string, unknown>;
      };
      const id = `m${state.messages.length + 1}`;
      const m: ServerMessage = {
        id,
        role: body.role,
        content: body.content,
        metadata: body.metadata,
      };
      state.messages.push(m);
      return HttpResponse.json(m);
    }),
    http.delete(`${ENDPOINT}/conversations/${SESSION_ID}`, () =>
      new HttpResponse(null, { status: 204 }),
    ),
  );
}

function decodeStateBlob<T>(msg: ServerMessage): T {
  const prefix = "__strands_state__:";
  if (!msg.content.startsWith(prefix)) throw new Error("missing state prefix");
  const b64 = msg.content.slice(prefix.length);
  return JSON.parse(Buffer.from(b64, "base64").toString("utf8")) as T;
}

function decodeManifestBlob<T>(msg: ServerMessage): T {
  const prefix = "__strands_manifest__:";
  if (!msg.content.startsWith(prefix)) throw new Error("missing manifest prefix");
  const b64 = msg.content.slice(prefix.length);
  return JSON.parse(Buffer.from(b64, "base64").toString("utf8")) as T;
}

function encodeBlobContent(prefix: string, blob: unknown): string {
  return `${prefix}${Buffer.from(JSON.stringify(blob), "utf8").toString("base64")}`;
}

describe("Neo4jSessionStorage — synthetic-message storage", () => {
  it("saveSnapshot writes one synthetic user message for a distinct snapshot + the real conversation messages", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snap({
        messages: [
          { role: "user", content: [{ text: "hello" }] },
          { role: "assistant", content: [{ text: "hi" }] },
        ],
        agentState: { fooBar: 1 },
      }),
    });

    // Real messages persisted as Message nodes (synthetic marker filtered out).
    const real = state.messages.filter(
      (m) =>
        (m.role === "user" || m.role === "assistant") &&
        !m.content.startsWith("__strands_state__:") &&
        !m.content.startsWith("__strands_manifest__:"),
    );
    expect(real).toHaveLength(2);
    expect(real[0]).toMatchObject({ role: "user", content: "hello" });

    // One synthetic state message with our marker.
    const synthetic = state.messages.filter((m) =>
      m.content.startsWith("__strands_state__:"),
    );
    expect(synthetic).toHaveLength(1);
    const blob = decodeStateBlob<{
      snapshotId: string;
      isLatest: boolean;
      snapshot: Snapshot;
    }>(synthetic[0]!);
    expect(blob.snapshotId).toBe("s1");
    expect(blob.isLatest).toBe(true);
    expect(blob.snapshot.data).toMatchObject({ fooBar: 1 });
    expect((blob.snapshot.data as Record<string, unknown>).messages).toBeUndefined();
  });

  it("loadSnapshot returns the latest blob + real messages (synthetic markers filtered)", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "s1",
      isLatest: true,
      snapshot: snap({
        messages: [{ role: "user", content: [{ text: "hello" }] }],
        agentState: { stored: 1 },
      }),
    });

    const loaded = await storage.loadSnapshot({ location: LOCATION });
    expect(loaded).not.toBeNull();
    expect((loaded!.data as Record<string, unknown>).stored).toBe(1);
    const messages = (loaded!.data as { messages?: unknown[] }).messages;
    // Should contain the real user message; should NOT contain the synthetic state marker.
    expect(Array.isArray(messages)).toBe(true);
    expect(messages).toHaveLength(1);
  });

  it("loadSnapshot honors an explicit snapshotId", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();

    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "first",
      isLatest: false,
      snapshot: snap({ messages: [], agentState: { tag: "first" } }),
    });
    await storage.saveSnapshot({
      location: LOCATION,
      snapshotId: "second",
      isLatest: true,
      snapshot: snap({ messages: [], agentState: { tag: "second" } }),
    });

    const a = await storage.loadSnapshot({ location: LOCATION, snapshotId: "first" });
    const b = await storage.loadSnapshot({ location: LOCATION, snapshotId: "second" });
    expect((a!.data as Record<string, unknown>).tag).toBe("first");
    expect((b!.data as Record<string, unknown>).tag).toBe("second");
  });

  it("loadSnapshot returns null when conversation has no synthetic state messages", async () => {
    const state: ServerState = {
      messages: [
        { id: "m1", role: "user", content: "regular message", metadata: undefined },
      ],
    };
    mountConversationHandlers(state);
    const storage = newStorage();
    const loaded = await storage.loadSnapshot({ location: LOCATION });
    expect(loaded).toBeNull();
  });

  it("listSnapshotIds returns IDs in save order", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();

    for (const id of ["a", "b", "c", "d"]) {
      await storage.saveSnapshot({
        location: LOCATION,
        snapshotId: id,
        isLatest: id === "d",
        snapshot: snap({ messages: [] }),
      });
    }

    expect(await storage.listSnapshotIds({ location: LOCATION })).toEqual(["a", "b", "c", "d"]);
    expect(await storage.listSnapshotIds({ location: LOCATION, limit: 2 })).toEqual(["a", "b"]);
    expect(
      await storage.listSnapshotIds({ location: LOCATION, startAfter: "b", limit: 2 }),
    ).toEqual(["c", "d"]);
  });

  it("deleteSession issues DELETE /conversations/:id", async () => {
    let deleted = false;
    server.use(
      http.delete(`${ENDPOINT}/conversations/${SESSION_ID}`, () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const storage = newStorage();
    await storage.deleteSession({ sessionId: SESSION_ID });
    expect(deleted).toBe(true);
  });

  it("manifest round-trip via a synthetic user message", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();
    const manifest = { schemaVersion: "1.0", updatedAt: "2026-05-16T00:00:00Z" };

    await storage.saveManifest({ location: LOCATION, manifest });
    const loaded = await storage.loadManifest({ location: LOCATION });
    expect(loaded).toEqual(manifest);

    const synthetic = state.messages.filter((m) =>
      m.content.startsWith("__strands_manifest__:"),
    );
    expect(synthetic).toHaveLength(1);
    const blob = decodeManifestBlob<{
      scopeId: string;
      manifest: { schemaVersion: string; updatedAt: string };
    }>(synthetic[0]!);
    expect(blob.scopeId).toBe("a1");
    expect(blob.manifest).toEqual(manifest);
  });
});

describe("Neo4jSessionStorage — idempotency + error surface", () => {
  it("re-saving the same snapshotId does not duplicate real messages or synthetic markers", async () => {
    const state: ServerState = { messages: [] };
    mountConversationHandlers(state);
    const storage = newStorage();
    const snapshot = snap({
      messages: [{ role: "user", content: [{ text: "hello" }] }],
    });

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

    const real = state.messages.filter(
      (m) =>
        (m.role === "user" || m.role === "assistant") &&
        !m.content.startsWith("__strands_state__:") &&
        !m.content.startsWith("__strands_manifest__:"),
    );
    // The user message is added only once (dedupe by role+content).
    expect(real).toHaveLength(1);
    const synthetic = state.messages.filter((m) =>
      m.content.startsWith("__strands_state__:"),
    );
    expect(synthetic).toHaveLength(1);
    expect(await storage.listSnapshotIds({ location: LOCATION })).toEqual(["s1"]);
  });

  it("loadSnapshot prefers the latest stored blob for an explicit snapshotId", async () => {
    const state: ServerState = {
      messages: [
        {
          id: "m1",
          role: "user",
          content: encodeBlobContent("__strands_state__:", {
            snapshotId: "s1",
            isLatest: false,
            snapshot: snap({ agentState: { version: 1 } }),
            savedAt: "2026-05-16T00:00:00Z",
          }),
        },
        {
          id: "m2",
          role: "user",
          content: encodeBlobContent("__strands_state__:", {
            snapshotId: "s1",
            isLatest: true,
            snapshot: snap({ agentState: { version: 2 } }),
            savedAt: "2026-05-16T00:01:00Z",
          }),
        },
      ],
    };
    mountConversationHandlers(state);
    const storage = newStorage();

    const loaded = await storage.loadSnapshot({ location: LOCATION, snapshotId: "s1" });
    expect((loaded!.data as Record<string, unknown>).version).toBe(2);
  });

  it("auth failure on read propagates AuthenticationError with requestId", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations/${SESSION_ID}/messages`, () =>
        new HttpResponse("nope", {
          status: 401,
          headers: { "x-request-id": "req-401" },
        }),
      ),
    );
    const storage = newStorage();
    try {
      await storage.loadSnapshot({ location: LOCATION });
      throw new Error("expected throw");
    } catch (e) {
      expect(e).toBeInstanceOf(AuthenticationError);
      expect((e as AuthenticationError).requestId).toBe("req-401");
    }
  });

  it("5xx on save surfaces as TransportError with requestId", async () => {
    server.use(
      // GET must succeed (the integration reads existing messages before
      // dedup-and-write) so we can reach the POST that fails.
      http.get(`${ENDPOINT}/conversations/${SESSION_ID}/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.post(`${ENDPOINT}/conversations/${SESSION_ID}/messages`, () =>
        HttpResponse.json(
          { error: "boom" },
          { status: 503, headers: { "x-request-id": "req-503" } },
        ),
      ),
    );
    const storage = newStorage();
    try {
      await storage.saveSnapshot({
        location: LOCATION,
        snapshotId: "s1",
        isLatest: true,
        snapshot: snap({ messages: [{ role: "user", content: [{ text: "hi" }] }] }),
      });
      throw new Error("expected throw");
    } catch (e) {
      expect(e).toBeInstanceOf(TransportError);
      expect((e as TransportError).statusCode).toBe(503);
      expect((e as TransportError).requestId).toBe("req-503");
    }
  });
});

describe("Neo4jSessionStorage — bridge-transport compatibility", () => {
  it("listSnapshotIds reads via the snake_case method on bridge transport", async () => {
    const bridgeRoot = "http://bridge.test";
    server.use(
      http.post(`${bridgeRoot}/get_conversation`, async ({ request }) => {
        const body = (await request.json()) as { session_id?: string };
        expect(body.session_id).toBe(SESSION_ID);
        const emptySnap = {
          scope: "agent",
          schemaVersion: "1.0",
          createdAt: "x",
          data: {},
          appData: {},
        };
        return HttpResponse.json({
          session_id: SESSION_ID,
          messages: [
            {
              id: "m1",
              role: "user",
              content: encodeBlobContent("__strands_state__:", {
                snapshotId: "x",
                isLatest: true,
                snapshot: emptySnap,
                savedAt: "x",
              }),
            },
            {
              id: "m2",
              role: "user",
              content: encodeBlobContent("__strands_state__:", {
                snapshotId: "y",
                isLatest: false,
                snapshot: emptySnap,
                savedAt: "x",
              }),
            },
          ],
        });
      }),
    );
    const client = new MemoryClient({ endpoint: bridgeRoot });
    const storage = new Neo4jSessionStorage(client);
    const ids = await storage.listSnapshotIds({ location: LOCATION });
    expect(ids).toEqual(["x", "y"]);
  });
});
