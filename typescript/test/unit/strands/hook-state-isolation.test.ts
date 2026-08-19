/**
 * Pure-logic tests for the reasoning hooks: when two invocations are
 * interleaved, the per-invocation state (stepId, tool-call map) stays
 * isolated. The test drives the hook callbacks directly against synthetic
 * `BeforeInvocationEvent` / `BeforeToolCallEvent` / `AfterToolCallEvent`
 * objects, with distinct `invocationState` bags per simulated invocation.
 *
 * No HTTP. The MemoryClient is replaced with a stub that records every
 * call but never errors.
 */

import { describe, it, expect, vi } from "vitest";
import {
  BeforeInvocationEvent,
  BeforeToolCallEvent,
  AfterToolCallEvent,
  AfterInvocationEvent,
  type LocalAgent,
} from "@strands-agents/sdk";
import { registerReasoningHooks } from "../../../src/integrations/strands/index.js";

type Recorded = { kind: string; args: unknown[] };

function makeMemoryStub() {
  const recorded: Recorded[] = [];
  let stepCounter = 0;
  let toolCounter = 0;
  const memory = {
    reasoning: {
      async recordStep(args: unknown) {
        stepCounter++;
        recorded.push({ kind: "recordStep", args: [args] });
        return { id: `step-${stepCounter}` } as unknown as { id: string };
      },
      async recordToolCall(...args: unknown[]) {
        toolCounter++;
        recorded.push({ kind: "recordToolCall", args });
        return { id: `tool-${toolCounter}` } as unknown as { id: string };
      },
    },
  } as unknown as Parameters<typeof registerReasoningHooks>[0];
  return { memory, recorded };
}

interface HookSlot {
  beforeInv?: (e: BeforeInvocationEvent) => Promise<void>;
  afterInv?: (e: AfterInvocationEvent) => Promise<void>;
  beforeTool?: (e: BeforeToolCallEvent) => Promise<void>;
  afterTool?: (e: AfterToolCallEvent) => Promise<void>;
}

function makeAgent(): { agent: LocalAgent; slot: HookSlot } {
  const slot: HookSlot = {};
  const agent = {
    addHook(eventClass: unknown, cb: (e: unknown) => Promise<void>) {
      if (eventClass === BeforeInvocationEvent)
        slot.beforeInv = cb as HookSlot["beforeInv"];
      else if (eventClass === AfterInvocationEvent)
        slot.afterInv = cb as HookSlot["afterInv"];
      else if (eventClass === BeforeToolCallEvent)
        slot.beforeTool = cb as HookSlot["beforeTool"];
      else if (eventClass === AfterToolCallEvent)
        slot.afterTool = cb as HookSlot["afterTool"];
      return () => {};
    },
  } as unknown as LocalAgent;
  return { agent, slot };
}

function makeBeforeInvEvent(state: Record<string, unknown>): BeforeInvocationEvent {
  return new BeforeInvocationEvent({
    agent: {} as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
    invocationState: state,
  });
}

function makeBeforeToolEvent(
  state: Record<string, unknown>,
  toolUseId: string,
  name: string,
  input: Record<string, unknown>,
): BeforeToolCallEvent {
  return new BeforeToolCallEvent({
    agent: {} as ConstructorParameters<typeof BeforeToolCallEvent>[0]["agent"],
    toolUse: { toolUseId, name, input },
    tool: undefined,
    invocationState: state,
  } as unknown as ConstructorParameters<typeof BeforeToolCallEvent>[0]);
}

function makeAfterToolEvent(
  state: Record<string, unknown>,
  toolUseId: string,
  name: string,
  input: Record<string, unknown>,
  err?: Error,
): AfterToolCallEvent {
  return new AfterToolCallEvent({
    agent: {} as ConstructorParameters<typeof AfterToolCallEvent>[0]["agent"],
    toolUse: { toolUseId, name, input },
    tool: undefined,
    result: { toolUseId, content: [], status: "success" } as ConstructorParameters<typeof AfterToolCallEvent>[0]["result"],
    error: err,
    invocationState: state,
  } as unknown as ConstructorParameters<typeof AfterToolCallEvent>[0]);
}

describe("registerReasoningHooks — state isolation", () => {
  it("two interleaved invocations get distinct step ids", async () => {
    const { memory, recorded } = makeMemoryStub();
    const { agent, slot } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: "c1" });

    const state1: Record<string, unknown> = {};
    const state2: Record<string, unknown> = {};

    await slot.beforeInv!(makeBeforeInvEvent(state1));
    await slot.beforeInv!(makeBeforeInvEvent(state2));

    expect(state1["__neo4jReasoningStepId"]).toBe("step-1");
    expect(state2["__neo4jReasoningStepId"]).toBe("step-2");
    expect(recorded.filter((r) => r.kind === "recordStep")).toHaveLength(2);
  });

  it("tool calls map to the step of their own invocation", async () => {
    const { memory, recorded } = makeMemoryStub();
    const { agent, slot } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: "c1" });

    const stateA: Record<string, unknown> = {};
    const stateB: Record<string, unknown> = {};

    await slot.beforeInv!(makeBeforeInvEvent(stateA));
    await slot.beforeInv!(makeBeforeInvEvent(stateB));

    await slot.beforeTool!(makeBeforeToolEvent(stateA, "tu-a", "search", { q: "neo4j" }));
    await slot.beforeTool!(makeBeforeToolEvent(stateB, "tu-b", "fetch", { url: "x" }));

    const toolRecords = recorded.filter((r) => r.kind === "recordToolCall");
    expect(toolRecords).toHaveLength(2);
    // First arg is the stepId.
    expect(toolRecords[0]!.args[0]).toBe("step-1");
    expect(toolRecords[1]!.args[0]).toBe("step-2");
  });

  it("AfterToolCall with an error records a failure status", async () => {
    const { memory, recorded } = makeMemoryStub();
    const { agent, slot } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: "c1" });

    const state: Record<string, unknown> = {};
    await slot.beforeInv!(makeBeforeInvEvent(state));
    await slot.beforeTool!(makeBeforeToolEvent(state, "tu", "fail-tool", {}));
    await slot.afterTool!(
      makeAfterToolEvent(state, "tu", "fail-tool", {}, new Error("boom")),
    );

    const last = recorded.filter((r) => r.kind === "recordToolCall").pop();
    expect(last).toBeDefined();
    const opts = last!.args[3] as { status: string; error: string };
    expect(opts.status).toBe("failure");
    expect(opts.error).toBe("boom");
  });

  it("reasoning recordStep failure does not propagate", async () => {
    const failingMemory = {
      reasoning: {
        async recordStep() {
          throw new Error("nams down");
        },
        async recordToolCall() {
          throw new Error("nams down");
        },
      },
    } as unknown as Parameters<typeof registerReasoningHooks>[0];

    const { agent, slot } = makeAgent();
    await registerReasoningHooks(failingMemory, agent, { conversationId: "c1" });

    const state: Record<string, unknown> = {};
    await expect(slot.beforeInv!(makeBeforeInvEvent(state))).resolves.toBeUndefined();
    await expect(slot.beforeTool!(makeBeforeToolEvent(state, "tu", "x", {}))).resolves.toBeUndefined();
    await expect(slot.afterTool!(makeAfterToolEvent(state, "tu", "x", {}))).resolves.toBeUndefined();
    await expect(
      slot.afterInv!(
        new AfterInvocationEvent({
          agent: {} as ConstructorParameters<typeof AfterInvocationEvent>[0]["agent"],
          invocationState: state,
        }),
      ),
    ).resolves.toBeUndefined();
  });
});

void vi;
