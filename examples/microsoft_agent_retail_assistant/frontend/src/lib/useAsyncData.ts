"use client";

import { useEffect, useState } from "react";

export interface AsyncData<T> {
  /** The most recently loaded value, or `null` before the first success. */
  data: T | null;
  error: string | null;
  /** True whenever the value on screen is older than the current request. */
  isLoading: boolean;
  /** Re-runs the loader (for a Refresh button). */
  reload: () => void;
}

/**
 * Minimal fetch-on-mount hook.
 *
 * Two details matter:
 *
 * 1. `load` must be memoised (`useCallback`) with the same inputs that appear
 *    in `key` — the effect re-runs whenever either changes.
 * 2. No state is set synchronously inside the effect body. Loading is derived
 *    by comparing the key of the last settled request against the current one,
 *    which is what keeps this clear of React 19's `set-state-in-effect` rule
 *    (and of the cascading render it warns about).
 */
export function useAsyncData<T>(
  key: string,
  load: () => Promise<T>
): AsyncData<T> {
  const [nonce, setNonce] = useState(0);
  const [settled, setSettled] = useState<{
    key: string;
    data: T | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const requestKey = `${key}#${nonce}`;

  useEffect(() => {
    let active = true;
    load()
      .then((data) => {
        if (active) setSettled({ key: requestKey, data, error: null });
      })
      .catch((err: unknown) => {
        if (active) {
          setSettled({
            key: requestKey,
            data: null,
            error: err instanceof Error ? err.message : "Request failed",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [load, requestKey]);

  return {
    data: settled.data,
    error: settled.error,
    isLoading: settled.key !== requestKey,
    reload: () => setNonce((n) => n + 1),
  };
}
