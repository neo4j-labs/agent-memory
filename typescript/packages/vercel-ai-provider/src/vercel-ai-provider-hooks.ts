/**
 * Hooks mode — runtime-controlled (deterministic) session memory.
 *
 * Instead of exposing session persistence to the LLM as tools, the runtime
 * reads and writes the conversation transcript around every generation:
 *
 *   - `loadSession(scope)` — call inside `prepareCall` (ToolLoopAgent) or
 *     before `generateText` / `streamText`; returns the prior user/assistant
 *     turns as `ModelMessage[]` to prepend to `messages`.
 *   - `onFinish(scope?)`   — pass as the `onFinish` callback; persists the
 *     user prompt plus every assistant and tool turn of the finished
 *     generation, exactly once per call, regardless of what the LLM decides.
 *
 * Session transcript only. No entity extraction here — turns are persisted as
 * short-term messages and NAMS extracts those server-side. Use tools mode for
 * long-term memory.
 */

import type { ModelMessage } from 'ai';
import type { MemoryClient } from '@neo4j-labs/agent-memory';
import {
  makeClient,
  getLogger,
  resolveConversation,
  findExistingConversation,
  type NamsConfig,
  type NamsScope,
} from './vercel-ai-provider-client';

// Options

export interface NamsHooksOptions extends NamsConfig {
  /** Default scope; every hook call may override per-call (or via runtimeContext). */
  userId?: string;
  conversationId?: string;
  /** Max prior turns `loadSession` restores (default: 40). */
  sessionLimit?: number;
}

export interface LoadSessionOptions {
  userId?: string;
  conversationId?: string;
  /** Max prior turns to restore. Defaults to the factory's `sessionLimit` (40). */
  limit?: number;
}

export interface OnFinishScope {
  userId?: string;
  conversationId?: string;
  /**
   * The user input of this generation, persisted as the `user` turn.
   * A `ModelMessage[]` prompt contributes its user messages' text.
   */
  prompt?: string | ModelMessage[];
  /**
   * Persist the user prompt as a session turn (default: true). Disable if you
   * already store the user turn yourself (e.g. in an API handler).
   */
  persistUserPrompt?: boolean;
}

// Event typing
//
// Structural rather than generic: the callback must be assignable to
// `onFinish` on ToolLoopAgent, generateText, and streamText without forcing
// the caller's TOOLS / RUNTIME_CONTEXT generics through this package.

interface ResponseMessageLike {
  role: string;
  content: unknown;
}

export interface NamsOnFinishEvent {
  /** Final assistant text. */
  readonly text?: string;
  /** Assistant + tool messages accumulated across all steps, in order. */
  readonly responseMessages?: ReadonlyArray<ResponseMessageLike>;
  /** Per-call scope in ai v7: `runtimeContext` flows through the generation. */
  readonly runtimeContext?: unknown;
  readonly finalStep?: { readonly runtimeContext?: unknown };
}

/**
 * The callback built by `onFinish()`. Compatible with `ToolLoopAgent`'s,
 * `generateText`'s, and `streamText`'s `onFinish` / `onEnd` options.
 */
export type NamsOnFinishCallback = (event: NamsOnFinishEvent) => Promise<void>;

// Turn extraction

/** Metadata key marking a persisted tool audit record. `loadSession` skips these. */
const AUDIT_KIND_KEY = 'namsKind';

interface SessionTurn {
  role: 'user' | 'assistant';
  content: string;
  metadata?: Record<string, unknown>;
}

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

/**
 * Flatten the generation's response messages into persistable session turns.
 * Assistant text becomes plain `assistant` turns; tool calls and tool results
 * become metadata-tagged audit records (NAMS has no `tool` message role).
 */
const turnsFromResponse = (messages: ReadonlyArray<ResponseMessageLike>): SessionTurn[] => {
  const turns: SessionTurn[] = [];

  for (const message of messages) {
    if (message.role === 'assistant') {
      if (typeof message.content === 'string') {
        if (message.content) turns.push({ role: 'assistant', content: message.content });
        continue;
      }
      if (!Array.isArray(message.content)) continue;
      // Preserve part order: consecutive text parts collapse into one turn,
      // flushed whenever a tool call interleaves.
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

/**
 * Create the runtime hooks for deterministic session memory. One factory holds
 * one memory client, so `loadSession` and `onFinish` resolve the same
 * conversation.
 *
 * ```ts
 * const session = createNamsHooks({ apiKey, userId: 'alice' });
 *
 * const agent = new ToolLoopAgent({
 *   model,
 *   callOptionsSchema: z.object({ userId: z.string(), conversationId: z.string().optional(), prompt: z.string() }),
 *   prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
 *     ...settings,
 *     messages: [
 *       ...(await session.loadSession(options)),
 *       { role: 'user', content: options!.prompt },
 *     ],
 *     runtimeContext: options,   // scope flows to onFinish
 *   }),
 *   onFinish: session.onFinish(),
 * });
 * ```
 */
export function createNamsHooks(options: NamsHooksOptions) {
  const client = makeClient(options);
  const log = getLogger(client);
  const sessionLimit = options.sessionLimit ?? 40;

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

  return {
    /**
     * Restore the prior transcript as `ModelMessage[]` — prepend it to
     * `messages` inside `prepareCall` (or before generateText/streamText).
     *
     * Only user/assistant turns are replayed. Tool audit records stay in NAMS
     * for audit/search but are not replayed, because providers reject orphan
     * tool results without the matching prior assistant tool call.
     *
     * Read-path guarantee: never creates a conversation and never throws on
     * backend errors — a brand-new user simply gets `[]`.
     */
    async loadSession(opts: LoadSessionOptions = {}): Promise<ModelMessage[]> {
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
    },

    /**
     * Build the post-generation hook. Persists the user prompt and every
     * assistant and tool turn — exactly once per generate()/stream() call.
     *
     * Two calling modes:
     *  1. **Closure mode** — bake scope in: `onFinish({ userId, prompt })`.
     *     Use with one-shot `generateText` / `streamText`.
     *  2. **Context mode** — `onFinish()` reads `{ userId, conversationId,
     *     prompt }` from the generation's `runtimeContext`. Required for
     *     `ToolLoopAgent`, which takes `onFinish` at construction time but
     *     forwards `runtimeContext` per call (set it inside `prepareCall`).
     *
     * Context wins per-call when both provide a field.
     */
    onFinish(scope: OnFinishScope = {}): NamsOnFinishCallback {
      return async (event) => {
        const context = (event.finalStep?.runtimeContext ?? event.runtimeContext ?? {}) as OnFinishScope;
        // Context wins per-call; closure scope back-fills what it omits.
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

        try {
          const convId = await resolveConversation(client, options, resolved);
          await persistTurns(client, convId, turns);
        } catch (err) {
          log.error('onFinish failed to persist the generation', err);
        }
      };
    },
  };
}

export type NamsHooks = ReturnType<typeof createNamsHooks>;
