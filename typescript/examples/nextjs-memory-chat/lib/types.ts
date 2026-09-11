/**
 * DTOs exchanged between the memory rail route handlers and the client
 * components. Kept in one file so a rename breaks both sides at `tsc` time
 * instead of at runtime.
 */

/** `shortTerm.getContext` flattened for the rail. */
export interface ContextPayload {
  conversationId: string;
  reflections: Array<{ id: string; content: string }>;
  observations: Array<{ id: string; content: string }>;
  recentMessages: Array<{ id: string; role: string; content: string }>;
}

/** A node in the rail's graph view. Shape is shared by the full and expand reads. */
export interface GraphNodePayload {
  id: string;
  name: string;
  type: string;
}

export interface GraphEdgePayload {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphPayload {
  nodes: GraphNodePayload[];
  edges: GraphEdgePayload[];
}

/** Answer from `POST /api/memory/extraction`. */
export interface ExtractionPayload {
  /** True when a *new* entity (one not in `knownIds`) appeared before the deadline. */
  settled: boolean;
  /** Entity names matching the probe query, newest extraction included. */
  entities: GraphNodePayload[];
}

/** Answer from `GET /api/memory/trace`. */
export interface TracePayload {
  conversationId: string;
  steps: Array<{
    id: string;
    reasoning: string;
    actionTaken: string;
    result?: string;
    createdAt: string;
  }>;
  toolCalls: Array<{ id: string; toolName: string; status: string }>;
}

/** Answer from `POST /api/conversation`. */
export interface ConversationPayload {
  id: string;
  userId?: string;
}
