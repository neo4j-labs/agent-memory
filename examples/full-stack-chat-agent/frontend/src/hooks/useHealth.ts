"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

export interface HealthState {
  health: Health | null;
  /** Set when `/health` itself could not be reached (backend down / CORS). */
  unreachable: string | null;
}

/**
 * Polls the backend's `/health` once on mount.
 *
 * This is what turns the two most common first-run failures into something
 * visible: the backend not running at all, and the backend running with
 * `memory_connected: false` (usually a Neo4j password mismatch between
 * `docker-compose.yml` and `.env`). Without it both present as a
 * normal-looking empty UI, which is the worst default for a demo whose whole
 * point is that memory is being stored.
 */
export function useHealth(): HealthState {
  const [health, setHealth] = useState<Health | null>(null);
  const [unreachable, setUnreachable] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const data = await api.health.get();
        if (!cancelled) {
          setHealth(data);
          setUnreachable(null);
        }
      } catch (err) {
        if (!cancelled) {
          setHealth(null);
          setUnreachable(
            err instanceof Error ? err.message : "Backend unreachable",
          );
        }
      }
    };

    check();

    return () => {
      cancelled = true;
    };
  }, []);

  return { health, unreachable };
}
