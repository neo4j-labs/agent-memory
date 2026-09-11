import type { GraphNode, GraphPayload } from "./api";

/**
 * Which memory layer (or the compliance domain) a graph node belongs to.
 *
 * The three memory layers are the ones neo4j-agent-memory writes:
 * - short-term: `(:Conversation)`, `(:Message)`
 * - long-term: `(:Entity)` (POLE+O), `(:Preference)`, `(:Fact)`, `(:User)`
 * - reasoning: `(:ReasoningTrace)`, `(:ReasoningStep)`, `(:ToolCall)`, `(:Tool)`
 *
 * Everything else — `Customer`, `Transaction`, `Alert`, … — is this app's own
 * domain data, which the agents query but the library does not own.
 */
export type GraphCategory = "domain" | "shortTerm" | "longTerm" | "reasoning";

const SHORT_TERM_LABELS = new Set(["Conversation", "Message"]);
const LONG_TERM_LABELS = new Set([
  "Entity",
  "Preference",
  "Fact",
  "User",
  "Extractor",
]);
const REASONING_LABELS = new Set([
  "ReasoningTrace",
  "ReasoningStep",
  "ToolCall",
  "Tool",
]);

/**
 * Classify a node by its labels.
 *
 * Memory labels win over domain labels because entity nodes carry the `:Entity`
 * super-label plus PascalCase type labels that can collide with domain ones
 * (e.g. `:Entity:Organization` vs a domain `:Organization`).
 */
export function categorizeNode(labels: string[]): GraphCategory {
  if (labels.some((l) => REASONING_LABELS.has(l))) return "reasoning";
  if (labels.some((l) => SHORT_TERM_LABELS.has(l))) return "shortTerm";
  if (labels.some((l) => LONG_TERM_LABELS.has(l))) return "longTerm";
  return "domain";
}

export type CategoryCounts = Record<GraphCategory, number>;

export function countCategories(nodes: GraphNode[]): CategoryCounts {
  const counts: CategoryCounts = {
    domain: 0,
    shortTerm: 0,
    longTerm: 0,
    reasoning: 0,
  };
  for (const node of nodes) counts[categorizeNode(node.labels ?? [])] += 1;
  return counts;
}

/**
 * Keep only nodes in the enabled categories, and only relationships whose both
 * endpoints survived. Relationships with a dangling endpoint are dropped so the
 * counters in the header match what NVL actually draws.
 */
export function filterGraph(
  payload: GraphPayload,
  enabled: Record<GraphCategory, boolean>,
): GraphPayload {
  const nodes = payload.nodes.filter(
    (node) => enabled[categorizeNode(node.labels ?? [])],
  );
  const ids = new Set(nodes.map((node) => node.id));
  const relationships = payload.relationships.filter(
    (rel) => rel.from && rel.to && ids.has(rel.from) && ids.has(rel.to),
  );
  return { nodes, relationships };
}

/** Colours keyed by node label, shared by the graph and its legend. */
export const NODE_COLORS: Record<string, string> = {
  // Compliance domain
  Customer: "#68BDF6",
  Organization: "#FB95AF",
  Transaction: "#FFD86E",
  Alert: "#FF6B6B",
  SanctionedEntity: "#E74C3C",
  PEP: "#9B59B6",
  PEPRelative: "#BB8FCE",
  SanctionAlias: "#E6B0AA",
  Document: "#A5D6A7",
  Investigation: "#F39C12",
  // Short-term memory
  Conversation: "#57C7E3",
  Message: "#8DCC93",
  // Long-term memory
  Entity: "#DE9BF9",
  Preference: "#F59E0B",
  Fact: "#C990C0",
  User: "#6366F1",
  // Reasoning memory
  ReasoningTrace: "#4C8EDA",
  ReasoningStep: "#A5ABB6",
  ToolCall: "#D9C8AE",
  Tool: "#ECB5C9",
};

export function nodeColor(labels: string[]): string {
  for (const label of labels) {
    const color = NODE_COLORS[label];
    if (color) return color;
  }
  return "#95A5A6";
}
