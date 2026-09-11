/**
 * Unit tests — `Neo4jMastraMemory`, the thin Mastra-vocabulary thread and
 * message-history adapter.
 *
 * Also pins the gap between this adapter and a real Mastra storage adapter
 * against the installed `@mastra/core` typings, so a Mastra release that
 * renames the storage-domain surface fails here rather than silently making the
 * documentation wrong.
 */

import { describe, it, expect } from "vitest";
import type { MemoryStorage } from "@mastra/core/storage";
import { MemoryClient } from "../../src/client.js";
import { NotSupportedError } from "../../src/errors.js";
import type { Transport } from "../../src/transport/index.js";
import { Neo4jMastraMemory } from "../../src/integrations/mastra.js";

interface Call {
  method: string;
  params: Record<string, unknown>;
}

/** Records calls and answers from a fixture map; unknown methods throw. */
function stubTransport(
  responses: Record<string, unknown | ((params: Record<string, unknown>) => unknown)>,
): { transport: Transport; calls: Call[] } {
  const calls: Call[] = [];
  const transport: Transport = {
    async connect() {},
    async close() {},
    async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
      calls.push({ method, params });
      if (!(method in responses)) {
        throw new NotSupportedError(`Method '${method}' is not supported by this transport.`);
      }
      const entry = responses[method];
      const value = typeof entry === "function"
        ? (entry as (p: Record<string, unknown>) => unknown)(params)
        : entry;
      return value as T;
    },
  };
  return { transport, calls };
}

function adapter(responses: Parameters<typeof stubTransport>[0]): {
  memory: Neo4jMastraMemory;
  calls: Call[];
} {
  const { transport, calls } = stubTransport(responses);
  return { memory: new Neo4jMastraMemory(new MemoryClient(transport)), calls };
}

describe("Neo4jMastraMemory — threads", () => {
  it("folds the title into conversation metadata", async () => {
    const { memory, calls } = adapter({
      create_conversation: { id: "c1", created_at: "t", metadata: { mastraTitle: "Scouting" } },
    });

    const thread = await memory.createThread({
      resourceId: "alice",
      title: "Scouting",
      metadata: { source: "test" },
    });

    expect(thread).toMatchObject({ id: "c1", resourceId: "alice", title: "Scouting" });
    expect(calls[0]!.params["metadata"]).toEqual({ source: "test", mastraTitle: "Scouting" });
  });

  it("never writes an undefined title key", async () => {
    const { memory, calls } = adapter({
      create_conversation: { id: "c1", created_at: "t" },
    });

    await memory.createThread({ resourceId: "alice" });

    const metadata = calls[0]!.params["metadata"] as Record<string, unknown>;
    expect("mastraTitle" in metadata).toBe(false);
  });

  it("gives each thread a distinct id when the transport cannot create one", async () => {
    // No `create_conversation` fixture → NotSupportedError → synthesized id.
    const { memory } = adapter({});

    const first = await memory.createThread({ resourceId: "alice" });
    const second = await memory.createThread({ resourceId: "alice" });

    expect(first.id).not.toBe(second.id);
    expect(first.id.startsWith("alice:")).toBe(true);
    expect(second.resourceId).toBe("alice");
  });

  it("reads the title back through getThreadById", async () => {
    const { memory } = adapter({
      get_conversation_metadata: {
        id: "c1",
        user_id: "alice",
        created_at: "t",
        metadata: { mastraTitle: "Scouting", source: "test" },
      },
    });

    const thread = await memory.getThreadById("c1");
    expect(thread).toMatchObject({ id: "c1", resourceId: "alice", title: "Scouting" });
    expect(thread!.metadata).toMatchObject({ source: "test" });
  });

  it("falls back to clearSession when the transport cannot delete a conversation", async () => {
    const { memory, calls } = adapter({ clear_session: null });
    await memory.deleteThread("c1");
    expect(calls.map((c) => c.method)).toEqual(["delete_conversation", "clear_session"]);
  });
});

describe("Neo4jMastraMemory — messages", () => {
  it("round-trips a message in order with its role", async () => {
    const { memory, calls } = adapter({
      add_message: (p) => ({
        id: "m1",
        role: p["role"],
        content: p["content"],
        created_at: "t1",
        metadata: {},
      }),
      get_conversation: {
        id: "c1",
        session_id: "c1",
        created_at: "t",
        messages: [
          { id: "m1", role: "user", content: "Hi", created_at: "t1", metadata: {} },
          { id: "m2", role: "assistant", content: "Hello", created_at: "t2", metadata: {} },
        ],
      },
    });

    const saved = await memory.saveMessage({ threadId: "c1", role: "user", content: "Hi" });
    expect(saved).toMatchObject({ id: "m1", threadId: "c1", role: "user", content: "Hi" });

    const history = await memory.getMessages("c1");
    expect(history.map((m) => `${m.role}:${m.content}`)).toEqual([
      "user:Hi",
      "assistant:Hello",
    ]);
    expect(history.every((m) => m.threadId === "c1")).toBe(true);
    expect(calls[0]!.method).toBe("add_message");
  });

  it("passes a limit through to the conversation read", async () => {
    const { memory, calls } = adapter({
      get_conversation: { id: "c1", session_id: "c1", created_at: "t", messages: [] },
    });
    await memory.getMessages("c1", { limit: 5 });
    expect(calls[0]!.params["limit"]).toBe(5);
  });
});

describe("Mastra storage-adapter gap", () => {
  /**
   * The abstract surface a real Mastra `memory` storage domain must implement.
   * Typed as `keyof MemoryStorage`, so a rename in `@mastra/core` breaks
   * compilation here instead of rotting in prose.
   */
  const REQUIRED: ReadonlyArray<keyof MemoryStorage> = [
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

  it("lists exactly which storage-domain methods this adapter does not provide", () => {
    const memory = new Neo4jMastraMemory(new MemoryClient({ apiKey: "k" }));
    const missing = REQUIRED.filter(
      (name) => typeof (memory as unknown as Record<string, unknown>)[name] !== "function",
    );

    // `getThreadById` and `deleteThread` happen to share Mastra's names; the
    // rest have no NAMS-backed implementation, which is why this adapter is not
    // wired in as a Mastra storage domain. See the module doc comment.
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
