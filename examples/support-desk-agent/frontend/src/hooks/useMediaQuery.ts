"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * `window.matchMedia` as a hook.
 *
 * The server (and the hydration pass) assume `serverValue`; the real value is
 * applied right after hydration without a mismatch warning. Used to choose
 * between an inline side panel and a drawer, so each panel is mounted exactly
 * once — a CSS-hidden copy would still fetch, and NVL would lay out a graph in
 * a zero-sized canvas.
 */
export function useMediaQuery(query: string, serverValue = true): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => serverValue,
  );
}
