/**
 * Middleware mode (also used by provider mode).
 *
 *  1. memories are added to the prompt before the model runs
 *  2. the prompt is unchanged when there are no memories
 *  3. the turn is saved after generate
 *  4. a streamed turn is saved when the stream closes
 *  5. a retrieval failure doesn't stop the model
 *  6. persistInteractions=false turns saving off
 *  7. an explicit conversationId is used as-is
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

/** A new user per test, so cached conversations never carry over. */
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

  it('injects stored relationships as triples, and explains the notation once', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { id: 'ent-alex', name: 'Alex', description: 'The user', type: 'person' },
    ]);
    fake.longTerm.getEntity.mockResolvedValue({
      id: 'ent-alex',
      name: 'Alex',
      relationships: [
        { id: 'r1', type: 'WORKS_AT', targetId: 'ent-tc', targetName: 'TechCorp' },
      ],
    });

    const { model, capturedParams } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    await wrapped.doGenerate({ prompt: userPrompt('Where do I work?') } as any);

    const text = capturedParams[0].prompt.at(-1).content.map((p: any) => p.text).join('');
    expect(text).toContain('[graph] (Alex)-[WORKS_AT]->(TechCorp)');
    expect(text).toContain('(subject)-[RELATIONSHIP]->(object)');
  });

  it('does not explain the triple notation when no relationship was found', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { id: 'ent-alex', name: 'Alex', description: 'The user', type: 'person' },
    ]);

    const { model, capturedParams } = makeFakeModel();
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, { userId: freshUser() });

    await wrapped.doGenerate({ prompt: userPrompt('Who am I?') } as any);

    const text = capturedParams[0].prompt.at(-1).content.map((p: any) => p.text).join('');
    expect(text).toContain('Alex — The user');
    expect(text).not.toContain('(subject)-[RELATIONSHIP]->(object)');
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
    // A memory is added to the prompt, but the saved text must be the original.
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
    // The memory block must not end up in the saved user message.
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

  it('saves no assistant message for a streamed step that calls tools', async () => {
    // Tool arguments are not an answer. Saved as one, they come back later as a "memory".
    const { model } = makeFakeModel('', [
      { type: 'tool-input-delta', id: 't1', delta: '{"city":' },
      { type: 'tool-input-delta', id: 't1', delta: '"Berlin"}' },
      { type: 'tool-call', toolCallId: 't1', toolName: 'weather', input: '{"city":"Berlin"}' },
      { type: 'finish', finishReason: 'tool-calls' },
    ]);
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-tools',
    });

    const { stream } = await wrapped.doStream({ prompt: userPrompt('Weather?') } as any);
    await drainStream(stream);
    await settle();

    expect(fake.shortTerm.addMessage.mock.calls).toEqual([['conv-tools', 'user', 'Weather?']]);
  });

  it('saves the answer when the only tool calls were run by the provider', async () => {
    const { model } = makeFakeModel();
    (model as any).doGenerate = vi.fn(async () => ({
      content: [
        { type: 'tool-call', toolCallId: 'w1', toolName: 'web_search', input: '{}', providerExecuted: true },
        { type: 'tool-result', toolCallId: 'w1', toolName: 'web_search', result: [] },
        { type: 'text', text: 'Found it.' },
      ],
      finishReason: 'stop',
      usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
      warnings: [],
    }));
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-provider-tool',
    });

    await wrapped.doGenerate({ prompt: userPrompt('Search the web') } as any);

    expect(fake.shortTerm.addMessage.mock.calls).toContainEqual(['conv-provider-tool', 'assistant', 'Found it.']);
  });

  it('saves one user and one assistant message for a whole tool loop, searching once', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Alex', description: 'lives in Paris', type: 'person', confidence: 0.9 },
    ]);
    const seen: any[] = [];
    let step = 0;
    const { model } = makeFakeModel();
    (model as any).doGenerate = vi.fn(async (p: any) => {
      seen.push(p.prompt);
      return ++step === 1
        ? {
            content: [{ type: 'tool-call', toolCallId: 'c1', toolName: 'weather', input: '{"city":"Paris"}' }],
            finishReason: 'tool-calls',
            usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
            warnings: [],
          }
        : {
            content: [{ type: 'text', text: 'Sunny in Paris.' }],
            finishReason: 'stop',
            usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
            warnings: [],
          };
    });
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-loop',
    });

    // What a tool loop sends: step 2 repeats the user message, then the call and its result.
    const first = userPrompt('Weather where I live?');
    await wrapped.doGenerate({ prompt: first } as any);
    const searchesAfterStep1 = fake.longTerm.searchEntities.mock.calls.length;
    await wrapped.doGenerate({
      prompt: [
        ...first,
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'c1', toolName: 'weather', input: { city: 'Paris' } }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'c1', toolName: 'weather', output: { type: 'text', value: 'sunny' } }] },
      ],
    } as any);

    expect(fake.shortTerm.addMessage.mock.calls).toEqual([
      ['conv-loop', 'user', 'Weather where I live?'],
      ['conv-loop', 'assistant', 'Sunny in Paris.'],
    ]);
    // Step 2 reuses step 1's memories instead of searching again...
    expect(fake.longTerm.searchEntities.mock.calls.length).toBe(searchesAfterStep1);
    // ...and still shows the model the same memory block.
    const userTextAt = (prompt: any[]) => prompt[0].content.map((c: any) => c.text).join('');
    expect(userTextAt(seen[1])).toBe(userTextAt(seen[0]));
    expect(userTextAt(seen[1])).toContain('lives in Paris');
  });

  it('saves a repeated user message as a new turn', async () => {
    // Apps that rely on memory send only the latest message, so "yes" twice is two turns.
    const { model } = makeFakeModel('ok');
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-repeat',
    });

    await wrapped.doGenerate({ prompt: userPrompt('yes') } as any);
    await wrapped.doGenerate({ prompt: userPrompt('yes') } as any);

    expect(fake.shortTerm.addMessage.mock.calls.map(c => `${c[1]}:${c[2]}`)).toEqual([
      'user:yes', 'assistant:ok', 'user:yes', 'assistant:ok',
    ]);
  });

  it('injects exactly one memory block when a step is retried', async () => {
    // Retries reuse the prompt array and rerun transformParams. Editing it in
    // place used to add a second memory block.
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
    const generate = (model as any).doGenerate;
    // The SDK retries only after a failed call, so the first attempt throws.
    (model as any).doGenerate = vi.fn()
      .mockRejectedValueOnce(new Error('503'))
      .mockImplementation(generate);
    const wrapped = createNamsMemory({ apiKey: 'k' }).wrap(model, {
      userId: freshUser(),
      conversationId: 'conv-retry-persist',
    });

    const prompt = userPrompt('Where do I work?');
    await expect(wrapped.doGenerate({ prompt } as any)).rejects.toThrow('503');
    await wrapped.doGenerate({ prompt } as any);

    const userCalls = fake.shortTerm.addMessage.mock.calls.filter(c => c[1] === 'user');
    expect(userCalls).toHaveLength(1);
    expect(userCalls[0][2]).toBe('Where do I work?');
    // A leaked block would be saved, then found again on later turns.
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
