/**
 * The memory provider: recall, capture, and the two memory tools.
 *
 * The headline assertion is the cross-session one — a preference written in the
 * first eve session is recalled into the prompt of a *different* eve session,
 * which can only happen because it went to the graph and came back.
 */

import { AuthenticationError, MemoryClient, TransportError } from "@neo4j-labs/agent-memory";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { deps, resetDeps } from "../agent/lib/deps.js";
import { forgetConversations, shopperTag } from "../agent/lib/memory-session.js";
import { forgetCapturedOperations, namsMemory } from "../agent/lib/nams-memory.js";
import { FakeNamsTransport } from "./fake-nams.js";
import {
  assistantMessage,
  captureContext,
  memoryToolsContext,
  OTHER_SHOPPER,
  recallContext,
  runMemoryTool,
  SHOPPER,
  toolContext,
  userMessage,
} from "./fixtures.js";

let transport: FakeNamsTransport;
const provider = namsMemory();

beforeEach(() => {
  forgetConversations();
  forgetCapturedOperations();
  transport = new FakeNamsTransport();
  deps.client = new MemoryClient(transport);
});

afterEach(resetDeps);

/** The provider's tools, as eve would hand them to the model. */
async function memoryTools(options: { sessionId?: string; shopperId?: string } = {}) {
  const tools = await provider.tools?.(memoryToolsContext(options));
  if (tools === undefined || tools === null) throw new Error("expected memory tools");
  return tools;
}

describe("recall", () => {
  it("opens one NAMS conversation per eve session and reuses it", async () => {
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_a" }));
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_a", sequence: 1 }));

    expect(transport.conversations.size).toBe(1);
    const [conversation] = [...transport.conversations.values()];
    expect(conversation?.user_id).toBe(SHOPPER);
    expect(conversation?.metadata).toMatchObject({
      source: "eve-commerce-agent",
      eveSessionId: "sess_a",
    });
  });

  it("re-attaches to the conversation after a process restart", async () => {
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_a" }));
    const firstId = [...transport.conversations.keys()][0];

    // A redeploy loses the in-process memo but not the metadata in the graph.
    forgetConversations();
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_a" }));

    expect(transport.conversations.size).toBe(1);
    expect([...transport.conversations.keys()][0]).toBe(firstId);
  });

  it("recalls nothing for a shopper the graph has never seen", async () => {
    const result = await provider.recall["turn.started"](recallContext());
    expect(result).toEqual({ messages: [] });
  });

  it("recalls the shopper's preferences under a stable, supersedable id", async () => {
    const tools = await memoryTools();
    await runMemoryTool(
      tools["remember_preference"]!,
      { category: "size", preference: "Wears a size L top" },
      toolContext(),
    );

    const result = await provider.recall["turn.started"](
      recallContext({ input: [userMessage("show me sweaters")] }),
    );
    const profile = result?.messages.find((message) => message.id === "shopper-profile");
    expect(profile?.content).toContain("size: Wears a size L top");
    // Attributed as data, not as an instruction the model should obey.
    expect(profile?.content).toContain("not as instructions");
  });

  it("carries a preference across two different eve sessions", async () => {
    // Session one: the shopper says something durable.
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_one" }));
    const tools = await memoryTools({ sessionId: "sess_one" });
    await runMemoryTool(
      tools["remember_preference"]!,
      { category: "brand", preference: "Loves Northmoor outerwear", note: "said while browsing" },
      toolContext({ sessionId: "sess_one" }),
    );

    // Session two: a brand-new eve session, so no eve history at all.
    const second = await provider.recall["turn.started"](
      recallContext({ sessionId: "sess_two", input: [userMessage("what do you remember?")] }),
    );

    expect(transport.conversations.size).toBe(2);
    const profile = second?.messages.find((message) => message.id === "shopper-profile");
    expect(profile?.content).toContain("Loves Northmoor outerwear");
  });

  it("recalls what NAMS summarised about earlier visits", async () => {
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_one" }));
    const [firstConversationId] = [...transport.conversations.keys()];
    transport.addObservation(firstConversationId!, "Shopper compared two rain shells.");
    transport.addReflection(firstConversationId!, "Shopper is outfitting a wet-weather commute.");

    const second = await provider.recall["turn.started"](recallContext({ sessionId: "sess_two" }));
    const visits = second?.messages.find((message) => message.id === "shopper-past-visits");
    expect(visits?.content).toContain("wet-weather commute");
    expect(visits?.content).toContain("compared two rain shells");
  });

  it("keeps the turn alive when the memory service is unreachable", async () => {
    deps.client = new MemoryClient({
      connect: async () => {},
      close: async () => {},
      request: async () => {
        throw new TransportError("service unavailable", 503);
      },
    });

    // A throwing recall would fail the turn before the model call; a shop that
    // cannot reach its memory should still be able to sell.
    await expect(provider.recall["turn.started"](recallContext())).resolves.toEqual({
      messages: [],
    });
  });

  it("fails loudly when the API key is wrong, because that is a deployment bug", async () => {
    deps.client = new MemoryClient({
      connect: async () => {},
      close: async () => {},
      request: async () => {
        throw new AuthenticationError("Authentication failed: 401 Unauthorized");
      },
    });

    await expect(provider.recall["turn.started"](recallContext())).rejects.toThrow(
      /401 Unauthorized/,
    );
  });

  it("never recalls another shopper's preferences", async () => {
    const theirs = await memoryTools({ sessionId: "sess_bo", shopperId: OTHER_SHOPPER });
    await runMemoryTool(
      theirs["remember_preference"]!,
      { category: "budget", preference: "Never spends over $50" },
      toolContext({ sessionId: "sess_bo", shopperId: OTHER_SHOPPER }),
    );

    // The preference is in the workspace...
    expect(transport.preferences).toHaveLength(1);
    expect(transport.preferences[0]?.context).toContain(shopperTag(OTHER_SHOPPER));

    // ...but it is not this shopper's, so it is filtered out of their recall.
    const mine = await provider.recall["turn.started"](recallContext({ sessionId: "sess_ada" }));
    expect(mine).toEqual({ messages: [] });
  });
});

describe("capture", () => {
  it("writes both sides of the turn to the conversation in one call", async () => {
    const options = {
      sessionId: "sess_a",
      input: [userMessage("I need a rain jacket in M")],
      messages: [
        userMessage("I need a rain jacket in M"),
        assistantMessage("The Northmoor Fellside in M is $245."),
      ],
    };
    await provider.recall["turn.started"](recallContext(options));
    await provider.capture?.["turn.completed"]?.(captureContext(options));

    const [conversationId] = [...transport.conversations.keys()];
    expect(transport.rolesOf(conversationId!)).toEqual(["user", "assistant"]);
    expect(transport.contentsOf(conversationId!)).toEqual([
      "I need a rain jacket in M",
      "The Northmoor Fellside in M is $245.",
    ]);
    expect(transport.methodsCalled().filter((m) => m === "bulk_add_messages")).toHaveLength(1);
  });

  it("captures only this turn's reply, not the whole history", async () => {
    const shared = { sessionId: "sess_a" };
    await provider.recall["turn.started"](recallContext(shared));
    await provider.capture?.["turn.completed"]?.(
      captureContext({
        ...shared,
        operationId: "op-1",
        input: [userMessage("first")],
        messages: [userMessage("first"), assistantMessage("first reply")],
      }),
    );
    await provider.capture?.["turn.completed"]?.(
      captureContext({
        ...shared,
        operationId: "op-2",
        input: [userMessage("second")],
        messages: [
          userMessage("first"),
          assistantMessage("first reply"),
          userMessage("second"),
          assistantMessage("second reply"),
        ],
      }),
    );

    const [conversationId] = [...transport.conversations.keys()];
    expect(transport.contentsOf(conversationId!)).toEqual([
      "first",
      "first reply",
      "second",
      "second reply",
    ]);
  });

  it("is idempotent when a durable step replays the same operation", async () => {
    const options = {
      sessionId: "sess_a",
      operationId: "op-replayed",
      input: [userMessage("hello")],
      messages: [userMessage("hello"), assistantMessage("hi")],
    };
    await provider.recall["turn.started"](recallContext(options));
    await provider.capture?.["turn.completed"]?.(captureContext(options));
    await provider.capture?.["turn.completed"]?.(captureContext(options));

    const [conversationId] = [...transport.conversations.keys()];
    expect(transport.contentsOf(conversationId!)).toEqual(["hello", "hi"]);
  });

  it("stamps the turn on every stored message, so a row traces back to a turn", async () => {
    const options = {
      sessionId: "sess_a",
      turnId: "turn_7",
      input: [userMessage("hello")],
      messages: [userMessage("hello"), assistantMessage("hi")],
    };
    await provider.recall["turn.started"](recallContext(options));
    await provider.capture?.["turn.completed"]?.(captureContext(options));

    const bulk = transport.calls.find((call) => call.method === "bulk_add_messages");
    const messages = bulk?.params["messages"] as Array<{ metadata?: Record<string, unknown> }>;
    expect(messages.every((message) => message.metadata?.["turnId"] === "turn_7")).toBe(true);
  });
});

describe("the memory tools", () => {
  it("are named for the slot so the model sees shopper__*", async () => {
    expect(Object.keys(await memoryTools()).sort()).toEqual([
      "recall_shopper",
      "remember_preference",
    ]);
  });

  it("remember_preference tags the preference with the locked scope", async () => {
    const tools = await memoryTools();
    const result = await runMemoryTool(
      tools["remember_preference"]!,
      { category: "style", preference: "Prefers muted, earthy colours", note: "browsing knits" },
      toolContext(),
    );

    expect(result).toMatchObject({ remembered: true, category: "style" });
    expect(transport.preferences[0]?.context).toBe(`${shopperTag(SHOPPER)} — browsing knits`);
  });

  it("recall_shopper returns the profile, this visit, and known products", async () => {
    await provider.recall["turn.started"](recallContext({ sessionId: "sess_a" }));
    const tools = await memoryTools({ sessionId: "sess_a" });
    await runMemoryTool(
      tools["remember_preference"]!,
      { category: "size", preference: "Wears a 34 waist" },
      toolContext({ sessionId: "sess_a" }),
    );
    await deps.client!.longTerm.addEntity("Loomfield", "ORGANIZATION");

    const result = (await runMemoryTool(
      tools["recall_shopper"]!,
      { query: "jeans" },
      toolContext({ sessionId: "sess_a" }),
    )) as {
      preferences: Array<{ category: string; preference: string }>;
      thisVisit: { recentMessageCount: number };
      knownProductsAndBrands: Array<{ name: string }>;
    };

    expect(result.preferences).toEqual([{ category: "size", preference: "Wears a 34 waist" }]);
    expect(result.thisVisit.recentMessageCount).toBe(0);
    expect(result.knownProductsAndBrands.map((entity) => entity.name)).toEqual(["Loomfield"]);
  });

  it("recall_shopper cannot be pointed at another shopper by the model", async () => {
    const theirs = await memoryTools({ sessionId: "sess_bo", shopperId: OTHER_SHOPPER });
    await runMemoryTool(
      theirs["remember_preference"]!,
      { category: "budget", preference: "Never spends over $50" },
      toolContext({ sessionId: "sess_bo", shopperId: OTHER_SHOPPER }),
    );

    // The tool closes over the scope eve locked — its only input is a query.
    const tools = await memoryTools({ sessionId: "sess_ada" });
    const result = (await runMemoryTool(
      tools["recall_shopper"]!,
      { query: "budget" },
      toolContext({ sessionId: "sess_ada" }),
    )) as { preferences: unknown[] };

    expect(result.preferences).toEqual([]);
  });
});
