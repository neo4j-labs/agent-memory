/**
 * retrieveMemories — fallback for NAMS's literal, case-sensitive substring
 * search (verified live: "Delh"/"elhi" both match "Delhi" as a pure substring,
 * but a lowercase query never matches capitalized stored text, and a query
 * broken across word boundaries that aren't contiguous in the source matches
 * nothing). A natural-language question is almost never a literal substring of
 * the fact it's asking about.
 *
 * Contract points:
 *  - a direct phrase search that already finds something is used as-is; no
 *    fallback calls happen
 *  - a direct phrase search that finds nothing retries with the query's
 *    significant words, in both their given case and Title Case
 *  - the merged fallback candidates are ranked by word overlap with the
 *    original query, so the most relevant hit isn't lost under the result cap
 *  - fallback failures are swallowed, not thrown
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeFakeClient, type FakeClient } from './vercel-ai-provider-helpers';
import { retrieveMemories } from '../src/vercel-ai-provider-client';

let fake: FakeClient;

beforeEach(() => {
  fake = makeFakeClient();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

const scope = { userId: 'u1' };

describe('retrieveMemories — substring-search fallback', () => {
  it('does not retry when the direct phrase search already finds a match', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Fact', description: 'User is from Delhi.', type: 'fact' },
    ]);

    await retrieveMemories(fake as any, scope, 'conv-1', 'User is from Delhi', 5);

    expect(fake.longTerm.searchEntities).toHaveBeenCalledTimes(1);
    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('User is from Delhi', expect.anything());
  });

  it('retries with Title-Case and as-given word variants when the direct search finds nothing', async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) =>
      q === 'Delhi' ? [{ name: 'Fact', description: 'User is from Delhi.', type: 'fact' }] : [],
    );

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'where do i live delhi', 5);

    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('where do i live delhi', expect.anything());
    // lowercase "delhi" as typed AND its Title-Case variant are both tried.
    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('delhi', expect.anything());
    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('Delhi', expect.anything());
    expect(hits).toContainEqual(expect.objectContaining({ content: 'Fact — User is from Delhi.' }));
  });

  it('ranks noisy fallback candidates by word overlap with the original query', async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) => {
      if (q !== 'User') return [];
      // A liberal substring match returns several "User..." entities — the one
      // that shares the most words with the query should be ranked first.
      return [
        { name: 'e1', description: "User's name is Alex.", type: 'fact' },
        { name: 'e2', description: 'User is from Delhi.', type: 'fact' },
      ];
    });

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'is the user from Delhi', 5);

    const contents = hits.map(h => h.content);
    expect(contents.indexOf('e2 — User is from Delhi.'))
      .toBeLessThan(contents.indexOf("e1 — User's name is Alex."));
  });

  it('does not throw when both the direct search and a fallback term reject', async () => {
    fake.longTerm.searchEntities.mockRejectedValue(new Error('boom'));

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'where is the user from', 5);

    expect(hits).toEqual([]);
  });

  it('skips the fallback entirely when the query has no significant words', async () => {
    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'hi ok', 5);

    expect(fake.longTerm.searchEntities).toHaveBeenCalledTimes(1);
    expect(hits).toEqual([]);
  });
});

/**
 * An entity's name is the fact; its description is only the entity's role
 * ("Preferred language", "City where the user works"). A hit that carries the
 * description alone cannot answer "which language do I prefer?" — the model
 * either reports the memory as incomplete or invents a fragment of the name.
 */
describe('retrieveMemories — long-term hits carry the entity name', () => {
  it('renders name and description together', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Python', description: 'Preferred language', type: 'ProgrammingLanguage' },
    ]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Preferred language', 5);

    expect(hits[0].content).toBe('Python — Preferred language');
  });

  it('falls back to the bare name when the entity has no description', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Bangalore', type: 'Location' },
    ]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Bangalore', 5);

    expect(hits[0].content).toBe('Bangalore');
  });

  it('lets name tokens participate in fallback overlap ranking', async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) =>
      q === 'prefer' || q === 'Prefer'
        ? [
            { name: 'Rust', description: 'A language the user prefers', type: 'lang' },
            { name: 'Python', description: 'A language the user prefers', type: 'lang' },
          ]
        : [],
    );

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'do i prefer Python', 5);
    const contents = hits.map(h => h.content);

    expect(contents.indexOf('Python — A language the user prefers'))
      .toBeLessThan(contents.indexOf('Rust — A language the user prefers'));
  });

  it('keeps distinct entities that happen to share a description', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { name: 'Python', description: 'Preferred language', type: 'lang' },
      { name: 'TypeScript', description: 'Preferred language', type: 'lang' },
    ]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Preferred language', 5);

    expect(hits.map(h => h.content)).toEqual([
      'Python — Preferred language',
      'TypeScript — Preferred language',
    ]);
  });
});
