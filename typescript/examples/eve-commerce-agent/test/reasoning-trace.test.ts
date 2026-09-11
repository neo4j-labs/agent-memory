/**
 * Reasoning memory: the hook that turns every tool call into an auditable step,
 * and the products and brands it registers as entities.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { deps, resetDeps } from "../agent/lib/deps.js";
import { forgetConversations, resolveConversation } from "../agent/lib/memory-session.js";
import { forgetRegisteredEntities, recordToolCall, touchedEntities } from "../agent/lib/trace.js";
import reasoningTrace from "../agent/hooks/reasoning-trace.js";
import searchCatalog from "../agent/tools/search_catalog.js";
import { FakeNamsTransport } from "./fake-nams.js";
import { hookContext, runTool, SHOPPER, toolContext } from "./fixtures.js";

let transport: FakeNamsTransport;
let conversationId: string;

beforeEach(async () => {
  forgetConversations();
  forgetRegisteredEntities();
  transport = new FakeNamsTransport();
  deps.client = new MemoryClient(transport);
  // The memory slot's recall opens this at turn.started in production.
  conversationId = await resolveConversation(deps.client, "sess_a", SHOPPER);
});

afterEach(resetDeps);

describe("touchedEntities", () => {
  it("reads products and brands out of a tool result", async () => {
    const output = await runTool(
      searchCatalog.execute({ brand: "Pavia", limit: 2 }, toolContext()),
    );
    const touched = touchedEntities(output);

    expect(touched.filter((entity) => entity.type === "ORGANIZATION")).toEqual([
      { name: "Pavia", type: "ORGANIZATION" },
    ]);
    expect(touched.filter((entity) => entity.type === "OBJECT").length).toBeGreaterThan(0);
  });

  it("ignores a result with no products in it", () => {
    expect(touchedEntities({ lines: [], itemCount: 0, total: 0 })).toEqual([]);
    expect(touchedEntities("a string")).toEqual([]);
    expect(touchedEntities(null)).toEqual([]);
  });
});

describe("recordToolCall", () => {
  it("writes one reasoning step naming the tool and what it touched", async () => {
    const output = await runTool(
      searchCatalog.execute({ query: "cashmere scarf", limit: 1 }, toolContext()),
    );
    const recorded = await recordToolCall("sess_a", {
      toolName: "search_catalog",
      input: { query: "cashmere scarf" },
      output,
    });

    expect(recorded?.conversationId).toBe(conversationId);
    expect(transport.steps).toHaveLength(1);
    expect(transport.steps[0]).toMatchObject({
      conversation_id: conversationId,
      action_taken: "search_catalog",
    });
    expect(transport.steps[0]?.reasoning).toContain("cashmere scarf");
    expect(transport.steps[0]?.result).toContain("touched: Pavia Cashmere Scarf, Pavia");
  });

  it("registers each touched product and brand as an entity, once", async () => {
    const output = await runTool(
      searchCatalog.execute({ query: "cashmere scarf", limit: 1 }, toolContext()),
    );
    await recordToolCall("sess_a", { toolName: "search_catalog", input: {}, output });
    await recordToolCall("sess_a", { toolName: "search_catalog", input: {}, output });

    expect(transport.entities.map((entity) => [entity.name, entity.type])).toEqual([
      ["Pavia Cashmere Scarf", "OBJECT"],
      ["Pavia", "ORGANIZATION"],
    ]);
    expect(transport.steps).toHaveLength(2);
  });

  it("stays quiet when no conversation is open for the session", async () => {
    const recorded = await recordToolCall("sess_unknown", {
      toolName: "view_cart",
      input: {},
      output: { itemCount: 0 },
    });
    expect(recorded).toBeUndefined();
    expect(transport.steps).toEqual([]);
  });
});

describe("the reasoning-trace hook", () => {
  const actionsRequested = reasoningTrace.events?.["actions.requested"];
  const actionResult = reasoningTrace.events?.["action.result"];

  it("correlates the input from actions.requested with the output from action.result", async () => {
    if (actionsRequested === undefined || actionResult === undefined) {
      throw new Error("expected both handlers");
    }
    const ctx = hookContext({ sessionId: "sess_a" });

    await actionsRequested(
      {
        type: "actions.requested",
        data: {
          actions: [
            {
              callId: "call_1",
              input: { productId: "kt-4002" },
              kind: "tool-call",
              toolName: "get_product",
            },
          ],
          sequence: 0,
          stepIndex: 0,
          turnId: "turn_0",
        },
        meta: { id: "evt_1", at: new Date().toISOString() },
      },
      ctx,
    );

    await actionResult(
      {
        type: "action.result",
        data: {
          result: {
            callId: "call_1",
            kind: "tool-result",
            output: {
              found: true,
              name: "Kestrel Daybreak 14L Sling",
              brand: "Kestrel",
              description: "14L crossbody sling in recycled ripstop.",
            },
            toolName: "get_product",
          },
          sequence: 1,
          stepIndex: 0,
          status: "completed",
          turnId: "turn_0",
        },
        meta: { id: "evt_2", at: new Date().toISOString() },
      },
      ctx,
    );

    expect(transport.steps).toHaveLength(1);
    expect(transport.steps[0]?.reasoning).toContain("kt-4002");
    expect(transport.entities.map((entity) => entity.name)).toEqual([
      "Kestrel Daybreak 14L Sling",
      "Kestrel",
    ]);
  });

  it("ignores failed tool calls and tools it does not track", async () => {
    if (actionResult === undefined) throw new Error("expected a handler");
    const ctx = hookContext({ sessionId: "sess_a" });

    await actionResult(
      {
        type: "action.result",
        data: {
          result: {
            callId: "c",
            kind: "tool-result",
            isError: true,
            output: {},
            toolName: "checkout",
          },
          sequence: 0,
          stepIndex: 0,
          status: "failed",
          turnId: "turn_0",
        },
        meta: { id: "evt_3", at: new Date().toISOString() },
      },
      ctx,
    );
    await actionResult(
      {
        type: "action.result",
        data: {
          result: { callId: "d", kind: "tool-result", output: {}, toolName: "web_search" },
          sequence: 1,
          stepIndex: 0,
          status: "completed",
          turnId: "turn_0",
        },
        meta: { id: "evt_4", at: new Date().toISOString() },
      },
      ctx,
    );

    expect(transport.steps).toEqual([]);
  });

  it("never fails the turn when the memory service is unreachable", async () => {
    if (actionResult === undefined) throw new Error("expected a handler");
    deps.client = new MemoryClient({
      connect: async () => {},
      close: async () => {},
      request: async () => {
        throw new Error("NAMS is down");
      },
    });

    await expect(
      actionResult(
        {
          type: "action.result",
          data: {
            result: { callId: "e", kind: "tool-result", output: {}, toolName: "view_cart" },
            sequence: 0,
            stepIndex: 0,
            status: "completed",
            turnId: "turn_0",
          },
          meta: { id: "evt_5", at: new Date().toISOString() },
        },
        hookContext({ sessionId: "sess_a" }),
      ),
    ).resolves.toBeUndefined();
  });
});
