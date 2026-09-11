/**
 * Hooks mode — runtime-controlled session memory.
 *
 * Contract points:
 *  - loadSession restores user/assistant turns as ModelMessage[], skips tool
 *    audit records, never creates a conversation, returns [] on errors
 *  - onFinish persists prompt + assistant text + tool call/result audit
 *    records via bulkAddMessages, exactly once per generation
 *  - onFinish falls back to per-message writes when bulk fails
 *  - context mode: scope read from runtimeContext, winning over closure scope
 *  - missing userId throws (wiring bug, not a runtime condition)
 *  - the built callback is assignable to the AI SDK's onFinish slots
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, type FakeClient } from './vercel-ai-provider-helpers';

const holder = vi.hoisted(() => ({ client: undefined as unknown }));

vi.mock('@neo4j-labs/agent-memory', () => ({
  MemoryClient: vi.fn().mockImplementation(() => holder.client),
}));

import { createNamsHooks } from '../src/vercel-ai-provider-hooks';
import { createNams } from '../src/index';

let fake: FakeClient;
let userCounter = 0;
const freshUser = () => `hooks-user-${Date.now()}-${userCounter++}`;

const config = { apiKey: 'test-key' };

beforeEach(() => {
  fake = makeFakeClient();
  holder.client = fake;
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('loadSession', () => {
  it('returns [] for a user with no conversations, without creating one', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser() });
    const messages = await hooks.loadSession();

    expect(messages).toEqual([]);
    expect(fake.shortTerm.createConversation).not.toHaveBeenCalled();
  });

  it('restores user/assistant turns and skips tool audit records', async () => {
    const userId = freshUser();
    fake.shortTerm.listConversations.mockResolvedValue([{ id: 'conv-1' }]);
    fake.shortTerm.getConversation.mockResolvedValue({
      id: 'conv-1',
      messages: [
        { role: 'user', content: 'Hi, I am Alex', metadata: {} },
        { role: 'assistant', content: '[tool-call] search: {}', metadata: { namsKind: 'tool-call' } },
        { role: 'assistant', content: '[tool-result] search: {}', metadata: { namsKind: 'tool-result' } },
        { role: 'assistant', content: 'Hello Alex!', metadata: {} },
        { role: 'system', content: 'internal', metadata: {} },
      ],
    });

    const hooks = createNamsHooks({ ...config, userId });
    const messages = await hooks.loadSession();

    expect(messages).toEqual([
      { role: 'user', content: 'Hi, I am Alex' },
      { role: 'assistant', content: 'Hello Alex!' },
    ]);
    expect(fake.shortTerm.getConversation).toHaveBeenCalledWith('conv-1', { limit: 40 });
  });

  it('passes an explicit limit through and prefers per-call scope', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser() });
    fake.shortTerm.getConversation.mockResolvedValue({ id: 'conv-x', messages: [] });

    await hooks.loadSession({ conversationId: 'conv-x', limit: 7 });

    expect(fake.shortTerm.getConversation).toHaveBeenCalledWith('conv-x', { limit: 7 });
    expect(fake.shortTerm.listConversations).not.toHaveBeenCalled();
  });

  it('returns [] instead of throwing when the backend read fails', async () => {
    fake.shortTerm.listConversations.mockResolvedValue([{ id: 'conv-1' }]);
    fake.shortTerm.getConversation.mockRejectedValue(new Error('boom'));

    const hooks = createNamsHooks({ ...config, userId: freshUser() });
    await expect(hooks.loadSession()).resolves.toEqual([]);
  });

  it('throws when no userId is available anywhere', async () => {
    const hooks = createNamsHooks(config);
    await expect(hooks.loadSession()).rejects.toThrow(/userId/);
  });
});

describe('onFinish — closure mode', () => {
  it('persists prompt, assistant text, and tool turns in order via bulkAddMessages', async () => {
    const userId = freshUser();
    const hooks = createNamsHooks({ ...config, userId, conversationId: 'conv-1' });

    const callback = hooks.onFinish({ prompt: 'What is the weather?' });
    await callback({
      text: 'It is sunny.',
      responseMessages: [
        {
          role: 'assistant',
          content: [
            { type: 'tool-call', toolCallId: 'c1', toolName: 'get_weather', input: { city: 'Oslo' } },
          ],
        },
        {
          role: 'tool',
          content: [
            { type: 'tool-result', toolCallId: 'c1', toolName: 'get_weather', output: { type: 'json', value: { temp: 21 } } },
          ],
        },
        { role: 'assistant', content: [{ type: 'text', text: 'It is sunny.' }] },
      ],
    });

    expect(fake.shortTerm.bulkAddMessages).toHaveBeenCalledTimes(1);
    const [convId, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(convId).toBe('conv-1');
    expect(turns).toEqual([
      { role: 'user', content: 'What is the weather?', metadata: undefined },
      {
        role: 'assistant',
        content: '[tool-call] get_weather: {"city":"Oslo"}',
        metadata: { namsKind: 'tool-call', toolName: 'get_weather', toolCallId: 'c1' },
      },
      {
        role: 'assistant',
        content: '[tool-result] get_weather: {"temp":21}',
        metadata: { namsKind: 'tool-result', toolName: 'get_weather', toolCallId: 'c1' },
      },
      { role: 'assistant', content: 'It is sunny.', metadata: undefined },
    ]);
    expect(fake.shortTerm.addMessage).not.toHaveBeenCalled();
  });

  it('collapses interleaved text around a tool call in part order', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });

    await hooks.onFinish({ persistUserPrompt: false })({
      responseMessages: [
        {
          role: 'assistant',
          content: [
            { type: 'text', text: 'Let me check. ' },
            { type: 'tool-call', toolCallId: 'c1', toolName: 'search', input: {} },
            { type: 'text', text: 'Done.' },
          ],
        },
      ],
    });

    const [, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(turns.map((t: { content: string }) => t.content)).toEqual([
      'Let me check. ',
      '[tool-call] search: {}',
      'Done.',
    ]);
  });

  it('falls back to event.text when responseMessages is absent', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });

    await hooks.onFinish({ prompt: 'Hi' })({ text: 'Hello!' });

    const [, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(turns).toEqual([
      { role: 'user', content: 'Hi', metadata: undefined },
      { role: 'assistant', content: 'Hello!', metadata: undefined },
    ]);
  });

  it('extracts user text from a ModelMessage[] prompt', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });

    await hooks.onFinish({
      prompt: [
        { role: 'system', content: 'be nice' },
        { role: 'user', content: [{ type: 'text', text: 'part one' }] },
        { role: 'user', content: 'part two' },
      ] as never,
    })({ text: 'ok' });

    const [, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(turns[0]).toEqual({ role: 'user', content: 'part one\npart two', metadata: undefined });
  });

  it('skips the user turn when persistUserPrompt is false', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });

    await hooks.onFinish({ prompt: 'Hi', persistUserPrompt: false })({ text: 'Hello!' });

    const [, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(turns).toEqual([{ role: 'assistant', content: 'Hello!', metadata: undefined }]);
  });

  it('falls back to per-message writes when bulkAddMessages fails', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });
    fake.shortTerm.bulkAddMessages.mockRejectedValue(new Error('bulk unsupported'));

    await hooks.onFinish({ prompt: 'Hi' })({ text: 'Hello!' });

    expect(fake.shortTerm.addMessage).toHaveBeenCalledTimes(2);
    expect(fake.shortTerm.addMessage).toHaveBeenNthCalledWith(1, 'conv-1', 'user', 'Hi', undefined);
    expect(fake.shortTerm.addMessage).toHaveBeenNthCalledWith(2, 'conv-1', 'assistant', 'Hello!', undefined);
  });

  it('creates a conversation when the user has none yet', async () => {
    const userId = freshUser();
    const hooks = createNamsHooks({ ...config, userId });

    await hooks.onFinish({ prompt: 'Hi' })({ text: 'Hello!' });

    expect(fake.shortTerm.createConversation).toHaveBeenCalledWith({ userId });
    const [convId] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(convId).toBe(`conv-${userId}`);
  });

  it('does not throw when persistence fails entirely', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser(), conversationId: 'conv-1' });
    fake.shortTerm.bulkAddMessages.mockRejectedValue(new Error('down'));
    fake.shortTerm.addMessage.mockRejectedValue(new Error('down'));

    await expect(hooks.onFinish({ prompt: 'Hi' })({ text: 'Hello!' })).resolves.toBeUndefined();
  });
});

describe('onFinish — context mode', () => {
  it('reads scope from runtimeContext and it wins over closure scope', async () => {
    const hooks = createNamsHooks({ ...config, userId: 'factory-user', conversationId: 'conv-factory' });

    await hooks.onFinish({ prompt: 'closure prompt' })({
      text: 'Hello!',
      runtimeContext: { userId: 'ctx-user', conversationId: 'conv-ctx', prompt: 'ctx prompt' },
    });

    const [convId, turns] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(convId).toBe('conv-ctx');
    expect(turns[0]).toEqual({ role: 'user', content: 'ctx prompt', metadata: undefined });
  });

  it('prefers finalStep.runtimeContext over the deprecated top-level field', async () => {
    const hooks = createNamsHooks(config);

    await hooks.onFinish()({
      text: 'Hello!',
      runtimeContext: { userId: 'old', conversationId: 'conv-old', prompt: 'p' },
      finalStep: { runtimeContext: { userId: 'new', conversationId: 'conv-new', prompt: 'p' } },
    });

    const [convId] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(convId).toBe('conv-new');
  });

  it('throws when neither closure scope nor context carries a userId', async () => {
    const hooks = createNamsHooks(config);
    await expect(hooks.onFinish()({ text: 'Hello!' })).rejects.toThrow(/userId/);
  });
});

describe('createNams().hooks()', () => {
  it('exposes the hooks bound to the factory scope', async () => {
    const userId = freshUser();
    const nams = createNams({ apiKey: 'test-key' });
    const session = nams.hooks({ userId, conversationId: 'conv-1' });

    await session.onFinish({ prompt: 'Hi' })({ text: 'Hello!' });

    const [convId] = fake.shortTerm.bulkAddMessages.mock.calls[0];
    expect(convId).toBe('conv-1');
    await expect(session.loadSession()).resolves.toBeDefined();
  });
});

describe('type compatibility', () => {
  it('the built callback is assignable to the AI SDK onFinish slots', () => {
    const hooks = createNamsHooks({ ...config, userId: 'u' });
    // Compile-time check: these assignments fail typecheck if the structural
    // event type drifts from the SDK's GenerateTextEndEvent.
    type GenerateTextArgs = NonNullable<Parameters<typeof import('ai').generateText>[0]['onFinish']>;
    const asGenerateTextOnFinish: GenerateTextArgs = hooks.onFinish();
    expect(asGenerateTextOnFinish).toBeTypeOf('function');
  });
});
