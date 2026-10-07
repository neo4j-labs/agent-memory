"use client";

import { useCallback, useState, useSyncExternalStore } from "react";

/**
 * A column width the user can drag, remembered in this browser.
 *
 * The stored width is read through `useSyncExternalStore`, so the server and
 * the hydration pass render `initial` and the remembered width is applied
 * right after, without a mismatch warning. While a drag is in progress the
 * width lives in component state and is written to storage once, on release.
 *
 * Storage can be missing or throw (private windows, blocked site data); the
 * width then lasts for the session only.
 */

const listeners = new Set<() => void>();
const memory = new Map<string, number>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function readStored(key: string): number | null {
  const remembered = memory.get(key);
  if (remembered !== undefined) return remembered;
  try {
    const raw = window.localStorage.getItem(key);
    const value = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: number | null): void {
  if (value === null) memory.delete(key);
  else memory.set(key, value);
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    // Kept in memory for this session.
  }
  for (const listener of listeners) listener();
}

export interface ResizableWidth {
  /** The width to render, before any viewport clamp the caller applies. */
  width: number;
  /** True while the user is dragging. */
  dragging: boolean;
  /** Live update during a drag (not stored). */
  preview: (width: number) => void;
  /** Store a width (end of a drag, or a keyboard step). */
  commit: (width: number) => void;
  /** Forget the stored width and go back to the default. */
  reset: () => void;
}

export function useResizableWidth(
  key: string,
  initial: number,
): ResizableWidth {
  const stored = useSyncExternalStore(
    subscribe,
    () => readStored(key),
    () => null,
  );
  const [dragWidth, setDragWidth] = useState<number | null>(null);

  const commit = useCallback(
    (width: number) => {
      writeStored(key, width);
      setDragWidth(null);
    },
    [key],
  );
  const reset = useCallback(() => {
    writeStored(key, null);
    setDragWidth(null);
  }, [key]);

  return {
    width: dragWidth ?? stored ?? initial,
    dragging: dragWidth !== null,
    preview: setDragWidth,
    commit,
    reset,
  };
}

/** `window.innerWidth`, kept current; the server assumes a desktop width. */
export function useViewportWidth(serverValue = 1440): number {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener("resize", onChange);
      return () => window.removeEventListener("resize", onChange);
    },
    () => window.innerWidth,
    () => serverValue,
  );
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}
