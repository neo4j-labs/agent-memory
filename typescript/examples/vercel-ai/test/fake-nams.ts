/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service.
 *
 * `MemoryClient` accepts any `Transport`, so the whole example runs in CI with
 * no API key and no network. Only the operations this example performs are
 * implemented; anything else throws, so a drifting example fails loudly.
 *
 * The fake stores messages per conversation and preferences per workspace —
 * the split that makes the cross-session assertions meaningful: a new
 * conversation starts with no history but still recalls the preference.
 */

import type { Transport } from "@neo4j-labs/agent-memory";

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

interface StoredConversation {
  id: string;
  user_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
  messages: StoredMessage[];
}

export class FakeNamsTransport implements Transport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly conversations = new Map<string, StoredConversation>();
  readonly preferences: Array<{ id: string; category: string; preference: string }> = [];
  closed = false;

  private counter = 0;

  /** Entities the fake "extraction pipeline" produces for any entity search. */
  entities: Array<{ id: string; name: string; type: string }> = [
    { id: "ent-1", name: "Euro-style board games", type: "OBJECT" },
  ];

  private nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter}`;
  }

  private conversation(params: Record<string, unknown>): StoredConversation {
    const id = String(params.session_id ?? params.conversation_id ?? "");
    const conv = this.conversations.get(id);
    if (!conv) throw new Error(`FakeNamsTransport: unknown conversation "${id}"`);
    return conv;
  }

  /** Roles stored on a conversation, in write order. */
  rolesOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.role);
  }

  /** Contents stored on a conversation, in write order. */
  contentsOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.content);
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
      case "create_conversation": {
        const conv: StoredConversation = {
          id: this.nextId("conv"),
          user_id: String(params.user_id),
          metadata: (params.metadata as Record<string, unknown>) ?? {},
          created_at: now,
          messages: [],
        };
        this.conversations.set(conv.id, conv);
        return {
          id: conv.id,
          user_id: conv.user_id,
          metadata: conv.metadata,
          created_at: conv.created_at,
        };
      }

      case "list_conversations":
        return [...this.conversations.values()]
          .filter((c) => params.userId === undefined || c.user_id === params.userId)
          .map((c) => ({
            id: c.id,
            user_id: c.user_id,
            metadata: c.metadata,
            created_at: c.created_at,
            message_count: c.messages.length,
          }));

      case "get_conversation_metadata": {
        const conv = this.conversation(params);
        return {
          id: conv.id,
          user_id: conv.user_id,
          metadata: conv.metadata,
          created_at: conv.created_at,
          message_count: conv.messages.length,
        };
      }

      case "add_message": {
        const conv = this.conversation(params);
        const message: StoredMessage = {
          id: this.nextId("msg"),
          role: String(params.role),
          content: String(params.content),
          created_at: now,
        };
        conv.messages.push(message);
        return message;
      }

      case "get_conversation": {
        const conv = this.conversation(params);
        return { id: conv.id, session_id: conv.id, messages: conv.messages, created_at: now };
      }

      case "get_context": {
        const conv = this.conversation(params);
        return {
          reflections: [],
          // One observation once the conversation has a couple of turns, so the
          // example's epilogue has something non-zero to print.
          observations:
            conv.messages.length >= 4
              ? [
                  {
                    id: "obs-1",
                    conversation_id: conv.id,
                    content: "User is building a euro-style board game recommender.",
                    created_at: now,
                  },
                ]
              : [],
          recent_messages: conv.messages,
        };
      }

      case "add_preference": {
        const pref = {
          id: this.nextId("pref"),
          category: String(params.category),
          preference: String(params.preference),
        };
        this.preferences.push(pref);
        return pref;
      }

      case "search_preferences":
        return this.preferences;

      case "search_entities":
        return this.entities;

      default:
        throw new Error(`FakeNamsTransport: unimplemented method "${method}"`);
    }
  }
}
