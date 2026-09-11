"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MemoryContext } from "@/lib/types";

const EMPTY_CONTEXT: MemoryContext = {
  preferences: [],
  entities: [],
  recent_topics: [],
  recent_messages: [],
};

/**
 * Fetch the memory the agent has accumulated for a thread.
 *
 * Backed by `GET /api/memory/context`, which returns the entities extracted
 * from the conversation (Wikipedia-enriched where available), the preferences
 * long-term memory has learned, and the most recent messages.
 *
 * @param threadId  conversation to scope the context to; `null` before the
 *                  first message, in which case nothing is fetched.
 * @param version   bump this to refetch - `useChat` increments its
 *                  `memoryVersion` every time a turn completes.
 */
export function useMemoryContext(threadId: string | null, version: number = 0) {
  const [context, setContext] = useState<MemoryContext>(EMPTY_CONTEXT);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!threadId) {
        setContext(EMPTY_CONTEXT);
        return;
      }
      setIsLoading(true);
      setError(null);
      try {
        const next = await api.memory.getContext({ threadId, signal });
        if (!signal?.aborted) setContext(next);
      } catch (err) {
        if (signal?.aborted) return;
        setError(
          err instanceof Error ? err.message : "Failed to load memory context",
        );
      } finally {
        if (!signal?.aborted) setIsLoading(false);
      }
    },
    [threadId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, version]);

  return { context, isLoading, error, refresh: load };
}
