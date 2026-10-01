import { MemoryClient, type Entity } from '@neo4j-labs/agent-memory';
import { DEFAULT_ENDPOINT, NamsConfig, NamsScope, NamsLogger, MemoryHit, StoreInput, GraphExtractor, ClientState } from './vercel-ai-provider-types';

export type { NamsConfig, NamsScope, NamsLogger, MemoryHit, StoreInput, GraphExtractor };

const defaultLogger: NamsLogger = {
  warn: (message, error) => console.warn(`[nams] ${message}`, error ?? ''),
  error: (message, error) => console.error(`[nams] ${message}`, error ?? ''),
};

// Per-client state, so users sharing one process don't mix up conversations.

const stateByClient = new WeakMap<MemoryClient, ClientState>();

function getState(client: MemoryClient): ClientState {
  let state = stateByClient.get(client);
  if (!state) {
    state = { convCache: new Map(), logger: defaultLogger };
    stateByClient.set(client, state);
  }
  return state;
}

/** The logger set by makeClient (default: console). */
export function getLogger(client: MemoryClient): NamsLogger {
  return getState(client).logger;
}

/** The configured logger, without needing a client. */
export function resolveLogger(config: NamsConfig): NamsLogger {
  return config.logger ?? defaultLogger;
}

/** Log a graph extraction failure: an error the first time, warnings after. */
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

/** Log a failed relationship write. "Not supported" is logged only once per client. */
export function reportRelationshipFailure(client: MemoryClient, err: unknown): void {
  const state = getState(client);

  if ((err as { name?: string })?.name !== 'NotSupportedError') {
    state.logger.warn('addRelationship failed', err);
    return;
  }

  if (state.relationshipWritesUnsupported) return;
  state.relationshipWritesUnsupported = true;
  state.logger.warn(
    'this backend does not accept relationship writes — extracted entities are stored, ' +
    'edges are skipped. NAMS builds edges server-side from saved turns. ' +
    'Further occurrences are suppressed.',
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

/** The conversation id: the given one, else the cached one, else the user's latest, else a new one. */
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

/** Like `resolveConversation`, but never creates one. Returns null if none exists. */
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

// Retrieval

const RETRIEVAL = {
  currentThreshold: 0.4,
  crossThreshold: 0.4,
  crossSessions: 5,
  maxReasoning: 6,
  maxTotal: 12,
  maxLongterm: 5,
  /** Matched entities to read relationships for. One request each. */
  graphExpansions: 2,
  /** Cap per entity, so one hub entity cannot fill the whole budget. */
  maxTriples: 5,
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

// Graph expansion

/** Read the relationships around the entities that matched. Search returns entities without them. */
async function expandEntityGraph(
  client: MemoryClient,
  entities: Entity[],
  maxEntities: number,
): Promise<MemoryHit[]> {
  const log = getLogger(client);
  const roots = entities.filter(e => e.id).slice(0, maxEntities);
  if (roots.length === 0) return [];

  const expanded = await Promise.all(
    roots.map(async (root) => {
      const detail = await client.longTerm.getEntity(root.id).catch((err: unknown) => {
        log.warn(`getEntity failed for "${root.name}", skipping its relationships`, err);
        return null;
      });

      const from = (detail?.canonicalName ?? detail?.name ?? root.name ?? '').trim();
      if (!from) return [];

      return (detail?.relationships ?? [])
        // A bare id tells the model nothing, so an unnamed target is dropped.
        .filter(ref => ref.type?.trim() && ref.targetName?.trim())
        .slice(0, RETRIEVAL.maxTriples)
        .map((ref): MemoryHit => ({
          content: `(${from})-[${ref.type.trim()}]->(${ref.targetName!.trim()})`,
          source: 'graph',
          type: ref.type.trim(),
        }));
    }),
  );

  return expanded.flat();
}

/** Take one hit from each source in turn, so no single source fills the prompt. */
function interleave(buckets: MemoryHit[][], limit: number): MemoryHit[] {
  // The loop checks the budget only after taking a hit, so zero is handled here.
  if (limit <= 0) return [];

  const out: MemoryHit[] = [];
  const deepest = Math.max(0, ...buckets.map(b => b.length));

  for (let i = 0; i < deepest; i++) {
    for (const bucket of buckets) {
      if (i >= bucket.length) continue;
      out.push(bucket[i]);
      if (out.length === limit) return out;
    }
  }
  return out;
}

// NAMS search matches exact text, so a whole question rarely finds anything.
// When it finds nothing, search again word by word.

/** First letter upper case, the rest lower case, so "NEOVIM" becomes "Neovim". */
function titleCase(word: string): string {
  return word.length ? word[0].toUpperCase() + word.slice(1).toLowerCase() : word;
}

/** The query's longest words, as typed and in Title Case: "Alex's?" → "Alex", "alex". */
function fallbackTerms(query: string, maxWords = 4): string[] {
  const words = query
    .split(/\s+/)
    .map(w => w.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '').replace(/['’]s$/iu, ''))
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

/** Sort items by how many words they share with the query. */
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

/** Search the whole query, then word by word if that finds nothing. */
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
  maxConversations: number,
): Promise<MemoryHit[]> {
  const log = getLogger(client);
  const seen = new Set<string>();
  const hits: MemoryHit[] = [];
  if (maxConversations <= 0) return hits;

  let convs: Array<{ id: string }>;
  try {
    // One extra, since the list may include the current conversation.
    convs = await client.shortTerm.listConversations({ userId, limit: maxConversations + 1 });
  } catch (err) {
    log.warn('cross-session listConversations failed', err);
    return hits;
  }

  const past = convs.filter(c => c.id !== currentConvId).slice(0, maxConversations);
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

/** Search every memory source, drop duplicates, and return the best few. */
export async function retrieveMemories(
  client: MemoryClient,
  scope: NamsScope,
  convId: string,
  query: string,
  limit = 5,
  opts: { crossSessionLimit?: number; graphExpansionLimit?: number } = {},
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
    searchPastConversations(
      client, scope.userId, convId, query, opts.crossSessionLimit ?? RETRIEVAL.crossSessions,
    ),
  ]);

  // Depends on which entities matched, so it cannot join the batch above.
  const triples = await expandEntityGraph(
    client, longHits, opts.graphExpansionLimit ?? RETRIEVAL.graphExpansions,
  );

  // One bucket per source, merged by taking turns rather than stacking.
  const seen = new Set<string>();
  const entityHits: MemoryHit[] = [];
  const graphHits: MemoryHit[] = [];
  const messageHits: MemoryHit[] = [];
  const crossSessionHits: MemoryHit[] = [];
  const reasoningHits: MemoryHit[] = [];

  for (const e of longHits) {
    deduplicatePush(entityHits, seen, {
      content: entityContent(e),
      source: 'long-term',
      type: e.type ?? 'entity',
      score: e.confidence,
    }, e.description ?? e.name);
  }
  for (const t of triples) deduplicatePush(graphHits, seen, t);
  for (const m of shortHits) {
    deduplicatePush(messageHits, seen, { content: m.content, source: 'conversation', type: 'message' });
  }
  for (const h of crossHits) deduplicatePush(crossSessionHits, seen, h);

  const reasoning = (reasoningSteps as any[])
    .filter(s => s.actionTaken === 'direct response' && s.reasoning)
    .slice(0, RETRIEVAL.maxReasoning);
  for (const s of reasoning) {
    deduplicatePush(reasoningHits, seen, { content: s.reasoning, source: 'reasoning', type: 'step' });
  }

  return interleave(
    [entityHits, graphHits, messageHits, crossSessionHits, reasoningHits],
    Math.min(limit, RETRIEVAL.maxTotal),
  );
}

// Storage

function entityName(content: string, max = 60): string {
  const s = content.replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) + '…' : s;
}

const sameText = (a: string | undefined, b: string): boolean =>
  a?.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * The stored entity with this name and type, or null. Hosted NAMS cannot look up
 * a name, so search is used — and only an exact name matches, since search would
 * otherwise return "Alexandra" for "Alex" and merge two real entities.
 */
export async function findEntity(client: MemoryClient, name: string, type: string): Promise<Entity | null> {
  const matchesWanted = (e: Entity): boolean =>
    sameText(e.type, type) && (sameText(e.name, name) || sameText(e.canonicalName, name));

  const direct = await client.longTerm.getEntityByName(name).catch(() => null);
  if (direct) return matchesWanted(direct) ? direct : null;

  const candidates = await client.longTerm
    .searchEntities(name, { limit: RETRIEVAL.maxLongterm })
    .catch(() => [] as Entity[]);
  return candidates.find(matchesWanted) ?? null;
}

/** Save a memory: an `interaction` to the conversation, anything else to the graph. */
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
  let entity = await findEntity(client, name, input.type);
  if (!entity) {
    entity = await client.longTerm.addEntity(name, input.type, { description: input.content });
  }
  if (entity?.id && input.confidence !== undefined) {
    await client.longTerm
      .setEntityFeedback(entity.id, { userScore: input.confidence, confirmed: input.confidence >= 0.8 })
      .catch((e: unknown) => getLogger(client).warn('setEntityFeedback failed', e));
  }
}
