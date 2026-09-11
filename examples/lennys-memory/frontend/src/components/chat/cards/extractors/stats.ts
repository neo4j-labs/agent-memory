/**
 * Stats extractor: turn memory/stat tool results into StatItem tiles.
 */

import type { StatItem } from "../types";

/**
 * Extract stats from tool results
 */
export function extractStats(toolName: string, result: unknown): StatItem[] {
  if (!result || typeof result !== "object") return [];

  const stats: StatItem[] = [];
  const name = toolName.toLowerCase();

  if (name.includes("get_stats") || name.includes("memory_stats")) {
    const r = result as Record<string, unknown>;
    if (r.total_episodes !== undefined) {
      stats.push({
        label: "Episodes",
        value: Number(r.total_episodes),
        colorPalette: "blue",
      });
    }
    if (r.total_speakers !== undefined) {
      stats.push({
        label: "Speakers",
        value: Number(r.total_speakers),
        colorPalette: "green",
      });
    }
    if (r.total_messages !== undefined) {
      stats.push({
        label: "Messages",
        value: Number(r.total_messages),
        colorPalette: "purple",
      });
    }
    if (r.total_entities !== undefined) {
      stats.push({
        label: "Entities",
        value: Number(r.total_entities),
        colorPalette: "orange",
      });
    }
    if (r.total_locations !== undefined) {
      stats.push({
        label: "Locations",
        value: Number(r.total_locations),
        colorPalette: "teal",
      });
    }
    if (r.total_preferences !== undefined) {
      stats.push({
        label: "Preferences",
        value: Number(r.total_preferences),
        colorPalette: "amber",
      });
    }
  }

  if (name.includes("top_entities") && Array.isArray(result)) {
    // Map entity types to color palettes matching EntityCard colors
    const typeColors: Record<string, string> = {
      PERSON: "pink",
      ORGANIZATION: "orange",
      LOCATION: "blue",
      EVENT: "purple",
      CONCEPT: "green",
      TOPIC: "green",
      OBJECT: "cyan",
    };
    const fallbackColors = [
      "blue",
      "green",
      "purple",
      "orange",
      "teal",
      "pink",
    ];

    result.slice(0, 6).forEach((entity, i) => {
      const e = entity as Record<string, unknown>;
      const entityType = String(e.type || "").toUpperCase();
      // Use type-specific color if available, otherwise cycle through fallback colors
      const colorPalette =
        typeColors[entityType] || fallbackColors[i % fallbackColors.length];

      stats.push({
        label: String(e.name || "Unknown"),
        value: Number(e.mentions || e.count || e.mention_count || 0),
        colorPalette,
      });
    });
  }

  return stats;
}
