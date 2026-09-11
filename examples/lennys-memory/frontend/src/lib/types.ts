/**
 * TypeScript types for the chat application.
 */

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  status: "pending" | "success" | "error";
  duration_ms?: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  toolCalls?: ToolCall[];
}

export interface Thread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ThreadWithMessages extends Thread {
  messages: Message[];
}

export interface Preference {
  id: string;
  category: string;
  preference: string;
  context?: string;
  confidence: number;
  created_at?: string;
}

export interface Entity {
  id: string;
  name: string;
  type: string;
  subtype?: string;
  description?: string;
  enriched_description?: string | null;
  wikipedia_url?: string | null;
  image_url?: string | null;
}

export interface RecentMessage {
  id: string;
  role: string;
  content: string;
  created_at?: string;
}

export interface MemoryContext {
  preferences: Preference[];
  entities: Entity[];
  recent_topics: string[];
  recent_messages: RecentMessage[];
}

// SSE Event Types
export interface SSETokenEvent {
  type: "token";
  content: string;
}

export interface SSEToolCallEvent {
  type: "tool_call";
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface SSEToolResultEvent {
  type: "tool_result";
  id: string;
  name: string;
  result: unknown;
  duration_ms: number;
}

export interface SSEDoneEvent {
  type: "done";
  message_id: string;
  trace_id?: string;
}

export interface SSEErrorEvent {
  type: "error";
  message: string;
}

export type SSEEvent =
  | SSETokenEvent
  | SSEToolCallEvent
  | SSEToolResultEvent
  | SSEDoneEvent
  | SSEErrorEvent;

// Graph Types for Memory Visualization
export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface MemoryGraph {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}

// Location Types for Map View
export interface ConversationRef {
  id: string;
  title: string | null;
}

export interface LocationEntity {
  id: string;
  name: string;
  subtype: string | null;
  description: string | null;
  enriched_description: string | null;
  wikipedia_url: string | null;
  latitude: number;
  longitude: number;
  conversations: ConversationRef[];
  distance_km?: number | null; // Distance from search point (for nearby queries)
}

// Quick-start suggestion shown on the landing page
export interface QuickStartSuggestion {
  id: string; // stable id (curated slug, or a thread id)
  firstMessage: string; // the query that will be sent
  category?: string; // short label describing what the query demonstrates
  timestamp?: string; // only set for suggestions derived from a real thread
}
