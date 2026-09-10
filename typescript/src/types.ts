/**
 * Core type definitions for neo4j-agent-memory TypeScript client.
 *
 * Canonical types use camelCase. Wire format depends on transport:
 *   - BridgeTransport: snake_case JSON
 *   - RestTransport: camelCase JSON (matches the hosted service)
 *
 * Sub-clients translate between wire and canonical forms.
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type MessageRole = "user" | "assistant" | "system";

export type ToolCallStatus =
  | "pending"
  | "success"
  | "failure"
  | "error"
  | "timeout"
  | "cancelled";

// ---------------------------------------------------------------------------
// Short-Term Memory Types
// ---------------------------------------------------------------------------

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  embedding?: number[];
  metadata: Record<string, unknown>;
  /** Hosted service: link back to the conversation. */
  conversationId?: string;
}

export interface Conversation {
  id: string;
  /** Bridge protocol session identifier (free-form string). */
  sessionId: string;
  messages: Message[];
  /** Hosted service: message count when returned by list/metadata endpoints. */
  messageCount?: number;
  title?: string;
  createdAt: string;
  updatedAt?: string;
  /** Hosted service: workspace owning this conversation. */
  workspaceId?: string;
  /** Hosted service: user the conversation belongs to. */
  userId?: string;
  metadata?: Record<string, unknown>;
}

export interface SessionInfo {
  sessionId: string;
  messageCount: number;
  createdAt: string;
  updatedAt?: string;
}

/** A 3-tier conversational context window — hosted service. */
export interface ConversationContext {
  reflections: Reflection[];
  observations: Observation[];
  recentMessages: Message[];
}

export interface Observation {
  id: string;
  conversationId: string;
  content: string;
  windowStart?: string;
  windowEnd?: string;
  createdAt: string;
}

export interface Reflection {
  id: string;
  conversationId: string;
  content: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Long-Term Memory Types
// ---------------------------------------------------------------------------

/** Bridge taxonomy. */
export type EntityType = "PERSON" | "ORGANIZATION" | "LOCATION" | "EVENT" | "OBJECT";

/** Hosted-service entity taxonomy. */
export type HostedEntityType =
  | "person"
  | "organization"
  | "location"
  | "concept"
  | "tool"
  | "custom";

export interface Entity {
  id: string;
  name: string;
  type: string;
  subtype?: string;
  description?: string;
  embedding?: number[];
  canonicalName?: string;
  createdAt: string;
  /** Hosted service. */
  updatedAt?: string;
  /** Hosted service: confidence score (0-1). */
  confidence?: number;
  /** Hosted service: which extraction stage produced the entity. */
  sourceStage?: string;
  /** Hosted service: relationships referenced by getEntity. */
  relationships?: EntityRelationshipRef[];
  /** Entity metadata; hosted merge provenance is stored under `nams_resolution`. */
  metadata?: Record<string, unknown>;
}

export interface EntityRelationshipRef {
  id: string;
  type: string;
  targetId: string;
  targetName?: string;
  properties?: Record<string, unknown>;
}

export interface EntityHistory {
  entityId: string;
  mentions: EntityMention[];
}

export interface EntityMention {
  conversationId: string;
  messageId?: string;
  content: string;
  timestamp: string;
}

export interface EntityGraphNode {
  id: string;
  name: string;
  type: string;
}

export interface EntityGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface EntityGraph {
  nodes: EntityGraphNode[];
  edges: EntityGraphEdge[];
}

/** A 1-hop graph fragment from `longTerm.expandGraph` — viz-oriented shape. */
export interface ExpandedGraph {
  nodes: Array<{ id: string; labels?: string[]; properties?: Record<string, unknown> }>;
  edges: Array<Record<string, unknown>>;
}

export interface EntityFeedbackResult {
  id: string;
  updated: boolean;
}

export interface EntityMergeResult {
  sourceId: string;
  targetId: string;
  status: string;
}

export interface Preference {
  id: string;
  category: string;
  preference: string;
  context?: string;
  embedding?: number[];
}

export interface Fact {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  embedding?: number[];
}

export interface Relationship {
  id: string;
  sourceId: string;
  targetId: string;
  relationshipType: string;
  properties: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Reasoning Memory Types
// ---------------------------------------------------------------------------

export interface ReasoningTrace {
  id: string;
  sessionId: string;
  task: string;
  steps: ReasoningStep[];
  outcome?: string;
  success?: boolean;
  startedAt: string;
  completedAt?: string;
}

export interface ReasoningStep {
  id: string;
  traceId: string;
  stepNumber: number;
  thought?: string;
  action?: string;
  observation?: string;
  toolCalls: ToolCall[];
}

export interface ToolCall {
  id: string;
  /** Reasoning step this tool call hangs off (hosted service exposes this). */
  stepId?: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  status: ToolCallStatus;
  durationMs?: number;
  error?: string;
}

export interface ToolStats {
  name: string;
  totalCalls: number;
  successfulCalls: number;
  failedCalls: number;
  successRate: number;
  avgDurationMs?: number;
}

/** Hosted-service flat reasoning step (per-conversation, no trace wrapper). */
export interface AgentStep {
  id: string;
  conversationId: string;
  reasoning: string;
  actionTaken: string;
  result?: string;
  createdAt: string;
}

/** Hosted: detailed step with tool calls + influenced entities. */
export interface AgentStepExplanation extends AgentStep {
  toolCalls: ToolCall[];
  influencedEntities: Entity[];
}

/** Hosted: flat reasoning trace (steps + tool calls under one conversation). */
export interface ConversationTrace {
  conversationId: string;
  steps: AgentStep[];
  toolCalls: ToolCall[];
}

/** Hosted: provenance of an entity's creation. */
export interface EntityProvenance {
  entityId: string;
  steps: AgentStep[];
}

// ---------------------------------------------------------------------------
// Query Console
// ---------------------------------------------------------------------------

export interface CypherResult {
  columns: string[];
  rows: unknown[][];
  stats?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Auth / API Keys (hosted service)
// ---------------------------------------------------------------------------

export interface ApiKey {
  id: string;
  label: string;
  scopes: string[];
  workspaceId: string;
  createdAt: string;
  expiresAt?: string;
  /** Plaintext key — only present at creation time. */
  key?: string;
}

export interface AccessTokenPair {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

// ---------------------------------------------------------------------------
// Client Configuration
// ---------------------------------------------------------------------------

export type TransportMode = "auto" | "bridge" | "rest";

export interface MemoryClientOptions {
  /** Service base URL (bridge endpoint, or `https://.../v1` for REST). */
  endpoint?: string;

  /** API key for authentication. */
  apiKey?: string;

  /**
   * NAMS workspace id. When set, transmitted as the `X-Workspace-Id` header on
   * every request — required by deployments that scope by header (e.g. the
   * development/staging service) rather than encoding the workspace in the API
   * key (production). Optional and harmless when unset. Falls back to the
   * `MEMORY_WORKSPACE_ID` environment variable.
   */
  workspaceId?: string;

  /** Override transport selection. Default: "auto" (REST if endpoint contains /v1). */
  transport?: TransportMode;

  /** OAuth refresh-token-aware token provider. Overrides apiKey when supplied. */
  tokenProvider?: () => string | Promise<string>;

  /** Shared entity namespace for multi-agent collaboration. */
  namespace?: string;

  /** Request timeout in milliseconds. Default: 30000. */
  timeout?: number;

  /** Additional headers to include in every request. */
  headers?: Record<string, string>;

  /**
   * Optional logger invoked once per request / response / error. Useful for
   * tracing requests in development. Caller controls log level by ignoring
   * unwanted event kinds. See {@link LogEvent}.
   */
  logger?: import("./observability.js").Logger;
}

// ---------------------------------------------------------------------------
// Operation Options
// ---------------------------------------------------------------------------

export interface AddMessageOptions {
  metadata?: Record<string, unknown>;
  /** Alias for the positional `sessionId` (NAMS is conversation-scoped). */
  conversationId?: string;
}

export interface GetConversationOptions {
  limit?: number;
  /** Alias for the positional `sessionId`. */
  conversationId?: string;
}

export interface SearchMessagesOptions {
  sessionId?: string;
  /** Alias for `sessionId` (NAMS is conversation-scoped). `sessionId` wins. */
  conversationId?: string;
  limit?: number;
  threshold?: number;
}

export interface ClearSessionOptions {
  /** Alias for the positional `sessionId`. */
  conversationId?: string;
}

export interface ListSessionsOptions {
  limit?: number;
}

export interface SearchEntitiesOptions {
  limit?: number;
  type?: string;
}

export interface WaitForExtractionOptions {
  /** Search string to poll. Defaults to the first of `expectedNames`. */
  query?: string;
  /** Succeed once every name appears in results (case-insensitive). */
  expectedNames?: string[];
  /** Succeed once at least this many entities match. Default 1. */
  minResults?: number;
  /** Custom predicate over the current results. */
  predicate?: (entities: Entity[]) => boolean;
  /** Max entities to fetch per poll. */
  limit?: number;
  /** Overall timeout in milliseconds. Default 30000. */
  timeoutMs?: number;
  /** Poll interval in milliseconds. Default 1000. */
  intervalMs?: number;
}

export interface SearchPreferencesOptions {
  category?: string;
  limit?: number;
}

export interface GetRelatedEntitiesOptions {
  relationshipType?: string;
  depth?: number;
}

export interface ListTracesOptions {
  sessionId?: string;
  limit?: number;
}

export interface RecordToolCallOptions {
  result?: unknown;
  status?: ToolCallStatus;
  durationMs?: number;
  error?: string;
}

export interface CompleteTraceOptions {
  outcome?: string;
  success?: boolean;
}

export interface AddRelationshipOptions {
  properties?: Record<string, unknown>;
}

export interface GetSimilarTracesOptions {
  limit?: number;
  successOnly?: boolean;
}

// Hosted-service options ----------------------------------------------------

export interface CreateConversationOptions {
  userId: string;
  metadata?: Record<string, unknown>;
}

export interface ListConversationsOptions {
  limit?: number;
  userId?: string;
}

export interface BulkMessageInput {
  role: MessageRole;
  content: string;
  metadata?: Record<string, unknown>;
}

export interface ListEntitiesOptions {
  type?: HostedEntityType | string;
  limit?: number;
}

export interface UpdateEntityOptions {
  name?: string;
  description?: string;
}

export interface SetEntityFeedbackOptions {
  userScore: number;
  confirmed: boolean;
}

export interface RecordStepInput {
  conversationId: string;
  reasoning: string;
  actionTaken: string;
  result?: string;
}

export interface CreateApiKeyInput {
  label: string;
  scopes: string[];
  workspaceId: string;
}
