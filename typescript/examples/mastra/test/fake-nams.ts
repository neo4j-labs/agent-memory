/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service.
 *
 * `MemoryClient` accepts any `Transport`, so the whole example runs in CI with
 * no API key and no network. The fake keeps messages per conversation, which is
 * what makes the cross-thread assertions meaningful: a preference written in
 * thread one is resource-scoped and therefore still visible in thread two,
 * while thread two's message list starts empty.
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
  messages: StoredMessage[];
}

export class FakeNamsTransport implements Transport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly conversations = new Map<string, StoredConversation>();
  readonly preferences: Array<{ id: string; category: string; preference: string }> = [];
  closed = false;

  private counter = 0;

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
          messages: [],
        };
        this.conversations.set(conv.id, conv);
        return { id: conv.id, user_id: conv.user_id, metadata: conv.metadata, created_at: now };
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

      case "get_conversation_metadata": {
        const conv = this.conversation(params);
        return {
          id: conv.id,
          user_id: conv.user_id,
          metadata: conv.metadata,
          created_at: now,
        };
      }

      case "list_conversations":
        return [...this.conversations.values()]
          .filter((c) => params.userId === undefined || c.user_id === params.userId)
          .map((c) => ({ id: c.id, user_id: c.user_id, metadata: c.metadata, created_at: now }));

      case "delete_conversation": {
        const conv = this.conversation(params);
        this.conversations.delete(conv.id);
        return null;
      }

      case "get_context": {
        const conv = this.conversation(params);
        return {
          reflections: [],
          observations:
            conv.messages.length >= 4
              ? [
                  {
                    id: "obs-1",
                    conversation_id: conv.id,
                    content: "User is planning a 7-day food-and-history trip to Lisbon.",
                    created_at: now,
                  },
                ]
              : [],
          recent_messages: conv.messages.slice(-6),
        };
      }

      case "search_messages": {
        // Crude stand-in for vector search: substring match on "Lisbon".
        const conv = this.conversation(params);
        return conv.messages.filter((m) => m.content.toLowerCase().includes("lisbon"));
      }

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
        // Preferences are resource-scoped, not thread-scoped — no filtering by
        // conversation, which is exactly the behaviour the example demonstrates.
        return this.preferences;

      default:
        throw new Error(`FakeNamsTransport: unhandled method "${method}"`);
    }
  }
}
