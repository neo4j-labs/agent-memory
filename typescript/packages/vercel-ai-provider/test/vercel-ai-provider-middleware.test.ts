/**
 * Middleware mode (createNamsMemory().wrap) — the contract points an adopter
 * depends on when they wrap their model with NAMS memory (this same
 * middleware also underpins provider mode via createNamsProvider):
 *
 *  1. relevant memories are injected into the prompt before the model runs
 *  2. the prompt is left untouched when there are no memories
 *  3. the user + assistant turn is persisted after generate
 *  4. the streamed turn is persisted after the stream closes
 *  5. retrieval failures are non-fatal — the model still answers
 *  6. persistInteractions=false disables persistence
 *  7. an explicit conversationId wins over lazy resolution
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, makeFakeModel, drainStream, settle, type FakeClient } from './vercel-ai-provider-helpers';

const holder = vi.hoisted(() => ({ client: undefined as unknown }));

vi.mock('@neo4j-labs/agent-memory', () => ({
  MemoryClient: vi.fn().mockImplementation(() => holder.client),
}));

import { createNamsMemory } from '../src/vercel-ai-provider-middleware';

let fake: FakeClient;
let userCounter = 0;

/** Unique per test — client.ts keeps a module-global conversation cache. */
const freshUser = () => `user-${Date.now()}-${userCounter++}`;

beforeEach(() => {
  fake = makeFakeClient();
  holder.client = fake;
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

const userPrompt = (text: string) => [
  { role: 'user', content: [{ type: 'text', text }] },
];

describe('middleware mode — memory injection', () => {
  it('injects retrieved memories into the last user message', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Alex', description: 'User is named Alex and works at TechCorp', type: 'person', score: 0.9 },
    ]);

    const { model, capturedParams } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    await wrapped.doGenerate({ prompt: userPrompt('Where do I work?') } as any);

    const prompt = capturedParams[0].prompt;
    const lastUser = prompt[prompt.length - 1];
    const text = lastUser.content.map((p: any) => p.text).join('');
    expect(text).toContain('Relevant long-term memory');
    expect(text).toContain('User is named Alex and works at TechCorp');
    expect(text).toContain('Where do I work?');
  });

  it('leaves the prompt unchanged when no memories are found', async () => {
    const { model, capturedParams } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    await wrapped.doGenerate({ prompt: userPrompt('Hello') } as any);

    const lastUser = capturedParams[0].prompt.at(-1);
    const text = lastUser.content.map((p: any) => p.text).join('');
    expect(text).toBe('Hello');
  });

  it('survives total retrieval failure and still calls the model', async () => {
    fake.longTerm.searchEntities.mockRejectedValue(new Error('boom'));
    fake.shortTerm.searchMessages.mockRejectedValue(new Error('boom'));
    fake.reasoning.listSteps.mockRejectedValue(new Error('boom'));

    const { model } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    const result = await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);
    expect((result as any).content[0].text).toBe('Hello back');
  });
});

describe('middleware mode — persistence', () => {
  it('persists the clean user text and the assistant response after generate', async () => {
    // Memory hit ensures injection happens — persisted text must still be the original.
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Fact', description: 'Some stored fact', type: 'fact' },
    ]);

    const { model } = makeFakeModel('Graphs model relationships.');
    const conversationId = 'conv-explicit';
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId,
    });

    await wrapped.doGenerate({ prompt: userPrompt('Tell me about graphs.') } as any);

    const calls = fake.shortTerm.addMessage.mock.calls;
    expect(calls).toContainEqual([conversationId, 'user', 'Tell me about graphs.']);
    expect(calls).toContainEqual([conversationId, 'assistant', 'Graphs model relationships.']);
    // The injected memory block must NOT leak into the persisted user message.
    const persistedUser = calls.find((c) => c[1] === 'user')![2];
    expect(persistedUser).not.toContain('Relevant long-term memory');
  });

  it('persists the accumulated text after a stream completes', async () => {
    const { model } = makeFakeModel('Streamed answer');
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-stream',
    });

    const { stream } = await wrapped.doStream({ prompt: userPrompt('Stream it') } as any);
    await drainStream(stream);
    await settle();

    const calls = fake.shortTerm.addMessage.mock.calls;
    expect(calls).toContainEqual(['conv-stream', 'user', 'Stream it']);
    expect(calls).toContainEqual(['conv-stream', 'assistant', 'Streamed answer']);
  });

  it('reassembles streamed tool-call args (object generation) for persistence', async () => {
    const { model } = makeFakeModel('', [
      { type: 'tool-call-delta', toolCallId: 't1', argsTextDelta: '{"city":' },
      { type: 'tool-call-delta', toolCallId: 't1', argsTextDelta: '"Berlin"}' },
      { type: 'finish', finishReason: 'tool-calls' },
    ]);
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-obj',
    });

    const { stream } = await wrapped.doStream({ prompt: userPrompt('Weather?') } as any);
    await drainStream(stream);
    await settle();

    expect(fake.shortTerm.addMessage.mock.calls).toContainEqual([
      'conv-obj', 'assistant', '{"city":"Berlin"}',
    ]);
  });

  it('handles V3 stream chunks: tool-input-delta and tool-call with input', async () => {
    const { model } = makeFakeModel('', [
      { type: 'tool-input-delta', id: 't1', delta: '{"city":' },
      { type: 'tool-input-delta', id: 't1', delta: '"Berlin"}' },
      // The full tool-call supersedes the deltas for the same id — no duplication.
      { type: 'tool-call', toolCallId: 't1', toolName: 'weather', input: '{"city":"Berlin"}' },
      { type: 'finish', finishReason: 'tool-calls' },
    ]);
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-v3',
    });

    const { stream } = await wrapped.doStream({ prompt: userPrompt('Weather?') } as any);
    await drainStream(stream);
    await settle();

    const assistantCall = fake.shortTerm.addMessage.mock.calls.find((c) => c[1] === 'assistant');
    expect(assistantCall![2]).toBe('{"city":"Berlin"}');
    // Regression: V3 tool-call chunks have `input`, not `args` — the literal
    // string "undefined" must never be persisted.
    expect(assistantCall![2]).not.toContain('undefined');
  });

  it('persists the same user message only once across multi-step tool loops', async () => {
    const { model } = makeFakeModel('step answer');
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-loop',
    });

    // A tool loop calls doGenerate once per step with the same last user message.
    await wrapped.doGenerate({ prompt: userPrompt('Run the tools') } as any);
    await wrapped.doGenerate({ prompt: userPrompt('Run the tools') } as any);

    const userCalls = fake.shortTerm.addMessage.mock.calls.filter((c) => c[1] === 'user');
    expect(userCalls).toHaveLength(1);
    const assistantCalls = fake.shortTerm.addMessage.mock.calls.filter((c) => c[1] === 'assistant');
    expect(assistantCalls).toHaveLength(2);
  });

  it('injects exactly one memory block when a step is retried', async () => {
    // The AI SDK rebuilds the params object per retry attempt but reuses the
    // same prompt array, and transformParams re-runs on every attempt. Editing
    // that array in place stacked a second memory block onto the first.
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Alex', description: 'works at TechCorp', type: 'person', confidence: 0.9 },
    ]);

    const seen: string[] = [];
    const { model } = makeFakeModel();
    (model as any).doGenerate = vi.fn(async (p: any) => {
      seen.push(p.prompt.at(-1).content.map((c: any) => c.text).join(''));
      return {
        content: [{ type: 'text', text: 'ok' }],
        finishReason: 'stop',
        usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        warnings: [],
      };
    });

    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-retry',
    });

    const prompt = userPrompt('Where do I work?');
    await wrapped.doGenerate({ prompt } as any);   // attempt 1
    await wrapped.doGenerate({ prompt } as any);   // retry: same prompt array

    const blocks = (s: string) => (s.match(/Relevant long-term memory/g) ?? []).length;
    expect(blocks(seen[0])).toBe(1);
    expect(blocks(seen[1])).toBe(1);
    // The caller's array is never touched.
    expect(prompt[0].content).toEqual([{ type: 'text', text: 'Where do I work?' }]);
  });

  it('persists the user text, never the memory-augmented prompt, across a retry', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Alex', description: 'works at TechCorp', type: 'person', confidence: 0.9 },
    ]);

    const { model } = makeFakeModel('answer');
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-retry-persist',
    });

    const prompt = userPrompt('Where do I work?');
    await wrapped.doGenerate({ prompt } as any);
    await wrapped.doGenerate({ prompt } as any);

    const userCalls = fake.shortTerm.addMessage.mock.calls.filter(c => c[1] === 'user');
    expect(userCalls).toHaveLength(1);
    expect(userCalls[0][2]).toBe('Where do I work?');
    // A leaked memory block would poison short-term memory permanently, since
    // the stored message is itself retrievable on later turns.
    expect(userCalls[0][2]).not.toContain('Relevant long-term memory');
  });

  it('does not persist when persistInteractions is false', async () => {
    const { model } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k', persistInteractions: false }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-nopersist',
    });

    await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);

    expect(fake.shortTerm.addMessage).not.toHaveBeenCalled();
  });

  it('forwards the generate result unchanged even when persistence fails', async () => {
    fake.shortTerm.addMessage.mockRejectedValue(new Error('write failed'));

    const { model } = makeFakeModel('Still fine');
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-failwrite',
    });

    const result = await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);
    expect((result as any).content[0].text).toBe('Still fine');
  });
});

describe('middleware mode — conversation resolution', () => {
  it('uses an explicit conversationId without listing or creating conversations', async () => {
    const { model } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-fixed',
    });

    await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);

    expect(fake.shortTerm.createConversation).not.toHaveBeenCalled();
    expect(fake.shortTerm.addMessage.mock.calls[0][0]).toBe('conv-fixed');
  });

  it('resumes the most recent conversation when none is supplied', async () => {
    fake.shortTerm.listConversations.mockImplementation(async ({ limit }: { limit: number }) =>
      limit === 1 ? [{ id: 'conv-resumed' }] : [],
    );

    const { model } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);

    expect(fake.shortTerm.createConversation).not.toHaveBeenCalled();
    expect(fake.shortTerm.addMessage.mock.calls[0][0]).toBe('conv-resumed');
  });

  it('creates a conversation when the user has none', async () => {
    const userId = freshUser();
    const { model } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId });

    await wrapped.doGenerate({ prompt: userPrompt('Hi') } as any);

    expect(fake.shortTerm.createConversation).toHaveBeenCalledWith({ userId });
    expect(fake.shortTerm.addMessage.mock.calls[0][0]).toBe(`conv-${userId}`);
  });
});
