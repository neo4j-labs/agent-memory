/**
 * Graph extraction — entities and relationships extracted from a stored
 * memory must land in the long-term graph via the real SDK API:
 * addEntity(name, type, options) and addRelationship(sourceId, targetId, type).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, type FakeClient } from './vercel-ai-provider-helpers';

vi.mock('ai', async (importOriginal) => ({
  ...(await importOriginal<typeof import('ai')>()),
  generateText: vi.fn(),
}));

import { generateText } from 'ai';
import { createGraphExtractor } from '../src/vercel-ai-provider-extract';
import { makeClient } from '../src/vercel-ai-provider-client';

const mockedGenerateText = vi.mocked(generateText);

const graphResult = (entities: any[], relationships: any[] = []) =>
  ({ output: { entities, relationships } }) as any;

let fake: FakeClient;

beforeEach(() => {
  vi.clearAllMocks();
  fake = makeFakeClient();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

describe('createGraphExtractor', () => {
  it('stores extracted entities and links them with positional addRelationship args', async () => {
    mockedGenerateText.mockResolvedValue(graphResult(
      [
        { name: 'Alex', type: 'person', description: 'A user' },
        { name: 'TechCorp', type: 'organization' },
      ],
      [{ from: 'Alex', to: 'TechCorp', type: 'WORKS_AT' }],
    ));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'Alex works at TechCorp', type: 'fact' });

    expect(fake.longTerm.addEntity).toHaveBeenCalledWith('Alex', 'person', { description: 'A user' });
    expect(fake.longTerm.addEntity).toHaveBeenCalledWith('TechCorp', 'organization', {
      description: 'Alex works at TechCorp',
    });
    // SDK signature: addRelationship(sourceId, targetId, relationshipType)
    expect(fake.longTerm.addRelationship).toHaveBeenCalledWith('ent-Alex', 'ent-TechCorp', 'WORKS_AT');
  });

  it('skips relationships whose endpoints were not extracted', async () => {
    mockedGenerateText.mockResolvedValue(graphResult(
      [{ name: 'Alex', type: 'person' }],
      [{ from: 'Alex', to: 'Ghost', type: 'KNOWS' }],
    ));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'Alex', type: 'fact' });

    expect(fake.longTerm.addRelationship).not.toHaveBeenCalled();
  });

  /**
   * The hosted REST API has no relationship endpoint, so every extracted edge
   * fails identically. Now that extraction actually runs, an unsuppressed log
   * would emit one line per edge per stored memory forever.
   */
  it('logs an unsupported relationship backend once, not once per edge', async () => {
    const warn = vi.fn();
    const notSupported = Object.assign(new Error('no equivalent in the hosted API'), {
      name: 'NotSupportedError',
    });
    fake.longTerm.addRelationship.mockRejectedValue(notSupported);
    mockedGenerateText.mockResolvedValue(graphResult(
      [{ name: 'A', type: 'x' }, { name: 'B', type: 'y' }, { name: 'C', type: 'z' }],
      [
        { from: 'A', to: 'B', type: 'R1' },
        { from: 'B', to: 'C', type: 'R2' },
        { from: 'A', to: 'C', type: 'R3' },
      ],
    ));

    const client = makeClient({ apiKey: 'k', logger: { warn, error: vi.fn() } });
    Object.assign(client, { longTerm: fake.longTerm });

    await createGraphExtractor({} as any)(client, { content: 'A B C', type: 'fact' });

    expect(fake.longTerm.addRelationship).toHaveBeenCalledTimes(3);
    const relWarnings = warn.mock.calls.filter(([m]) => /relationship endpoint/.test(m));
    expect(relWarnings).toHaveLength(1);
  });

  it('keeps logging genuine relationship write failures every time', async () => {
    const warn = vi.fn();
    fake.longTerm.addRelationship.mockRejectedValue(new Error('network blip'));
    mockedGenerateText.mockResolvedValue(graphResult(
      [{ name: 'A', type: 'x' }, { name: 'B', type: 'y' }],
      [{ from: 'A', to: 'B', type: 'R1' }, { from: 'B', to: 'A', type: 'R2' }],
    ));

    const client = makeClient({ apiKey: 'k', logger: { warn, error: vi.fn() } });
    Object.assign(client, { longTerm: fake.longTerm });

    await createGraphExtractor({} as any)(client, { content: 'A B', type: 'fact' });

    expect(warn.mock.calls.filter(([m]) => /addRelationship failed/.test(m))).toHaveLength(2);
  });

  it('treats relationship write failures as non-fatal', async () => {
    mockedGenerateText.mockResolvedValue(graphResult(
      [
        { name: 'A', type: 'concept' },
        { name: 'B', type: 'concept' },
      ],
      [{ from: 'A', to: 'B', type: 'RELATES_TO' }],
    ));
    fake.longTerm.addRelationship.mockRejectedValue(new Error('write failed'));

    const extract = createGraphExtractor({} as any);
    await expect(
      extract(fake as any, { content: 'A relates to B', type: 'fact' }),
    ).resolves.toBeUndefined();
  });

  it('records confidence feedback on stored entities when provided', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([{ name: 'Alex', type: 'person' }]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'Alex', type: 'fact', confidence: 0.9 });

    expect(fake.longTerm.setEntityFeedback).toHaveBeenCalledWith('ent-Alex', {
      userScore: 0.9,
      confirmed: true,
    });
  });

  it('falls back to the memory text when the model returns an empty description', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: 'Alex', type: 'person', description: '' },
    ]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'Alex works at TechCorp', type: 'fact' });

    expect(fake.longTerm.addEntity).toHaveBeenCalledWith('Alex', 'person', {
      description: 'Alex works at TechCorp',
    });
  });
});

describe('graph extraction schema — OpenAI strict-mode compliance', () => {
  const strictViolations = (node: any, path = '$'): string[] => {
    if (!node || typeof node !== 'object') return [];
    const out: string[] = [];
    if (node.type === 'object' && node.properties) {
      const keys = Object.keys(node.properties);
      const required: string[] = node.required ?? [];
      const missing = keys.filter(k => !required.includes(k));
      if (missing.length) out.push(`${path}: ${missing.join(', ')}`);
      for (const k of keys) out.push(...strictViolations(node.properties[k], `${path}.${k}`));
    }
    if (node.type === 'array' && node.items) out.push(...strictViolations(node.items, `${path}[]`));
    return out;
  };

  it('marks every property required, at every level', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'anything', type: 'fact' });

    const { output } = mockedGenerateText.mock.calls[0][0] as any;
    const { schema } = await output.responseFormat;

    expect(strictViolations(schema)).toEqual([]);
  });
});

describe('createGraphExtractor — self-referential guard', () => {
  it('does not turn an agent memory summary into entities', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: 'long-term memories', type: 'Concept', description: 'Retrieved long-term memories indicate…' },
      { name: 'past interactions', type: 'Event', description: 'The text lists past interactions…' },
      { name: 'profile details', type: 'Object', description: 'Profile details were stored…' },
      { name: 'preferences', type: 'Concept', description: 'The text lists preferences…' },
    ]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, {
      content: 'Retrieved long-term memories indicate a preference for concise technical ' +
        'answers. I also have profile details stored for future sessions.',
      type: 'fact',
    });

    expect(fake.longTerm.addEntity).not.toHaveBeenCalled();
  });

  it('skips entities named after their own type and snake_case tool-output keys', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: 'Organization', type: 'Organization' },
      { name: 'organization_count', type: 'Metric' },
      { name: 'query_memory', type: 'SoftwareTool' },
    ]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'tool output', type: 'fact' });

    expect(fake.longTerm.addEntity).not.toHaveBeenCalled();
  });

  /**
   * The guard keys on proper-vs-common noun, so a script that has no case must
   * make it decline rather than reject every entity in that language.
   */
  it('does not reject entities written in caseless scripts', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: '北京', type: 'Location' },
      { name: 'القاهرة', type: 'Location' },
      { name: '田中', type: 'Person' },
    ]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'locations', type: 'fact' });

    const stored = fake.longTerm.addEntity.mock.calls.map((c: any[]) => c[0]);
    expect(stored).toEqual(['北京', 'القاهرة', '田中']);
  });

  it('keeps proper nouns whose styling is unusual', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: 'iPhone', type: 'Product' },
      { name: 'macOS', type: 'OperatingSystem' },
      { name: 'eBay', type: 'Organization' },
    ]));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'products', type: 'fact' });

    const stored = fake.longTerm.addEntity.mock.calls.map((c: any[]) => c[0]);
    expect(stored).toEqual(['iPhone', 'macOS', 'eBay']);
  });

  it('still stores the real entities alongside skipped ones, and drops their dangling edges', async () => {
    mockedGenerateText.mockResolvedValue(graphResult(
      [
        { name: 'Alex', type: 'Person' },
        { name: 'Acme Analytics', type: 'Organization' },
        { name: 'stored memories', type: 'Concept' },
      ],
      [
        { from: 'Alex', to: 'Acme Analytics', type: 'WORKS_AT' },
        { from: 'Alex', to: 'stored memories', type: 'HAS' },
      ],
    ));

    const extract = createGraphExtractor({} as any);
    await extract(fake as any, { content: 'Alex works at Acme Analytics', type: 'fact' });

    const stored = fake.longTerm.addEntity.mock.calls.map((c: any[]) => c[0]);
    expect(stored).toEqual(['Alex', 'Acme Analytics']);
    expect(fake.longTerm.addRelationship).toHaveBeenCalledTimes(1);
    expect(fake.longTerm.addRelationship).toHaveBeenCalledWith(
      'ent-Alex', 'ent-Acme Analytics', 'WORKS_AT',
    );
  });

  it('honours a caller-supplied skipEntity override', async () => {
    mockedGenerateText.mockResolvedValue(graphResult([
      { name: 'preferences', type: 'Concept' },
      { name: 'Alex', type: 'Person' },
    ]));

    // A domain where "preferences" is a real entity and people are not wanted.
    const extract = createGraphExtractor({} as any, {
      skipEntity: (e) => e.type === 'Person',
    });
    await extract(fake as any, { content: 'anything', type: 'fact' });

    const stored = fake.longTerm.addEntity.mock.calls.map((c: any[]) => c[0]);
    expect(stored).toEqual(['preferences']);
  });
});
