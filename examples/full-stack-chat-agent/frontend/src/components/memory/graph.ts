/**
 * Pure helpers for the memory-graph explorer: label resolution, memory-type
 * classification, colours, captions and Neo4j value formatting.
 *
 * Keeping these out of the React components makes them readable on their own
 * and keeps the dialog/canvas/panel files small.
 *
 * The labels handled here are the ones `neo4j-agent-memory` actually writes:
 *
 * - short-term  — `Conversation`, `Message`
 * - long-term   — `Preference`, `Entity` (+ the PascalCase POLE+O type labels
 *                 `Person`, `Object`, `Location`, `Event`, `Organization`)
 * - reasoning   — `ReasoningTrace`, `ReasoningStep`, `ToolCall`, `Tool`
 */

import type { GraphNode } from "@/lib/types";

/** The three memory layers of the library, used for the filter toggles. */
export type MemoryType = "short-term" | "long-term" | "reasoning";

export type MemoryTypeFilters = Record<MemoryType, boolean>;

export const ALL_MEMORY_TYPES: MemoryType[] = [
  "short-term",
  "long-term",
  "reasoning",
];

/**
 * Node colours, from the Neo4j Bloom/NVL palette. NVL renders to a canvas and
 * takes literal colours, so these cannot be Chakra tokens — but the chrome
 * around the canvas uses semantic tokens so dark mode works.
 */
export const NODE_COLORS: Record<string, string> = {
  // short-term
  Conversation: "#4C8EDA",
  Message: "#57C7E3",
  // reasoning
  ReasoningTrace: "#8DCC93",
  ReasoningStep: "#6FB77A",
  ToolCall: "#F79767",
  Tool: "#C990C0",
  // long-term
  Preference: "#FFC454",
  Entity: "#9B59B6",
  Person: "#DE9BF9",
  Organization: "#FB95AF",
  Location: "#68BDF6",
  Event: "#D9C8AE",
  Object: "#A5ABB6",
};

export const UNKNOWN_NODE_COLOR = "#CCCCCC";

export const MEMORY_TYPE_META: Record<
  MemoryType,
  { label: string; color: string; palette: string; legend: string[] }
> = {
  "short-term": {
    label: "Short-Term",
    color: NODE_COLORS.Conversation,
    palette: "blue",
    legend: ["Conversation", "Message"],
  },
  "long-term": {
    label: "Long-Term",
    color: NODE_COLORS.Preference,
    palette: "yellow",
    legend: ["Preference", "Entity"],
  },
  reasoning: {
    label: "Reasoning",
    color: NODE_COLORS.ReasoningTrace,
    palette: "green",
    legend: ["ReasoningTrace", "ReasoningStep", "ToolCall", "Tool"],
  },
};

/** Most specific label first, so `:Entity:Person` resolves to `Person`. */
const LABEL_PRIORITY: string[] = [
  "Conversation",
  "Message",
  "ReasoningTrace",
  "ReasoningStep",
  "ToolCall",
  "Tool",
  "Preference",
  "Person",
  "Organization",
  "Location",
  "Event",
  "Object",
  "Entity",
];

const MEMORY_TYPE_BY_LABEL: Record<string, MemoryType> = {
  Conversation: "short-term",
  Message: "short-term",
  ReasoningTrace: "reasoning",
  ReasoningStep: "reasoning",
  ToolCall: "reasoning",
  Tool: "reasoning",
  Preference: "long-term",
  Person: "long-term",
  Organization: "long-term",
  Location: "long-term",
  Event: "long-term",
  Object: "long-term",
  Entity: "long-term",
};

/** The primary (most specific) label of a node. */
export function getNodeLabel(node: GraphNode): string {
  for (const label of LABEL_PRIORITY) {
    if (node.labels.includes(label)) return label;
  }
  return node.labels[0] || "Unknown";
}

export function getNodeColor(node: GraphNode): string {
  return NODE_COLORS[getNodeLabel(node)] ?? UNKNOWN_NODE_COLOR;
}

export function getNodeMemoryType(node: GraphNode): MemoryType {
  return MEMORY_TYPE_BY_LABEL[getNodeLabel(node)] ?? "short-term";
}

/** Relative node sizes, so the hubs of each layer read as hubs. */
export function getNodeSize(node: GraphNode): number {
  switch (getNodeLabel(node)) {
    case "Conversation":
      return 25;
    case "Tool":
      return 22;
    case "ReasoningTrace":
      return 21;
    case "ReasoningStep":
      return 19;
    case "Message":
      return 18;
    case "ToolCall":
      return 14;
    case "Preference":
      return 16;
    case "Entity":
    case "Person":
    case "Organization":
    case "Location":
    case "Event":
    case "Object":
      return 18;
    default:
      return 15;
  }
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

/** A short human-readable caption for the canvas. */
export function getNodeCaption(node: GraphNode): string {
  const props = node.properties;
  const str = (key: string): string | undefined => {
    const value = props[key];
    return typeof value === "string" && value ? value : undefined;
  };

  switch (getNodeLabel(node)) {
    case "Conversation": {
      const title = str("title");
      if (title) return truncate(title, 30);
      const sessionId = str("session_id");
      if (sessionId) return `Session: ${truncate(sessionId, 20)}`;
      return "Conversation";
    }
    case "Message": {
      const role = str("role") ?? "unknown";
      const text = str("content") ?? str("text") ?? "";
      return `${role}: ${truncate(text, 30)}`;
    }
    case "ReasoningTrace": {
      const task = str("task");
      return task ? `Task: ${truncate(task, 25)}` : "ReasoningTrace";
    }
    case "ReasoningStep": {
      const thought = str("thought") ?? str("reasoning_text");
      const step = props.step_number ?? "?";
      return thought
        ? `Step ${String(step)}: ${truncate(thought, 25)}`
        : `Step ${String(step)}`;
    }
    case "ToolCall":
      return str("tool_name") ?? "Tool Call";
    case "Tool":
      return str("name") ?? "Tool";
    case "Preference":
      return truncate(str("preference") ?? "Preference", 35);
    default:
      return str("name") ?? str("title") ?? getNodeLabel(node);
  }
}

function neo4jIntToNumber(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (typeof value === "object" && value !== null && "low" in value) {
    const low = (value as { low?: unknown }).low;
    if (typeof low === "number") return low;
  }
  return undefined;
}

/**
 * Render a Neo4j property for the detail panel.
 *
 * Handles the two shapes the HTTP layer can produce for temporals: a
 * serialised `neo4j.time.DateTime` object (`{year, month, day, ...}`, whose
 * fields may themselves be `{low, high}` integers) and an ISO-8601 string.
 */
export function formatPropertyValue(value: unknown): string {
  if (value === null || value === undefined) return "null";

  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if ("year" in obj && "month" in obj && "day" in obj) {
      const year = neo4jIntToNumber(obj.year);
      const month = neo4jIntToNumber(obj.month);
      const day = neo4jIntToNumber(obj.day);
      if (year !== undefined && month !== undefined && day !== undefined) {
        const pad = (n: number | undefined) => String(n ?? 0).padStart(2, "0");
        return (
          `${year}-${pad(month)}-${pad(day)} ` +
          `${pad(neo4jIntToNumber(obj.hour))}:` +
          `${pad(neo4jIntToNumber(obj.minute))}:` +
          `${pad(neo4jIntToNumber(obj.second))}`
        );
      }
    }
    return JSON.stringify(value, null, 2);
  }

  if (typeof value === "string") {
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value)) {
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) {
        return date.toLocaleString("en-US", {
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        });
      }
    }
  }

  return String(value);
}
