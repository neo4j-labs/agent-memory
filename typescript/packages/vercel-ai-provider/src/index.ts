/**
 * Neo4j Agent Memory (NAMS) for the Vercel AI SDK. Four ways to add memory:
 * a provider, middleware, tools the model calls, or hooks your code drives.
 * See the README for each one.
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
  PrepareOptions, PrepareResult, NamsToolBlocked, SessionTurn,
} from './vercel-ai-provider-hooks';
export { compileHooks } from './vercel-ai-provider-hook-events';
export type {
  NamsHookEvent, NamsHookConfig, NamsHookGroup, NamsHookEntry, NamsHookHandler,
  NamsHookInputs, NamsHookOutputs, NamsHookResult, NamsHookRegistry,
  NamsHookBase, NamsSystemMessageSink,
  SessionStartInput, UserPromptSubmitInput, PreToolUseInput, PostToolUseInput,
  PostToolUseFailureInput, PreMemoryWriteInput, StopInput, SessionEndInput,
  SessionStartOutput, UserPromptSubmitOutput, PreToolUseOutput, PostToolUseOutput,
  PostToolUseFailureOutput, PreMemoryWriteOutput, StopOutput, SessionEndOutput,
} from './vercel-ai-provider-hook-events';

import type { LanguageModel } from 'ai';
import type { NamsConfig, NamsScope } from './vercel-ai-provider-client';
import type { NamsMemoryConfig, WrappableModel, WrappedModel } from './vercel-ai-provider-middleware';
import type { GraphExtractorOptions } from './vercel-ai-provider-extract';
import type { McpConfig } from './vercel-ai-provider-tools';
import { resolveLogger } from './vercel-ai-provider-client';
import { createNamsMemory } from './vercel-ai-provider-middleware';
import { createNamsMemoryTools, createNamsTools } from './vercel-ai-provider-tools';
import { createNamsHooks, type NamsHooksOptions } from './vercel-ai-provider-hooks';

/** The four NAMS integration modes. */
export type NamsMode = 'provider' | 'middleware' | 'tools' | 'hooks';

export interface NamsFactoryConfig extends NamsConfig {
  /** Tools mode only: pull entities out of each stored memory. One extra model call per memory. */
  extractionModel?: LanguageModel;
  /** Extractor options, e.g. a custom `skipEntity` filter. */
  extractionOptions?: GraphExtractorOptions;
  maxMemories?: number;
  persistInteractions?: boolean;
}

/** Memory for middleware, tools, and hooks modes. For provider mode use `createNamsProvider`. */
export function createNams(config: NamsFactoryConfig) {
  const providerConfig: NamsMemoryConfig = {
    apiKey: config.apiKey,
    endpoint: config.endpoint,
    workspaceId: config.workspaceId,
    logger: config.logger,
    crossSessionLimit: config.crossSessionLimit,
    graphExpansionLimit: config.graphExpansionLimit,
    maxMemories: config.maxMemories,
    persistInteractions: config.persistInteractions,
  };

  const memory = createNamsMemory(providerConfig);

  // Warn once when extractionModel is set in a mode that ignores it.
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
    /** Middleware mode. Returns the model with memory added. No tool calls. */
    wrap(model: WrappableModel, scope: NamsScope): WrappedModel {
      warnExtractionIgnored('middleware');
      return memory.wrap(model, scope);
    },

    /** Tools mode. Returns `query_memory` and `store_memory` for the model to call. */
    tools(scope: NamsScope) {
      return createNamsMemoryTools({
        ...config,
        userId: scope.userId,
        conversationId: scope.conversationId,
        extractionModel: config.extractionModel,
      });
    },

    /** Tools mode plus MCP server tools. Call `close()` when done. */
    async toolsWithMcp(scope: NamsScope, mcpConfig?: McpConfig) {
      return createNamsTools({
        ...config,
        userId: scope.userId,
        conversationId: scope.conversationId,
        extractionModel: config.extractionModel,
        mcp: mcpConfig,
      });
    },

    /** Hooks mode. Your code loads the session and saves it; the model is not involved. */
    hooks(scope?: Partial<NamsScope> & Pick<NamsHooksOptions, 'hooks' | 'onSystemMessage' | 'sessionLimit'>) {
      warnExtractionIgnored('hooks');
      const { extractionModel: _m, extractionOptions: _o, ...hooksConfig } = config;
      return createNamsHooks({
        ...hooksConfig,
        userId: scope?.userId,
        conversationId: scope?.conversationId,
        hooks: scope?.hooks,
        onSystemMessage: scope?.onSystemMessage,
        sessionLimit: scope?.sessionLimit,
      });
    },
  };
}
