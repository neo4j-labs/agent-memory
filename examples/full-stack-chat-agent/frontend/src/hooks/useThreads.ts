"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Thread } from "@/lib/types";

export function useThreads() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch threads on mount
  useEffect(() => {
    let cancelled = false;

    const fetchThreads = async () => {
      try {
        const data = await api.threads.list();
        if (cancelled) return;
        setThreads(data);
        // Select the most recent thread. `activeThreadId` is always null on
        // mount, so there is nothing to preserve here.
        if (data.length > 0) {
          setActiveThreadId(data[0].id);
        }
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to fetch threads",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchThreads();

    return () => {
      cancelled = true;
    };
  }, []);

  const createThread = useCallback(async (title?: string) => {
    try {
      const thread = await api.threads.create(title);
      setThreads((prev) => [thread, ...prev]);
      setActiveThreadId(thread.id);
      setError(null);
      return thread;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create thread");
      throw err;
    }
  }, []);

  const deleteThread = useCallback(async (id: string) => {
    try {
      await api.threads.delete(id);
      // Functional updates only: the next active thread is derived from the
      // list React is about to render, not from a captured snapshot — so the
      // callback needs no `threads`/`activeThreadId` dependency and cannot
      // act on a stale one.
      setThreads((prev) => {
        const remaining = prev.filter((t) => t.id !== id);
        setActiveThreadId((current) =>
          current === id ? (remaining[0]?.id ?? null) : current,
        );
        return remaining;
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete thread");
      throw err;
    }
  }, []);

  const selectThread = useCallback((id: string) => {
    setActiveThreadId(id);
  }, []);

  const updateThreadTitle = useCallback(async (id: string, title: string) => {
    try {
      const updated = await api.threads.update(id, title);
      setThreads((prev) =>
        prev.map((t) => (t.id === id ? { ...t, title: updated.title } : t)),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update thread");
      throw err;
    }
  }, []);

  return {
    threads,
    activeThreadId,
    activeThread: threads.find((t) => t.id === activeThreadId) || null,
    isLoading,
    error,
    clearError: useCallback(() => setError(null), []),
    createThread,
    deleteThread,
    selectThread,
    updateThreadTitle,
  };
}
