/**
 * API client for Google Cloud Financial Advisor backend.
 */

import {
  createSseParserState,
  flushSseParser,
  pushSseChunk,
  type SseFrame,
} from "./sse";

/**
 * Base URL for the backend API.
 *
 * Defaults to the relative `/api` path, which works with the Vite dev proxy
 * (see `vite.config.ts`) and with the nginx reverse proxy in the container
 * image. Set `VITE_API_BASE_URL` at build time (see `.env.example`) to point a
 * standalone SPA deployment at a remote backend, e.g.
 * `VITE_API_BASE_URL=https://financial-advisor-backend-xxxx.run.app/api`.
 */
export const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

export interface Customer {
  id: string;
  name: string;
  type: "individual" | "corporate";
  email?: string;
  phone?: string;
  nationality?: string;
  address?: string;
  occupation?: string;
  employer?: string;
  jurisdiction?: string;
  business_type?: string;
  kyc_status: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  risk_score: number;
  risk_factors: string[];
}

export interface Alert {
  id: string;
  customer_id: string;
  customer_name?: string;
  type: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  status: string;
  title: string;
  description: string;
  transaction_id?: string;
  evidence: string[];
  requires_sar: boolean;
  created_at: string;
}

export interface AlertSummary {
  total: number;
  by_severity: Record<string, number>;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  critical_unresolved: number;
  high_unresolved: number;
}

export interface Investigation {
  id: string;
  customer_id: string;
  type: string;
  reason: string;
  status: string;
  priority: string;
  overall_risk_level?: string;
  risk_score?: number;
  summary?: string;
  recommendations: string[];
  agents_consulted: string[];
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

// Customer API
export async function getCustomers(): Promise<Customer[]> {
  const res = await fetch(`${API_BASE}/customers`);
  if (!res.ok) throw new Error("Failed to fetch customers");
  return res.json();
}

// Alert API
export async function getAlerts(params?: {
  status?: string;
  severity?: string;
  customer_id?: string;
}): Promise<Alert[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.severity) searchParams.set("severity", params.severity);
  if (params?.customer_id) searchParams.set("customer_id", params.customer_id);

  const url = `${API_BASE}/alerts${searchParams.toString() ? "?" + searchParams : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch alerts");
  return res.json();
}

export async function getAlertSummary(): Promise<AlertSummary> {
  const res = await fetch(`${API_BASE}/alerts/summary`);
  if (!res.ok) throw new Error("Failed to fetch alert summary");
  return res.json();
}

export async function updateAlert(
  id: string,
  update: {
    status?: string;
    severity?: string;
    notes?: string;
  },
): Promise<Alert> {
  const res = await fetch(`${API_BASE}/alerts/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error("Failed to update alert");
  return res.json();
}

// Investigation API
export async function getInvestigations(params?: {
  status?: string;
  customer_id?: string;
}): Promise<Investigation[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.customer_id) searchParams.set("customer_id", params.customer_id);

  const url = `${API_BASE}/investigations${searchParams.toString() ? "?" + searchParams : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch investigations");
  return res.json();
}

export async function createInvestigation(data: {
  customer_id: string;
  type?: string;
  reason: string;
  priority?: string;
}): Promise<Investigation> {
  const res = await fetch(`${API_BASE}/investigations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create investigation");
  return res.json();
}

export async function startInvestigation(id: string): Promise<{
  investigation_id: string;
  status: string;
  overall_risk_level: string;
  summary: string;
  agents_consulted: string[];
  duration_seconds: number;
}> {
  const res = await fetch(`${API_BASE}/investigations/${id}/start`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to start investigation");
  return res.json();
}

export async function getAuditTrail(investigationId: string): Promise<
  Array<{
    timestamp: string;
    action: string;
    agent?: string;
    details?: string;
    tool_used?: string;
  }>
> {
  const res = await fetch(
    `${API_BASE}/investigations/${investigationId}/audit-trail`,
  );
  if (!res.ok) throw new Error("Failed to fetch audit trail");
  return res.json();
}

// Chat API

export interface StoredChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp?: string;
}

/**
 * Replay a conversation that is already stored in Neo4j.
 *
 * The backend writes every turn through `MemoryClient.short_term`, so reopening
 * the app (or hitting reload) can restore the conversation from the graph
 * instead of losing it with the browser tab.
 */
export async function getChatHistory(
  sessionId: string,
  limit = 50,
): Promise<{ session_id: string; messages: StoredChatMessage[] }> {
  const res = await fetch(
    `${API_BASE}/chat/history/${encodeURIComponent(sessionId)}?limit=${limit}`,
  );
  if (!res.ok) throw new Error("Failed to fetch chat history");
  return res.json();
}

// SSE Streaming Chat API

export type AgentEvent =
  | { type: "agent_start"; agent: string; timestamp: number }
  | { type: "agent_delegate"; from: string; to: string; timestamp: number }
  | { type: "agent_complete"; agent: string; timestamp: number }
  | { type: "thinking"; agent: string; thought: string; timestamp: number }
  | {
      type: "tool_call";
      agent: string;
      tool: string;
      args: Record<string, unknown>;
      timestamp: number;
    }
  | {
      type: "tool_result";
      agent: string;
      tool: string;
      result: unknown;
      timestamp: number;
    }
  | {
      type: "memory_access";
      agent: string;
      operation: "search" | "store";
      tool: string;
      query?: string;
      timestamp: number;
    }
  | { type: "response"; content: string; session_id: string }
  | {
      type: "trace_saved";
      trace_id: string;
      step_count: number;
      tool_call_count: number;
    }
  | {
      type: "done";
      session_id: string;
      agents_consulted: string[];
      tool_call_count: number;
      total_duration_ms: number;
      trace_id?: string;
    }
  | { type: "error"; message: string };

/** Known agent event names, used to skip frames we do not model. */
const AGENT_EVENT_TYPES = new Set<AgentEvent["type"]>([
  "agent_start",
  "agent_delegate",
  "agent_complete",
  "thinking",
  "tool_call",
  "tool_result",
  "memory_access",
  "response",
  "trace_saved",
  "done",
  "error",
]);

/** Turn one SSE frame into a typed `AgentEvent`, or null if unrecognised. */
export function toAgentEvent(frame: SseFrame): AgentEvent | null {
  if (!AGENT_EVENT_TYPES.has(frame.event as AgentEvent["type"])) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;
  return { type: frame.event, ...payload } as AgentEvent;
}

export async function streamChatMessage(
  data: {
    message: string;
    session_id?: string;
    customer_id?: string;
    investigation_id?: string;
  },
  onEvent: (event: AgentEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Failed to start stream");
  if (!res.body) throw new Error("Stream response had no body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParserState();

  const dispatch = (frames: SseFrame[]) => {
    for (const frame of frames) {
      const event = toAgentEvent(frame);
      if (event) onEvent(event);
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      dispatch(pushSseChunk(parser, decoder.decode(value, { stream: true })));
    }
    // Flush any multi-byte character left in the decoder, then any event that
    // was not terminated by a blank line before the stream ended.
    dispatch(pushSseChunk(parser, decoder.decode()));
    dispatch(flushSseParser(parser));
  } finally {
    reader.releaseLock();
  }
}

// Reasoning Traces API

export interface TraceToolCall {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: unknown;
  status: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface TraceStep {
  id: string;
  step_number: number;
  thought: string | null;
  action: string | null;
  observation: string | null;
  tool_calls: TraceToolCall[];
}

export interface ReasoningTrace {
  id: string;
  session_id: string;
  task: string;
  outcome: string | null;
  success: boolean | null;
  started_at: string | null;
  completed_at: string | null;
  steps: TraceStep[];
}

export async function getSessionTraces(
  sessionId: string,
): Promise<ReasoningTrace[]> {
  const res = await fetch(`${API_BASE}/traces/${sessionId}`);
  if (!res.ok) throw new Error("Failed to fetch traces");
  return res.json();
}

export async function getTraceDetail(traceId: string): Promise<ReasoningTrace> {
  const res = await fetch(`${API_BASE}/traces/detail/${traceId}`);
  if (!res.ok) throw new Error("Failed to fetch trace");
  return res.json();
}


// Graph API

export interface GraphNode {
  id: string;
  /** Neo4j element id — stable even when a node has no `id` property. */
  element_id?: string;
  label: string | null;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
}

export interface GraphPayload {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}

/**
 * Fetch the Context Graph for visualization.
 *
 * `sessionId` scopes the graph to one conversation so the view shows the memory
 * a chat turn just produced (`GET /api/graph/memory?session_id=...`).
 */
export async function getMemoryGraph(params?: {
  sessionId?: string;
  limit?: number;
}): Promise<GraphPayload> {
  const searchParams = new URLSearchParams();
  searchParams.set("limit", String(params?.limit ?? 500));
  if (params?.sessionId) searchParams.set("session_id", params.sessionId);

  const res = await fetch(`${API_BASE}/graph/memory?${searchParams}`);
  if (!res.ok) throw new Error("Failed to load graph");
  return res.json();
}

export interface NeighborsPayload {
  entity_id: string;
  depth: number;
  nodes: Array<{
    id: string;
    label: string | null;
    type: string;
    isRoot: boolean;
  }>;
  edges: Array<{ from: string; to: string; relationship: string }>;
  total_nodes: number;
  total_edges: number;
}

export async function getGraphNeighbors(
  nodeId: string,
  params?: { depth?: number; limit?: number },
): Promise<NeighborsPayload> {
  const searchParams = new URLSearchParams({
    depth: String(params?.depth ?? 1),
    limit: String(params?.limit ?? 20),
  });
  const res = await fetch(
    `${API_BASE}/graph/neighbors/${encodeURIComponent(nodeId)}?${searchParams}`,
  );
  if (!res.ok) throw new Error("Failed to expand node");
  return res.json();
}

/** One reasoning step that touched an entity, via `(:ReasoningStep)-[:TOUCHED]->(:Entity)`. */
export interface AuditTrailStep {
  entity_name: string;
  trace_id: string | null;
  session_id: string | null;
  task: string | null;
  outcome: string | null;
  step_id: string | null;
  step_number: number | null;
  thought: string | null;
  action: string | null;
  tools: string[];
}

/**
 * "Which reasoning steps touched this entity, and via which tool?" — the
 * one-hop audit query that `record_tool_call(touched_entities=…)` makes
 * possible, and the proof point a compliance reviewer actually needs.
 */
export async function getEntityAuditTrail(
  entityName: string,
  limit = 50,
): Promise<{
  entity_name: string;
  steps: AuditTrailStep[];
  total: number;
}> {
  const res = await fetch(
    `${API_BASE}/graph/audit-trail/${encodeURIComponent(entityName)}?limit=${limit}`,
  );
  if (!res.ok) throw new Error("Failed to load audit trail");
  return res.json();
}
