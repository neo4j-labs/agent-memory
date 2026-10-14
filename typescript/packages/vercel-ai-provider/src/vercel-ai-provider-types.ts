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
  logger?: NamsLogger;
  crossSessionLimit?: number;
  graphExpansionLimit?: number;
  workspaceGraph?: boolean;
}

export interface NamsScope {
  userId: string;
  conversationId?: string;
}

export type MemorySource = 'long-term' | 'graph' | 'conversation' | 'cross-session' | 'reasoning';
export type MemoryType = 'fact' | 'interaction' | 'pattern' | 'user_preference';

export interface MemoryHit {
  content: string;
  source: MemorySource;
  type: string;
  score?: number;
}

export interface StoreInput {
  content: string;
  type: MemoryType;
  confidence?: number;
  tags?: string[];
}

/** `scope.userId`, when given, is the user whose memory the extracted entities go in. */
export type GraphExtractor = (
  client: MemoryClient,
  input: StoreInput,
  scope?: { userId?: string },
) => Promise<void>;

export interface ClientState {
  convCache: Map<string, string>;
  logger: NamsLogger;
  extractionFailed?: boolean;
  relationshipWritesUnsupported?: boolean;
}