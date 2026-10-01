/** Hooks mode: your code, not the model, loads and saves the conversation. */

import type { ModelMessage, ToolSet } from 'ai';
import type { MemoryClient } from '@neo4j-labs/agent-memory';
import {
  makeClient,
  getLogger,
  resolveConversation,
  findExistingConversation,
  type NamsConfig,
  type NamsScope,
} from './vercel-ai-provider-client';
import {
  compileHooks,
  type NamsHookConfig,
  type NamsSystemMessageSink,
  type SessionTurn,
} from './vercel-ai-provider-hook-events';

export type { SessionTurn };

// Options

export interface NamsHooksOptions extends NamsConfig {
  /** Default scope. Every hook call can override it. */
  userId?: string;
  conversationId?: string;
  /** Max prior turns `loadSession` restores (default: 40). */
  sessionLimit?: number;
  /** Lifecycle hooks by event. See `NamsHookConfig`. */
  hooks?: NamsHookConfig;
  /** Where a hook's `systemMessage` goes. Defaults to the logger. */
  onSystemMessage?: NamsSystemMessageSink;
}

export interface LoadSessionOptions {
  userId?: string;
  conversationId?: string;
  /** Max prior turns to restore. Defaults to `sessionLimit`. */
  limit?: number;
}

export interface PrepareOptions {
  userId?: string;
  conversationId?: string;
  /** The user's message for this turn. */
  prompt: string;
  /** Max prior turns to restore. Defaults to `sessionLimit`. */
  limit?: number;
}

export interface PrepareResult {
  /** The history, then this turn's prompt. Never contains a system message. */
  messages: ModelMessage[];
  /** Extra context from hooks. Add it to your own `instructions`. */
  instructions?: string;
  /** The prompt after any `UserPromptSubmit` rewrite. Pass this one to `onFinish`. */
  prompt: string;
  /** True when a `UserPromptSubmit` hook blocked the turn. Do not call the model. */
  blocked: boolean;
  blockReason?: string;
  /** Anything a hook asked the app to show. */
  systemMessages: string[];
}

/** What a denied tool returns to the model in place of its result. */
export interface NamsToolBlocked {
  blocked: true;
  toolName: string;
  reason: string;
}

export interface OnFinishScope {
  userId?: string;
  conversationId?: string;
  /** The user input of this generation, saved as the `user` turn. */
  prompt?: string | ModelMessage[];
  /** Save the user prompt as a turn (default: true). */
  persistUserPrompt?: boolean;
}

// Plain shapes, so one callback works with ToolLoopAgent, generateText and streamText.

interface ResponseMessageLike {
  role: string;
  content: unknown;
}

export interface NamsOnFinishEvent {
  /** Final assistant text. */
  readonly text?: string;
  /** Assistant and tool messages from every step, in order. */
  readonly responseMessages?: ReadonlyArray<ResponseMessageLike>;
  /** Per-call scope in ai v7. */
  readonly runtimeContext?: unknown;
  readonly finalStep?: { readonly runtimeContext?: unknown };
}

/** The callback built by `onFinish()`. */
export type NamsOnFinishCallback = (event: NamsOnFinishEvent) => Promise<void>;

// Turn extraction

/** Marks a saved tool record. `loadSession` skips these. */
const AUDIT_KIND_KEY = 'namsKind';

/** Max hook context lines queued per scope. */
const MAX_PENDING_CONTEXT = 20;

const stringify = (value: unknown): string => {
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value) ?? String(value); } catch { return String(value); }
};

const textOfParts = (content: unknown): string => {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((p: any) => p?.type === 'text' && typeof p.text === 'string')
    .map((p: any) => p.text as string)
    .join('');
};

const promptText = (prompt: string | ModelMessage[]): string => {
  if (typeof prompt === 'string') return prompt;
  return prompt
    .filter(m => m.role === 'user')
    .map(m => textOfParts(m.content))
    .filter(Boolean)
    .join('\n');
};

const auditTurn = (
  kind: 'tool-call' | 'tool-result',
  part: { toolCallId?: string; toolName?: string },
  payload: unknown,
): SessionTurn => ({
  role: 'assistant',
  content: `[${kind}] ${part.toolName ?? 'unknown'}: ${stringify(payload)}`.slice(0, 4000),
  metadata: {
    [AUDIT_KIND_KEY]: kind,
    toolName: part.toolName,
    toolCallId: part.toolCallId,
  },
});

/** Response messages as turns. Tool calls become tagged assistant turns, since NAMS has no `tool` role. */
const turnsFromResponse = (messages: ReadonlyArray<ResponseMessageLike>): SessionTurn[] => {
  const turns: SessionTurn[] = [];

  for (const message of messages) {
    if (message.role === 'assistant') {
      if (typeof message.content === 'string') {
        if (message.content) turns.push({ role: 'assistant', content: message.content });
        continue;
      }
      if (!Array.isArray(message.content)) continue;
      // Keep order: text joins into one turn until a tool call.
      let text = '';
      const flushText = () => {
        if (text) turns.push({ role: 'assistant', content: text });
        text = '';
      };
      for (const part of message.content as any[]) {
        if (part?.type === 'text' && typeof part.text === 'string') text += part.text;
        else if (part?.type === 'tool-call') {
          flushText();
          turns.push(auditTurn('tool-call', part, part.input));
        }
      }
      flushText();
    } else if (message.role === 'tool' && Array.isArray(message.content)) {
      for (const part of message.content as any[]) {
        if (part?.type !== 'tool-result') continue;
        const output = part.output && typeof part.output === 'object' && 'value' in part.output
          ? (part.output as { value: unknown }).value
          : part.output;
        turns.push(auditTurn('tool-result', part, output));
      }
    }
  }

  return turns;
};

// Persistence

async function persistTurns(
  client: MemoryClient,
  convId: string,
  turns: SessionTurn[],
): Promise<void> {
  if (turns.length === 0) return;
  const log = getLogger(client);

  const bulk = turns.map(t => ({ role: t.role, content: t.content, metadata: t.metadata }));
  try {
    await client.shortTerm.bulkAddMessages(convId, bulk);
    return;
  } catch (err) {
    log.warn('bulkAddMessages failed, falling back to per-message writes', err);
  }

  for (const turn of turns) {
    await client.shortTerm
      .addMessage(convId, turn.role, turn.content, turn.metadata && { metadata: turn.metadata })
      .catch(e => log.error(`persist ${turn.role} turn failed`, e));
  }
}

// Factory

/** Create the session hooks. See the README for a full example. */
export function createNamsHooks(options: NamsHooksOptions) {
  const client = makeClient(options);
  const log = getLogger(client);
  const sessionLimit = options.sessionLimit ?? 40;
  const registry = compileHooks(
    options.hooks,
    log,
    options.onSystemMessage ?? ((message, event) => log.warn(`${event} hook: ${message}`)),
  );

  const resolveScope = (call: { userId?: string; conversationId?: string }, hookName: string): NamsScope => {
    const userId = call.userId ?? options.userId;
    if (!userId) {
      throw new Error(
        `${hookName} needs a userId — pass it per call, set it on createNamsHooks(), ` +
        `or (for onFinish) provide it via runtimeContext`,
      );
    }
    return { userId, conversationId: call.conversationId ?? options.conversationId };
  };

  const base = (scope: NamsScope) => ({
    userId: scope.userId,
    conversationId: scope.conversationId,
    timestamp: new Date(),
  });

  // Hook context queued for the next prepare().
  const pendingContext = new Map<string, string[]>();
  const startedSessions = new Set<string>();
  const scopeKey = (scope: NamsScope): string => `${scope.userId}::${scope.conversationId ?? ''}`;

  const queueContext = (scope: NamsScope, lines: string[]): void => {
    if (lines.length === 0) return;
    const key = scopeKey(scope);
    const queued = [...(pendingContext.get(key) ?? []), ...lines];
    pendingContext.set(key, queued.slice(-MAX_PENDING_CONTEXT));
  };

  const drainContext = (scope: NamsScope): string[] => {
    const key = scopeKey(scope);
    const queued = pendingContext.get(key) ?? [];
    pendingContext.delete(key);
    return queued;
  };

  /** Load past user and assistant messages (not tool records). Never throws; a new user gets `[]`. */
  const loadSession = async (opts: LoadSessionOptions = {}): Promise<ModelMessage[]> => {
    const scope = resolveScope(opts, 'loadSession');

    try {
      const convId = await findExistingConversation(client, options, scope);
      if (!convId) return [];

      const conversation = await client.shortTerm.getConversation(convId, {
        limit: opts.limit ?? sessionLimit,
      });

      return (conversation.messages ?? [])
        .filter(m =>
          (m.role === 'user' || m.role === 'assistant') &&
          !(m.metadata as Record<string, unknown> | undefined)?.[AUDIT_KIND_KEY])
        .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }));
    } catch (err) {
      log.warn('loadSession failed, continuing without history', err);
      return [];
    }
  };

  /** Build the messages for one turn and run the prompt hooks. Check `blocked` before calling the model. */
  const prepare = async (opts: PrepareOptions): Promise<PrepareResult> => {
    const scope = resolveScope(opts, 'prepare');
    const systemMessages: string[] = [];
    const context: string[] = [];
    let prompt = opts.prompt;

    const existing = await findExistingConversation(client, options, scope).catch(() => null);
    const known: NamsScope = { userId: scope.userId, conversationId: existing ?? scope.conversationId };

    if (registry.has('SessionStart') && !startedSessions.has(scopeKey(scope))) {
      startedSessions.add(scopeKey(scope));
      const started = await registry.run('SessionStart', {
        event: 'SessionStart',
        ...base(known),
        reason: existing ? 'resumed' : 'created',
      });
      context.push(...started.additionalContext);
      systemMessages.push(...started.systemMessages);
    }

    if (registry.has('UserPromptSubmit')) {
      const submitted = await registry.run('UserPromptSubmit', {
        event: 'UserPromptSubmit',
        ...base(known),
        prompt,
      });
      context.push(...submitted.additionalContext);
      systemMessages.push(...submitted.systemMessages);
      prompt = submitted.input.prompt;

      if (submitted.blocked) {
        return { messages: [], prompt, blocked: true, blockReason: submitted.reason, systemMessages };
      }
    }

    const history = await loadSession({ ...scope, limit: opts.limit });
    const carried = [...drainContext(scope), ...context];

    return {
      messages: [...history, { role: 'user', content: prompt }],
      instructions: carried.length ? carried.join('\n') : undefined,
      prompt,
      blocked: false,
      systemMessages,
    };
  };

  /** Wrap tools so each call runs the tool hooks. A denied tool returns `NamsToolBlocked` instead. */
  const withHooks = <T extends ToolSet>(tools: T, scope: Partial<NamsScope> = {}): T => {
    const wraps =
      registry.has('PreToolUse') || registry.has('PostToolUse') || registry.has('PostToolUseFailure');
    if (!wraps) return tools;

    const wrapped: Record<string, unknown> = {};

    for (const [toolName, definition] of Object.entries(tools)) {
      const execute = (definition as { execute?: (input: unknown, opts: unknown) => unknown }).execute;
      if (typeof execute !== 'function') {
        wrapped[toolName] = definition;
        continue;
      }

      wrapped[toolName] = {
        ...definition,
        execute: async (input: unknown, callOptions: { toolCallId?: string } = {}) => {
          const userId = scope.userId ?? options.userId;
          if (!userId) {
            log.warn(`withHooks needs a userId to run ${toolName} hooks, running the tool unhooked`);
            return execute(input, callOptions);
          }

          const resolved: NamsScope = {
            userId,
            conversationId: scope.conversationId ?? options.conversationId,
          };
          const call = { toolName, toolCallId: callOptions?.toolCallId };

          const pre = await registry.run('PreToolUse', {
            event: 'PreToolUse',
            ...base(resolved),
            ...call,
            input,
          });
          queueContext(resolved, pre.additionalContext);

          if (pre.blocked) {
            const reason = pre.reason ?? 'blocked by a PreToolUse hook';
            log.warn(`PreToolUse denied ${toolName}: ${reason}`);
            const blocked: NamsToolBlocked = { blocked: true, toolName, reason };
            return blocked;
          }

          const args = pre.input.input;

          for (let attempt = 1; ; attempt++) {
            try {
              const output = await execute(args, callOptions);
              const post = await registry.run('PostToolUse', {
                event: 'PostToolUse',
                ...base(resolved),
                ...call,
                input: args,
                output,
              });
              queueContext(resolved, post.additionalContext);
              return post.input.output;
            } catch (err) {
              const failure = await registry.run('PostToolUseFailure', {
                event: 'PostToolUseFailure',
                ...base(resolved),
                ...call,
                input: args,
                error: err,
                attempt,
              });
              queueContext(resolved, failure.additionalContext);
              // One retry only, so a hook cannot loop the tool forever.
              if (failure.retry && attempt === 1) continue;
              throw err;
            }
          }
        },
      };
    }

    return wrapped as unknown as T;
  };

  /** The callback that saves the turn. With `ToolLoopAgent`, pass the scope on `runtimeContext`. */
  const onFinish = (scope: OnFinishScope = {}): NamsOnFinishCallback => {
    return async (event) => {
      const context = (event.finalStep?.runtimeContext ?? event.runtimeContext ?? {}) as OnFinishScope;
      const merged: OnFinishScope = {
        userId: context.userId ?? scope.userId,
        conversationId: context.conversationId ?? scope.conversationId,
        prompt: context.prompt ?? scope.prompt,
        persistUserPrompt: context.persistUserPrompt ?? scope.persistUserPrompt,
      };
      const resolved = resolveScope(merged, 'onFinish');

      const turns: SessionTurn[] = [];
      if (merged.persistUserPrompt !== false && merged.prompt !== undefined) {
        const text = promptText(merged.prompt);
        if (text) turns.push({ role: 'user', content: text });
      }
      if (event.responseMessages?.length) {
        turns.push(...turnsFromResponse(event.responseMessages));
      } else if (event.text) {
        turns.push({ role: 'assistant', content: event.text });
      }

      let toPersist = turns;
      if (registry.has('PreMemoryWrite')) {
        const check = await registry.run('PreMemoryWrite', {
          event: 'PreMemoryWrite',
          ...base(resolved),
          turns,
        });
        queueContext(resolved, check.additionalContext);

        if (check.blocked) {
          log.warn(`PreMemoryWrite dropped this write: ${check.reason}`);
          toPersist = [];
        } else {
          toPersist = check.input.turns;
        }
      }

      try {
        const convId = await resolveConversation(client, options, resolved);
        await persistTurns(client, convId, toPersist);
      } catch (err) {
        log.error('onFinish failed to persist the generation', err);
      }

      if (registry.has('Stop')) {
        const stopped = await registry.run('Stop', {
          event: 'Stop',
          ...base(resolved),
          text: event.text,
          turns: toPersist,
        });
        queueContext(resolved, stopped.additionalContext);
      }
    };
  };

  /** Run `SessionEnd` and reset, so the next `prepare()` starts a new session. */
  const end = async (scope: { userId?: string; conversationId?: string; reason?: string } = {}): Promise<void> => {
    const resolved = resolveScope(scope, 'end');

    if (registry.has('SessionEnd')) {
      await registry.run('SessionEnd', {
        event: 'SessionEnd',
        ...base(resolved),
        reason: scope.reason ?? 'other',
      });
    }

    const key = scopeKey(resolved);
    startedSessions.delete(key);
    pendingContext.delete(key);
  };

  return { loadSession, prepare, withHooks, onFinish, end };
}

export type NamsHooks = ReturnType<typeof createNamsHooks>;
