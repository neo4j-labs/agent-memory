/**
 * retrieveMemories search fallback. NAMS search is a case-sensitive substring
 * match, so a full question rarely matches.
 *
 * Checks:
 *  - a direct search with results is used as-is
 *  - an empty direct search retries each word, as typed and in Title Case,
 *    without end punctuation or a possessive 's
 *  - fallback results are sorted by word overlap with the query
 *  - fallback errors are swallowed
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
    // Both "delhi" and "Delhi" are tried.
    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('delhi', expect.anything());
    expect(fake.longTerm.searchEntities).toHaveBeenCalledWith('Delhi', expect.anything());
    expect(hits).toContainEqual(expect.objectContaining({ content: 'Fact — User is from Delhi.' }));
  });

  it('ranks noisy fallback candidates by word overlap with the original query', async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) => {
      if (q !== 'User') return [];
      // Several "User..." entities match. The one sharing the most query words comes first.
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

  it('drops punctuation at the ends of fallback words, keeping it inside them', async () => {
    await retrieveMemories(fake as any, scope, 'conv-1', 'Which plugins suit "Neovim"? And Node.js?', 5);

    const queries = fake.longTerm.searchEntities.mock.calls.map(c => c[0]);
    expect(queries).toContain('Neovim');
    expect(queries).toContain('Node.js');
    expect(queries.filter(q => /^\W|\W$/.test(q))).toEqual([
      'Which plugins suit "Neovim"? And Node.js?',  // the direct search, as typed
    ]);
  });

  it("drops a possessive 's, so \"Alex's\" finds Alex", async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) =>
      q === 'Alex' ? [{ name: 'Alex', description: 'The user.', type: 'person' }] : [],
    );

    const query = "what is Alex’s editor? ask O'Brien";
    const hits = await retrieveMemories(fake as any, scope, 'conv-1', query, 5);

    const fallbackQueries = fake.longTerm.searchEntities.mock.calls.map(c => c[0]).filter(q => q !== query);
    expect(fallbackQueries).toContain('Alex');
    expect(fallbackQueries).toContain("O'Brien");
    expect(fallbackQueries.filter(q => /['’]s$/.test(q))).toEqual([]);
    expect(hits).toContainEqual(expect.objectContaining({ content: 'Alex — The user.' }));
  });

  it('lower-cases the rest of the Title Case variant, so "NEOVIM" finds Neovim', async () => {
    fake.longTerm.searchEntities.mockImplementation(async (q: string) =>
      q === 'Neovim' ? [{ name: 'Neovim', description: 'A text editor.', type: 'tool' }] : [],
    );

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'DO I USE NEOVIM', 5);

    const queries = fake.longTerm.searchEntities.mock.calls.map(c => c[0]);
    expect(queries).toEqual(expect.arrayContaining(['NEOVIM', 'Neovim']));
    expect(hits).toContainEqual(expect.objectContaining({ content: 'Neovim — A text editor.' }));
  });

  it('skips the fallback entirely when the query has no significant words', async () => {
    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'hi ok', 5);

    expect(fake.longTerm.searchEntities).toHaveBeenCalledTimes(1);
    expect(hits).toEqual([]);
  });
});

/**
 * An entity's name holds the fact. Its description is only its role
 * ("Preferred language"), so a hit must include the name.
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

/** Each past conversation searched costs two requests, so the number is capped. */
describe('retrieveMemories — cross-session search', () => {
  const pastConversations = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ id: `past-${i}` }));

  it('searches the 5 most recent other conversations by default', async () => {
    fake.shortTerm.listConversations.mockResolvedValue([{ id: 'conv-1' }, ...pastConversations(5)]);

    await retrieveMemories(fake as any, scope, 'conv-1', 'Neovim', 5);

    expect(fake.shortTerm.listConversations).toHaveBeenCalledWith({ userId: 'u1', limit: 6 });
    const searched = fake.shortTerm.searchMessages.mock.calls.map(c => c[1].sessionId);
    expect(searched.filter(id => id.startsWith('past-'))).toHaveLength(5);
    expect(fake.reasoning.listSteps.mock.calls.map(c => c[0]))
      .toEqual(expect.arrayContaining(['past-0', 'past-4']));
  });

  it('honours crossSessionLimit', async () => {
    fake.shortTerm.listConversations.mockResolvedValue(pastConversations(3));

    await retrieveMemories(fake as any, scope, 'conv-1', 'Neovim', 5, { crossSessionLimit: 2 });

    expect(fake.shortTerm.listConversations).toHaveBeenCalledWith({ userId: 'u1', limit: 3 });
    const searched = fake.shortTerm.searchMessages.mock.calls.map(c => c[1].sessionId);
    expect(searched.filter(id => id.startsWith('past-'))).toEqual(['past-0', 'past-1']);
  });

  it('skips other conversations entirely when crossSessionLimit is 0', async () => {
    await retrieveMemories(fake as any, scope, 'conv-1', 'Neovim', 5, { crossSessionLimit: 0 });

    expect(fake.shortTerm.listConversations).not.toHaveBeenCalled();
    expect(fake.shortTerm.searchMessages.mock.calls.every(c => c[1].sessionId === 'conv-1')).toBe(true);
  });
});

/**
 * Entity search returns entities without their edges, so the relationships
 * NAMS stored are only in the prompt if they are read back.
 */
describe('retrieveMemories — graph expansion', () => {
  const alex = { id: 'ent-alex', name: 'Alex', description: 'The user', type: 'person' };

  const relationships = (...refs: Array<[string, string?]>) =>
    refs.map(([type, targetName], i) => ({ id: `r${i}`, type, targetId: `t${i}`, targetName }));

  it('renders each stored relationship as a triple', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([alex]);
    fake.longTerm.getEntity.mockResolvedValue({
      ...alex,
      relationships: relationships(['WORKS_AT', 'TechCorp'], ['USES', 'Neovim']),
    });

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5);

    expect(fake.longTerm.getEntity).toHaveBeenCalledWith('ent-alex');
    expect(hits).toContainEqual({
      content: '(Alex)-[WORKS_AT]->(TechCorp)', source: 'graph', type: 'WORKS_AT',
    });
    expect(hits).toContainEqual(
      expect.objectContaining({ content: '(Alex)-[USES]->(Neovim)' }),
    );
  });

  it('names the subject by its canonical name, so both spellings share one graph', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([{ ...alex, name: 'alex' }]);
    fake.longTerm.getEntity.mockResolvedValue({
      ...alex, canonicalName: 'Alex Rivera', relationships: relationships(['USES', 'Neovim']),
    });

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'alex', 5);

    expect(hits).toContainEqual(
      expect.objectContaining({ content: '(Alex Rivera)-[USES]->(Neovim)' }),
    );
  });

  it('expands the first two matches only, since each one costs a request', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { ...alex, id: 'e0' }, { ...alex, id: 'e1' }, { ...alex, id: 'e2' },
    ]);

    await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5);

    expect(fake.longTerm.getEntity.mock.calls.map(c => c[0])).toEqual(['e0', 'e1']);
  });

  it('honours graphExpansionLimit, including 0 for no graph reads at all', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([{ ...alex, id: 'e0' }, { ...alex, id: 'e1' }]);

    await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5, { graphExpansionLimit: 1 });
    expect(fake.longTerm.getEntity.mock.calls.map(c => c[0])).toEqual(['e0']);

    fake.longTerm.getEntity.mockClear();
    await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5, { graphExpansionLimit: 0 });
    expect(fake.longTerm.getEntity).not.toHaveBeenCalled();
  });

  it('drops a relationship with no target name, which would put a bare id in the prompt', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([alex]);
    fake.longTerm.getEntity.mockResolvedValue({
      ...alex, relationships: relationships(['WORKS_AT', undefined], ['USES', 'Neovim']),
    });

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5);
    const graph = hits.filter(h => h.source === 'graph');

    expect(graph).toHaveLength(1);
    expect(graph[0].content).toBe('(Alex)-[USES]->(Neovim)');
  });

  it('caps the triples taken from one hub entity', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([alex]);
    fake.longTerm.getEntity.mockResolvedValue({
      ...alex,
      relationships: Array.from({ length: 20 }, (_, i) => ({
        id: `r${i}`, type: 'KNOWS', targetId: `t${i}`, targetName: `Person ${i}`,
      })),
    });

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 12);

    expect(hits.filter(h => h.source === 'graph')).toHaveLength(5);
  });

  it('still returns the other sources when reading an entity fails', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([alex]);
    fake.longTerm.getEntity.mockRejectedValue(new Error('unsupported'));
    fake.shortTerm.searchMessages.mockResolvedValue([{ content: 'I use Neovim' }]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Alex', 5);

    expect(hits.some(h => h.source === 'graph')).toBe(false);
    expect(hits.map(h => h.content)).toEqual(
      expect.arrayContaining(['Alex — The user', 'I use Neovim']),
    );
  });
});

/**
 * Sources are merged by taking turns. Entities used to be ordered ahead of
 * everything else by their stored confidence — which measures extraction, not
 * relevance — and crowded the current conversation out of a small budget.
 */
describe('retrieveMemories — merging the sources', () => {
  const entities = (n: number, confidence?: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: `e${i}`, name: `e${i}`, description: `fact ${i}`, type: 'fact', confidence,
    }));

  it('gives each source a turn instead of filling the budget from one', async () => {
    fake.longTerm.searchEntities.mockResolvedValue(entities(5));
    fake.shortTerm.searchMessages.mockResolvedValue(
      Array.from({ length: 5 }, (_, i) => ({ content: `message ${i}` })),
    );

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'anything', 4);

    expect(hits.map(h => h.source)).toEqual([
      'long-term', 'conversation', 'long-term', 'conversation',
    ]);
  });

  it('keeps the conversation even when every entity carries a confidence', async () => {
    fake.longTerm.searchEntities.mockResolvedValue(entities(5, 0.99));
    fake.shortTerm.searchMessages.mockResolvedValue([{ content: 'I asked about Delhi' }]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'Delhi', 3);

    expect(hits.map(h => h.content)).toContain('I asked about Delhi');
  });

  it('returns nothing when the caller asks for no memories', async () => {
    fake.longTerm.searchEntities.mockResolvedValue(entities(3));
    fake.shortTerm.searchMessages.mockResolvedValue([{ content: 'a message' }]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'anything', 0);

    expect(hits).toEqual([]);
  });

  it('keeps the order the backend returned each source in', async () => {
    fake.longTerm.searchEntities.mockResolvedValue([
      { id: 'e0', name: 'first', type: 'fact', confidence: 0.1 },
      { id: 'e1', name: 'second', type: 'fact', confidence: 0.9 },
    ]);

    const hits = await retrieveMemories(fake as any, scope, 'conv-1', 'anything', 5, {
      graphExpansionLimit: 0,
    });

    expect(hits.map(h => h.content)).toEqual(['first', 'second']);
  });
});
