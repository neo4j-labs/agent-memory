/**
 * Table extractor: pick columns per tool family, with an auto-detect fallback.
 */

import type { ColumnDef } from "../types";

/**
 * Extract table columns and rows from search results
 */
export function extractTableData(
  toolName: string,
  result: unknown,
): {
  columns: ColumnDef[];
  rows: Record<string, unknown>[];
  title?: string;
} {
  if (!result || !Array.isArray(result)) {
    return { columns: [], rows: [] };
  }

  const name = toolName.toLowerCase();
  let columns: ColumnDef[] = [];
  let title: string | undefined;

  // Podcast search results (including search_episode which returns transcript segments)
  if (
    name.includes("search_podcast") ||
    name.includes("search_by_speaker") ||
    name.includes("search_episode")
  ) {
    title = "Podcast Matches";
    columns = [
      { key: "speaker", label: "Speaker", width: "20%" },
      { key: "content", label: "Content", width: "60%" },
      { key: "episode_guest", label: "Episode", width: "20%" },
    ];
  }
  // Episode list (but not search_episode which is handled above)
  else if (name.includes("list_episode") || name === "episode") {
    title = "Episodes";
    columns = [
      { key: "guest", label: "Guest", width: "50%" },
      { key: "session_id", label: "Episode ID", width: "30%" },
      { key: "message_count", label: "Messages", width: "20%" },
    ];
  }
  // Speaker list
  else if (name.includes("list_speaker") || name.includes("speaker")) {
    title = "Speakers";
    columns = [
      { key: "name", label: "Name", width: "40%" },
      { key: "role", label: "Role", width: "30%" },
      { key: "episode_count", label: "Episodes", width: "30%" },
    ];
  }
  // Entity search
  else if (name.includes("entities") || name.includes("entity")) {
    title = "Entities";
    columns = [
      { key: "name", label: "Name", width: "25%" },
      { key: "type", label: "Type", width: "15%" },
      { key: "subtype", label: "Subtype", width: "15%" },
      { key: "description", label: "Description", width: "45%" },
    ];
  }
  // Preferences
  else if (name.includes("preferences")) {
    title = "User Preferences";
    columns = [
      { key: "category", label: "Category", width: "25%" },
      { key: "preference", label: "Preference", width: "50%" },
      { key: "confidence", label: "Confidence", width: "25%" },
    ];
  }
  // Similar queries
  else if (name.includes("similar")) {
    title = "Similar Past Queries";
    columns = [
      { key: "task", label: "Query", width: "50%" },
      { key: "outcome", label: "Outcome", width: "35%" },
      { key: "similarity", label: "Match", width: "15%" },
    ];
  }
  // Auto-detect columns from first result
  else if (result.length > 0) {
    const firstRow = result[0] as Record<string, unknown>;
    columns = Object.keys(firstRow)
      .filter(
        (key) => !key.startsWith("_") && key !== "id" && key !== "embedding",
      )
      .slice(0, 4)
      .map((key) => ({
        key,
        label: key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase()),
      }));
  }

  return {
    columns,
    rows: result as Record<string, unknown>[],
    title,
  };
}
