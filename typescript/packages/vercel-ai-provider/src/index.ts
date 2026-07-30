/**
 * Neo4j Agent Memory (NAMS) — unified entry point.
 *
 * Three integration modes backed by the same @neo4j-labs/agent-memory client:
 * - Provider   — `createNamsProvider(...)`: a registrable ProviderV4; memory is
 *                retrieved + persisted automatically on every call
 * - Middleware — `createNams(...).wrap(model, scope)`: decorate an existing
 *                model instance with the same transparent memory
 * - Tools      — `createNams(...).tools(scope)`: the model calls query_memory /
 *                store_memory itself; `.toolsWithMcp(scope, mcp)` optionally
 *                merges tools from an MCP server into the same tool set
 *
 * @example
 * ```ts
 * const nams  = createNams({ apiKey: process.env.MEMORY_API_KEY! });
 * const model = nams.wrap(openai('gpt-5.4-mini'), { userId });
 * ```
 */

export type { NamsConfig, NamsScope, NamsLogger, MemoryHit, StoreInput, GraphExtractor } from './vercel-ai-provider-client';
export type { NamsMemoryConfig } from './vercel-ai-provider-middleware';
export type {
  NamsToolsOptions, NamsToolsWithMcpOptions, NamsToolsResult, McpConnectionStatus,
  McpConfig, QueryInput, StoreInput as ToolStoreInput,
  QueryOutput, StoreOutput
} from './vercel-ai-provider-tools';
export type { NamsProviderOptions } from './vercel-ai-provider';

export { makeClient, getLogger, resolveConversation, findExistingConversation, retrieveMemories, storeMemory } from './vercel-ai-provider-client';
export { createGraphExtractor } from './vercel-ai-provider-extract';
export { createNamsMemory } from './vercel-ai-provider-middleware';
export { createNamsMemoryTools, createNamsTools, enforceQueryMemory, NamsMemoryTools, NamsMcpConnectionError } from './vercel-ai-provider-tools';
export type { EnforceQueryMemoryOptions } from './vercel-ai-provider-tools';
export { createNamsProvider } from './vercel-ai-provider';

import type { LanguageModel } from 'ai';
import type { LanguageModelV4 } from '@ai-sdk/provider';
import type { NamsConfig, NamsScope } from './vercel-ai-provider-client';
import type { NamsMemoryConfig } from './vercel-ai-provider-middleware';
import type { McpConfig } from './vercel-ai-provider-tools';
import { createNamsMemory } from './vercel-ai-provider-middleware';
import { createNamsMemoryTools, createNamsTools } from './vercel-ai-provider-tools';

/** The three NAMS integration modes. */
export type NamsMode = 'provider' | 'middleware' | 'tools';

export interface NamsFactoryConfig extends NamsConfig {
  extractionModel?: LanguageModel;
  maxMemories?: number;
  persistInteractions?: boolean;
}

/**
 * Create a unified NAMS instance covering the middleware and tools modes.
 * (For provider mode — a registrable ProviderV4 — use `createNamsProvider`.)
 *
 * - `.wrap(model, scope)`             → middleware mode (transparent memory)
 * - `.tools(scope)`                   → tools mode (query_memory / store_memory)
 * - `.toolsWithMcp(scope, mcpConfig)` → tools mode with MCP tools merged in
 */
export function createNams(config: NamsFactoryConfig) {
  const providerConfig: NamsMemoryConfig = {
    apiKey: config.apiKey,
    endpoint: config.endpoint,
    workspaceId: config.workspaceId,
    logger: config.logger,
    extractionModel: config.extractionModel,
    maxMemories: config.maxMemories,
    persistInteractions: config.persistInteractions,
  };

  const memory = createNamsMemory(providerConfig);

  return {
    /**
     * Middleware mode — wrap an existing model instance.
     * Memory is retrieved + persisted automatically; no tool calls emitted.
     * Pass the returned model directly to ToolLoopAgent / generateText.
     */
    wrap(model: LanguageModelV4, scope: NamsScope): LanguageModelV4 {
      return memory.wrap(model, scope);
    },

    /**
     * Tools mode — model-driven memory.
     * Returns { query_memory, store_memory } as AI SDK tool()s.
     * Pair with a system prompt that instructs: query → answer → store.
     */
    tools(scope: NamsScope) {
      return createNamsMemoryTools({
        ...config,
        userId: scope.userId,
        conversationId: scope.conversationId,
        extractionModel: config.extractionModel,
      });
    },

    /**
     * Tools mode with MCP (optional extension of tools mode).
     * Connects to an MCP server and merges its tools with NAMS memory tools.
     * Returns { tools, close, mcp } — call close() in ToolLoopAgent's onFinish,
     * and read `mcp.toolNames` to build a system prompt from the tools that are
     * actually available rather than the ones you expect to be.
     * When mcpConfig is omitted, behaves identically to .tools() with a no-op close.
     *
     * Rejects with NamsMcpConnectionError if the server refuses the connection;
     * set `mcp.optional` to degrade to memory-only tools instead.
     */
    async toolsWithMcp(scope: NamsScope, mcpConfig?: McpConfig) {
      return createNamsTools({
        ...config,
        userId: scope.userId,
        conversationId: scope.conversationId,
        extractionModel: config.extractionModel,
        mcp: mcpConfig,
      });
    },
  };
}
