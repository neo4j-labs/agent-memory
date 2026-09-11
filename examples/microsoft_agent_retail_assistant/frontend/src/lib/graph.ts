/**
 * Pure translation from the backend's `/memory/graph` payload to what the
 * force-graph component wants. Kept out of the component so it can be tested
 * without a DOM.
 */

import type { GraphData } from "./api";

/** Node colours by memory layer, so the three layers stay visually distinct. */
export const TYPE_COLORS: Record<string, string> = {
  // short-term
  Message: "#38a169",
  Conversation: "#2f855a",
  // long-term
  Entity: "#dd6b20",
  Product: "#3182ce",
  Category: "#319795",
  Brand: "#805ad5",
  Preference: "#d69e2e",
  // reasoning
  ReasoningTrace: "#9f7aea",
  ReasoningStep: "#b794f4",
  ToolCall: "#667eea",
  Tool: "#7f9cf5",
  // identity
  User: "#e53e3e",
};

export const FALLBACK_COLOR = "#a0aec0";

export interface VizNode {
  id: string;
  label: string;
  type: string;
  color: string;
}

export interface VizLink {
  source: string;
  target: string;
  type: string;
}

export interface VizGraph {
  nodes: VizNode[];
  links: VizLink[];
}

/**
 * Map nodes and drop dangling edges.
 *
 * The backend caps nodes per memory type, so an edge can legitimately point at
 * a node that did not make the cut; force-graph would otherwise invent a node
 * for the missing endpoint.
 */
export function toVizGraph(data: GraphData): VizGraph {
  const nodes: VizNode[] = data.nodes.map((node) => ({
    id: node.id,
    label: node.label,
    type: node.type,
    color: TYPE_COLORS[node.type] ?? FALLBACK_COLOR,
  }));

  const ids = new Set(nodes.map((n) => n.id));
  const links: VizLink[] = data.edges
    .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      type: edge.type,
    }));

  return { nodes, links };
}
