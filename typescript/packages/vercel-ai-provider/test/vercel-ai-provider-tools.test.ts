/**
 * Tools mode — query_memory / store_memory as model-driven AI SDK tools.
 *
 * Contract points:
 *  - query_memory returns found=true with hits, found=false when empty
 *  - store_memory(interaction) → short-term message
 *  - store_memory(fact) → long-term entity + confidence feedback
 *  - existing entities are reused, not duplicated
 *  - a storage failure reports stored=false instead of throwing into the loop
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, type FakeClient } from './vercel-ai-provider-helpers';

const holder = vi.hoisted(() => ({ client: undefined as unknown }));

vi.mock('@neo4j-labs/agent-memory', () => ({
  MemoryClient: vi.fn().mockImplementation(() => holder.client),
}));

const mcpHolder = vi.hoisted(() => ({
  tools: {} as Record<string, unknown>,
  close: vi.fn(),
}));

vi.mock('@ai-sdk/mcp', () => ({
  createMCPClient: vi.fn().mockResolvedValue({
    tools: () => Promise.resolve(mcpHolder.tools),
    close: mcpHolder.close,
  }),
}));

import {
  createNamsMemoryTools,
  createNamsTools,
  enforceQueryMemory,
} from '../src/vercel-ai-provider-tools';
import type { QueryOutput, StoreOutput } from '../src/vercel-ai-provider-tools';

let fake: FakeClient;
let userCounter = 0;
const freshUser = () => `tools-user-${Date.now()}-${userCounter++}`;

const toolOptions = { toolCallId: 'call-1', messages: [] } as any;

/** Tool execute() is typed T | AsyncIterable<T>; our tools always return T. */
async function callTool<T>(t: { execute?: unknown }, input: unknown): Promise<T> {
  return (await (t.execute as (i: unknown, o: unknown) => Promise<unknown>)(input, toolOptions)) as T;
}

beforeEach(() => {
  fake = makeFakeClient();
  holder.client = fake;
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('query_memory', () => {
  it('returns found=true with ranked memories', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Alex', description: 'User is named Alex', type: 'person', confidence: 0.92 },
    ]);
    fake.shortTerm.searchMessages.mockResolvedValue([
      { content: 'I love terse answers' },
    ]);

    const { query_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-q',
    });

    const out = await callTool<QueryOutput>(query_memory, { query: 'who am I', limit: 5 });

    expect(out.found).toBe(true);
    expect(out.count).toBe(2);
    // Scores present → sorted descending, long-term hit first.
    expect(out.memories[0]).toMatchObject({ content: 'User is named Alex', source: 'long-term' });
    expect(out.memories[1]).toMatchObject({ content: 'I love terse answers', source: 'conversation' });
  });

  it('returns found=false when nothing matches', async () => {
    const { query_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-q2',
    });

    const out = await callTool<QueryOutput>(query_memory, { query: 'anything', limit: 5 });

    expect(out.found).toBe(false);
    expect(out.memories).toEqual([]);
    expect(out.message).toMatch(/no relevant memories/i);
  });

  it('deduplicates identical content across sources', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'x', description: 'duplicate fact', type: 'fact' },
    ]);
    fake.shortTerm.searchMessages.mockResolvedValue([{ content: 'duplicate fact' }]);

    const { query_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-q3',
    });

    const out = await callTool<QueryOutput>(query_memory, { query: 'dup', limit: 5 });
    expect(out.memories).toHaveLength(1);
  });

  it('caps the number of returned memories at the requested limit', async () => {
    fake.longTerm.searchEntities.mockResolvedValue(
      Array.from({ length: 5 }, (_, i) => ({ name: `e${i}`, description: `fact ${i}`, type: 'fact' })),
    );
    fake.shortTerm.searchMessages.mockResolvedValue(
      Array.from({ length: 5 }, (_, i) => ({ content: `message ${i}` })),
    );

    const { query_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-q4',
    });

    const out = await callTool<QueryOutput>(query_memory, { query: 'lots', limit: 3 });
    expect(out.memories).toHaveLength(3);
  });
});

describe('store_memory', () => {
  it('stores an interaction as a short-term message', async () => {
    const { store_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-s1',
    });

    const out = await callTool<StoreOutput>(store_memory,
      { content: 'User asked about pricing', type: 'interaction', confidence: 0.7, tags: [] },
    );

    expect(out.stored).toBe(true);
    expect(fake.shortTerm.addMessage).toHaveBeenCalledWith(
      'conv-s1', 'assistant', 'User asked about pricing',
    );
    expect(fake.longTerm.addEntity).not.toHaveBeenCalled();
  });

  it('stores a fact as a long-term entity with confidence feedback', async () => {
    const { store_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-s2',
    });

    const out = await callTool<StoreOutput>(store_memory,
      { content: 'Prefers dark mode', type: 'user_preference', confidence: 0.9, tags: [] },
    );

    expect(out.stored).toBe(true);
    expect(fake.longTerm.addEntity).toHaveBeenCalledWith(
      'Prefers dark mode', 'user_preference', { description: 'Prefers dark mode' },
    );
    expect(fake.longTerm.setEntityFeedback).toHaveBeenCalledWith(
      'ent-Prefers dark mode', { userScore: 0.9, confirmed: true },
    );
  });

  it('reuses an existing entity instead of duplicating it', async () => {
    fake.longTerm.getEntityByName.mockResolvedValue({ id: 'ent-existing', name: 'Prefers dark mode' });

    const { store_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-s3',
    });

    await callTool<StoreOutput>(store_memory,
      { content: 'Prefers dark mode', type: 'fact', confidence: 0.7, tags: [] },
    );

    expect(fake.longTerm.addEntity).not.toHaveBeenCalled();
    expect(fake.longTerm.setEntityFeedback).toHaveBeenCalledWith(
      'ent-existing', expect.objectContaining({ userScore: 0.7 }),
    );
  });

  it('reports stored=false on failure instead of throwing', async () => {
    fake.shortTerm.addMessage.mockRejectedValue(new Error('write failed'));

    const { store_memory } = createNamsMemoryTools({
      apiKey: 'k',
      userId: freshUser(),
      conversationId: 'conv-s4',
    });

    const out = await callTool<StoreOutput>(store_memory,
      { content: 'x', type: 'interaction', confidence: 0.7, tags: [] },
    );

    expect(out.stored).toBe(false);
    expect(out.message).toMatch(/failed/i);
  });
});

describe('enforceQueryMemory', () => {
  // enforceQueryMemory reads only stepNumber and steps. AI SDK v7 widened the
  // prepareStep options with several more required fields (instructions,
  // initialMessages, responseMessages, …) that this hook never touches, so the
  // fixture casts once here rather than restating unused fields at every call.
  type StepOptions = Parameters<ReturnType<typeof enforceQueryMemory>>[0];

  const stepOptions = (stepNumber: number, toolNames: string[][] = []) => ({
    stepNumber,
    steps: toolNames.map(names => ({ toolCalls: names.map(toolName => ({ toolName })) })),
  }) as unknown as StepOptions;

  it('requires a tool call while query_memory has not been executed', async () => {
    const prepareStep = enforceQueryMemory();
    expect(await prepareStep(stepOptions(0))).toEqual({ toolChoice: 'required' });
    // Other tools ran, but query_memory still hasn't → still constrained.
    expect(await prepareStep(stepOptions(2, [['read_file'], ['mcp_search']])))
      .toEqual({ toolChoice: 'required' });
  });

  it('forces query_memory directly once the grace window is exhausted', async () => {
    const prepareStep = enforceQueryMemory();  // default graceSteps: 3
    expect(await prepareStep(stepOptions(3, [['read_file'], ['mcp_search'], ['read_file']])))
      .toEqual({ toolChoice: { type: 'tool', toolName: 'query_memory' } });
  });

  it('drops all constraints once query_memory has been executed', async () => {
    const prepareStep = enforceQueryMemory();
    expect(await prepareStep(stepOptions(2, [['read_file'], ['query_memory']]))).toBeUndefined();
    // Parallel call in the same step counts too.
    expect(await prepareStep(stepOptions(1, [['read_file', 'query_memory']]))).toBeUndefined();
    // Even past the grace window, no constraint once queried.
    expect(await prepareStep(stepOptions(4, [['read_file'], ['query_memory'], ['read_file']])))
      .toBeUndefined();
  });

  it('graceSteps: 0 forces query_memory as the very first step', async () => {
    const prepareStep = enforceQueryMemory({ graceSteps: 0 });
    expect(await prepareStep(stepOptions(0)))
      .toEqual({ toolChoice: { type: 'tool', toolName: 'query_memory' } });
    expect(await prepareStep(stepOptions(1, [['query_memory']]))).toBeUndefined();
  });
});

describe('createNamsTools with MCP', () => {
  it('prefixes MCP tool names when toolPrefix is set', async () => {
    mcpHolder.tools = { search: { description: 'mcp search' }, query_memory: { description: 'mcp memory' } };

    const { tools } = await createNamsTools({
      apiKey: 'k',
      userId: freshUser(),
      mcp: { url: 'https://mcp.example.com/mcp', toolPrefix: 'mcp_' },
    });

    expect(Object.keys(tools).sort()).toEqual(
      ['mcp_query_memory', 'mcp_search', 'query_memory', 'store_memory'],
    );
    // NAMS query_memory is intact, not shadowed by the MCP tool of the same name.
    expect((tools.query_memory as { description?: string }).description).toMatch(/NAMS/);
  });

  it('warns on collision when no prefix is set and MCP tool wins', async () => {
    mcpHolder.tools = { query_memory: { description: 'mcp memory' } };
    const warn = vi.fn();

    const { tools } = await createNamsTools({
      apiKey: 'k',
      userId: freshUser(),
      logger: { warn, error: vi.fn() },
      mcp: { url: 'https://mcp.example.com/mcp' },
    });

    expect((tools.query_memory as { description?: string }).description).toBe('mcp memory');
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('toolPrefix'));
  });
});
