/**
 * Location extractors: turn the three shapes the geospatial tools return
 * (flat list, graph path, country clusters) into LocationData for MapCard.
 */

import type { LocationData, PathNode } from "../types";

/** Stable fallback id, so React keys do not change between renders. */
function fallbackId(prefix: string, index: number): string {
  return `${prefix}-${index}`;
}

/**
 * Extract location data from the various tool result formats.
 */
export function extractLocations(result: unknown): LocationData[] {
  if (!result) return [];

  // Direct array of locations
  if (Array.isArray(result)) {
    return result
      .filter(
        (item): item is Record<string, unknown> =>
          !!item &&
          typeof item === "object" &&
          "latitude" in item &&
          "longitude" in item,
      )
      .map((item, index) => ({
        id: String(item.id ?? fallbackId("location", index)),
        name: String(item.name || "Unknown"),
        latitude: Number(item.latitude),
        longitude: Number(item.longitude),
        subtype: item.subtype as string | undefined,
        description: String(
          item.description || item.enriched_description || "",
        ),
        episodeCount: Array.isArray(item.conversations)
          ? item.conversations.length
          : (item.episode_count as number | undefined),
      }));
  }

  if (typeof result !== "object") return [];

  // Path result format
  if ("nodes" in result) {
    const pathResult = result as { nodes: unknown[] };
    return pathResult.nodes
      .filter((n): n is Record<string, unknown> => {
        if (!n || typeof n !== "object") return false;
        const obj = n as Record<string, unknown>;
        return "latitude" in obj && "longitude" in obj;
      })
      .map((n, index) => ({
        id: String(n.id ?? fallbackId("path-node", index)),
        name: String(n.name || n.id || "Unknown"),
        latitude: Number(n.latitude),
        longitude: Number(n.longitude),
        subtype: n.subtype as string | undefined,
      }));
  }

  // Clusters result format
  if ("clusters" in result) {
    const clustersResult = result as { clusters: unknown[] };
    const locations: LocationData[] = [];
    clustersResult.clusters.forEach((cluster, clusterIndex) => {
      if (!cluster || typeof cluster !== "object") return;
      if (!("locations" in (cluster as Record<string, unknown>))) return;

      const clusterData = cluster as {
        locations: unknown[];
        country?: string;
      };
      (clusterData.locations as Record<string, unknown>[]).forEach(
        (loc, locIndex) => {
          if (!("latitude" in loc) || !("longitude" in loc)) return;
          locations.push({
            id: String(
              loc.id ?? fallbackId(`cluster-${clusterIndex}`, locIndex),
            ),
            name: String(loc.name || "Unknown"),
            latitude: Number(loc.latitude),
            longitude: Number(loc.longitude),
            subtype: (loc.subtype as string) || clusterData.country,
          });
        },
      );
    });
    return locations;
  }

  return [];
}

/**
 * Extract path nodes for map visualization (nodes without coordinates are kept
 * so the card can report gaps in the path).
 */
export function extractPathNodes(result: unknown): PathNode[] {
  if (!result || typeof result !== "object" || !("nodes" in result)) return [];

  const pathResult = result as { nodes: unknown[] };
  return pathResult.nodes.map((n, index) => {
    const node = (n ?? {}) as Record<string, unknown>;
    return {
      id: String(node.id ?? fallbackId("path-node", index)),
      name: String(node.name || node.id || "Unknown"),
      latitude: node.latitude as number | undefined,
      longitude: node.longitude as number | undefined,
    };
  });
}
