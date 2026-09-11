/**
 * Mastra integration — a **thin thread-and-message-history adapter** over
 * `MemoryClient`, in Mastra's vocabulary (`resourceId`, thread, message).
 *
 * ## What this is not
 *
 * It is **not** a Mastra `Memory` implementation and **not** a Mastra storage
 * adapter, so it cannot be passed to `new Agent({ memory })` or to
 * `new Memory({ storage })`. Mastra 1.x takes a custom backend as a *storage
 * adapter*: a `MastraCompositeStore` whose `memory` domain extends the abstract
 * `MemoryStorage` class from `@mastra/core/storage` — nine abstract methods over
 * `MastraDBMessage` / `StorageListMessagesInput`, plus resource-scoped working
 * memory. Several of those (`listMessagesById`, `updateMessages`) have no
 * equivalent on the NAMS API yet, so shipping a half-implemented domain would
 * fail inside Mastra's own recall paths rather than at wiring time.
 * `test/unit/mastra.test.ts` pins that gap against the installed `@mastra/core`
 * typings so it fails loudly when either side changes.
 *
 * ## What it is for
 *
 * Driving NAMS directly with Mastra-shaped calls — storing turns a Mastra agent
 * produced, and reading a thread back — while Mastra keeps its own storage:
 *
 * @example
 * ```ts
 * import { MemoryClient } from "@neo4j-labs/agent-memory";
 * import { Neo4jMastraMemory } from "@neo4j-labs/agent-memory/integrations/mastra";
 *
 * const client = new MemoryClient({ apiKey: process.env.MEMORY_API_KEY! });
 * const memory = new Neo4jMastraMemory(client);
 *
 * const thread = await memory.createThread({ resourceId: "alice", title: "Scouting" });
 * await memory.saveMessage({ threadId: thread.id, role: "user", content: "Hi" });
 * const history = await memory.getMessages(thread.id);
 * ```
 *
 * Everything written this way is a real NAMS conversation, so entity
 * extraction, three-tier context (`client.shortTerm.getContext`) and graph
 * queries all apply to it.
 */

import type { MemoryClient } from "../client.js";
import { NotSupportedError } from "../errors.js";

export interface MastraThread {
  id: string;
  resourceId: string;
  title?: string;
  metadata?: Record<string, unknown>;
}

export interface MastraMemoryMessage {
  id: string;
  threadId: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
}

/** Metadata key the thread title is folded into on the NAMS conversation. */
const TITLE_KEY = "mastraTitle";

export class Neo4jMastraMemory {
  constructor(private readonly client: MemoryClient) {}

  /** Create a new thread (Mastra term) — backed by createConversation on REST. */
  async createThread(input: {
    resourceId: string;
    title?: string;
    metadata?: Record<string, unknown>;
  }): Promise<MastraThread> {
    // Only fold the title in when there is one — writing `mastraTitle:
    // undefined` would put an explicit null-ish key on the conversation.
    const metadata: Record<string, unknown> = { ...input.metadata };
    if (input.title !== undefined) metadata[TITLE_KEY] = input.title;

    try {
      const conv = await this.client.shortTerm.createConversation({
        userId: input.resourceId,
        metadata,
      });
      return {
        id: conv.id,
        resourceId: input.resourceId,
        title: input.title,
        metadata: conv.metadata,
      };
    } catch (err) {
      if (err instanceof NotSupportedError) {
        // Bridge transport: there is no create call, so the thread id is the
        // session id we synthesize here. It must be unique per thread — a
        // resource owns many threads, and reusing `resourceId` would collapse
        // every one of them into a single session.
        return {
          id: `${input.resourceId}:${randomId()}`,
          resourceId: input.resourceId,
          title: input.title,
          metadata: input.metadata,
        };
      }
      throw err;
    }
  }

  /**
   * Read a thread back by id, including the title `createThread` folded into
   * the conversation metadata. REST transport only — bridge transports have no
   * conversation-metadata endpoint and throw `NotSupportedError`.
   */
  async getThreadById(threadId: string): Promise<MastraThread | null> {
    const conv = await this.client.shortTerm.getConversationMetadata(threadId);
    const metadata = conv.metadata ?? {};
    const title = typeof metadata[TITLE_KEY] === "string" ? metadata[TITLE_KEY] : conv.title;
    return {
      id: conv.id,
      resourceId: conv.userId ?? "",
      ...(title !== undefined ? { title } : {}),
      metadata,
    };
  }

  async getMessages(threadId: string, opts?: { limit?: number }): Promise<MastraMemoryMessage[]> {
    const conv = await this.client.shortTerm.getConversation(threadId, { limit: opts?.limit });
    return conv.messages.map((m) => ({
      id: m.id,
      threadId,
      role: m.role,
      content: m.content,
      createdAt: m.timestamp,
    }));
  }

  async saveMessage(input: {
    threadId: string;
    role: "user" | "assistant" | "system";
    content: string;
    metadata?: Record<string, unknown>;
  }): Promise<MastraMemoryMessage> {
    const m = await this.client.shortTerm.addMessage(input.threadId, input.role, input.content, {
      metadata: input.metadata,
    });
    return {
      id: m.id,
      threadId: input.threadId,
      role: m.role,
      content: m.content,
      createdAt: m.timestamp,
    };
  }

  async deleteThread(threadId: string): Promise<void> {
    try {
      await this.client.shortTerm.deleteConversation(threadId);
    } catch (err) {
      if (err instanceof NotSupportedError) {
        await this.client.shortTerm.clearSession(threadId);
        return;
      }
      throw err;
    }
  }
}

/**
 * A CSPRNG-backed random id for synthesized bridge thread ids. Mirrors the
 * fallback chain in `src/middleware/vercel-ai.ts` — `Math.random` would be
 * cryptographically insecure and can repeat within a tick.
 */
function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const buf = new Uint8Array(16);
    crypto.getRandomValues(buf);
    return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  throw new Error("Secure randomness is unavailable in this runtime.");
}
