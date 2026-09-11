"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, streamChat } from "@/lib/api";
import type { Message, ToolCall } from "@/lib/types";

interface UseChatOptions {
  /**
   * Called when a turn finishes (the SSE `done` event). The page uses this to
   * bump a `memoryVersion` counter so the memory panels refetch and the user
   * can watch memory being written.
   */
  onTurnComplete?: () => void;
}

export function useChat(threadId: string | null, options: UseChatOptions = {}) {
  const { onTurnComplete } = options;
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The in-flight turn, so it can be cancelled by the Stop button or by
  // switching threads. Without this the fetch ran to completion with its
  // tokens landing nowhere.
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  // Load messages when thread changes, and cancel any turn still streaming
  // into the thread we are leaving.
  useEffect(() => {
    let cancelled = false;

    const loadMessages = async () => {
      if (!threadId) {
        if (!cancelled) setMessages([]);
        return;
      }
      try {
        const thread = await api.threads.get(threadId);
        if (!cancelled) {
          setMessages(thread.messages);
          setError(null);
        }
      } catch (err) {
        if (cancelled) return;
        // A brand-new thread with no messages yet is a 404 and is expected.
        // Anything else is a real failure and must be visible (an empty
        // message list otherwise looks exactly like a working empty thread).
        setMessages([]);
        if (err instanceof ApiError && err.status === 404) {
          setError(null);
        } else {
          setError(
            err instanceof Error
              ? `Could not load conversation: ${err.message}`
              : "Could not load conversation",
          );
        }
      }
    };

    loadMessages();

    return () => {
      cancelled = true;
      // Whatever is streaming belongs to the thread we are leaving.
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [threadId]);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!threadId || !content.trim() || isStreaming) return;

      setError(null);
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      // Add user message
      const userMessage: Message = {
        id: crypto.randomUUID(),
        role: "user",
        content,
        timestamp: new Date().toISOString(),
      };

      // Create assistant message placeholder
      const assistantId = crypto.randomUUID();
      const assistantMessage: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        toolCalls: [],
      };
      setMessages((prev) => [...prev, userMessage, assistantMessage]);

      /** Patch the assistant turn in place, keyed on the client-minted id. */
      const patchAssistant = (patch: Partial<Message>) =>
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId ? { ...msg, ...patch } : msg,
          ),
        );

      try {
        // Track tool calls by ID
        const toolCallsMap = new Map<string, ToolCall>();

        for await (const event of streamChat(
          threadId,
          content,
          memoryEnabled,
          controller.signal,
        )) {
          switch (event.type) {
            case "token": {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? { ...msg, content: msg.content + event.content }
                    : msg,
                ),
              );
              break;
            }

            case "tool_call": {
              toolCallsMap.set(event.id, {
                id: event.id,
                name: event.name,
                args: event.args,
                status: "pending",
              });
              patchAssistant({ toolCalls: Array.from(toolCallsMap.values()) });
              break;
            }

            case "tool_result": {
              const existing = toolCallsMap.get(event.id);
              if (existing) {
                // Replace rather than mutate: the old object is already
                // referenced by rendered state, so mutating it would make a
                // memoised `ToolCallDisplay` stop updating.
                toolCallsMap.set(event.id, {
                  ...existing,
                  result: event.result,
                  status: "success",
                  duration_ms: event.duration_ms,
                });
                patchAssistant({
                  toolCalls: Array.from(toolCallsMap.values()),
                });
              }
              break;
            }

            case "done": {
              // Memory for this turn is written by the time the backend emits
              // `done`, so this is when the memory panels should refetch.
              onTurnComplete?.();
              break;
            }

            case "error": {
              setError(event.message);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        content: msg.content || `Error: ${event.message}`,
                      }
                    : msg,
                ),
              );
              break;
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // Deliberate cancellation: keep whatever streamed, say so.
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    content: msg.content
                      ? `${msg.content}\n\n_(stopped)_`
                      : "_(stopped)_",
                  }
                : msg,
            ),
          );
        } else {
          const errorMessage =
            err instanceof Error ? err.message : "Failed to send message";
          setError(errorMessage);
          patchAssistant({ content: `Error: ${errorMessage}` });
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        setIsStreaming(false);
      }
    },
    [threadId, isStreaming, memoryEnabled, onTurnComplete],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return {
    messages,
    isStreaming,
    memoryEnabled,
    setMemoryEnabled,
    error,
    clearError: useCallback(() => setError(null), []),
    sendMessage,
    stop,
    clearMessages,
  };
}
