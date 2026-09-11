"use client";

import { useMemo } from "react";
import type { QuickStartSuggestion } from "@/lib/types";

/**
 * Curated quick-start suggestions.
 *
 * These used to be read back from `GET /api/threads`, which meant a first-time
 * visitor's landing page was populated with whatever other visitors of the
 * public demo had typed (the backend thread registry is process-global), at the
 * cost of 1 + N sequential requests on first paint. A static list is faster,
 * deterministic, survives a backend restart and leaks nothing.
 *
 * Keep this list in sync with the "Example Questions" section of the example
 * README - each entry should exercise a different tool group.
 */
export const SUGGESTED_QUERIES: readonly QuickStartSuggestion[] = [
  {
    id: "semantic-search",
    category: "Semantic search",
    firstMessage: "What did Brian Chesky say about product management?",
  },
  {
    id: "cross-episode",
    category: "Cross-episode comparison",
    firstMessage: "Compare what Brian Chesky and Andy Johns said about growth",
  },
  {
    id: "entity-graph",
    category: "Entity knowledge graph",
    firstMessage:
      "Who are the most frequently mentioned people across all episodes?",
  },
  {
    id: "entity-context",
    category: "Entity knowledge graph",
    firstMessage: "Tell me about Y Combinator - what do guests say about it?",
  },
  {
    id: "geospatial",
    category: "Geospatial analysis",
    firstMessage: "What locations are mentioned in the Brian Chesky episode?",
  },
  {
    id: "geospatial-near",
    category: "Geospatial analysis",
    firstMessage: "Find cities mentioned within 100km of San Francisco",
  },
  {
    id: "preferences",
    category: "Personalization",
    firstMessage: "I prefer detailed answers with direct quotes from guests",
  },
  {
    id: "reasoning",
    category: "Reasoning memory",
    firstMessage: "What similar questions have been asked before?",
  },
  {
    id: "stats",
    category: "Memory stats",
    firstMessage: "How much is in your memory right now?",
  },
];

/**
 * Hook returning the curated quick-start suggestions.
 *
 * Kept as a hook (rather than importing the constant directly) so the call
 * sites stay unchanged if per-visitor history is reintroduced later - that
 * requires backend multi-tenancy so one visitor never sees another's threads.
 */
export function useQuickStart(limit: number = 10) {
  const suggestions = useMemo(
    () => SUGGESTED_QUERIES.slice(0, limit),
    [limit],
  );

  return {
    suggestions,
    isLoading: false,
    error: null as string | null,
  };
}
