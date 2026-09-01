/**
 * Neo4j Agent Memory (NAMS) — unified entry point.
 *
 * Four integration modes backed by the same @neo4j-labs/agent-memory client:
 * - Provider   — `createNamsProvider(...)`: a registrable ProviderV4; memory is
 *                retrieved + persisted automatically on every call
 * - Middleware — `createNams(...).wrap(model, scope)`: decorate an existing
 *                model instance with the same transparent memory
 * - Tools      — `createNams(...).tools(scope)`: the model calls query_memory /
 *                store_memory itself; `.toolsWithMcp(scope, mcp)` optionally
 *                merges tools from an MCP server into the same tool set
 * - Hooks      — `createNams(...).hooks(scope?)`: runtime-controlled session
 *                memory; `loadSession()` restores the transcript before each
 *                generation and `onFinish()` persists every turn exactly once,
 *                regardless of what the LLM decides
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
export type { GraphExtractorOptions } from './vercel-ai-provider-extract';
export { createNamsMemory } from './vercel-ai-provider-middleware';
export { createNamsMemoryTools, createNamsTools, enforceQueryMemory, ensureMemoryStored, NamsMemoryTools, NamsMcpConnectionError } from './vercel-ai-provider-tools';
export type {
  EnforceQueryMemoryOptions, EnsureMemoryStoredOptions, EnsureMemoryStoredResult,
  FinishedTurn, UnstoredTurn,
} from './vercel-ai-provider-tools';
export { createNamsProvider } from './vercel-ai-provider';
export { createNamsHooks } from './vercel-ai-provider-hooks';
export type {
  NamsHooks, NamsHooksOptions, LoadSessionOptions, OnFinishScope,
  NamsOnFinishEvent, NamsOnFinishCallback,
} from './vercel-ai-provider-hooks';

import type { LanguageModel } from 'ai';
import type { LanguageModelV4 } from '@ai-sdk/provider';
import type { NamsConfig, NamsScope } from './vercel-ai-provider-client';
import type { NamsMemoryConfig } from './vercel-ai-provider-middleware';
import type { GraphExtractorOptions } from './vercel-ai-provider-extract';
import type { McpConfig } from './vercel-ai-provider-tools';
import { resolveLogger } from './vercel-ai-provider-client';
import { createNamsMemory } from './vercel-ai-provider-middleware';
import { createNamsMemoryTools, createNamsTools } from './vercel-ai-provider-tools';
import { createNamsHooks } from './vercel-ai-provider-hooks';

/** The four NAMS integration modes. */
export type NamsMode = 'provider' | 'middleware' | 'tools' | 'hooks';

export interface NamsFactoryConfig extends NamsConfig {
  /**
   * Tools mode only. Builds an entity graph from each `store_memory` fact /
   * preference / pattern write, which NAMS does not extract itself. One extra
   * model call per stored memory. Other modes ignore it and log a warning.
   */
  extractionModel?: LanguageModel;
  /** Tunes the extractor built from `extractionModel` (e.g. override the self-referential guard). */
  extractionOptions?: GraphExtractorOptions;
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
    maxMemories: config.maxMemories,
    persistInteractions: config.persistInteractions,
  };

  const memory = createNamsMemory(providerConfig);

  // extractionModel applies to tools mode only — say so instead of no-opping.
  let extractionScopeWarned = false;
  const warnExtractionIgnored = (mode: string): void => {
    if (extractionScopeWarned || !config.extractionModel) return;
    extractionScopeWarned = true;
    resolveLogger(config).warn(
      `extractionModel is ignored in ${mode} mode — NAMS extracts persisted ` +
      `turns server-side. It applies to .tools()/.toolsWithMcp() only.`,
    );
  };

  return {
    /**
     * Middleware mode — wrap an existing model instance.
     * Memory is retrieved + persisted automatically; no tool calls emitted.
     * Pass the returned model directly to ToolLoopAgent / generateText.
     */
    wrap(model: LanguageModelV4, scope: NamsScope): LanguageModelV4 {
      warnExtractionIgnored('middleware');
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

    /**
     * Hooks mode — runtime-controlled (deterministic) session memory.
     * Returns { loadSession, onFinish }: call `loadSession()` in `prepareCall`
     * (or before generateText/streamText) to restore the transcript, and pass
     * `onFinish()` as the finish callback to persist every user, assistant,
     * and tool turn exactly once per generation — no tool calls, no LLM
     * discretion. Scope is optional here; it can also be supplied per call or
     * via `runtimeContext` (see `createNamsHooks`). Combine with `.tools()`
     * if long-term memory should stay model-driven on the same agent.
     */
    hooks(scope?: Partial<NamsScope>) {
      warnExtractionIgnored('hooks');
      const { extractionModel: _m, extractionOptions: _o, ...hooksConfig } = config;
      return createNamsHooks({
        ...hooksConfig,
        userId: scope?.userId,
        conversationId: scope?.conversationId,
      });
    },
  };
}
