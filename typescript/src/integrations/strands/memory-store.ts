/**
 * Strands `MemoryStore` over a NAMS context graph.
 *
 * Long-term recall for the agent loop — distinct from `Neo4jSessionStorage`,
 * which persists and restores the transcript. NAMS exposes entity endpoints
 * only, so `search()` is entities-only and the preference/fact search knobs the
 * Python store carries are deliberately absent from the options type rather
 * than silently ignored.
 */

import type {
  AddMessagesContext,
  ExtractionConfig,
  JSONValue,
  MemoryEntry,
  MemoryStore,
  MemoryStoreConfig,
  MessageData,
  SearchOptions,
  Tool,
} from "@strands-agents/sdk";

import { MemoryClient } from "../../client.js";
import { NotSupportedError } from "../../errors.js";
import type { BulkMessageInput, Entity, MemoryClientOptions, MessageRole } from "../../types.js";
import { strandsMessageToText } from "./messages.js";
import { buildStoreTools } from "./store-tools.js";

/** Conversation-metadata key marking a conversation as this store's write sink. */
const STORE_METADATA_KEY = "strands_memory_store";

/** `userId` recorded on a sink conversation when the store is not tenant-scoped.
 *  `createConversation` requires one (src/types.ts:445-448); the sink is still
 *  found by its metadata, so this value is a label, not an identity. */
const UNSCOPED_SINK_USER_ID = "strands-memory-store";

/** Conversations scanned when looking for an existing sink. */
const SINK_SCAN_LIMIT = 1000;

/** Strands' own per-store default when neither caller nor store sets a limit
 *  (mirrors DEFAULT_MAX_SEARCH_RESULTS in the SDK's memory-manager, which is
 *  not exported from the package root). The manager always resolves the limit
 *  before calling a store, so this only applies to a direct `search()` call. */
const DEFAULT_MAX_SEARCH_RESULTS = 3;

/** bulkAddMessages rejects more than 100 messages per call
 *  (src/short-term/index.ts:283-285). */
const BULK_CHUNK = 100;

export interface Neo4jMemoryStoreOptions
  extends Pick<
    MemoryStoreConfig,
    "name" | "description" | "maxSearchResults" | "writable" | "extraction"
  > {
  /** A live client. Borrowed: `close()` leaves it open. */
  client?: MemoryClient;
  /** Client settings the store builds a client from and owns. */
  clientOptions?: MemoryClientOptions;
  /** Explicit write target. Omit to use a deterministic sink conversation. */
  conversationId?: string;
  /** Scopes writes and the sink name to one tenant. Reads are not narrowed:
   *  `searchEntities` takes no user filter. */
  userId?: string;
  /** Include entities in `search()`. Defaults to `true`; `false` makes the
   *  store a write-only sink. */
  includeEntities?: boolean;
  /** Expose the store's graph tool from `getTools()`. Defaults to `true`. */
  graphTools?: boolean;
}

export interface Neo4jMemoryAddResult {
  kind: "message" | "entity" | "preference" | "fact";
  id: string;
}

export interface Neo4jMemoryAddMessagesResult {
  written: number;
  skipped: number;
}

export class Neo4jMemoryStore implements MemoryStore {
  // The five members MemoryStore requires a store to expose as attributes.
  readonly name: string;
  readonly description: string;
  readonly maxSearchResults: number | undefined;
  readonly writable: boolean;
  readonly extraction: boolean | ExtractionConfig;

  readonly userId: string | undefined;
  readonly graphTools: boolean;

  /** The store's own client. Public so `store-tools.ts` can reach it without
   *  a second connection; the tools go through the store, never around it. */
  readonly client: MemoryClient;

  protected readonly includeEntities: boolean;
  protected readonly ownsClient: boolean;

  /** Names the coordinator generation for the addMessages dedupe key. See
   *  `addMessages` for why a bare sequence number is not enough. */
  protected readonly runId: string = newRunId();
  protected readonly written = new Set<string>();
  protected readonly warnedUnsupportedKinds = new Set<string>();
  protected sinkKey: string | undefined;
  private connected = false;

  constructor(options: Neo4jMemoryStoreOptions) {
    if (!options?.name) {
      throw new Error("Neo4jMemoryStore: 'name' is required and must be non-empty");
    }
    const hasClient = options.client !== undefined;
    const hasClientOptions = options.clientOptions !== undefined;
    if (hasClient === hasClientOptions) {
      throw new Error(
        "Neo4jMemoryStore: pass exactly one of 'client' (borrowed, left open) or " +
          "'clientOptions' (a client is constructed and owned by the store)",
      );
    }

    this.name = options.name;
    this.description =
      options.description ??
      `Neo4j context graph '${options.name}': entities extracted from conversations.`;
    this.maxSearchResults = options.maxSearchResults;
    this.writable = options.writable ?? true;
    this.extraction = options.extraction ?? false;

    this.userId = options.userId;
    this.graphTools = options.graphTools ?? true;
    this.includeEntities = options.includeEntities ?? true;
    this.sinkKey = options.conversationId;

    this.ownsClient = !hasClient;
    this.client = options.client ?? new MemoryClient(options.clientOptions);
  }

  /**
   * Build a store against hosted NAMS, reading `MEMORY_ENDPOINT` /
   * `MEMORY_API_KEY` from the environment when not passed. Mirrors
   * `Neo4jMemoryStore.for_nams` in the Python SDK. The caller's options are
   * not mutated.
   */
  static forNams(
    options: Omit<Neo4jMemoryStoreOptions, "client" | "clientOptions">,
    connection: { endpoint?: string; apiKey?: string } = {},
  ): Neo4jMemoryStore {
    const endpoint = connection.endpoint ?? readEnv("MEMORY_ENDPOINT");
    const apiKey = connection.apiKey ?? readEnv("MEMORY_API_KEY");
    return new Neo4jMemoryStore({
      ...options,
      clientOptions: {
        ...(endpoint !== undefined && { endpoint }),
        ...(apiKey !== undefined && { apiKey }),
      },
    });
  }

  /** Deterministic sink name, stable across processes and restarts. */
  protected get sinkName(): string {
    return `strands-memory-store/${this.userId ?? "_"}/${this.name}`;
  }

  /**
   * Connect the client. Idempotent, and cheap on a repeat: the transport is
   * lazily connected anyway, so this exists to surface an auth or endpoint
   * error at agent construction rather than mid-turn.
   */
  async initialize(): Promise<void> {
    if (this.connected) return;
    await this.client.connect();
    this.connected = true;
  }

  /**
   * Search long-term memory. No sink resolution: reads do not need one.
   *
   * Entities only — `search_preferences` and `search_facts` have no REST route
   * (transport/rest.ts:172-174). Limit precedence: per-call option, then
   * `maxSearchResults`, then Strands' default. A failure propagates: the
   * manager fans out with `Promise.allSettled` and logs a dead store, which is
   * more useful than a misleadingly-successful empty result.
   */
  async search(query: string, options?: SearchOptions): Promise<MemoryEntry[]> {
    if (!this.includeEntities) return [];
    await this.initialize();
    const limit =
      options?.maxSearchResults ?? this.maxSearchResults ?? DEFAULT_MAX_SEARCH_RESULTS;
    const entities = await this.client.longTerm.searchEntities(query, { limit });
    return entities.map(toMemoryEntry);
  }

  /**
   * Add one piece of content.
   *
   * Default sink: a message in the store's conversation, which NAMS extracts
   * server-side — the one path available on every backend. `metadata.kind` opts
   * into a typed write; where the backend has no endpoint for that kind the
   * write falls back to the sink, so a memory is never silently dropped.
   *
   * Extraction writes are at-least-once, so this tolerates duplicates.
   */
  async add(
    content: string,
    metadata?: Record<string, JSONValue>,
  ): Promise<Neo4jMemoryAddResult> {
    this.assertWritable("add");
    if (!content.trim()) {
      throw new Error(`Neo4jMemoryStore '${this.name}': content must not be empty`);
    }
    await this.initialize();

    const meta = metadata ?? {};
    const kind = typeof meta.kind === "string" ? meta.kind : undefined;
    if (kind === "entity" || kind === "preference" || kind === "fact") {
      try {
        return await this.addTyped(kind, content, meta);
      } catch (error) {
        if (!(error instanceof NotSupportedError)) throw error;
        if (!this.warnedUnsupportedKinds.has(kind)) {
          this.warnedUnsupportedKinds.add(kind);
          console.warn(
            `Neo4jMemoryStore '${this.name}': ${kind} writes are unsupported on this ` +
              `backend (${error.message}); falling back to the message sink. Logged ` +
              `once per store — further ${kind} writes fall back silently.`,
          );
        }
      }
    }
    return this.addToSink(content);
  }

  private async addTyped(
    kind: "entity" | "preference" | "fact",
    content: string,
    meta: Record<string, JSONValue>,
  ): Promise<Neo4jMemoryAddResult> {
    const longTerm = this.client.longTerm;
    if (kind === "entity") {
      const entity = await longTerm.addEntity(
        asString(meta.name) ?? content,
        asString(meta.type) ?? "OBJECT",
      );
      return { kind, id: entity.id };
    }
    if (kind === "preference") {
      const preference = await longTerm.addPreference(
        asString(meta.category) ?? "memory",
        content,
      );
      return { kind, id: preference.id };
    }
    const subject = asString(meta.subject);
    const predicate = asString(meta.predicate);
    const object = asString(meta.object);
    if (!subject || !predicate || !object) {
      // Validated before the call, so a malformed triple is a caller error
      // rather than a backend gap — it must not be swallowed by the sink
      // fallback.
      throw new Error(
        `Neo4jMemoryStore '${this.name}': kind='fact' requires subject, ` +
          `predicate and object in metadata`,
      );
    }
    const fact = await longTerm.addFact(subject, predicate, object);
    return { kind, id: fact.id };
  }

  private async addToSink(content: string): Promise<Neo4jMemoryAddResult> {
    const sink = await this.resolveSink();
    const message = await this.client.shortTerm.addMessage(sink, "user", content);
    return { kind: "message", id: message.id };
  }

  protected assertWritable(method: string): void {
    if (!this.writable) {
      throw new Error(
        `Neo4jMemoryStore '${this.name}': store is not writable. ` +
          `Set writable: true to enable ${method}().`,
      );
    }
  }

  /**
   * Ingest a batch of conversation turns into the sink conversation. NAMS
   * extracts them server-side, so no model call happens here.
   *
   * Extraction writes are at-least-once and `sequenceNumbers` repeat on a
   * retry, so a `(runId, sequenceNumber)` set skips turns this instance already
   * wrote. In-process only: sequence numbers belong to the extraction
   * coordinator, which is created per `initAgent`, so there is nothing durable
   * to key on. One consequence, documented: handing the same store instance to
   * a second Agent restarts the numbering and its first turns are skipped.
   */
  async addMessages(
    messages: MessageData[],
    context?: AddMessagesContext,
  ): Promise<Neo4jMemoryAddMessagesResult> {
    this.assertWritable("addMessages");
    await this.initialize();

    const sequenceNumbers = context?.sequenceNumbers ?? [];
    const payload: BulkMessageInput[] = [];
    const tokens: Array<string | undefined> = [];
    let skipped = 0;

    messages.forEach((message, index) => {
      const text = strandsMessageToText(message);
      if (!text.trim()) {
        skipped += 1;
        return;
      }
      let token: string | undefined;
      const sequenceNumber = sequenceNumbers[index];
      if (sequenceNumber !== undefined) {
        token = `${this.runId}:${sequenceNumber}`;
        if (this.written.has(token)) {
          skipped += 1;
          return;
        }
      }
      payload.push({ role: (message.role ?? "user") as MessageRole, content: text });
      tokens.push(token);
    });

    if (payload.length === 0) return { written: 0, skipped };

    const sink = await this.resolveSink();
    for (let start = 0; start < payload.length; start += BULK_CHUNK) {
      const end = start + BULK_CHUNK;
      await this.client.shortTerm.bulkAddMessages(sink, payload.slice(start, end));
      // Banked per chunk, not after the loop: Strands rolls its high-water mark
      // back and retries the whole batch when this throws, so tokens banked
      // only at the end would let an already-written chunk be written twice.
      for (const token of tokens.slice(start, end)) {
        if (token !== undefined) this.written.add(token);
      }
    }
    return { written: payload.length, skipped };
  }

  /**
   * Graph-native tools registered alongside the manager's own. Never includes
   * `search_memory` or `add_memory` — those belong to the `MemoryManager`.
   * Synchronous by contract: the plugin registry calls this before `initAgent`.
   */
  getTools(): Tool[] {
    if (!this.graphTools) return [];
    return buildStoreTools(this);
  }

  /**
   * Close the client only when the store constructed it. Resets the connected
   * flag either way, so re-entering the store reconnects rather than
   * short-circuiting on a stale flag.
   */
  async close(): Promise<void> {
    if (this.ownsClient) {
      await this.client.close();
    }
    this.connected = false;
  }

  /**
   * Resolve the conversation writes go to, creating the sink on first use.
   *
   * NAMS mints its own conversation ids and drops a client-supplied session id,
   * so the only portable handle is conversation metadata: list, match this
   * store's deterministic sink name under `STORE_METADATA_KEY`, else create one
   * carrying it. Metadata is accepted at creation and not settable afterwards.
   * The resolved id is cached, so the scan happens at most once per instance.
   */
  protected async resolveSink(): Promise<string> {
    if (this.sinkKey !== undefined) return this.sinkKey;

    const existing = await this.client.shortTerm.listConversations({
      limit: SINK_SCAN_LIMIT,
      ...(this.userId !== undefined && { userId: this.userId }),
    });
    for (const conversation of existing) {
      if ((conversation.metadata ?? {})[STORE_METADATA_KEY] === this.sinkName) {
        this.sinkKey = conversation.id;
        return this.sinkKey;
      }
    }

    const created = await this.client.shortTerm.createConversation({
      userId: this.userId ?? UNSCOPED_SINK_USER_ID,
      metadata: { [STORE_METADATA_KEY]: this.sinkName, sessionType: "MEMORY_STORE" },
    });
    this.sinkKey = created.id;
    return this.sinkKey;
  }
}

function asString(value: JSONValue | undefined): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function entityType(entity: Entity): string {
  return entity.subtype ? `${entity.type}:${entity.subtype}` : entity.type;
}

function toMemoryEntry(entity: Entity): MemoryEntry {
  const type = entityType(entity);
  const name = entity.canonicalName ?? entity.name;
  const suffix = entity.description ? ` — ${entity.description}` : "";
  return {
    content: `[entity] ${name} (${type})${suffix}`,
    // No `score`: NAMS returns no similarity on entity search, and defaulting
    // one to 0 would misrepresent an unscored hit as a bad match.
    metadata: { kind: "entity", id: entity.id, type },
  };
}

function readEnv(name: string): string | undefined {
  // Edge runtimes read env from the request scope, not module init — guard
  // rather than assume, as src/client.ts:102-106 does.
  if (typeof process === "undefined" || !process.env) return undefined;
  return process.env[name];
}

let runCounter = 0;

function newRunId(): string {
  // crypto.randomUUID exists on every supported runtime (Node 20+, Bun, Deno,
  // Workers). The fallback keeps a stripped-down runtime working: this token
  // only has to be unique among one store instance's own runs, so a
  // timestamp-plus-counter suffix is sufficient.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  runCounter += 1;
  return `${Date.now().toString(36)}-${runCounter}`;
}
