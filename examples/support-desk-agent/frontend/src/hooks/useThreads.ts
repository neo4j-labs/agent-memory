"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { ThreadSummary } from "@/lib/types";

/**
 * The thread list (seeded conversations included) and the active thread.
 *
 * The first load selects the newest thread. `refresh()` refetches without
 * touching the selection — called after each turn, since a turn changes the
 * thread's `message_count` / `updated_at` (and possibly its title).
 */
export function useThreads() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.threads.list({ signal: controller.signal }).then(
      (data) => {
        if (controller.signal.aborted) return;
        setThreads(data);
        setError(null);
        setLoaded(true);
        // Select the newest thread on first load, and recover if the active
        // thread disappeared (e.g. after a reseed).
        setActiveThreadId((current) =>
          current && data.some((t) => t.id === current)
            ? current
            : (data[0]?.id ?? null),
        );
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setError(`Could not load conversations: ${errorMessage(err)}`);
        setLoaded(true);
      },
    );
    return () => controller.abort();
  }, [refreshToken]);

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), []);

  const createThread = useCallback(async (title?: string) => {
    try {
      const thread = await api.threads.create(title);
      setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
      setActiveThreadId(thread.id);
      setError(null);
      return thread;
    } catch (err) {
      setError(`Could not create a conversation: ${errorMessage(err)}`);
      return null;
    }
  }, []);

  const selectThread = useCallback((id: string) => setActiveThreadId(id), []);

  return {
    threads,
    activeThreadId,
    activeThread: threads.find((t) => t.id === activeThreadId) ?? null,
    loaded,
    error,
    clearError: useCallback(() => setError(null), []),
    refresh,
    createThread,
    selectThread,
  };
}
