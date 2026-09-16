/**
 * @neo4j-labs/agent-memory — TypeScript client for the Neo4j Agent Memory Service.
 *
 * Usage against the hosted service:
 *
 *     import { MemoryClient } from "@neo4j-labs/agent-memory";
 *
 *     // Reads MEMORY_API_KEY from env; defaults to the hosted endpoint.
 *     const client = new MemoryClient();
 *
 *     const conv = await client.shortTerm.createConversation({ userId: "alice" });
 *     await client.shortTerm.addMessage(conv.id, "user", "Hello!");
 *     const ctx = await client.shortTerm.getContext(conv.id);
 *
 * Bridge transport for TCK conformance testing is available via the
 * `@neo4j-labs/agent-memory/testing` subpath.
 */

export { MemoryClient } from "./client.js";

// Types — short-term
export type {
  Message,
  Conversation,
  SessionInfo,
  ConversationContext,
  Observation,
  Reflection,
  MessageRole,
} from "./types.js";

// Types — long-term
export type {
  Entity,
  EntityType,
  HostedEntityType,
  EntityRelationshipRef,
  EntityHistory,
  EntityMention,
  EntityGraph,
  ExpandedGraph,
  EntityGraphNode,
  EntityGraphEdge,
  EntityFeedbackResult,
  EntityMergeResult,
  Preference,
  Fact,
  Relationship,
} from "./types.js";

// Types — reasoning
export type {
  ReasoningTrace,
  ReasoningStep,
  ToolCall,
  ToolCallStatus,
  ToolStats,
  AgentStep,
  AgentStepExplanation,
  ConversationTrace,
  EntityProvenance,
} from "./types.js";

// Types — query / auth
export type { CypherResult, ApiKey, AccessTokenPair } from "./types.js";

// Options
export type {
  MemoryClientOptions,
  TransportMode,
  AddMessageOptions,
  GetConversationOptions,
  SearchMessagesOptions,
  ListSessionsOptions,
  SearchEntitiesOptions,
  SearchPreferencesOptions,
  GetRelatedEntitiesOptions,
  ListTracesOptions,
  RecordToolCallOptions,
  CompleteTraceOptions,
  AddRelationshipOptions,
  GetSimilarTracesOptions,
  CreateConversationOptions,
  ListConversationsOptions,
  BulkMessageInput,
  ListEntitiesOptions,
  UpdateEntityOptions,
  SetEntityFeedbackOptions,
  RecordStepInput,
  CreateApiKeyInput,
} from "./types.js";

// Transports
export type { Transport } from "./transport/index.js";
export { RestTransport } from "./transport/index.js";
export type { RestTransportOptions, TokenProvider } from "./transport/index.js";

// Sub-clients (advanced)
export { ShortTermMemory } from "./short-term/index.js";
export { LongTermMemory } from "./long-term/index.js";
export { ReasoningMemory } from "./reasoning/index.js";
export { QueryConsole } from "./query/index.js";
export { AuthClient } from "./auth/index.js";
export { OntologyClient } from "./ontology/index.js";
export type {
  Ontology,
  OntologySummary,
  OntologyVersion,
  OntologyRecord,
  OntologyDocument,
  ActiveOntology,
  DomainInfo,
  EntityTypeDef,
  PoleType,
  PropertyDef,
  RelationshipDef,
  CreateOntologyOptions,
  UpdateOntologyOptions,
  ImportOntologyOptions,
  OntologyImportResult,
  ImportWarning,
  OntologyDiff,
  MigrateOptions,
  TypeMapping,
  MigrationJob,
} from "./ontology/index.js";

// Errors
export {
  MemoryError,
  ConnectionError,
  AuthenticationError,
  NotFoundError,
  NotSupportedError,
  ValidationError,
  TransportError,
} from "./errors.js";

// Observability
export type { Logger, LogEvent } from "./observability.js";

// Version
export { VERSION } from "./version.js";
