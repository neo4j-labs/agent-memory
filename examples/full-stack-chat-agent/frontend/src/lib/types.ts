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

/**
 * `GET /threads/{id}`. The backend's `Thread` model omits `message_count`
 * (that lives on the list payload only), so this does not extend `Thread`.
 */
export interface ThreadWithMessages {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: Message[];
}

/** Raw `ChatMessage` as the backend serialises it (snake_case tool calls). */
export interface ApiChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  tool_calls?: ToolCall[];
}

/** Raw `GET /threads/{id}` payload, before `toMessage()` normalisation. */
export interface ApiThreadWithMessages
  extends Omit<ThreadWithMessages, "messages"> {
  messages: ApiChatMessage[];
}

/**
 * `GET /health` on the backend root. `memory_connected: false` means the
 * agent is running without the memory graph — usually a Neo4j password
 * mismatch between `docker-compose.yml` and `.env`.
 */
export interface Health {
  status: string;
  memory_connected: boolean;
  memory_error: string | null;
  news_connected: boolean;
  news_error: string | null;
  extraction_mode?: string;
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
