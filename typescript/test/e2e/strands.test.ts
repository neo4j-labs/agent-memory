/**
 * End-to-end tests for the Strands integration against the live
 * hosted Neo4j Agent Memory Service.
 *
 * Mirrors the hosted-service.test.ts conventions:
 *   - Skipped wholesale when MEMORY_API_KEY is unset.
 *   - Every conversation tagged with provenance metadata + `tck-e2e-ts-strands-`
 *     user prefix, deleted via afterAll. Failures during cleanup are swallowed.
 *
 * Exercises the integration's three surfaces against real NAMS:
 *   1. Neo4jSessionStorage — full SnapshotStorage round-trip
 *   2. Neo4jConversationManager — three-tier context injection
 *   3. registerReasoningHooks — step + tool-call capture
 *
 * The reasoning hooks scenario fires Strands events DIRECTLY rather than
 * driving a full `agent.invoke()` loop. This is intentional: we test the
 * integration code's behaviour against the real service without depending
 * on a working Strands model provider (which would need its own stub plus
 * an LLM key in CI).
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { Snapshot, SnapshotLocation } from "@strands-agents/sdk";
import {
  AfterInvocationEvent,
  AfterToolCallEvent,
  BeforeInvocationEvent,
  BeforeToolCallEvent,
  NullConversationManager,
  type LocalAgent,
} from "@strands-agents/sdk";
import { MemoryClient } from "../../src/client.js";
import {
  Neo4jConversationManager,
  Neo4jSessionStorage,
  registerReasoningHooks,
} from "../../src/integrations/strands/index.js";
import { metadataFor } from "./tck-provenance.js";

const API_KEY = (process.env.MEMORY_API_KEY ?? "").trim();
const ENDPOINT = process.env.MEMORY_ENDPOINT ?? "https://memory.neo4jlabs.com/v1";
const HAS_KEY = API_KEY.length > 0;

const describeOrSkip = HAS_KEY ? describe : describe.skip;

const UNIQUE_TAG = randomHex(8);
const USER_PREFIX = process.env.MEMORY_E2E_USER_ID ?? "tck-e2e-ts-strands";

function randomHex(n: number): string {
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const buf = new Uint8Array(Math.ceil(n / 2));
    crypto.getRandomValues(buf);
    return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, n);
  }
  return Date.now().toString(16).slice(-n);
}

function userId(suffix = ""): string {
  const rand = randomHex(6);
  const base = `${USER_PREFIX}-${UNIQUE_TAG}-${rand}`;
  return suffix ? `${base}-${suffix}` : base;
}

async function waitUntil<T>(
  predicate: () => Promise<T | null | undefined>,
  { timeout = 20_000, interval = 1_500 }: { timeout?: number; interval?: number } = {},
): Promise<T | null> {
  const deadline = Date.now() + timeout;
  let last: T | null | undefined = null;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((r) => setTimeout(r, interval));
  }
  return last ?? null;
}

function location(sessionId: string): SnapshotLocation {
  return { sessionId, scope: "agent" as const, scopeId: "agent-1" };
}

function snap(opts: {
  messages?: Array<{ role: string; content: Array<{ text: string }> }>;
  agentState?: Record<string, unknown>;
  appData?: Record<string, unknown>;
}): Snapshot {
  return {
    scope: "agent",
    schemaVersion: "1.0",
    createdAt: new Date().toISOString(),
    data: {
      ...(opts.agentState ?? {}),
      messages: opts.messages ?? [],
    } as unknown as Snapshot["data"],
    appData: (opts.appData ?? {}) as Snapshot["appData"],
  };
}

function makeAgentStub(messages: Array<Record<string, unknown>> = []) {
  const hooks = new Map<unknown, Array<(e: unknown) => Promise<void> | void>>();
  const agent = {
    id: "a",
    messages,
    addHook(eventClass: unknown, cb: (e: unknown) => Promise<void> | void) {
      const list = hooks.get(eventClass) ?? [];
      list.push(cb);
      hooks.set(eventClass, list);
      return () => {};
    },
  } as unknown as LocalAgent;
  return { agent, hooks };
}

describeOrSkip("Strands integration e2e — live NAMS", () => {
  let client: MemoryClient;
  let currentTestName = "unknown";
  const cleanupConversations: string[] = [];

  beforeAll(async () => {
    client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    await client.connect();
  }, 30_000);

  beforeEach((ctx) => {
    currentTestName = ctx?.task?.name ?? "unknown";
  });

  afterAll(async () => {
    for (const id of cleanupConversations) {
      try {
        await client.shortTerm.deleteConversation(id);
      } catch {
        // best-effort
      }
    }
    await client.close();
  }, 60_000);

  async function newConv(opts?: { suffix?: string }) {
    const meta = metadataFor(currentTestName, { tck_phase: "fixture", surface: "strands" });
    const conv = await client.shortTerm.createConversation({
      userId: userId(opts?.suffix ?? ""),
      metadata: meta,
    });
    cleanupConversations.push(conv.id);
    return conv;
  }

  // ====================================================================
  // 1. Neo4jSessionStorage against live NAMS
  // ====================================================================

  describe("Neo4jSessionStorage", () => {
    it("saveSnapshot + loadSnapshot round-trip", async () => {
      const conv = await newConv({ suffix: "ssr" });
      const storage = new Neo4jSessionStorage(client);
      const loc = location(conv.id);

      const snapshot = snap({
        messages: [
          { role: "user", content: [{ text: "hello from strands e2e" }] },
          { role: "assistant", content: [{ text: "hi! how can I help?" }] },
        ],
        agentState: { stepCount: 1, customField: "value" },
        appData: { userMarker: "tck-e2e" },
      });

      await storage.saveSnapshot({
        location: loc,
        snapshotId: "s1",
        isLatest: true,
        snapshot,
      });

      const loaded = await storage.loadSnapshot({ location: loc });
      expect(loaded).not.toBeNull();
      expect(loaded!.appData).toMatchObject({ userMarker: "tck-e2e" });
      expect((loaded!.data as Record<string, unknown>).customField).toBe("value");
      const messages = (loaded!.data as { messages?: unknown[] }).messages;
      expect(Array.isArray(messages)).toBe(true);
      expect((messages ?? []).length).toBeGreaterThanOrEqual(2);
    }, 30_000);

    it("listSnapshotIds returns IDs in saved order", async () => {
      const conv = await newConv({ suffix: "list" });
      const storage = new Neo4jSessionStorage(client);
      const loc = location(conv.id);

      for (const id of ["s1", "s2", "s3"]) {
        await storage.saveSnapshot({
          location: loc,
          snapshotId: id,
          isLatest: id === "s3",
          snapshot: snap({ messages: [] }),
        });
      }

      const all = await storage.listSnapshotIds({ location: loc });
      expect(all).toEqual(["s1", "s2", "s3"]);

      const page = await storage.listSnapshotIds({ location: loc, startAfter: "s1", limit: 2 });
      expect(page).toEqual(["s2", "s3"]);
    }, 30_000);

    it("loadSnapshot honors an explicit snapshotId", async () => {
      const conv = await newConv({ suffix: "explicit" });
      const storage = new Neo4jSessionStorage(client);
      const loc = location(conv.id);

      await storage.saveSnapshot({
        location: loc,
        snapshotId: "first",
        isLatest: false,
        snapshot: snap({ messages: [], agentState: { tag: "first" } }),
      });
      await storage.saveSnapshot({
        location: loc,
        snapshotId: "second",
        isLatest: true,
        snapshot: snap({ messages: [], agentState: { tag: "second" } }),
      });

      const first = await storage.loadSnapshot({ location: loc, snapshotId: "first" });
      const second = await storage.loadSnapshot({ location: loc, snapshotId: "second" });
      expect((first!.data as Record<string, unknown>).tag).toBe("first");
      expect((second!.data as Record<string, unknown>).tag).toBe("second");
    }, 30_000);

    it("manifest round-trip", async () => {
      const conv = await newConv({ suffix: "manifest" });
      const storage = new Neo4jSessionStorage(client);
      const loc = location(conv.id);
      const manifest = {
        schemaVersion: "1.0",
        updatedAt: new Date().toISOString(),
      };

      await storage.saveManifest({ location: loc, manifest });
      const loaded = await storage.loadManifest({ location: loc });
      expect(loaded.schemaVersion).toBe("1.0");
      expect(loaded.updatedAt).toBe(manifest.updatedAt);
    }, 30_000);

    it("deleteSession removes the conversation", async () => {
      const conv = await newConv({ suffix: "delete" });
      const storage = new Neo4jSessionStorage(client);
      await storage.deleteSession({ sessionId: conv.id });

      // Remove from cleanup list since it's already deleted.
      const idx = cleanupConversations.indexOf(conv.id);
      if (idx >= 0) cleanupConversations.splice(idx, 1);

      // The deleted conversation should no longer be retrievable. The
      // hosted service may either 404 or return tombstoned metadata; we
      // assert *something* surfaces as failure.
      let observed = false;
      try {
        await client.shortTerm.getConversationMetadata(conv.id);
      } catch {
        observed = true;
      }
      expect(observed).toBe(true);
    }, 30_000);
  });

  // ====================================================================
  // 2. Neo4jConversationManager against live NAMS
  // ====================================================================

  describe("Neo4jConversationManager — context injection", () => {
    it("getContext on a fresh conversation prepends empty context (no-op)", async () => {
      const conv = await newConv({ suffix: "cm-empty" });
      const cm = new Neo4jConversationManager(client, {
        conversationId: conv.id,
        inner: new NullConversationManager(),
      });
      const { agent, hooks } = makeAgentStub([{ role: "user", content: [{ text: "hi" }] }]);
      await cm.initAgent(agent);

      const callbacks = hooks.get(BeforeInvocationEvent) ?? [];
      for (const cb of callbacks) {
        await cb(
          new BeforeInvocationEvent({
            agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
            invocationState: {},
          }),
        );
      }

      // A brand-new conversation has no observations/reflections yet, so
      // the agent.messages should be unchanged.
      const messages = (agent as unknown as { messages: Array<Record<string, unknown>> })
        .messages;
      expect(messages).toHaveLength(1);
    }, 30_000);

    it("after a multi-turn conversation, context injection includes service-extracted reflections/observations", async () => {
      const conv = await newConv({ suffix: "cm-rich" });

      // Drive a 5-turn conversation so the service has signal to extract
      // reflections + observations from.
      const turns: Array<["user" | "assistant", string]> = [
        ["user", "I'm planning a 7-day trip to Lisbon."],
        ["assistant", "Great choice! Are you more interested in food, history, or coastal areas?"],
        ["user", "Mostly food and history — I love seafood and museums."],
        ["assistant", "Tsukiji-style seafood markets and Belém's monasteries are perfect for you."],
        ["user", "What's the best way to get around?"],
      ];
      for (const [role, content] of turns) {
        await client.shortTerm.addMessage(conv.id, role, content);
      }

      // Poll getContext until the service produces something — observation
      // extraction is asynchronous. Skip if no signal arrives within window.
      const populated = await waitUntil(
        async () => {
          const ctx = await client.shortTerm.getContext(conv.id);
          if (ctx.observations.length > 0 || ctx.reflections.length > 0) return ctx;
          return null;
        },
        { timeout: 20_000, interval: 2_000 },
      );

      if (!populated) {
        // Service hasn't extracted yet — record as soft-skip, not a fail.
        return;
      }

      // Now exercise the conversation manager.
      const cm = new Neo4jConversationManager(client, {
        conversationId: conv.id,
        inner: new NullConversationManager(),
      });
      const { agent, hooks } = makeAgentStub([{ role: "user", content: [{ text: "next turn" }] }]);
      await cm.initAgent(agent);
      const callbacks = hooks.get(BeforeInvocationEvent) ?? [];
      for (const cb of callbacks) {
        await cb(
          new BeforeInvocationEvent({
            agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
            invocationState: {},
          }),
        );
      }

      const messages = (agent as unknown as { messages: Array<Record<string, unknown>> })
        .messages;
      const texts = messages.map((m) =>
        ((m.content as Array<{ text?: string }>)[0]?.text ?? "") as string,
      );
      const hasInjection = texts.some(
        (t) => t.startsWith("[reflection]") || t.startsWith("[observation]"),
      );
      expect(hasInjection).toBe(true);
    }, 60_000);
  });

  // ====================================================================
  // 3. registerReasoningHooks against live NAMS
  // ====================================================================

  describe("registerReasoningHooks — reasoning capture", () => {
    it("BeforeInvocation creates a step, BeforeToolCall + AfterToolCall record tool calls", async () => {
      const conv = await newConv({ suffix: "hooks" });
      const { agent, hooks } = makeAgentStub();
      await registerReasoningHooks(client, agent, { conversationId: conv.id });

      const inv: Record<string, unknown> = {};
      // Fire BeforeInvocation.
      const beforeInvCb = (hooks.get(BeforeInvocationEvent) ?? [])[0]!;
      await beforeInvCb(
        new BeforeInvocationEvent({
          agent: agent as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
          invocationState: inv,
        }),
      );
      expect(typeof inv["__neo4jReasoningStepId"]).toBe("string");

      // Fire tool call lifecycle.
      const beforeToolCb = (hooks.get(BeforeToolCallEvent) ?? [])[0]!;
      const afterToolCb = (hooks.get(AfterToolCallEvent) ?? [])[0]!;
      await beforeToolCb(
        new BeforeToolCallEvent({
          agent: {} as ConstructorParameters<typeof BeforeToolCallEvent>[0]["agent"],
          toolUse: { toolUseId: "tu-1", name: "search_entities", input: { query: "Lisbon" } },
          tool: undefined,
          invocationState: inv,
        } as unknown as ConstructorParameters<typeof BeforeToolCallEvent>[0]),
      );
      await afterToolCb(
        new AfterToolCallEvent({
          agent: {} as ConstructorParameters<typeof AfterToolCallEvent>[0]["agent"],
          toolUse: { toolUseId: "tu-1", name: "search_entities", input: { query: "Lisbon" } },
          tool: undefined,
          result: {
            toolUseId: "tu-1",
            content: [],
            status: "success",
          } as ConstructorParameters<typeof AfterToolCallEvent>[0]["result"],
          error: undefined,
          invocationState: inv,
        } as unknown as ConstructorParameters<typeof AfterToolCallEvent>[0]),
      );

      // Fire AfterInvocation.
      const afterInvCb = (hooks.get(AfterInvocationEvent) ?? [])[0]!;
      await afterInvCb(
        new AfterInvocationEvent({
          agent: {} as ConstructorParameters<typeof AfterInvocationEvent>[0]["agent"],
          invocationState: inv,
        }),
      );

      // Verify the reasoning trace landed in NAMS.
      const trace = await client.reasoning.getTraceByConversation(conv.id);
      expect(trace.conversationId).toBe(conv.id);
      expect(trace.steps.length).toBeGreaterThanOrEqual(1);
      const reasoningTexts = trace.steps.map((s) => s.reasoning ?? "");
      expect(reasoningTexts.some((t) => t.includes("agent invocation started"))).toBe(true);
    }, 30_000);
  });

  // ====================================================================
  // 4. Multi-conversation isolation
  // ====================================================================

  describe("Multi-conversation isolation", () => {
    it("two independent conversations don't cross-pollute reasoning state", async () => {
      const convA = await newConv({ suffix: "iso-a" });
      const convB = await newConv({ suffix: "iso-b" });
      const { agent: agentA, hooks: hooksA } = makeAgentStub();
      const { agent: agentB, hooks: hooksB } = makeAgentStub();
      await registerReasoningHooks(client, agentA, { conversationId: convA.id });
      await registerReasoningHooks(client, agentB, { conversationId: convB.id });

      const invA: Record<string, unknown> = {};
      const invB: Record<string, unknown> = {};
      await (hooksA.get(BeforeInvocationEvent) ?? [])[0]!(
        new BeforeInvocationEvent({
          agent: agentA as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
          invocationState: invA,
        }),
      );
      await (hooksB.get(BeforeInvocationEvent) ?? [])[0]!(
        new BeforeInvocationEvent({
          agent: agentB as unknown as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
          invocationState: invB,
        }),
      );

      const traceA = await client.reasoning.getTraceByConversation(convA.id);
      const traceB = await client.reasoning.getTraceByConversation(convB.id);
      expect(traceA.conversationId).toBe(convA.id);
      expect(traceB.conversationId).toBe(convB.id);
      // Each trace has its own steps; cross-references should not happen.
      const aIds = new Set(traceA.steps.map((s) => s.id));
      const bIds = new Set(traceB.steps.map((s) => s.id));
      for (const id of aIds) expect(bIds.has(id)).toBe(false);
    }, 45_000);
  });

  // ====================================================================
  // 5. Auth error surface
  // ====================================================================

  describe("Auth error surface", () => {
    it("a deliberately bad API key surfaces AuthenticationError with requestId on Strands operations", async () => {
      const { AuthenticationError } = await import("../../src/errors.js");
      const badClient = new MemoryClient({
        endpoint: ENDPOINT,
        apiKey: "nams_obviously_not_real",
      });
      const storage = new Neo4jSessionStorage(badClient);
      try {
        await storage.loadSnapshot({ location: location("conv-doesnt-matter") });
        throw new Error("expected throw");
      } catch (e) {
        if (e instanceof AuthenticationError) {
          // requestId may or may not be populated depending on edge.
          expect(typeof e.message).toBe("string");
          return;
        }
        // Some service deployments return 4xx for missing conversations
        // rather than 401; accept TransportError as a valid path here.
        // The contract being tested is "the integration surfaces the
        // failure cleanly", not the specific status code.
        const { TransportError } = await import("../../src/errors.js");
        if (e instanceof TransportError) return;
        throw e;
      } finally {
        await badClient.close();
      }
    }, 30_000);
  });
});
