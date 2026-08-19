/**
 * {@link registerReasoningHooks} — wires Strands hook events to our
 * reasoning subclient. Each invocation opens a `ReasoningStep`; each tool
 * call records against that step.
 */

import type {
  BeforeInvocationEvent as BeforeInvocationEventType,
  AfterInvocationEvent as AfterInvocationEventType,
  BeforeToolCallEvent as BeforeToolCallEventType,
  AfterToolCallEvent as AfterToolCallEventType,
  LocalAgent,
} from "@strands-agents/sdk";

import type { MemoryClient } from "../../client.js";
import { loadStrands } from "./internal.js";

/** Options for {@link registerReasoningHooks}. */
export interface ReasoningHooksOptions {
  /** NAMS Conversation id to attribute reasoning steps and tool calls to. */
  conversationId: string;
}

/** Key in `invocationState` where the current reasoning step id is stashed. */
const INVOCATION_STEP_ID_KEY = "__neo4jReasoningStepId";
/** Key in `invocationState` where the per-invocation tool-call → toolCallId map lives. */
const TOOL_CALL_MAP_KEY = "__neo4jReasoningToolCalls";

/**
 * Wire reasoning capture onto a Strands `HookRegistry`.
 *
 * - `BeforeInvocationEvent` → `reasoning.recordStep` (opens a step; stashes
 *   step id on `event.invocationState`).
 * - `AfterInvocationEvent` → re-records the step with a `result` field
 *   (best-effort; we don't have a public `updateStep` API yet, so the
 *   second write supplements rather than mutates).
 * - `BeforeToolCallEvent` → `reasoning.recordToolCall` with status
 *   `pending`. Strands tool-call id → our tool-call id map stashed on
 *   `invocationState`.
 * - `AfterToolCallEvent` → updates the recorded tool call's status.
 *
 * All capture is best-effort: every reasoning write is wrapped in try/catch
 * and silently swallowed on failure. Reasoning capture must never break the
 * agent run.
 */
export async function registerReasoningHooks(
  memory: MemoryClient,
  agent: LocalAgent,
  options: ReasoningHooksOptions,
): Promise<void> {
  return registerReasoningHooksOnAgent(memory, agent, options);
}

export async function registerReasoningHooksOnAgent(
  memory: MemoryClient,
  agent: LocalAgent,
  options: ReasoningHooksOptions,
): Promise<void> {
  const strands = await loadStrands();
  const conversationId = options.conversationId;

  agent.addHook(strands.BeforeInvocationEvent, async (event: BeforeInvocationEventType) => {
    try {
      const step = await memory.reasoning.recordStep({
        conversationId,
        reasoning: "agent invocation started",
        actionTaken: "invoke_agent",
      });
      (event.invocationState as Record<string, unknown>)[INVOCATION_STEP_ID_KEY] = step.id;
      (event.invocationState as Record<string, unknown>)[TOOL_CALL_MAP_KEY] = new Map<
        string,
        string
      >();
    } catch {
      /* best-effort */
    }
  });

  agent.addHook(strands.AfterInvocationEvent, async (event: AfterInvocationEventType) => {
    try {
      const stepId = (event.invocationState as Record<string, unknown>)[INVOCATION_STEP_ID_KEY];
      if (typeof stepId !== "string") return;
      // Record a follow-up step with the result, since the current
      // reasoning API doesn't expose updateStep. This is intentional —
      // the after-invocation marker is a separate point in the trace.
      await memory.reasoning.recordStep({
        conversationId,
        reasoning: `agent invocation completed (step ${stepId})`,
        actionTaken: "invocation_complete",
        result: "ok",
      });
    } catch {
      /* best-effort */
    }
  });

  agent.addHook(strands.BeforeToolCallEvent, async (event: BeforeToolCallEventType) => {
    try {
      const stepId = (event.invocationState as Record<string, unknown>)[INVOCATION_STEP_ID_KEY];
      if (typeof stepId !== "string") return;
      const toolCall = await memory.reasoning.recordToolCall(
        stepId,
        event.toolUse.name,
        event.toolUse.input as Record<string, unknown>,
        { status: "pending" },
      );
      const map = (event.invocationState as Record<string, unknown>)[TOOL_CALL_MAP_KEY];
      if (map instanceof Map) {
        map.set(event.toolUse.toolUseId, toolCall.id);
      }
    } catch {
      /* best-effort */
    }
  });

  agent.addHook(strands.AfterToolCallEvent, async (event: AfterToolCallEventType) => {
    try {
      const stepId = (event.invocationState as Record<string, unknown>)[INVOCATION_STEP_ID_KEY];
      if (typeof stepId !== "string") return;
      // We don't have a public updateToolCall API yet either — record a
      // follow-up tool-call entry with the resolved status. Pair-up via
      // the same toolUseId-keyed map for future updateToolCall support.
      await memory.reasoning.recordToolCall(
        stepId,
        event.toolUse.name,
        event.toolUse.input as Record<string, unknown>,
        {
          status: event.error ? "failure" : "success",
          error: event.error?.message,
        },
      );
    } catch {
      /* best-effort */
    }
  });
}
