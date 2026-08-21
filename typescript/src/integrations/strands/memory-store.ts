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
  ExtractionConfig,
  MemoryStoreConfig,
} from "@strands-agents/sdk";

import { MemoryClient } from "../../client.js";
import type { MemoryClientOptions } from "../../types.js";

/** Conversation-metadata key marking a conversation as this store's write sink. */
const STORE_METADATA_KEY = "strands_memory_store";

/** `userId` recorded on a sink conversation when the store is not tenant-scoped.
 *  `createConversation` requires one (src/types.ts:445-448); the sink is still
 *  found by its metadata, so this value is a label, not an identity. */
const UNSCOPED_SINK_USER_ID = "strands-memory-store";

/** Conversations scanned when looking for an existing sink. */
const SINK_SCAN_LIMIT = 1000;

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

// `implements MemoryStore` is added once `search` lands (a following task) —
// `MemoryStore.search` is a required member, so declaring it early fails
// `tsc --noEmit` before this store can actually satisfy the interface.
export class Neo4jMemoryStore {
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
