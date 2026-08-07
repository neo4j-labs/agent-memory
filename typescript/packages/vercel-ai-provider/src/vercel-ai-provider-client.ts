import { MemoryClient } from '@neo4j-labs/agent-memory';
import { DEFAULT_ENDPOINT, NamsConfig, NamsScope, NamsLogger, MemoryHit, StoreInput, GraphExtractor, ClientState } from './vercel-ai-provider-types';

export type { NamsConfig, NamsScope, NamsLogger, MemoryHit, StoreInput, GraphExtractor };

const defaultLogger: NamsLogger = {
  warn: (message, error) => console.warn(`[nams] ${message}`, error ?? ''),
  error: (message, error) => console.error(`[nams] ${message}`, error ?? ''),
};

// Per-instance state
//
// The conversation cache is scoped to each MemoryClient instance (one per
// createNams / createNamsProvider / tools factory call). Nothing is shared
// across instances, so warm serverless workers can hold multiple providers
// for different users without cross-talk.

const stateByClient = new WeakMap<MemoryClient, ClientState>();

function getState(client: MemoryClient): ClientState {
  let state = stateByClient.get(client);
  if (!state) {
    state = { convCache: new Map(), logger: defaultLogger };
    stateByClient.set(client, state);
  }
  return state;
}

/** The logger bound to a client via makeClient (default: console). */
export function getLogger(client: MemoryClient): NamsLogger {
  return getState(client).logger;
}

/** Resolve the configured logger without needing a live client instance. */
export function resolveLogger(config: NamsConfig): NamsLogger {
  return config.logger ?? defaultLogger;
}

/**
 * Report a graph-extraction failure. The first one logs at `error`, the rest at
 * `warn`.
 *
 * Extraction failing is not a transient miss: a rejected schema or a bad model
 * config fails identically on every call, forever, while storage still succeeds
 * and the request still returns. Logging the first occurrence at `warn` is how
 * `extractionModel` stayed a silent no-op through a release.
 */
export function reportExtractionFailure(client: MemoryClient, message: string, err: unknown): void {
  const state = getState(client);
  if (state.extractionFailed) {
    state.logger.warn(message, err);
    return;
  }
  state.extractionFailed = true;
  state.logger.error(
    `${message} — graph extraction is disabled for every memory until this is fixed`,
    err,
  );
}

/**
 * Report a failed relationship write.
 *
 * The hosted REST API has no relationship endpoint and raises `NotSupportedError`
 * for every edge — a permanent condition, not a transient one. Since graph
 * extraction attempts one write per extracted edge per stored memory, logging
 * each occurrence would emit an unbounded stream of identical lines. The
 * unsupported case is therefore reported once per client and then suppressed;
 * genuine write failures still log every time.
 */
export function reportRelationshipFailure(client: MemoryClient, err: unknown): void {
  const state = getState(client);

  if ((err as { name?: string })?.name !== 'NotSupportedError') {
    state.logger.warn('addRelationship failed', err);
    return;
  }

  if (state.relationshipWritesUnsupported) return;
  state.relationshipWritesUnsupported = true;
  state.logger.warn(
    'this backend has no relationship endpoint — extracted entities are stored, ' +
    'edges are skipped. Further occurrences are suppressed.',
    err,
  );
}

export function makeClient(config: NamsConfig): MemoryClient {
  const client = new MemoryClient({
    endpoint: config.endpoint ?? DEFAULT_ENDPOINT,
    apiKey: config.apiKey,
    workspaceId: config.workspaceId,
  });
  stateByClient.set(client, { convCache: new Map(), logger: resolveLogger(config) });
  return client;
}

// Conversation resolution

function cacheKey(config: NamsConfig, userId: string): string {
  return `${config.workspaceId ?? 'default'}:${userId}`;
}

/**
 * Resolve a conversation id. Precedence:
 *   1. explicit scope.conversationId
 *   2. this instance's cache
 *   3. the user's most recent existing conversation in NAMS (GET)
 *   4. create a new one (CREATE)
 */
export async function resolveConversation(
  client: MemoryClient,
  config: NamsConfig,
  scope: NamsScope,
): Promise<string> {
  const { convCache } = getState(client);
  const key = cacheKey(config, scope.userId);

  if (scope.conversationId) {
    convCache.set(key, scope.conversationId);
    return scope.conversationId;
  }

  const cached = convCache.get(key);
  if (cached) return cached;

  try {
    const convs = await client.shortTerm.listConversations({ userId: scope.userId, limit: 1 });
    if (convs.length > 0) {
      convCache.set(key, convs[0].id);
      return convs[0].id;
    }
  } catch (err) {
    getLogger(client).warn('listConversations failed, creating new conversation', err);
  }

  const conv = await client.shortTerm.createConversation({ userId: scope.userId });
  convCache.set(key, conv.id);
  return conv.id;
}

/**
 * Find an existing conversation without creating one.
 * Returns null if the user has no conversations yet
 * (e.g. reasoning trace) that should not side-effect a new conversation.
 */
export async function findExistingConversation(
  client: MemoryClient,
  config: NamsConfig,
  scope: NamsScope,
): Promise<string | null> {
  if (scope.conversationId) return scope.conversationId;

  const { convCache } = getState(client);
  const key = cacheKey(config, scope.userId);
  const cached = convCache.get(key);
  if (cached) return cached;

  try {
    const convs = await client.shortTerm.listConversations({ userId: scope.userId, limit: 1 });
    if (convs.length === 0) return null;
    convCache.set(key, convs[0].id);
    return convs[0].id;
  } catch {
    return null;
  }
}

//Retrieval

const RETRIEVAL = {
  currentThreshold: 0.4,
  crossThreshold: 0.4,
  maxReasoning: 6,
  maxTotal: 12,
  maxLongterm:5
};

function deduplicatePush(
  hits: MemoryHit[],
  seen: Set<string>,
  hit: MemoryHit,
  aliasKey?: string,
): void {
  const k = hit.content?.trim();
  if (!k || seen.has(k)) return;
  seen.add(k);
  const alias = aliasKey?.trim();
  if (alias) seen.add(alias);
  hits.push(hit);
}

function entityContent(e: { name?: string; description?: string }): string {
  const name = e.name?.trim();
  const description = e.description?.trim();
  if (name && description) return `${name} — ${description}`;
  return name || description || '';
}

// Keyword + case-variant fallback
//
// Verified live against the hosted NAMS API: searchEntities/searchMessages do a
// literal, case-sensitive substring match 

function titleCase(word: string): string {
  return word.length ? word[0].toUpperCase() + word.slice(1) : word;
}

/** Significant query words (own case + Title Case), longest-first, capped. */
function fallbackTerms(query: string, maxWords = 4): string[] {
  const words = query
    .split(/\s+/)
    .filter(w => w.replace(/[^\p{L}\p{N}]/gu, '').length >= 3);
  const significant = [...new Set(words)]
    .sort((a, b) => b.length - a.length)
    .slice(0, maxWords);

  const variants = new Set<string>();
  for (const w of significant) {
    variants.add(w);
    variants.add(titleCase(w));
  }
  return [...variants];
}

function tokenize(text: string): Set<string> {
  return new Set(
    text.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean),
  );
}

/** Rank candidates by case-insensitive word overlap with the original query. */
function rankByOverlap<T>(candidates: T[], query: string, contentOf: (item: T) => string | undefined): T[] {
  const queryTokens = tokenize(query);
  const overlap = (item: T) => {
    const tokens = tokenize(contentOf(item) ?? '');
    let n = 0;
    for (const t of tokens) if (queryTokens.has(t)) n++;
    return n;
  };
  return [...candidates].sort((a, b) => overlap(b) - overlap(a));
}

/**
 * Try `query` as-is first; if NAMS's literal substring match finds nothing,
 * retry with individual query words (own case + Title Case), merge, dedupe,
 * and rank the result by word overlap with the original query.
 */
async function searchWithFallback<T>(
  query: string,
  search: (q: string) => Promise<T[]>,
  contentOf: (item: T) => string | undefined,
  log: NamsLogger,
  label: string,
): Promise<T[]> {
  const direct = await search(query).catch((e: unknown) => { log.warn(`${label} failed`, e); return [] as T[]; });
  if (direct.length > 0) return direct;

  const terms = fallbackTerms(query);
  if (terms.length === 0) return direct;

  const perTerm = await Promise.all(
    terms.map(t => search(t).catch((e: unknown) => {
      log.warn(`${label} fallback ("${t}") failed`, e);
      return [] as T[];
    })),
  );

  const seen = new Set<string>();
  const merged: T[] = [];
  for (const item of perTerm.flat()) {
    const key = contentOf(item)?.trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    merged.push(item);
  }

  return rankByOverlap(merged, query, contentOf);
}

async function searchPastConversations(
  client: MemoryClient,
  userId: string,
  currentConvId: string,
  query: string,
): Promise<MemoryHit[]> {
  const log = getLogger(client);
  const seen = new Set<string>();
  const hits: MemoryHit[] = [];

  let convs: Array<{ id: string }>;
  try {
    convs = await client.shortTerm.listConversations({ userId, limit: 20 });
  } catch (err) {
    log.warn('cross-session listConversations failed', err);
    return hits;
  }

  const past = convs.filter(c => c.id !== currentConvId);
  await Promise.all(
    past.map(async (conv) => {
      const [messages, steps] = await Promise.all([
        client.shortTerm
          .searchMessages(query, { sessionId: conv.id, limit: 4, threshold: RETRIEVAL.crossThreshold })
          .catch((e: unknown) => { log.warn('cross-session searchMessages failed', e); return [] as any[]; }),
        client.reasoning.listSteps(conv.id)
          .catch((e: unknown) => { log.warn('cross-session listSteps failed', e); return [] as any[]; }),
      ]);

      for (const m of messages) {
        deduplicatePush(hits, seen, { content: m.content, source: 'cross-session', type: 'message' });
      }
      for (const s of steps) {
        if (s.actionTaken !== 'direct response' || !s.reasoning) continue;
        deduplicatePush(hits, seen, { content: s.reasoning, source: 'cross-session', type: 'reasoning' });
      }
    }),
  );

  return hits;
}

/**
 * Search all four NAMS sources in parallel, dedupe, rank, and cap.
 * Priority (when no score): long-term > current conversation > cross-session > reasoning.
 * When scores are present the results are sorted by score descending.
 */
export async function retrieveMemories(
  client: MemoryClient,
  scope: NamsScope,
  convId: string,
  query: string,
  limit = 5,
): Promise<MemoryHit[]> {
  const log = getLogger(client);
  const [shortHits, longHits, reasoningSteps, crossHits] = await Promise.all([
    searchWithFallback(
      query,
      (q) => client.shortTerm.searchMessages(q, { sessionId: convId, limit, threshold: RETRIEVAL.currentThreshold }),
      (m) => m.content,
      log,
      'searchMessages',
    ),
    searchWithFallback(
      query,
      (q) => client.longTerm.searchEntities(q, { limit: RETRIEVAL.maxLongterm }),
      entityContent,
      log,
      'searchEntities',
    ),
    client.reasoning.listSteps(convId)
      .catch((e: unknown) => { log.warn('listSteps failed', e); return [] as any[]; }),
    searchPastConversations(client, scope.userId, convId, query),
  ]);

  const seen = new Set<string>();
  const hits: MemoryHit[] = [];

  for (const e of longHits) {
    deduplicatePush(hits, seen, {
      content: entityContent(e),
      source: 'long-term',
      type: e.type ?? 'entity',
      score: e.confidence,
    }, e.description ?? e.name);
  }
  for (const m of shortHits) {
    deduplicatePush(hits, seen, { content: m.content, source: 'conversation', type: 'message' });
  }
  for (const h of crossHits) deduplicatePush(hits, seen, h);

  const reasoning = (reasoningSteps as any[])
    .filter(s => s.actionTaken === 'direct response' && s.reasoning)
    .slice(0, RETRIEVAL.maxReasoning);
  for (const s of reasoning) {
    deduplicatePush(hits, seen, { content: s.reasoning, source: 'reasoning', type: 'step' });
  }

  const hasScores = hits.some(h => typeof h.score === 'number');
  const ranked = hasScores ? [...hits].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)) : hits;
  return ranked.slice(0, Math.min(limit, RETRIEVAL.maxTotal));
}

// Storage

function entityName(content: string, max = 60): string {
  const s = content.replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) + '…' : s;
}

/**
 * Persist a memory:
 *   - `interaction`               → short-term conversation thread
 *   - fact / preference / pattern → long-term graph
 * If `extractor` is provided, real entities + relationships are extracted
 * so the graph actually forms. Otherwise falls back to a single entity node.
 */
export async function storeMemory(
  client: MemoryClient,
  convId: string,
  input: StoreInput,
  opts: { extractor?: GraphExtractor } = {},
): Promise<void> {
  if (input.type === 'interaction') {
    await client.shortTerm.addMessage(convId, 'assistant', input.content);
    return;
  }

  if (opts.extractor) {
    try {
      await opts.extractor(client, input);
      return;
    } catch (err) {
      reportExtractionFailure(client, 'graph extraction failed, falling back to flat entity', err);
    }
  }

  const name = entityName(input.content);
  let entity = await client.longTerm.getEntityByName(name).catch(() => null);
  if (!entity) {
    entity = await client.longTerm.addEntity(name, input.type, { description: input.content });
  }
  if (entity?.id && input.confidence !== undefined) {
    await client.longTerm
      .setEntityFeedback(entity.id, { userScore: input.confidence, confirmed: input.confidence >= 0.8 })
      .catch((e: unknown) => getLogger(client).warn('setEntityFeedback failed', e));
  }
}
