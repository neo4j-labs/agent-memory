/**
 * Graph styling: one place that decides how a contract node/relationship is
 * coloured and sized, used by both the canvas and the legend.
 */

import { labelColor, primaryLabel } from "@/lib/labels";
import type { GraphNode, GraphRelationship } from "@/lib/types";

export const MENTIONS_COLOR = "#A5ABB6";
export const SAME_AS_COLOR = "#F79767";
export const RELATED_COLOR = "#6366F1";
export const STRUCTURE_COLOR = "#C4C9D2";

/** Edges that are memory structure rather than domain facts. */
const STRUCTURAL_TYPES = new Set([
  "HAS_MESSAGE",
  "FIRST_MESSAGE",
  "NEXT_MESSAGE",
]);

/** The label a node is coloured and listed by. */
export function nodeLegendLabel(node: GraphNode): string {
  if (node.kind === "conversation") return "Conversation";
  if (node.kind === "message") return "Message";
  return primaryLabel(node.labels);
}

export function nodeColor(node: GraphNode): string {
  return labelColor(nodeLegendLabel(node));
}

/**
 * Node diameters. NVL sizes a caption to its node's on-screen radius and drops
 * it when the node renders too small, so entities are large enough to stay
 * labelled once a conversation's graph is fitted to the panel.
 */
export function nodeSize(node: GraphNode): number {
  switch (node.kind) {
    case "conversation":
      return 56;
    case "message":
      return 26;
    default:
      return 48;
  }
}

const CONVERSATION_CAPTION = 22;

/**
 * What the canvas writes on a node. NVL hides a caption that does not fit,
 * so a message shows its role (`user`, `assistant`) and a conversation a
 * shortened title; the property panel keeps the full text.
 */
export function nodeCaption(node: GraphNode): string {
  if (node.kind === "message") {
    const role = node.caption.split(":", 1)[0].trim();
    return role === "user" || role === "assistant" ? role : "message";
  }
  if (node.kind === "conversation" && node.caption.length > CONVERSATION_CAPTION) {
    return `${node.caption.slice(0, CONVERSATION_CAPTION - 1).trimEnd()}…`;
  }
  return node.caption;
}

export type EdgeKind = "mentions" | "same_as" | "structure" | "related";

export function edgeKind(rel: GraphRelationship): EdgeKind {
  if (rel.type === "MENTIONS") return "mentions";
  if (rel.type === "SAME_AS") return "same_as";
  if (STRUCTURAL_TYPES.has(rel.type)) return "structure";
  return "related";
}

export const EDGE_STYLE: Record<
  EdgeKind,
  { color: string; width: number; label: string }
> = {
  related: { color: RELATED_COLOR, width: 2, label: "Typed RELATED_TO" },
  mentions: { color: MENTIONS_COLOR, width: 1, label: "MENTIONS" },
  same_as: { color: SAME_AS_COLOR, width: 2, label: "SAME_AS (review)" },
  structure: { color: STRUCTURE_COLOR, width: 1, label: "Conversation structure" },
};

/** `r.type` for RELATED_TO edges is the caption the backend supplies. */
export function edgeCaption(rel: GraphRelationship): string {
  return rel.caption || rel.type;
}
