/**
 * Card-type selection: which card should render a given tool result?
 *
 * This is deliberately the only decision this module makes. The per-card data
 * extractors live in ./extractors, so a reader who wants "how do I pick a card"
 * does not have to read 700 lines of result munging to find it.
 */

import type { CardType } from "./types";

/**
 * Type guard: does this result look like an entity knowledge-panel payload?
 */
export function hasEntityData(result: unknown): boolean {
  if (!result || typeof result !== "object") return false;

  // Entity context format: { entity: {...}, mentions: [...] }
  if ("entity" in result) {
    const entity = (result as { entity: unknown }).entity;
    return (
      entity !== null &&
      typeof entity === "object" &&
      "name" in (entity as Record<string, unknown>)
    );
  }

  // Direct entity format: { name, type, ... }
  return "name" in result && "type" in result;
}

/**
 * Type guard: does this result carry coordinates we can put on a map?
 */
export function hasLocationData(result: unknown): boolean {
  if (!result || typeof result !== "object") return false;

  // Array of locations
  if (Array.isArray(result)) {
    return (
      result.length > 0 &&
      !!result[0] &&
      typeof result[0] === "object" &&
      "latitude" in result[0] &&
      "longitude" in result[0]
    );
  }

  // Path result with nodes
  if ("nodes" in result && Array.isArray((result as { nodes: unknown[] }).nodes)) {
    const nodes = (result as { nodes: unknown[] }).nodes;
    return nodes.some(
      (n) =>
        !!n &&
        typeof n === "object" &&
        "latitude" in (n as Record<string, unknown>) &&
        "longitude" in (n as Record<string, unknown>),
    );
  }

  // Clusters result
  return (
    "clusters" in result &&
    Array.isArray((result as { clusters: unknown[] }).clusters)
  );
}

/**
 * Determines which card type to render based on tool name and result structure.
 */
export function getCardTypeForTool(
  toolName: string,
  result: unknown,
): CardType {
  const name = toolName.toLowerCase();

  // Location tools -> MapCard
  if (
    name.includes("location") ||
    name.includes("_near") ||
    name.includes("_path") ||
    name.includes("_clusters")
  ) {
    if (hasLocationData(result)) {
      return "map";
    }
  }

  // Entity context -> EntityCard (knowledge panel)
  if (name.includes("entity_context") || name.includes("get_entity")) {
    if (hasEntityData(result)) {
      return "entity";
    }
  }

  // Related entities -> GraphCard (shows relationships)
  if (name.includes("related_entities")) {
    return "graph";
  }

  // Memory graph search -> MemoryGraphCard (vector + graph visualization)
  if (name.includes("memory_graph_search")) {
    return "memory_graph";
  }

  // Stats tools -> StatsCard
  if (
    name.includes("get_stats") ||
    name.includes("top_entities") ||
    name.includes("memory_stats")
  ) {
    return "stats";
  }

  // Search results, lists, entity data -> DataCard
  if (
    name.includes("search") ||
    name.includes("list") ||
    name.includes("get_entity") ||
    name.includes("preferences") ||
    name.includes("similar") ||
    name.includes("speaker")
  ) {
    return "data";
  }

  // Default fallback
  return "raw";
}

/**
 * Get a friendly display title for a tool.
 */
export function getToolDisplayTitle(toolName: string): string {
  return toolName
    .replace(/^tool_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (l) => l.toUpperCase());
}
