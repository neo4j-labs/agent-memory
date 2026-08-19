/**
 * Integration tests — registerReasoningHooks against MSW-mocked
 * /v1/reasoning/steps and /v1/reasoning/tool-calls. Exercises the full
 * Strands hook lifecycle through the real RestTransport.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  AfterInvocationEvent,
  AfterToolCallEvent,
  BeforeInvocationEvent,
  BeforeToolCallEvent,
  type LocalAgent,
} from "@strands-agents/sdk";
import { MemoryClient } from "../../src/client.js";
import { registerReasoningHooks } from "../../src/integrations/strands/index.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";
const CONV = "conv-hooks";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/**
 * The RestTransport snakeToCamel-transforms outgoing bodies, so what MSW
 * actually sees in `await request.json()` uses camelCase keys.
 */
interface CapturedStep {
  conversationId?: string;
  reasoning?: string;
  actionTaken?: string;
  result?: string;
}

interface CapturedToolCall {
  stepId?: string;
  toolName?: string;
  arguments?: Record<string, unknown>;
  status?: string;
  error?: string;
}

function mountReasoning(state: {
  steps: CapturedStep[];
  toolCalls: CapturedToolCall[];
  failNext?: boolean;
}) {
  server.use(
    http.post(`${ENDPOINT}/reasoning/steps`, async ({ request }) => {
      if (state.failNext) {
        state.failNext = false;
        return new HttpResponse("nope", { status: 500 });
      }
      const body = (await request.json()) as Record<string, unknown>;
      state.steps.push(body as CapturedStep);
      return HttpResponse.json({
        id: `step-${state.steps.length}`,
        conversation_id: (body as CapturedStep).conversationId,
        reasoning: (body as CapturedStep).reasoning,
        action_taken: (body as CapturedStep).actionTaken,
        result: (body as CapturedStep).result,
        created_at: "x",
      });
    }),
    http.post(`${ENDPOINT}/reasoning/tool-calls`, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.toolCalls.push(body as CapturedToolCall);
      return HttpResponse.json({
        id: `tc-${state.toolCalls.length}`,
        step_id: (body as CapturedToolCall).stepId,
        tool_name: (body as CapturedToolCall).toolName,
        arguments: (body as CapturedToolCall).arguments,
        status: (body as CapturedToolCall).status ?? "pending",
        created_at: "x",
      });
    }),
  );
}

interface HookSlots {
  beforeInv?: (e: BeforeInvocationEvent) => Promise<void>;
  afterInv?: (e: AfterInvocationEvent) => Promise<void>;
  beforeTool?: (e: BeforeToolCallEvent) => Promise<void>;
  afterTool?: (e: AfterToolCallEvent) => Promise<void>;
}

function makeAgent(): { agent: LocalAgent; slots: HookSlots } {
  const slots: HookSlots = {};
  const agent = {
    id: "a",
    addHook(eventClass: unknown, cb: (e: unknown) => Promise<void>) {
      if (eventClass === BeforeInvocationEvent) slots.beforeInv = cb as HookSlots["beforeInv"];
      else if (eventClass === AfterInvocationEvent) slots.afterInv = cb as HookSlots["afterInv"];
      else if (eventClass === BeforeToolCallEvent) slots.beforeTool = cb as HookSlots["beforeTool"];
      else if (eventClass === AfterToolCallEvent) slots.afterTool = cb as HookSlots["afterTool"];
      return () => {};
    },
  } as unknown as LocalAgent;
  return { agent, slots };
}

function beforeInvEvent(state: Record<string, unknown>): BeforeInvocationEvent {
  return new BeforeInvocationEvent({
    agent: {} as ConstructorParameters<typeof BeforeInvocationEvent>[0]["agent"],
    invocationState: state,
  });
}

function beforeToolEvent(
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

function afterToolEvent(
  state: Record<string, unknown>,
  toolUseId: string,
  name: string,
  input: Record<string, unknown>,
  error?: Error,
): AfterToolCallEvent {
  return new AfterToolCallEvent({
    agent: {} as ConstructorParameters<typeof AfterToolCallEvent>[0]["agent"],
    toolUse: { toolUseId, name, input },
    tool: undefined,
    result: { toolUseId, content: [], status: "success" } as ConstructorParameters<typeof AfterToolCallEvent>[0]["result"],
    error,
    invocationState: state,
  } as unknown as ConstructorParameters<typeof AfterToolCallEvent>[0]);
}

function afterInvEvent(state: Record<string, unknown>): AfterInvocationEvent {
  return new AfterInvocationEvent({
    agent: {} as ConstructorParameters<typeof AfterInvocationEvent>[0]["agent"],
    invocationState: state,
  });
}

describe("registerReasoningHooks — full lifecycle", () => {
  it("BeforeInvocation creates a step and stashes the id on invocationState", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(inv));

    expect(state.steps).toHaveLength(1);
    expect(state.steps[0]).toMatchObject({
      conversationId: CONV,
      actionTaken: "invoke_agent",
    });
    expect(inv["__neo4jReasoningStepId"]).toBe("step-1");
  });

  it("BeforeToolCall records a tool call against the current step", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(inv));
    await slots.beforeTool!(beforeToolEvent(inv, "tu-1", "search_entities", { query: "neo4j" }));

    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0]).toMatchObject({
      stepId: "step-1",
      toolName: "search_entities",
      status: "pending",
    });
  });

  it("AfterToolCall records a follow-up entry with the resolved status", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(inv));
    await slots.beforeTool!(beforeToolEvent(inv, "tu", "fetch", {}));
    await slots.afterTool!(afterToolEvent(inv, "tu", "fetch", {}));

    expect(state.toolCalls).toHaveLength(2);
    expect(state.toolCalls[0]!.status).toBe("pending");
    expect(state.toolCalls[1]!.status).toBe("success");
  });

  it("AfterToolCall with error records failure status", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(inv));
    await slots.beforeTool!(beforeToolEvent(inv, "tu", "fail-tool", {}));
    await slots.afterTool!(afterToolEvent(inv, "tu", "fail-tool", {}, new Error("kaboom")));

    const last = state.toolCalls[state.toolCalls.length - 1]!;
    expect(last.status).toBe("failure");
    expect(last.error).toBe("kaboom");
  });

  it("AfterInvocation records a closing step", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(inv));
    await slots.afterInv!(afterInvEvent(inv));

    expect(state.steps).toHaveLength(2);
    expect(state.steps[1]!.actionTaken).toBe("invocation_complete");
    expect(state.steps[1]!.result).toBe("ok");
  });

  it("concurrent invocations record under separate step ids", async () => {
    const state = { steps: [] as CapturedStep[], toolCalls: [] as CapturedToolCall[] };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const a: Record<string, unknown> = {};
    const b: Record<string, unknown> = {};
    await slots.beforeInv!(beforeInvEvent(a));
    await slots.beforeInv!(beforeInvEvent(b));
    await slots.beforeTool!(beforeToolEvent(a, "tu-a", "fA", {}));
    await slots.beforeTool!(beforeToolEvent(b, "tu-b", "fB", {}));

    expect(state.toolCalls).toHaveLength(2);
    expect(state.toolCalls[0]!.stepId).toBe("step-1");
    expect(state.toolCalls[1]!.stepId).toBe("step-2");
  });

  it("recordStep failure is swallowed (agent run continues)", async () => {
    const state = {
      steps: [] as CapturedStep[],
      toolCalls: [] as CapturedToolCall[],
      failNext: true,
    };
    mountReasoning(state);
    const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
    const { agent, slots } = makeAgent();
    await registerReasoningHooks(memory, agent, { conversationId: CONV });

    const inv: Record<string, unknown> = {};
    // First recordStep fails; should not throw.
    await expect(slots.beforeInv!(beforeInvEvent(inv))).resolves.toBeUndefined();
    expect(inv["__neo4jReasoningStepId"]).toBeUndefined();
  });
});
