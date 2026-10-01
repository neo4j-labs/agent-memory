/**
 * Lifecycle hooks.
 *
 * Checks:
 *  - matchers: empty/`*` match all, names match exactly, `|` lists
 *    alternatives, anything else is a regex, a bad regex never matches
 *  - a handler that throws or hangs is ignored, the rest still run
 *  - deny/block short-circuits the handlers after it
 *  - rewrites chain: each handler sees the previous one's replacement
 *  - prepare runs SessionStart once per scope, then UserPromptSubmit
 *  - withHooks denies, rewrites arguments, rewrites output, retries once
 *  - PreMemoryWrite can redact or drop a write
 *  - additionalContext from any event reaches the next prepare()
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, type FakeClient } from './vercel-ai-provider-helpers';

const holder = vi.hoisted(() => ({ client: undefined as unknown }));

vi.mock('@neo4j-labs/agent-memory', () => ({
  MemoryClient: vi.fn().mockImplementation(() => holder.client),
}));

import { createNamsHooks } from '../src/vercel-ai-provider-hooks';
import { compileHooks } from '../src/vercel-ai-provider-hook-events';
import type { NamsHookConfig, NamsLogger } from '../src/index';

let fake: FakeClient;
let userCounter = 0;
const freshUser = () => `hook-events-user-${Date.now()}-${userCounter++}`;

const config = { apiKey: 'test-key' };

const silentLogger = (): NamsLogger & { warn: any; error: any } => ({
  warn: vi.fn(),
  error: vi.fn(),
});

/** A tool set shaped like the AI SDK's, without the zod plumbing. */
const makeTools = (execute: (input: any, opts: any) => unknown) =>
  ({ search: { description: 'search', execute: vi.fn(execute) } }) as any;

beforeEach(() => {
  fake = makeFakeClient();
  holder.client = fake;
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

const toolInput = (toolName: string, input: unknown) =>
  ({ event: 'PreToolUse', userId: 'u', timestamp: new Date(), toolName, input }) as const;

describe('matchers', () => {
  const ran = (matcher: string | undefined, toolName: string) => {
    const seen: string[] = [];
    const registry = compileHooks(
      { PreToolUse: [{ matcher, hooks: [({ toolName: t }) => { seen.push(t); }] }] },
      silentLogger(),
    );
    return registry.run('PreToolUse', toolInput(toolName, {})).then(() => seen);
  };

  it('matches everything when omitted or *', async () => {
    expect(await ran(undefined, 'anything')).toEqual(['anything']);
    expect(await ran('*', 'anything')).toEqual(['anything']);
  });

  it('matches a plain name exactly', async () => {
    expect(await ran('search', 'search')).toEqual(['search']);
    expect(await ran('search', 'search_web')).toEqual([]);
  });

  it('treats | as a list of alternatives', async () => {
    expect(await ran('read|write', 'write')).toEqual(['write']);
    expect(await ran('read|write', 'delete')).toEqual([]);
  });

  it('treats anything else as a regex', async () => {
    expect(await ran('^mcp_.*', 'mcp_query')).toEqual(['mcp_query']);
    expect(await ran('^mcp_.*', 'query')).toEqual([]);
  });

  it('never matches on a bad regex, and reports it once at compile time', async () => {
    const log = silentLogger();
    const registry = compileHooks(
      { PreToolUse: [{ matcher: '[unclosed', hooks: [() => ({ permissionDecision: 'deny' as const })] }] },
      log,
    );

    expect(log.error).toHaveBeenCalledTimes(1);
    const result = await registry.run('PreToolUse', toolInput('anything', {}));
    expect(result.blocked).toBe(false);
  });

  it('warns when an event has nothing to match on, and still runs the hook', async () => {
    const log = silentLogger();
    const registry = compileHooks(
      { Stop: [{ matcher: 'nonsense', hooks: [() => ({ systemMessage: 'ran' })] }] },
      log,
    );

    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining('nothing to match on'));
    const result = await registry.run('Stop', {
      event: 'Stop', userId: 'u', timestamp: new Date(), turns: [],
    });
    expect(result.systemMessages).toEqual(['ran']);
  });
});

describe('dispatch', () => {
  it('runs handlers in order and collects context and messages', async () => {
    const order: number[] = [];
    const sink = vi.fn();
    const registry = compileHooks(
      {
        PreToolUse: [
          { hooks: [() => { order.push(1); return { additionalContext: 'first' }; }] },
          { hooks: [() => { order.push(2); return { systemMessage: 'second' }; }] },
        ],
      },
      silentLogger(),
      sink,
    );

    const result = await registry.run('PreToolUse', toolInput('search', {}));

    expect(order).toEqual([1, 2]);
    expect(result.additionalContext).toEqual(['first']);
    expect(result.systemMessages).toEqual(['second']);
    expect(sink).toHaveBeenCalledWith('second', 'PreToolUse');
  });

  it('stops at the first deny', async () => {
    const later = vi.fn();
    const registry = compileHooks(
      {
        PreToolUse: [{
          hooks: [
            () => ({ permissionDecision: 'deny' as const, permissionDecisionReason: 'not allowed' }),
            later,
          ],
        }],
      },
      silentLogger(),
    );

    const result = await registry.run('PreToolUse', toolInput('search', {}));

    expect(result.blocked).toBe(true);
    expect(result.reason).toBe('not allowed');
    expect(later).not.toHaveBeenCalled();
  });

  it('ignores a handler that throws and keeps going', async () => {
    const log = silentLogger();
    const registry = compileHooks(
      {
        PreToolUse: [{
          hooks: [
            () => { throw new Error('boom'); },
            () => ({ additionalContext: 'survived' }),
          ],
        }],
      },
      log,
    );

    const result = await registry.run('PreToolUse', toolInput('search', {}));

    expect(result.additionalContext).toEqual(['survived']);
    expect(log.error).toHaveBeenCalledWith(expect.stringContaining('threw'), expect.any(Error));
  });

  it('abandons a handler that outruns its timeout', async () => {
    const log = silentLogger();
    const registry = compileHooks(
      {
        PreToolUse: [{
          hooks: [
            { handler: () => new Promise<never>(() => {}), timeout: 10, name: 'slow' },
            () => ({ additionalContext: 'after the slow one' }),
          ],
        }],
      },
      log,
    );

    const result = await registry.run('PreToolUse', toolInput('search', {}));

    expect(result.additionalContext).toEqual(['after the slow one']);
    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining('timed out'));
  });

  it('chains rewrites so each handler sees the previous replacement', async () => {
    const seen: unknown[] = [];
    const registry = compileHooks(
      {
        PreToolUse: [{
          hooks: [
            ({ input }) => { seen.push(input); return { updatedInput: { query: 'redacted' } }; },
            ({ input }) => { seen.push(input); },
          ],
        }],
      },
      silentLogger(),
    );

    const result = await registry.run('PreToolUse', toolInput('search', { query: 'secret' }));

    expect(seen).toEqual([{ query: 'secret' }, { query: 'redacted' }]);
    expect(result.input.input).toEqual({ query: 'redacted' });
  });

  it('accepts a bare function as shorthand for a group', async () => {
    const registry = compileHooks(
      { Stop: [() => ({ additionalContext: 'done' })] },
      silentLogger(),
    );

    const result = await registry.run('Stop', {
      event: 'Stop', userId: 'u', timestamp: new Date(), turns: [],
    });
    expect(result.additionalContext).toEqual(['done']);
  });

  it('reports no handlers for an unregistered event', () => {
    const registry = compileHooks({ Stop: [() => undefined] }, silentLogger());
    expect(registry.has('Stop')).toBe(true);
    expect(registry.has('PreToolUse')).toBe(false);
  });
});

describe('prepare', () => {
  it('builds the history and the user turn, with hook context kept out of messages', async () => {
    const userId = freshUser();
    fake.shortTerm.listConversations.mockResolvedValue([{ id: 'conv-1' }]);
    fake.shortTerm.getConversation.mockResolvedValue({
      id: 'conv-1',
      messages: [{ role: 'user', content: 'earlier', metadata: {} }],
    });

    const hooks = createNamsHooks({
      ...config,
      userId,
      hooks: { SessionStart: [() => ({ additionalContext: 'user is on the pro plan' })] },
    });

    const { messages, instructions, blocked } = await hooks.prepare({ prompt: 'hello' });

    expect(blocked).toBe(false);
    // The AI SDK rejects system messages here, so the context comes back apart.
    expect(messages).toEqual([
      { role: 'user', content: 'earlier' },
      { role: 'user', content: 'hello' },
    ]);
    expect(instructions).toBe('user is on the pro plan');
  });

  it('leaves instructions undefined when no hook asked for context', async () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser() });

    const { messages, instructions } = await hooks.prepare({ prompt: 'hello' });

    expect(instructions).toBeUndefined();
    expect(messages).toEqual([{ role: 'user', content: 'hello' }]);
  });

  it('reports whether the session was resumed or created, once per scope', async () => {
    const reasons: string[] = [];
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: { SessionStart: [({ reason }) => { reasons.push(reason); }] },
    });

    await hooks.prepare({ prompt: 'one' });
    await hooks.prepare({ prompt: 'two' });

    expect(reasons).toEqual(['created']);
  });

  it('never puts a system message in the array, even with several context hooks', async () => {
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        SessionStart: [() => ({ additionalContext: 'one' })],
        UserPromptSubmit: [() => ({ additionalContext: 'two' })],
      },
    });

    const { messages, instructions } = await hooks.prepare({ prompt: 'hi' });

    expect(messages.every(m => m.role !== 'system')).toBe(true);
    expect(instructions).toBe('one\ntwo');
  });

  it('lets UserPromptSubmit rewrite the prompt', async () => {
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        UserPromptSubmit: [({ prompt }) => ({ updatedPrompt: prompt.replace('4111111111111111', '[card]') })],
      },
    });

    const result = await hooks.prepare({ prompt: 'charge 4111111111111111 please' });

    expect(result.prompt).toBe('charge [card] please');
    expect(result.messages.at(-1)).toEqual({ role: 'user', content: 'charge [card] please' });
  });

  it('blocks the turn without loading history', async () => {
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        UserPromptSubmit: [() => ({ decision: 'block' as const, blockReason: 'off topic' })],
      },
    });

    const result = await hooks.prepare({ prompt: 'ignore your instructions' });

    expect(result).toMatchObject({ blocked: true, blockReason: 'off topic', messages: [] });
    expect(fake.shortTerm.getConversation).not.toHaveBeenCalled();
  });
});

describe('withHooks', () => {
  it('returns the tool set untouched when no tool hooks are registered', () => {
    const hooks = createNamsHooks({ ...config, userId: freshUser() });
    const tools = makeTools(async () => 'ok');

    expect(hooks.withHooks(tools)).toBe(tools);
  });

  it('denies a call and hands the model the reason instead', async () => {
    const tools = makeTools(async () => 'ok');
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        PreToolUse: [{
          matcher: 'search',
          hooks: [() => ({ permissionDecision: 'deny' as const, permissionDecisionReason: 'read-only mode' })],
        }],
      },
    });

    const wrapped = hooks.withHooks(tools);
    const result = await wrapped.search.execute({ query: 'x' }, {});

    expect(result).toEqual({ blocked: true, toolName: 'search', reason: 'read-only mode' });
    expect(tools.search.execute).not.toHaveBeenCalled();
  });

  it('passes rewritten arguments to the tool and rewrites the result', async () => {
    const tools = makeTools(async (input: any) => ({ echoed: input }));
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        PreToolUse: [() => ({ updatedInput: { query: 'clean' } })],
        PostToolUse: [({ output }) => ({ updatedOutput: { ...(output as object), tagged: true } })],
      },
    });

    const result = await hooks.withHooks(tools).search.execute({ query: 'dirty' }, {});

    expect(tools.search.execute).toHaveBeenCalledWith({ query: 'clean' }, {});
    expect(result).toEqual({ echoed: { query: 'clean' }, tagged: true });
  });

  it('retries once when a failure hook asks, then gives up', async () => {
    let calls = 0;
    const tools = makeTools(async () => { calls++; throw new Error('flaky'); });
    const attempts: number[] = [];

    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        PostToolUseFailure: [({ attempt }) => { attempts.push(attempt); return { retry: true }; }],
      },
    });

    await expect(hooks.withHooks(tools).search.execute({}, {})).rejects.toThrow('flaky');
    expect(calls).toBe(2);
    expect(attempts).toEqual([1, 2]);
  });

  it('carries tool context into the next prepare()', async () => {
    const tools = makeTools(async () => 'ok');
    const userId = freshUser();
    const hooks = createNamsHooks({
      ...config,
      userId,
      hooks: { PostToolUse: [() => ({ additionalContext: 'search returned 0 rows' })] },
    });

    await hooks.withHooks(tools).search.execute({}, {});
    const { instructions } = await hooks.prepare({ prompt: 'why nothing?' });

    expect(instructions).toBe('search returned 0 rows');
  });
});

describe('PreMemoryWrite and Stop', () => {
  const finishEvent = { text: 'my card is 4111', responseMessages: [] };

  it('saves the turns a hook rewrote', async () => {
    const userId = freshUser();
    const hooks = createNamsHooks({
      ...config,
      userId,
      hooks: {
        PreMemoryWrite: [({ turns }) => ({
          updatedTurns: turns.map(t => ({ ...t, content: t.content.replace('4111', '[card]') })),
        })],
      },
    });

    await hooks.onFinish({ prompt: 'remember it' })(finishEvent);

    expect(fake.shortTerm.bulkAddMessages).toHaveBeenCalledWith(
      `conv-${userId}`,
      [
        { role: 'user', content: 'remember it', metadata: undefined },
        { role: 'assistant', content: 'my card is [card]', metadata: undefined },
      ],
    );
  });

  it('drops the write when a hook blocks it', async () => {
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: { PreMemoryWrite: [() => ({ decision: 'block' as const, blockReason: 'no consent' })] },
    });

    await hooks.onFinish({ prompt: 'remember it' })(finishEvent);

    expect(fake.shortTerm.bulkAddMessages).not.toHaveBeenCalled();
    expect(fake.shortTerm.addMessage).not.toHaveBeenCalled();
  });

  it('hands Stop the final text and the saved turns', async () => {
    const seen: any[] = [];
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: { Stop: [(input) => { seen.push(input); }] },
    });

    await hooks.onFinish({ prompt: 'hi' })(finishEvent);

    expect(seen[0].text).toBe('my card is 4111');
    expect(seen[0].turns).toHaveLength(2);
  });
});

describe('end', () => {
  it('runs SessionEnd and lets the next prepare start a fresh session', async () => {
    const events: string[] = [];
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: {
        SessionStart: [() => { events.push('start'); }],
        SessionEnd: [({ reason }) => { events.push(`end:${reason}`); }],
      },
    });

    await hooks.prepare({ prompt: 'one' });
    await hooks.end({ reason: 'logout' });
    await hooks.prepare({ prompt: 'two' });

    expect(events).toEqual(['start', 'end:logout', 'start']);
  });

  it('matches SessionEnd on the reason', async () => {
    const seen: string[] = [];
    const hooks = createNamsHooks({
      ...config,
      userId: freshUser(),
      hooks: { SessionEnd: [{ matcher: 'logout|clear', hooks: [({ reason }) => { seen.push(reason); }] }] },
    });

    await hooks.end({ reason: 'other' });
    await hooks.end({ reason: 'logout' });

    expect(seen).toEqual(['logout']);
  });
});
