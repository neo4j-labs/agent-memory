/**
 * Tools mode — model-driven memory.
 *
 * createNamsMemoryTools() returns { query_memory, store_memory } AI SDK tools.
 * createNamsTools() is the async variant that can also merge in MCP tools
 * (an optional extension of tools mode, not a separate mode).
 */

import {
  tool,
  zodSchema,
  type LanguageModel,
  type PrepareStepFunction,
  type Tool,
  type ToolSet,
} from 'ai';
import { z } from 'zod';
import type { MemoryClient } from '@neo4j-labs/agent-memory';
import {
  makeClient,
  getLogger,
  resolveLogger,
  resolveConversation,
  retrieveMemories,
  storeMemory,
  type NamsConfig,
  type NamsScope,
  type MemoryHit,
  type GraphExtractor,
  type StoreInput as MemoryStoreInput,
} from './vercel-ai-provider-client';
import { createGraphExtractor, type GraphExtractorOptions } from './vercel-ai-provider-extract';

//Schemas

const querySchema = z.object({
  query: z.string().describe('Keywords or phrase to search in memory'),
  limit: z.number().int().min(1).max(20).default(5),
});

const storeSchema = z.object({
  content: z.string().min(1).max(2000).describe('The information to remember'),
  type: z.enum(['fact', 'interaction', 'pattern', 'user_preference']).describe(
    'fact=persistent knowledge | interaction=conversation event | ' +
    'pattern=recurring behaviour | user_preference=explicit setting',
  ),
  confidence: z.number().min(0).max(1).default(0.7).describe(
    'Confidence 0–1: 0.8–1.0 very high · 0.6–0.8 high · 0.3–0.6 medium · 0–0.3 low',
  ),
  tags: z.array(z.string().max(40)).max(10).default([]),
});

export type QueryInput = z.infer<typeof querySchema>;
export type StoreInput = z.infer<typeof storeSchema>;
export type QueryOutput = { found: boolean; count?: number; message?: string; memories: MemoryHit[] };
export type StoreOutput = { stored: boolean; type: string; preview: string; message: string };

/**
 * AI SDK v7 added a third `CONTEXT` generic to `tool<INPUT, OUTPUT, CONTEXT>`.
 */
type ToolContext = Record<string, unknown>;

//Options

export interface NamsToolsOptions extends NamsConfig, NamsScope {
  extractionModel?: LanguageModel;
  /** Tunes the extractor built from `extractionModel` (e.g. override the self-referential guard). */
  extractionOptions?: GraphExtractorOptions;
}

/** MCP server connection config. Headers are sent on every request (e.g. Authorization). */
export interface McpConfig {
  url: string;
  headers?: Record<string, string> | (() => Record<string, string> | Promise<Record<string, string>>);
  /** Prepended to every MCP tool name (e.g. `'mcp_'`), namespacing them away from `query_memory`/`store_memory`. */
  toolPrefix?: string;
  optional?: boolean;
}

export interface NamsToolsWithMcpOptions extends NamsToolsOptions {
  mcp?: McpConfig;
}

export interface McpConnectionStatus {
  connected: boolean;
  /** Tool names as exposed to the model, i.e. after `toolPrefix` is applied. */
  toolNames: string[];
  /** Set when `mcp.optional` swallowed a connection failure. */
  error?: NamsMcpConnectionError;
}

export interface NamsToolsResult {
  /** Merged NAMS + MCP tools, ready to pass to ToolLoopAgent `tools:`. */
  tools: ToolSet;
  /** Close the MCP connection (no-op when MCP was not configured). Call in `onFinish`. */
  close: () => Promise<void>;
  mcp?: McpConnectionStatus;
}

/**
 * Raised when an MCP server cannot be reached or refuses the connection.
 *
 * `@ai-sdk/mcp` reports transport failures as a bare message string, so on a
 * 401 we re-probe the endpoint to read its `WWW-Authenticate` challenge and
 * surface the scheme the server actually wants — the difference between
 * "HTTP 401" and "it wants Bearer, you sent Basic".
 */
export class NamsMcpConnectionError extends Error {
  readonly url: string;
  readonly status?: number;
  readonly wwwAuthenticate?: string;

  constructor(
    url: string,
    detail: { status?: number; wwwAuthenticate?: string; cause?: unknown },
  ) {
    const scheme = detail.wwwAuthenticate?.split(/[\s,]/)[0];
    super(
      `MCP connection to ${url} failed` +
      (detail.status ? ` (HTTP ${detail.status})` : '') +
      (scheme ? `. The server requires ${scheme} authentication — supply a matching ` +
        `Authorization header via mcp.headers. Challenge: ${detail.wwwAuthenticate}` : '') +
      (!scheme && detail.cause instanceof Error ? `: ${detail.cause.message}` : ''),
      { cause: detail.cause },
    );
    this.name = 'NamsMcpConnectionError';
    this.url = url;
    this.status = detail.status;
    this.wwwAuthenticate = detail.wwwAuthenticate;
  }
}

interface MemoryToolsHandle {
  client: MemoryClient;
  getConvId: () => Promise<string>;
  extractor?: GraphExtractor;
}

const handleByToolSet = new WeakMap<object, MemoryToolsHandle>();

export function createNamsMemoryTools(options: NamsToolsOptions) {
  const client = makeClient(options);
  const scope: NamsScope = { userId: options.userId, conversationId: options.conversationId };
  const extractor = options.extractionModel
    ? createGraphExtractor(options.extractionModel, options.extractionOptions)
    : undefined;

  let convIdPromise: Promise<string> | null = null;
  const getConvId = (): Promise<string> =>
    (convIdPromise ??= resolveConversation(client, options, scope));

  const query_memory = tool<QueryInput, QueryOutput, ToolContext>({
    description:
      'Search NAMS (Neo4j Agent Memory System) for context relevant to the current message. ' +
      'Call this before answering, every turn.',
    inputSchema: zodSchema(querySchema),
    execute: async ({ query, limit }) => {
      try {
        const convId = await getConvId();
        const memories = await retrieveMemories(client, scope, convId, query, limit);
        if (memories.length === 0)
          return { found: false, message: 'No relevant memories found.', memories: [] };
        return { found: true, count: memories.length, memories };
      } catch (err) {
        getLogger(client).error('query_memory failed', err);
        return { found: false, message: 'Memory lookup failed.', memories: [] };
      }
    },
  });

  const store_memory = tool<StoreInput, StoreOutput, ToolContext>({
    description:
      'Persist important information to NAMS (Neo4j graph). ' +
      'Call this BEFORE giving your final answer whenever the conversation ' +
      'contains facts, preferences, or patterns worth remembering. ' +
      'Store only NEW information the user supplied in this conversation. ' +
      'Never store what query_memory returned, or a summary of what you ' +
      'remember — that is already stored, and re-storing it degrades recall.',
    inputSchema: zodSchema(storeSchema),
    execute: async ({ content, type, confidence, tags }) => {
      try {
        const convId = await getConvId();
        await storeMemory(client, convId, { content, type, confidence, tags }, { extractor });
        return {
          stored: true,
          type,
          preview: content.slice(0, 80),
          message: `Memory stored (${type}, confidence=${confidence})`,
        };
      } catch (err) {
        getLogger(client).error('store_memory failed', err);
        return { stored: false, type, preview: content.slice(0, 80), message: 'Failed to store memory.' };
      }
    },
  });

  const tools = { query_memory, store_memory };
  handleByToolSet.set(tools, { client, getConvId, extractor });
  return tools;
}

export interface FinishedTurn {
  /** Assistant text from the final step. */
  text?: string;
  /** Tool calls across all steps, as the AI SDK aggregates them. */
  toolCalls?: ReadonlyArray<{ toolName: string }>;
  /** Per-step tool calls, used when the aggregate is absent. */
  steps?: ReadonlyArray<{ toolCalls?: ReadonlyArray<{ toolName: string }> }>;
}

/** The turn as handed to a custom `fallback`, once it is known nothing was stored. */
export interface UnstoredTurn {
  /** Final assistant text, or `''` when the model produced none. */
  text: string;
  /** Every tool the model called this turn, deduplicated. */
  toolNames: string[];
}

export interface EnsureMemoryStoredOptions {
  /**
   * What to persist when the model never called `store_memory`. Return `null`
   * to store nothing. Default: the assistant's final text as an `interaction`.
   */
  fallback?: (turn: UnstoredTurn) => MemoryStoreInput | null;
}

export type EnsureMemoryStoredResult =
  | { stored: true; input: MemoryStoreInput }
  | { stored: false; reason: 'already-stored' | 'nothing-to-store' | 'failed' };

/**
 * `interaction` routes to short-term conversation memory, which mirrors what
 * middleware mode guarantees. Storing the assistant's own text as a `fact`
 * instead would feed it to the graph extractor — and an agent summarising what
 * it remembers is precisely the self-referential input the extractor's skip
 * guard exists to reject. Callers who do want facts pass their own `fallback`.
 */
const defaultFallback = (turn: UnstoredTurn): MemoryStoreInput | null =>
  turn.text ? { content: turn.text, type: 'interaction' } : null;

const toolNamesOf = (event: FinishedTurn): string[] => [
  ...new Set([
    ...(event.toolCalls ?? []).map(c => c.toolName),
    ...(event.steps ?? []).flatMap(s => (s.toolCalls ?? []).map(c => c.toolName)),
  ]),
];


export function ensureMemoryStored(
  tools: ToolSet,
  options: EnsureMemoryStoredOptions = {},
): (event: FinishedTurn) => Promise<EnsureMemoryStoredResult> {
  const handle = handleByToolSet.get(tools);
  if (!handle) {
    throw new Error(
      'ensureMemoryStored() expects the tool set returned by createNamsMemoryTools() / ' +
      'createNams().tools() / .toolsWithMcp(), not a copy of it. Pass that object directly — ' +
      'spreading it into a new object ({ ...tools }) loses the memory client it is bound to.',
    );
  }
  const fallback = options.fallback ?? defaultFallback;

  return async (event) => {
    const toolNames = toolNamesOf(event);
    if (toolNames.includes('store_memory')) return { stored: false, reason: 'already-stored' };

    const input = fallback({ text: event.text?.trim() ?? '', toolNames });
    if (!input?.content?.trim()) return { stored: false, reason: 'nothing-to-store' };

    try {
      const convId = await handle.getConvId();
      await storeMemory(handle.client, convId, input, { extractor: handle.extractor });
      return { stored: true, input };
    } catch (err) {
      getLogger(handle.client).error('ensureMemoryStored failed to persist the turn', err);
      return { stored: false, reason: 'failed' };
    }
  };
}

export interface EnforceQueryMemoryOptions {
  /**
   * How many steps the model may spend on other tools before `query_memory`
   * is forced directly. During the grace window a text-only answer is blocked
   * (`toolChoice: 'required'`) but the model picks which tools to call; once
   * the window is exhausted the next step forces `query_memory` itself, so
   * the loop can never end without the query having run. `0` forces
   * `query_memory` as the very first step. Default: 3.
   *
   * Keep this at least two below the agent's `stopWhen` step budget so the
   * forced query and the final answer both still fit.
   */
  graceSteps?: number;
}

/**
 * prepareStep hook that guarantees `query_memory` runs before the final
 * answer, without dictating tool order. While `query_memory` is absent from
 * the executed tool calls, each step requires *some* tool call (the model may
 * read files, hit MCP tools, etc.), so it cannot finish with a text-only
 * answer; after `graceSteps` steps it is forced to call `query_memory`
 * directly. Once `query_memory` has run, all constraints drop.
 *
 * ```ts
 * const agent = new ToolLoopAgent({
 *   model, tools,
 *   prepareStep: enforceQueryMemory(),
 *   stopWhen: stepCountIs(10),
 * });
 * ```
 */
export function enforceQueryMemory<
  TOOLS extends ToolSet & { query_memory: Tool },
>(options: EnforceQueryMemoryOptions = {}): PrepareStepFunction<TOOLS> {
  const graceSteps = options.graceSteps ?? 3;
  return ({ stepNumber, steps }) => {
    const queried = steps.some(step =>
      step.toolCalls.some(call => call.toolName === 'query_memory'));
    if (queried) return undefined;
    return stepNumber < graceSteps
      ? { toolChoice: 'required' }
      : { toolChoice: { type: 'tool', toolName: 'query_memory' as Extract<keyof TOOLS, string> } };
  };
}

/**
 * Reads the HTTP status out of an @ai-sdk/mcp transport error, which formats it
 * into the message ("...(HTTP 401):") rather than exposing it as a field.
 */
function statusFromTransportError(err: unknown): number | undefined {
  const match = /\bHTTP (\d{3})\b/.exec(err instanceof Error ? err.message : String(err));
  return match ? Number(match[1]) : undefined;
}

async function readAuthChallenge(url: string): Promise<string | undefined> {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: '{}',
    });
    return res.headers.get('www-authenticate') ?? undefined;
  } catch {
    return undefined;
  }
}

/**
 * Async variant of createNamsMemoryTools. Optionally connects to an MCP
 * server and merges its tools with the NAMS memory tools.
 */
export async function createNamsTools(options: NamsToolsWithMcpOptions): Promise<NamsToolsResult> {
  const namsTools = createNamsMemoryTools(options);

  if (!options.mcp) {
    return { tools: namsTools, close: async () => { } };
  }

  const { url, toolPrefix: prefix, optional } = options.mcp;

  try {
    const headers = typeof options.mcp.headers === 'function'
      ? await options.mcp.headers()
      : options.mcp.headers;

    const { createMCPClient } = await import('@ai-sdk/mcp');
    const mcpClient = await createMCPClient({
      transport: { type: 'http', url, headers },
    });

    const rawMcpTools = await mcpClient.tools();
    const mcpTools: ToolSet = prefix
      ? Object.fromEntries(Object.entries(rawMcpTools).map(([name, t]) => [`${prefix}${name}`, t]))
      : rawMcpTools;

    const toolNames = Object.keys(mcpTools);
    const collisions = toolNames.filter(name => name in namsTools);
    if (collisions.length > 0) {
      resolveLogger(options).warn(
        `MCP tool(s) [${collisions.join(', ')}] share a name with NAMS memory tools and will override them` +
        (prefix ? '' : ' — set mcp.toolPrefix to namespace MCP tools and avoid this'),
      );
    }
    const merged: ToolSet = { ...namsTools, ...mcpTools };
    const handle = handleByToolSet.get(namsTools);
    if (handle) handleByToolSet.set(merged, handle);

    return {
      tools: merged,
      close: () => mcpClient.close(),
      mcp: { connected: true, toolNames },
    };
  } catch (cause) {
    const status = statusFromTransportError(cause);
    const error = new NamsMcpConnectionError(url, {
      status,
      wwwAuthenticate: status === 401 ? await readAuthChallenge(url) : undefined,
      cause,
    });

    if (!optional) throw error;

    resolveLogger(options).warn(`${error.message} — continuing with NAMS memory tools only`);
    return {
      tools: namsTools,
      close: async () => { },
      mcp: { connected: false, toolNames: [], error },
    };
  }
}

export class NamsMemoryTools {
  constructor(private readonly base: Omit<NamsToolsOptions, 'userId' | 'conversationId'>) { }

  /** Synchronous — returns NAMS memory tools only. */
  forUser(userId: string, conversationId?: string) {
    return createNamsMemoryTools({ ...this.base, userId, conversationId });
  }

  /** Async — returns NAMS + optional MCP tools merged, plus a close() handle. */
  async forUserWithMcp(
    userId: string,
    mcp?: McpConfig,
    conversationId?: string,
  ): Promise<NamsToolsResult> {
    return createNamsTools({ ...this.base, userId, conversationId, mcp });
  }
}
