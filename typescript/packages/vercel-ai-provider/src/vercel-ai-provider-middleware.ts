/** Middleware mode: `wrap(model, scope)` adds memories to each turn and saves the conversation. Provider mode uses this too. */

import { wrapLanguageModel } from 'ai';
import type { LanguageModelMiddleware } from 'ai';
import {
  makeClient,
  getLogger,
  resolveConversation,
  retrieveMemories,
} from './vercel-ai-provider-client';
import { MemoryHit, NamsConfig, NamsScope } from './vercel-ai-provider-types';

// Model types come from `ai`, so they match the caller's `ai` version.
export type WrappableModel = Parameters<typeof wrapLanguageModel>[0]['model'];
export type WrappedModel = ReturnType<typeof wrapLanguageModel>;

export interface NamsMemoryConfig extends NamsConfig {
  /** Max memories added to the prompt per turn (default: 6). */
  maxMemories?: number;
  /** Save each turn to NAMS (default: true). */
  persistInteractions?: boolean;
}


const lastUserIndex = (prompt: any[]): number => {
  for (let i = prompt.length - 1; i >= 0; i--) {
    if (prompt[i]?.role === 'user') return i;
  }
  return -1;
}

/** True for a later step of the same turn: tool results come after the last user message. */
const continuesTurn = (prompt: any[]): boolean => {
  const i = lastUserIndex(prompt);
  return i >= 0 && prompt.slice(i + 1).some(m => m?.role === 'tool');
}

/** True when the model asks the app to run a tool. */
const callsAppTool = (part: any): boolean =>
  part?.type === 'tool-call' && !part.providerExecuted;

/** Copy the prompt, with `block` added to the start of the last user message. */
const withMemoryBlock = (prompt: any[], block: string): any[] => {
  const i = lastUserIndex(prompt);
  if (i < 0) return prompt;

  const msg = prompt[i];
  let content: unknown;
  if (typeof msg.content === 'string') content = `${block}\n\n${msg.content}`;
  else if (Array.isArray(msg.content)) content = [{ type: 'text', text: `${block}\n\n` }, ...msg.content];
  else return prompt;

  const next = [...prompt];
  next[i] = { ...msg, content };
  return next;
}

/** The model's answer, or '' when this step only calls tools. */
const answerFromResult = (result: any): string => {
  const content: any[] = Array.isArray(result?.content) ? result.content : [];
  if (content.some(callsAppTool)) return '';
  if (typeof result?.text === 'string' && result.text) return result.text;
  return content
    .filter(p => p?.type === 'text')
    .map(p => p.text as string)
    .join('');
}

const formatMemoryBlock = (memories: MemoryHit[]): string => {
  // Explained only when there is a triple to explain.
  const graphNote = memories.some(m => m.source === 'graph')
    ? '\nA [graph] line is a stored relationship, written (subject)-[RELATIONSHIP]->(object).'
    : '';

  return (
    'Relevant long-term memory about this user (use it to personalise your answer):\n' +
    memories.map((m, i) => `${i + 1}. [${m.source}] ${m.content}`).join('\n') +
    graphNote
  );
}

// Text of the last user message.
const lastUserText = (prompt: any[]): string => {
  const i = lastUserIndex(prompt);
  if (i < 0) return '';
  const msg = prompt[i];
  if (typeof msg.content === 'string') return msg.content;
  if (Array.isArray(msg.content))
    return msg.content
      .filter((p: any) => p?.type === 'text')
      .map((p: any) => p.text as string)
      .join('')
      .trim();
  return '';
}

const buildMiddleware = (
  config: NamsMemoryConfig,
  scope: NamsScope,
  maxMemories: number,
  persist: boolean,
): LanguageModelMiddleware => {
  const client = makeClient(config);
  const log = getLogger(client);

  let convIdPromise: Promise<string> | null = null;
  const getConvId = (): Promise<string> =>
    (convIdPromise ??= resolveConversation(client, config, scope));

  const originalUserText = new WeakMap<object, string>();

  // Memories found at the start of the turn. Later steps reuse them.
  let turnMemories: { userText: string; memories: MemoryHit[] } | undefined;

  // Save the user's message on the first step and the answer on the last.
  async function persistTurn(params: any, assistantText: string): Promise<void> {
    if (!persist) return;
    const convId = await getConvId();
    if (!continuesTurn(params.prompt)) {
      const userText = originalUserText.get(params as object) ?? lastUserText(params.prompt);
      if (userText) await client.shortTerm.addMessage(convId, 'user', userText)
        .catch(e => log.error('persist user message failed', e));
    }
    if (assistantText) await client.shortTerm.addMessage(convId, 'assistant', assistantText)
      .catch(e => log.error('persist assistant message failed', e));
  }

  async function memoriesFor(prompt: any[], userText: string): Promise<MemoryHit[] | null> {
    if (continuesTurn(prompt) && turnMemories?.userText === userText) return turnMemories.memories;

    let convId: string;
    try {
      convId = await getConvId();
    } catch (e) {
      log.warn('resolveConversation failed', e);
      return null;
    }

    const memories = await retrieveMemories(
      client, scope, convId, userText, maxMemories,
      { crossSessionLimit: config.crossSessionLimit, graphExpansionLimit: config.graphExpansionLimit },
    ).catch(e => { log.warn('retrieve failed', e); return [] as MemoryHit[]; });
    turnMemories = { userText, memories };
    return memories;
  }

  return {
    specificationVersion: 'v4',
    // Find memories for the user message and add them to the prompt.
    transformParams: async ({ params }) => {
      const userText = lastUserText(params.prompt);
      if (!userText) return params;
      originalUserText.set(params as object, userText);

      const memories = await memoriesFor(params.prompt, userText);
      if (!memories?.length) return params;

      const augmented = { ...params, prompt: withMemoryBlock(params.prompt, formatMemoryBlock(memories)) };
      originalUserText.set(augmented as object, userText);
      return augmented;
    },

    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      await persistTurn(params, answerFromResult(result))
        .catch(e => log.warn('persist failed', e));
      return result;
    },

    // Collect the streamed text. Save the turn when the stream closes.
    wrapStream: async ({ doStream, params }) => {
      const { stream, ...rest } = await doStream();
      let text = '';
      let handsOffToTools = false;

      const tap = new TransformStream({
        transform(chunk: any, controller) {
          if (chunk?.type === 'text-delta')
            text += (chunk.delta ?? chunk.textDelta ?? chunk.text ?? '') as string;
          else if (chunk?.type === 'text')
            text += (chunk.text ?? '') as string;
          else if (callsAppTool(chunk))
            handsOffToTools = true;
          controller.enqueue(chunk);
        },
        async flush() {
          await persistTurn(params, handsOffToTools ? '' : text)
            .catch(e => log.warn('persist failed', e));
        },
      });

      return { stream: stream.pipeThrough(tap), ...rest };
    },
  };
}

/** Create memory middleware. `wrap(model, scope)` returns the model with memory. */
export function createNamsMemory(config: NamsMemoryConfig) {
  const maxMemories = config.maxMemories ?? 6;
  const persist = config.persistInteractions ?? true;

  return {
    wrap(model: WrappableModel, scope: NamsScope, providerId?: string): WrappedModel {
      const middleware = buildMiddleware(config, scope, maxMemories, persist);
      return wrapLanguageModel({ model, middleware, ...(providerId && { providerId }) });
    },
  };
}
