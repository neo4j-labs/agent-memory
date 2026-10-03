"use client";

import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "@/lib/api";

/** A memoised loader. Its identity is the request: new params, new loader. */
export type Loader<T> = (signal: AbortSignal) => Promise<T>;

interface Settled<T> {
  data: T | null;
  error: string | null;
  loader: Loader<T> | null;
  version: string;
}

export interface ApiResource<T> {
  data: T | null;
  error: string | null;
  /** True while a request for the current loader/version is in flight. */
  loading: boolean;
  /** Refetch with the same parameters. */
  reload: () => void;
}

/**
 * Fetch-on-change for one endpoint.
 *
 * - `load` must be memoised (`useCallback`) on its parameters: a new loader
 *   means new parameters, so the previous data is hidden until it settles —
 *   switching threads never shows the last thread's entities.
 * - `refreshKey` is for "something was written, fetch again": the data stays
 *   on screen while the refetch runs.
 * - `null` disables the resource.
 *
 * Loading is derived (the settled result is tagged with the loader and
 * version it belongs to) rather than set at the top of the effect, which is
 * what React's `set-state-in-effect` rule asks for. Late responses from a
 * superseded request are dropped through the abort signal.
 */
export function useApi<T>(
  load: Loader<T> | null,
  refreshKey: number | string = 0,
): ApiResource<T> {
  const [reloadToken, setReloadToken] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({
    data: null,
    error: null,
    loader: null,
    version: "",
  });
  const version = `${refreshKey}:${reloadToken}`;

  useEffect(() => {
    if (!load) return;
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setSettled({ data, error: null, loader: load, version });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setSettled((prev) => ({
          // Keep the last good data for the same request; drop it otherwise.
          data: prev.loader === load ? prev.data : null,
          error: errorMessage(err),
          loader: load,
          version,
        }));
      },
    );
    return () => controller.abort();
  }, [load, version]);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  const current = load !== null && settled.loader === load;
  return {
    data: current ? settled.data : null,
    error: current ? settled.error : null,
    loading: load !== null && (!current || settled.version !== version),
    reload,
  };
}
