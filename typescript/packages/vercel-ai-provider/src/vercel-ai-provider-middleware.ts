/**
 * Middleware mode — transparent memory, no tool calls.
 *
 * createNamsMemory(config).wrap(model, scope) returns a LanguageModel that
 * injects relevant memories before every call and persists each turn after.
 * This middleware also underpins provider mode (see vercel-ai-provider.ts).
 */

import { wrapLanguageModel, type LanguageModel } from 'ai';
import type { LanguageModelV4, LanguageModelV4Middleware } from '@ai-sdk/provider';
import {
  makeClient,
  getLogger,
  resolveConversation,
  retrieveMemories,
} from './vercel-ai-provider-client';
import { createGraphExtractor } from './vercel-ai-provider-extract';
import { GraphExtractor, MemoryHit, NamsConfig, NamsScope } from './vercel-ai-provider-types';

export interface NamsMemoryConfig extends NamsConfig {
  /** Max memories retrieved and injected into the prompt per turn (default: 6). Does not affect storage. */
  maxMemories?: number;
  /** Persist each turn to NAMS short-term memory (default: true). */
  persistInteractions?: boolean;
  /** When set, build a real entity graph per stored turn (one extra model call). */
  extractionModel?: LanguageModel;
}


const injectIntoLastUser = (prompt: any[], block: string): void => {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const msg = prompt[i];
    if (msg?.role !== 'user') continue;
    if (typeof msg.content === 'string') {
      msg.content = `${block}\n\n${msg.content}`;
    } else if (Array.isArray(msg.content)) {
      msg.content.unshift({ type: 'text', text: `${block}\n\n` });
    }
    return;
  }
}

const toolCallInput = (part: any): string => {
  if (typeof part?.input === 'string') return part.input;
  const args = part?.input ?? part?.args;
  if (args === undefined) return '';
  try { return JSON.stringify(args) ?? ''; } catch { return ''; }
}

// Extract assistant text from a generate result. Falls back to serialized
// tool-call input so structured responses (e.g. generateObject) still persist.
const textFromResult = (result: any): string => {
  if (typeof result?.text === 'string' && result.text) return result.text;
  if (Array.isArray(result?.content)) {
    const textParts = (result.content as any[])
      .filter(p => p?.type === 'text')
      .map(p => p.text as string)
      .join('');
    if (textParts) return textParts;
    return (result.content as any[])
      .filter(p => p?.type === 'tool-call')
      .map(toolCallInput)
      .join('');
  }
  return '';
}

const formatMemoryBlock = (memories: MemoryHit[]): string => {
  return (
    'Relevant long-term memory about this user (use it to personalise your answer):\n' +
    memories.map((m, i) => `${i + 1}. [${m.source}] ${m.content}`).join('\n')
  );
}

// Text of the most recent user message in the prompt.
const lastUserText = (prompt: any[]): string => {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const msg = prompt[i];
    if (msg?.role !== 'user') continue;
    if (typeof msg.content === 'string') return msg.content;
    if (Array.isArray(msg.content))
      return msg.content
        .filter((p: any) => p?.type === 'text')
        .map((p: any) => p.text as string)
        .join('')
        .trim();
  }
  return '';
}

const buildMiddleware = (
  config: NamsMemoryConfig,
  scope: NamsScope,
  extractor: GraphExtractor | undefined,
  maxMemories: number,
  persist: boolean,
): LanguageModelV4Middleware => {
  const client = makeClient(config);
  const log = getLogger(client);

  let convIdPromise: Promise<string> | null = null;
  const getConvId = (): Promise<string> =>
    (convIdPromise ??= resolveConversation(client, config, scope));

  const originalUserText = new WeakMap<object, string>();

  // In a multi-step tool loop every step carries the same last user message —
  // remember what was persisted so it is stored once per turn, not per step.
  let lastPersistedUserText: string | undefined;

  async function persistTurn(params: any, assistantText: string): Promise<void> {
    if (!persist) return;
    const convId = await getConvId();
    const userText = originalUserText.get(params as object) ?? lastUserText(params.prompt);
    if (userText && userText !== lastPersistedUserText) {
      lastPersistedUserText = userText;
      await client.shortTerm.addMessage(convId, 'user', userText)
        .catch(e => log.error('persist user message failed', e));
    }
    if (assistantText) await client.shortTerm.addMessage(convId, 'assistant', assistantText)
      .catch(e => log.error('persist assistant message failed', e));
    if (extractor && (userText || assistantText)) {
      const combined = `User: ${userText}\nAssistant: ${assistantText}`.trim();
      await extractor(client, { content: combined, type: 'interaction' })
        .catch(e => log.warn('turn extraction failed', e));
    }
  }

  return {
    specificationVersion: 'v4',
    // Retrieve memories for the user query and inject them into the prompt.
    transformParams: async ({ params }) => {
      const userText = lastUserText(params.prompt);
      if (!userText) return params;

      originalUserText.set(params as object, userText);

      let convId: string;
      try {
        convId = await getConvId();
      } catch (e) {
        log.warn('resolveConversation failed', e);
        return params;
      }

      const memories = await retrieveMemories(client, scope, convId, userText, maxMemories)
        .catch(e => { log.warn('retrieve failed', e); return [] as MemoryHit[]; });

      if (memories.length === 0) return params;

      injectIntoLastUser(params.prompt, formatMemoryBlock(memories));
      return params;
    },

    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      await persistTurn(params, textFromResult(result))
        .catch(e => log.warn('persist failed', e));
      return result;
    },

    // Tap the stream to accumulate text and tool-call args; persist the full
    // turn in flush once the stream closes.
    wrapStream: async ({ doStream, params }) => {
      const { stream, ...rest } = await doStream();
      let text = '';
      const pendingToolArgs = new Map<string, string>();

      const tap = new TransformStream({
        transform(chunk: any, controller) {
          if (chunk?.type === 'text-delta')
            text += (chunk.delta ?? chunk.textDelta ?? chunk.text ?? '') as string;
          else if (chunk?.type === 'text')
            text += (chunk.text ?? '') as string;
          else if (chunk?.type === 'tool-input-delta') {
            const id = chunk.id as string;
            pendingToolArgs.set(id, (pendingToolArgs.get(id) ?? '') + (chunk.delta ?? ''));
          } else if (chunk?.type === 'tool-call-delta') {
            const id = chunk.toolCallId as string;
            pendingToolArgs.set(id, (pendingToolArgs.get(id) ?? '') + (chunk.argsTextDelta ?? ''));
          } else if (chunk?.type === 'tool-call') {
            pendingToolArgs.set((chunk.toolCallId ?? chunk.id) as string, toolCallInput(chunk));
          }
          controller.enqueue(chunk);
        },
        async flush() {
          for (const args of pendingToolArgs.values()) {
            if (args) text += args;
          }
          await persistTurn(params, text)
            .catch(e => log.warn('persist failed', e));
        },
      });

      return { stream: stream.pipeThrough(tap), ...rest };
    },
  };
}

/**
 * Create a NAMS memory provider. `wrap(model, scope)` returns a drop-in
 * LanguageModelV4 with transparent memory retrieval and persistence.
 */
export function createNamsMemory(config: NamsMemoryConfig) {
  const extractor = config.extractionModel ? createGraphExtractor(config.extractionModel) : undefined;
  const maxMemories = config.maxMemories ?? 6;
  const persist = config.persistInteractions ?? true;

  return {
    wrap(model: LanguageModelV4, scope: NamsScope, providerId?: string): LanguageModelV4 {
      const middleware = buildMiddleware(config, scope, extractor, maxMemories, persist);
      return wrapLanguageModel({ model, middleware, ...(providerId && { providerId }) });
    },
  };
}
