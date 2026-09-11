/**
 * Graph extractors: build nodes/relationships for the NVL-backed cards from
 * entity tool results.
 */

import type { GraphNodeData, GraphRelationshipData } from "../types";

/**
 * Extract graph data for visualization.
 */
export function extractGraphData(result: unknown): {
  nodes: GraphNodeData[];
  relationships: GraphRelationshipData[];
} {
  if (!result || typeof result !== "object") {
    return { nodes: [], relationships: [] };
  }

  // Related entities format (array): star layout around the first entity.
  if (Array.isArray(result)) {
    const nodes: GraphNodeData[] = result.map((entity, idx) => {
      const e = entity as Record<string, unknown>;
      return {
        id: String(e.id || e.name || idx),
        label: String(e.name || "Unknown"),
        type: String(e.type || "Entity"),
        properties: {
          coOccurrences: e.co_occurrences,
          subtype: e.subtype,
          description: e.description,
        },
      };
    });

    const relationships: GraphRelationshipData[] = [];
    if (nodes.length > 1) {
      const centralNode = nodes[0];
      for (let i = 1; i < nodes.length; i++) {
        relationships.push({
          id: `rel-${i}`,
          from: centralNode.id,
          to: nodes[i].id,
          type: "RELATED_TO",
        });
      }
    }

    return { nodes, relationships };
  }

  // Entity context format (single entity with mentions).
  if ("entity" in result || "name" in result) {
    const entity =
      (result as { entity?: Record<string, unknown> }).entity ||
      (result as Record<string, unknown>);
    const mentions = (result as { mentions?: unknown[] }).mentions || [];

    const nodes: GraphNodeData[] = [
      {
        id: String(entity.id || entity.name),
        label: String(entity.name || "Unknown"),
        type: String(entity.type || "Entity"),
        properties: entity,
      },
    ];

    mentions.forEach((mention, idx) => {
      const m = mention as Record<string, unknown>;
      nodes.push({
        id: `mention-${idx}`,
        label: String(m.speaker || m.episode || `Mention ${idx + 1}`),
        type: "Mention",
        properties: m,
      });
    });

    const relationships: GraphRelationshipData[] = mentions.map((_, idx) => ({
      id: `rel-mention-${idx}`,
      from: nodes[0].id,
      to: `mention-${idx}`,
      type: "MENTIONED_IN",
    }));

    return { nodes, relationships };
  }

  return { nodes: [], relationships: [] };
}
