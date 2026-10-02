/**
 * The backend's HTTP contract, mirrored one-to-one.
 *
 * Every interface here is a response (or request) body of an endpoint under
 * `/api`. The field names are the wire names (snake_case) on purpose: the UI
 * reads these objects directly, so a renamed field shows up as a type error
 * here rather than as an `undefined` somewhere in a panel. UI-only shapes
 * (chat state, tool-call progress) live next to the hooks that own them.
 */

// --- shared ----------------------------------------------------------------

/** A POLE+O type in upper case, e.g. `PERSON`, `EVENT`, `OBJECT`. */
export type PoleType = string;

/**
 * An entity a tool read (a TOUCHED audit edge). `labels` are the node's labels
 * without `Entity`, ontology label first; `type` is the POLE+O type, a fallback
 * when the labels could not be looked up.
 */
export interface TouchedRef {
  id: string;
  name: string;
  type: PoleType;
  labels: string[];
}

/** A resolved touched entity on a recorded tool call. */
export interface TouchedEntity {
  id: string;
  name: string;
  labels: string[];
}

export type MessageRole = "user" | "assistant" | "system";

// --- GET /health ------------------------------------------------------------

export interface HealthOntology {
  domain_id: string | null;
  revision: number | null;
  validation_mode: string | null;
}

export interface Health {
  status: string;
  neo4j: boolean;
  /** Any PydanticAI model string; `test` is the keyless TestModel. */
  agent_model: string;
  ontology: HealthOntology;
}

// --- /threads ---------------------------------------------------------------

/** `GET /threads` item and `POST /threads` response. */
export interface ThreadSummary {
  id: string;
  title: string;
  message_count: number;
  updated_at: string | null;
  seeded: boolean;
}

export interface CreateThreadRequest {
  title?: string;
}

export interface ThreadMessage {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string | null;
  /** Set on user messages that initiated a reasoning trace. */
  trace_id: string | null;
  /** User messages: the tool calls of the trace they initiated. */
  tool_calls: TraceToolCall[];
}

/** `GET /threads/{id}`. */
export interface ThreadDetail {
  id: string;
  title: string;
  seeded: boolean;
  messages: ThreadMessage[];
}

// --- POST /chat (SSE) -------------------------------------------------------

export interface ChatRequest {
  thread_id: string;
  message: string;
}

/** An entity extracted from the user's message as it was stored. */
export interface StoredEntity {
  name: string;
  type: PoleType;
  subtype: string | null;
  labels: string[];
}

export interface MessageStoredEvent {
  type: "message_stored";
  message_id: string;
  entities: StoredEntity[];
}

export interface TraceStartedEvent {
  type: "trace_started";
  trace_id: string;
}

export interface TokenEvent {
  type: "token";
  content: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResultEvent {
  type: "tool_result";
  id: string;
  name: string;
  result: unknown;
  duration_ms: number;
  touched: TouchedRef[];
}

export interface DoneEvent {
  type: "done";
  message_id: string;
  trace_id: string | null;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type ChatEvent =
  | MessageStoredEvent
  | TraceStartedEvent
  | TokenEvent
  | ToolCallEvent
  | ToolResultEvent
  | DoneEvent
  | ErrorEvent;

// --- /memory ----------------------------------------------------------------

/** An entity mentioned in the thread. `labels` excludes `Entity`. */
export interface MemoryEntity {
  id: string;
  name: string;
  type: PoleType;
  subtype: string | null;
  labels: string[];
  aliases: string[];
  mentions: number;
}

export interface DuplicateEndpoint {
  id: string;
  name: string;
  labels: string[];
}

/** A pending `SAME_AS` pair awaiting human review. */
export interface PendingDuplicate {
  source: DuplicateEndpoint;
  target: DuplicateEndpoint;
  confidence: number;
  match_type: string | null;
}

/** `GET /memory/context?thread_id=`. */
export interface MemoryContext {
  entities: MemoryEntity[];
  pending_duplicates: PendingDuplicate[];
}

export interface ReviewDuplicateRequest {
  source_id: string;
  target_id: string;
  confirm: boolean;
}

export interface OkResponse {
  ok: boolean;
}

// --- /graph -----------------------------------------------------------------

export type GraphNodeKind = "entity" | "message" | "conversation";

export interface GraphNode {
  id: string;
  caption: string;
  labels: string[];
  kind: GraphNodeKind;
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
  caption: string;
  properties: Record<string, unknown>;
}

/** `GET /graph?thread_id=` and `GET /graph/neighbors/{node_id}`. */
export interface GraphData {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}

// --- /ontology --------------------------------------------------------------

export interface OntologyEntityType {
  label: string;
  pole_type: PoleType;
  subtype: string | null;
  description: string | null;
}

export interface OntologyRelationship {
  type: string;
  source: string;
  target: string;
}

export interface ActiveOntology {
  ontology_id: string;
  version_id: string;
  revision: number;
  validation_mode: string;
  domain_id: string;
  entity_types: OntologyEntityType[];
  relationships: OntologyRelationship[];
}

/** What the app's own (bolt) client resolved when it last connected. */
export interface ClientOntology {
  domain_id: string | null;
  validation_mode: string | null;
}

export interface OntologyRevision {
  version_id: string;
  revision: number;
  validation_mode: string;
  is_active: boolean;
  created_at: string | null;
  labels: string[];
}

/** `GET /ontology`. */
export interface OntologyOverview {
  active: ActiveOntology | null;
  client: ClientOntology;
  revisions: OntologyRevision[];
  /** Every label any revision declares -> count of `:Entity` nodes with it. */
  label_counts: Record<string, number>;
}

/**
 * One section of a structural diff. The leaves mirror the library's
 * `diff_documents()`: `added` / `removed` hold full type or relationship
 * definitions, `modified` holds `{label | type, changes: {field: {from, to}}}`
 * and `renamed` is whatever the backend reports (the bolt store leaves it
 * empty — a rename reads as one removal plus one addition).
 */
export interface DiffSection {
  added: unknown[];
  removed: unknown[];
  renamed: unknown[];
  modified: unknown[];
}

export interface ModeChange {
  from: string;
  to: string;
}

/** `GET /ontology/diff?from_revision=&to_revision=`. */
export interface OntologyDiff {
  from_revision: number;
  to_revision: number;
  entity_types: DiffSection;
  relationships: DiffSection;
  mode_change: ModeChange | null;
}

export interface RenameRequest {
  old: string;
  new: string;
  validation_mode: string;
}

export interface MigrationResult {
  id: string;
  status: string;
  total: number;
  processed: number;
  errored: number;
}

/** `POST /ontology/rename`. */
export interface RenameResponse {
  revision: number;
  version_id: string;
  diff: OntologyDiff;
  dry_run_total: number;
  migration: MigrationResult;
  client: ClientOntology;
}

export interface ActivateRequest {
  version_id: string;
}

/** `POST /ontology/activate`. */
export interface ActivateResponse {
  revision: number;
  validation_mode: string;
  client: ClientOntology;
}

// --- /traces ----------------------------------------------------------------

/** `GET /traces?thread_id=` item. `success` is null while still running. */
export interface TraceSummary {
  id: string;
  task: string;
  started_at: string | null;
  completed_at: string | null;
  success: boolean | null;
  outcome_summary: string | null;
  step_count: number;
  tool_call_count: number;
  message_id: string | null;
  seeded: boolean;
}

export interface TraceToolCall {
  id: string;
  tool_name: string;
  arguments: unknown;
  result: unknown;
  status: string;
  duration_ms: number | null;
  touched: TouchedEntity[];
}

export interface TraceStep {
  id: string;
  thought: string | null;
  action: string | null;
  observation: string | null;
  tool_calls: TraceToolCall[];
}

/** `GET /traces/{id}`. */
export interface TraceDetail {
  id: string;
  task: string;
  success: boolean | null;
  outcome_summary: string | null;
  metrics: Record<string, unknown>;
  seeded: boolean;
  steps: TraceStep[];
}

/** `GET /traces/similar?task=&limit=` item. */
export interface SimilarTrace {
  id: string;
  task: string;
  success: boolean | null;
  outcome_summary: string | null;
  seeded: boolean;
}

/** `GET /tool-stats` item. */
export interface ToolStat {
  name: string;
  calls: number;
  success_rate: number;
  avg_duration_ms: number | null;
}
