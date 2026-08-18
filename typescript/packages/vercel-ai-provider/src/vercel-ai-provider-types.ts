import type { MemoryClient } from '@neo4j-labs/agent-memory';

export const DEFAULT_ENDPOINT = 'https://memory.neo4jlabs.com/v1';

/** Pluggable logger for non-fatal errors (network failures, fallbacks). */
export interface NamsLogger {
  warn: (message: string, error?: unknown) => void;
  error: (message: string, error?: unknown) => void;
}

export interface NamsConfig {
  apiKey: string;
  endpoint?: string;
  workspaceId?: string;
  /** Reports non-fatal errors. Defaults to console; pass your own to redirect or silence. */
  logger?: NamsLogger;
}

export interface NamsScope {
  userId: string;
  conversationId?: string;
}

export type MemorySource = 'long-term' | 'conversation' | 'cross-session' | 'reasoning';
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

export type GraphExtractor = (client: MemoryClient, input: StoreInput) => Promise<void>;

export interface ClientState {
  convCache: Map<string, string>;
  logger: NamsLogger;
  /** Set once graph extraction has failed, so only the first failure logs at error level. */
  extractionFailed?: boolean;
  /** Set once the backend has reported relationship writes unsupported, to suppress per-edge repeats. */
  relationshipWritesUnsupported?: boolean;
}