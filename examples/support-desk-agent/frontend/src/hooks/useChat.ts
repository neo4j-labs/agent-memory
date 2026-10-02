"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, streamChat } from "@/lib/api";
import type {
  DoneEvent,
  MessageRole,
  MessageStoredEvent,
  StoredEntity,
  ThreadMessage,
  TouchedRef,
  TraceToolCall,
} from "@/lib/types";

export interface ToolCallState {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "pending" | "success" | "error";
  result?: unknown;
  durationMs?: number;
  touched: TouchedRef[];
}

/** A chat message as the UI renders it (history or streamed). */
export interface ChatMessage {
  /** Stable React key; client-minted for messages streamed in this session. */
  key: string;
  /** Server message id, once known. */
  id: string | null;
  role: MessageRole;
  content: string;
  createdAt: string | null;
  /**
   * The reasoning trace of the turn. On history it comes from the user
   * message that initiated the trace and is copied onto the assistant reply,
   * which is where the "trace" chip is rendered.
   */
  traceId: string | null;
  toolCalls: ToolCallState[];
  /** User messages: what extraction found when the message was stored. */
  entities: StoredEntity[] | null;
  streaming: boolean;
}

export interface UseChatCallbacks {
  onMessageStored?: (event: MessageStoredEvent) => void;
  onTraceStarted?: (traceId: string) => void;
  /** A tool finished; its step and tool call are recorded in the trace. */
  onToolResult?: () => void;
  onTurnComplete?: (event: DoneEvent | null) => void;
}

interface ThreadMessages {
  threadId: string | null;
  messages: ChatMessage[];
}

const FAILED_STATUSES = new Set(["error", "failure", "failed", "timeout"]);

/** A recorded tool call, shaped like the ones a live turn streams. */
function fromRecorded(call: TraceToolCall): ToolCallState {
  const args = call.arguments;
  return {
    id: call.id,
    name: call.tool_name,
    args:
      typeof args === "object" && args !== null && !Array.isArray(args)
        ? (args as Record<string, unknown>)
        : {},
    status: FAILED_STATUSES.has(call.status.toLowerCase()) ? "error" : "success",
    result: call.result,
    durationMs: call.duration_ms ?? undefined,
    touched: call.touched.map((entity) => ({
      id: entity.id,
      name: entity.name,
      type: "",
      labels: entity.labels,
    })),
  };
}

function fromHistory(messages: ThreadMessage[]): ChatMessage[] {
  // The trace (and its tool calls) belongs to the user message that started
  // it; the UI shows both on the assistant reply that follows.
  let pendingTrace: string | null = null;
  let pendingCalls: ToolCallState[] = [];
  return messages.map((message) => {
    let traceId = message.trace_id;
    let toolCalls: ToolCallState[] = [];
    if (message.role === "user") {
      pendingTrace = message.trace_id;
      pendingCalls = (message.tool_calls ?? []).map(fromRecorded);
    } else if (message.role === "assistant") {
      traceId = traceId ?? pendingTrace;
      toolCalls = pendingCalls;
      pendingTrace = null;
      pendingCalls = [];
    }
    return {
      key: message.id,
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
      traceId,
      toolCalls,
      entities: null,
      streaming: false,
    };
  });
}

/** A tool result shaped like `{"error": ...}` is reported as a failed call. */
function looksLikeError(result: unknown): boolean {
  return (
    typeof result === "object" &&
    result !== null &&
    !Array.isArray(result) &&
    "error" in result &&
    Boolean((result as { error: unknown }).error)
  );
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useChat(
  threadId: string | null,
  callbacks: UseChatCallbacks = {},
) {
  const { onMessageStored, onTraceStarted, onToolResult, onTurnComplete } =
    callbacks;
  const [state, setState] = useState<ThreadMessages>({
    threadId: null,
    messages: [],
  });
  const [historyError, setHistoryError] = useState<{
    threadId: string;
    message: string;
  } | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Load the history whenever the thread changes; cancel a turn still
  // streaming into the thread being left.
  useEffect(() => {
    if (!threadId) return;
    const controller = new AbortController();
    api.threads.get(threadId, { signal: controller.signal }).then(
      (detail) => {
        if (controller.signal.aborted) return;
        // A turn sent before the history arrived wins over the history.
        setState((prev) =>
          prev.threadId === threadId
            ? prev
            : { threadId, messages: fromHistory(detail.messages ?? []) },
        );
        setHistoryError(null);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setState((prev) =>
          prev.threadId === threadId ? prev : { threadId, messages: [] },
        );
        // A thread created a moment ago may not exist server-side until its
        // first message; that 404 is an empty thread, not a failure.
        setHistoryError(
          err instanceof ApiError && err.status === 404
            ? null
            : {
                threadId,
                message: `Could not load the conversation: ${errorMessage(err)}`,
              },
        );
      },
    );
    return () => {
      controller.abort();
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [threadId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const sendMessage = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!threadId || !text || isStreaming) return;

      setStreamError(null);
      setIsStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const userKey = newKey();
      const assistantKey = newKey();
      const now = new Date().toISOString();

      const patch = (key: string, update: (m: ChatMessage) => ChatMessage) =>
        setState((prev) =>
          prev.threadId !== threadId
            ? prev
            : {
                ...prev,
                messages: prev.messages.map((m) =>
                  m.key === key ? update(m) : m,
                ),
              },
        );

      setState((prev) => ({
        threadId,
        messages: [
          ...(prev.threadId === threadId ? prev.messages : []),
          {
            key: userKey,
            id: null,
            role: "user",
            content: text,
            createdAt: now,
            traceId: null,
            toolCalls: [],
            entities: null,
            streaming: false,
          },
          {
            key: assistantKey,
            id: null,
            role: "assistant",
            content: "",
            createdAt: now,
            traceId: null,
            toolCalls: [],
            entities: null,
            streaming: true,
          },
        ],
      }));

      let completed: DoneEvent | null = null;
      try {
        for await (const event of streamChat(threadId, text, controller.signal)) {
          switch (event.type) {
            case "message_stored":
              patch(userKey, (m) => ({
                ...m,
                id: event.message_id,
                entities: event.entities ?? [],
              }));
              onMessageStored?.(event);
              break;

            case "trace_started":
              patch(userKey, (m) => ({ ...m, traceId: event.trace_id }));
              patch(assistantKey, (m) => ({ ...m, traceId: event.trace_id }));
              onTraceStarted?.(event.trace_id);
              break;

            case "token":
              patch(assistantKey, (m) => ({
                ...m,
                content: m.content + event.content,
              }));
              break;

            case "tool_call":
              patch(assistantKey, (m) => ({
                ...m,
                toolCalls: [
                  ...m.toolCalls.filter((call) => call.id !== event.id),
                  {
                    id: event.id,
                    name: event.name,
                    args: event.args ?? {},
                    status: "pending",
                    touched: [],
                  },
                ],
              }));
              break;

            case "tool_result":
              patch(assistantKey, (m) => {
                const known = m.toolCalls.some((call) => call.id === event.id);
                const finished: ToolCallState = {
                  id: event.id,
                  name: event.name,
                  args:
                    m.toolCalls.find((call) => call.id === event.id)?.args ??
                    {},
                  status: looksLikeError(event.result) ? "error" : "success",
                  result: event.result,
                  durationMs: event.duration_ms,
                  touched: event.touched ?? [],
                };
                return {
                  ...m,
                  toolCalls: known
                    ? m.toolCalls.map((call) =>
                        call.id === event.id ? finished : call,
                      )
                    : [...m.toolCalls, finished],
                };
              });
              onToolResult?.();
              break;

            case "done":
              completed = event;
              patch(assistantKey, (m) => ({
                ...m,
                id: event.message_id,
                traceId: event.trace_id ?? m.traceId,
                streaming: false,
              }));
              break;

            case "error":
              setStreamError(event.message);
              patch(assistantKey, (m) => ({
                ...m,
                content: m.content || `**Error:** ${event.message}`,
              }));
              break;
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          patch(assistantKey, (m) => ({
            ...m,
            content: m.content ? `${m.content}\n\n_(stopped)_` : "_(stopped)_",
          }));
        } else {
          const message = errorMessage(err, "Failed to send the message");
          setStreamError(message);
          patch(assistantKey, (m) => ({
            ...m,
            content: m.content || `**Error:** ${message}`,
          }));
        }
      } finally {
        patch(assistantKey, (m) => ({ ...m, streaming: false }));
        if (abortRef.current === controller) abortRef.current = null;
        setIsStreaming(false);
        // Refetch the panels whether the turn finished or failed: the user
        // message (and possibly a trace) was written either way.
        onTurnComplete?.(completed);
      }
    },
    [
      threadId,
      isStreaming,
      onMessageStored,
      onTraceStarted,
      onToolResult,
      onTurnComplete,
    ],
  );

  const current = threadId !== null && state.threadId === threadId;
  const error =
    streamError ??
    (historyError && historyError.threadId === threadId
      ? historyError.message
      : null);

  return {
    messages: current ? state.messages : [],
    loadingHistory: threadId !== null && !current,
    isStreaming,
    error,
    clearError: useCallback(() => {
      setStreamError(null);
      setHistoryError(null);
    }, []),
    sendMessage,
    stop,
  };
}
