/**
 * Memory-graph extractor: shape the combined vector + graph search result
 * used by MemoryGraphCard.
 */

/**
 * Memory graph node for visualization
 */
export interface MemoryGraphNode {
  id: string;
  label: string;
  type: string;
  properties?: Record<string, unknown>;
}

/**
 * Memory graph relationship for visualization
 */
export interface MemoryGraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
}

/**
 * Memory graph search result summary
 */
export interface MemoryGraphSummary {
  messages_found: number;
  entities_found: number;
  relationships_found: number;
}

/**
 * Complete memory graph search result
 */
export interface MemoryGraphSearchResult {
  query: string;
  nodes: MemoryGraphNode[];
  relationships: MemoryGraphRelationship[];
  summary: MemoryGraphSummary;
  error?: string;
  message?: string;
}

/**
 * Extract memory graph data from tool result
 */
export function extractMemoryGraphData(
  result: unknown,
): MemoryGraphSearchResult | null {
  if (!result || typeof result !== "object") return null;

  const r = result as Record<string, unknown>;

  // Check for error response
  if (r.error) {
    return {
      query: String(r.query || ""),
      nodes: [],
      relationships: [],
      summary: {
        messages_found: 0,
        entities_found: 0,
        relationships_found: 0,
      },
      error: String(r.error),
    };
  }

  // Check for valid memory graph structure
  if (!("nodes" in r) || !Array.isArray(r.nodes)) return null;

  return {
    query: String(r.query || ""),
    nodes: (r.nodes as unknown[]).map((n) => {
      const node = n as Record<string, unknown>;
      return {
        id: String(node.id || ""),
        label: String(node.label || "Unknown"),
        type: String(node.type || "Entity"),
        properties: node.properties as Record<string, unknown> | undefined,
      };
    }),
    relationships: ((r.relationships as unknown[]) || []).map((rel) => {
      const relationship = rel as Record<string, unknown>;
      return {
        id: String(relationship.id || ""),
        from: String(relationship.from || ""),
        to: String(relationship.to || ""),
        type: String(relationship.type || "RELATED"),
      };
    }),
    summary: (r.summary as MemoryGraphSummary) || {
      messages_found: 0,
      entities_found: 0,
      relationships_found: 0,
    },
    message: r.message as string | undefined,
  };
}
