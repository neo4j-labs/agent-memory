/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service.
 *
 * `MemoryClient` accepts any `Transport`, so the whole example can run in CI
 * with no API key and no network. The fake also models the one NAMS behaviour
 * the example has to cope with: entity extraction is asynchronous, so entities
 * only become searchable after a couple of polls.
 */

import type { Transport } from "@neo4j-labs/agent-memory";

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface FakeNamsOptions {
  /** Number of `search_entities` calls before extraction "completes". */
  pollsBeforeExtraction?: number;
}

export class FakeNamsTransport implements Transport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly messages: StoredMessage[] = [];
  readonly preferences: Array<{ id: string; category: string; preference: string }> = [];
  closed = false;
  entitySearches = 0;

  private readonly pollsBeforeExtraction: number;
  private conversationId = "";
  private counter = 0;

  constructor(options: FakeNamsOptions = {}) {
    this.pollsBeforeExtraction = options.pollsBeforeExtraction ?? 2;
  }

  /** Did a message mentioning Neo4j get persisted? Gates fake extraction. */
  private get hasExtractableContent(): boolean {
    return this.messages.some((m) => m.content.toLowerCase().includes("neo4j"));
  }

  private nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter}`;
  }

  async connect(): Promise<void> {}

  async close(): Promise<void> {
    this.closed = true;
  }

  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    this.calls.push({ method, params });
    return this.handle(method, params) as T;
  }

  private handle(method: string, params: Record<string, unknown>): unknown {
    const now = new Date().toISOString();
    switch (method) {
      case "create_conversation":
        this.conversationId = this.nextId("conv");
        return { id: this.conversationId, user_id: params.user_id, created_at: now };

      case "add_message": {
        const message: StoredMessage = {
          id: this.nextId("msg"),
          role: String(params.role),
          content: String(params.content),
          created_at: now,
        };
        this.messages.push(message);
        return message;
      }

      case "get_conversation":
        return { id: this.conversationId, messages: this.messages, created_at: now };

      case "get_context":
        return {
          reflections:
            this.messages.length >= 4
              ? [
                  {
                    id: "refl-1",
                    conversation_id: this.conversationId,
                    content: "User is evaluating graph databases for a recommendation engine.",
                    created_at: now,
                  },
                ]
              : [],
          observations: [],
          recent_messages: this.messages.slice(-6),
        };

      case "add_preference": {
        const preference = {
          id: this.nextId("pref"),
          category: String(params.category),
          preference: String(params.preference),
        };
        this.preferences.push(preference);
        return preference;
      }

      case "search_preferences":
        return this.preferences;

      case "search_entities": {
        this.entitySearches += 1;
        if (!this.hasExtractableContent || this.entitySearches < this.pollsBeforeExtraction) {
          return [];
        }
        return [
          {
            id: "entity-neo4j",
            name: "Neo4j",
            type: "concept",
            description: "Graph database the user is evaluating",
            created_at: now,
          },
        ];
      }

      case "expand_graph":
        return {
          nodes: [
            { id: "entity-neo4j", labels: ["Entity"], properties: { name: "Neo4j" } },
            { id: "entity-recs", labels: ["Entity"], properties: { name: "recommendation engine" } },
          ],
          edges: [{ id: "rel-1", source: "entity-neo4j", target: "entity-recs", type: "RELATED_TO" }],
        };

      default:
        throw new Error(`FakeNamsTransport: unhandled method "${method}"`);
    }
  }
}
