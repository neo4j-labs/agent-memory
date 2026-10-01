import type { MemoryClient } from '@neo4j-labs/agent-memory';

export const DEFAULT_ENDPOINT = 'https://memory.neo4jlabs.com/v1';

/** Logger for non-fatal errors. */
export interface NamsLogger {
  warn: (message: string, error?: unknown) => void;
  error: (message: string, error?: unknown) => void;
}

export interface NamsConfig {
  apiKey: string;
  endpoint?: string;
  workspaceId?: string;
  /** Logger for non-fatal errors (default: console). */
  logger?: NamsLogger;
  /** How many of the user's other recent conversations to also search (default: 5). `0` turns this off. */
  crossSessionLimit?: number;
  /** How many matched entities to read relationships for, one request each (default: 2). `0` turns this off. */
  graphExpansionLimit?: number;
}

export interface NamsScope {
  userId: string;
  conversationId?: string;
}

export type MemorySource = 'long-term' | 'graph' | 'conversation' | 'cross-session' | 'reasoning';
export type MemoryType = 'fact' | 'interaction' | 'pattern' | 'user_preference';

export interface MemoryHit {
  /** For a `graph` hit, one relationship: `(Alex)-[WORKS_AT]->(TechCorp)`. */
  content: string;
  source: MemorySource;
  type: string;
  /** The entity's stored confidence, where there is one. It rates the extraction, not the match, so hits are not ordered by it. */
  score?: number;
}

export interface StoreInput {
  content: string;
  type: MemoryType;
  confidence?: number;
  tags?: string[];
}

export type GraphExtractor = (client: MemoryClient, input: StoreInput) => Promise<void>;

export interface ClientState {
  convCache: Map<string, string>;
  logger: NamsLogger;
  /** True after the first graph extraction failure. */
  extractionFailed?: boolean;
  /** True once the backend has said it can't write relationships. */
  relationshipWritesUnsupported?: boolean;
}