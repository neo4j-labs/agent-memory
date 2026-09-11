/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service.
 *
 * `MemoryClient` accepts any `Transport`, so the whole agent runs in CI with no
 * API key and no network. Only the operations this example performs are
 * implemented; anything else throws, so a drifting example fails loudly instead
 * of silently skipping a memory write.
 *
 * The storage split is what makes the cross-session assertions meaningful:
 * messages belong to a conversation, conversations belong to a shopper
 * (`user_id`), and preferences, entities and facts are workspace-wide — exactly
 * as in the real service. That is why `nams-memory.ts` tags preferences with the
 * shopper and filters them on read, and why the tests can prove the filter works.
 */

import type { Transport } from "@neo4j-labs/agent-memory";

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

interface StoredConversation {
  id: string;
  user_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
  messages: StoredMessage[];
  /** Rollups the real service computes in the background. */
  observations: string[];
  reflections: string[];
}

interface StoredPreference {
  id: string;
  category: string;
  preference: string;
  context?: string;
}

interface StoredEntity {
  id: string;
  name: string;
  type: string;
  description?: string;
}

interface StoredStep {
  id: string;
  conversation_id: string;
  reasoning: string;
  action_taken: string;
  result?: string;
  created_at: string;
}

interface StoredFact {
  id: string;
  subject: string;
  predicate: string;
  object: string;
}

export class FakeNamsTransport implements Transport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly conversations = new Map<string, StoredConversation>();
  readonly preferences: StoredPreference[] = [];
  readonly entities: StoredEntity[] = [];
  readonly steps: StoredStep[] = [];
  readonly facts: StoredFact[] = [];
  closed = false;

  private counter = 0;

  private nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter}`;
  }

  private conversation(params: Record<string, unknown>): StoredConversation {
    const id = String(params["session_id"] ?? params["conversation_id"] ?? "");
    const conversation = this.conversations.get(id);
    if (conversation === undefined) {
      throw new Error(`FakeNamsTransport: unknown conversation "${id}"`);
    }
    return conversation;
  }

  /** Seed a rollup the way the background pipeline eventually would. */
  addObservation(conversationId: string, content: string): void {
    const conversation = this.conversations.get(conversationId);
    if (conversation === undefined) throw new Error(`unknown conversation ${conversationId}`);
    conversation.observations.push(content);
  }

  addReflection(conversationId: string, content: string): void {
    const conversation = this.conversations.get(conversationId);
    if (conversation === undefined) throw new Error(`unknown conversation ${conversationId}`);
    conversation.reflections.push(content);
  }

  /** Roles stored on a conversation, in write order. */
  rolesOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.role);
  }

  /** Contents stored on a conversation, in write order. */
  contentsOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.content);
  }

  conversationsFor(userId: string): StoredConversation[] {
    return [...this.conversations.values()].filter((c) => c.user_id === userId);
  }

  methodsCalled(): string[] {
    return this.calls.map((call) => call.method);
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
    const now = new Date(Date.now() + this.counter).toISOString();
    switch (method) {
      case "create_conversation": {
        const conversation: StoredConversation = {
          id: this.nextId("conv"),
          user_id: String(params["user_id"]),
          metadata: (params["metadata"] as Record<string, unknown>) ?? {},
          created_at: now,
          messages: [],
          observations: [],
          reflections: [],
        };
        this.conversations.set(conversation.id, conversation);
        return {
          id: conversation.id,
          user_id: conversation.user_id,
          metadata: conversation.metadata,
          created_at: conversation.created_at,
        };
      }

      case "list_conversations":
        return [...this.conversations.values()]
          .filter((c) => params["userId"] === undefined || c.user_id === params["userId"])
          .map((c) => ({
            id: c.id,
            user_id: c.user_id,
            metadata: c.metadata,
            created_at: c.created_at,
            message_count: c.messages.length,
          }));

      case "get_conversation_metadata": {
        const conversation = this.conversation(params);
        return {
          id: conversation.id,
          user_id: conversation.user_id,
          metadata: conversation.metadata,
          created_at: conversation.created_at,
          message_count: conversation.messages.length,
        };
      }

      case "add_message": {
        const conversation = this.conversation(params);
        const message: StoredMessage = {
          id: this.nextId("msg"),
          role: String(params["role"]),
          content: String(params["content"]),
          created_at: now,
          metadata: params["metadata"] as Record<string, unknown> | undefined,
        };
        conversation.messages.push(message);
        return message;
      }

      case "bulk_add_messages": {
        const conversation = this.conversation(params);
        const inbound = (params["messages"] ?? []) as Array<{
          role: string;
          content: string;
          metadata?: Record<string, unknown>;
        }>;
        return inbound.map((entry) => {
          const message: StoredMessage = {
            id: this.nextId("msg"),
            role: entry.role,
            content: entry.content,
            created_at: now,
            metadata: entry.metadata,
          };
          conversation.messages.push(message);
          return message;
        });
      }

      case "get_conversation": {
        const conversation = this.conversation(params);
        return {
          id: conversation.id,
          session_id: conversation.id,
          messages: conversation.messages,
          created_at: conversation.created_at,
        };
      }

      case "get_context": {
        const conversation = this.conversation(params);
        return {
          reflections: conversation.reflections.map((content, index) => ({
            id: `refl-${conversation.id}-${index}`,
            conversation_id: conversation.id,
            content,
            created_at: now,
          })),
          observations: conversation.observations.map((content, index) => ({
            id: `obs-${conversation.id}-${index}`,
            conversation_id: conversation.id,
            content,
            created_at: now,
          })),
          recent_messages: conversation.messages,
        };
      }

      case "add_preference": {
        const preference: StoredPreference = {
          id: this.nextId("pref"),
          category: String(params["category"]),
          preference: String(params["preference"]),
          context: params["context"] === undefined ? undefined : String(params["context"]),
        };
        this.preferences.push(preference);
        return preference;
      }

      // Workspace-wide, like the real service: the caller filters.
      case "search_preferences":
        return this.preferences.slice(0, Number(params["limit"] ?? 10));

      case "add_entity": {
        const name = String(params["name"]);
        const existing = this.entities.find((entity) => entity.name === name);
        if (existing !== undefined) return existing;
        const entity: StoredEntity = {
          id: this.nextId("ent"),
          name,
          type: String(params["entity_type"] ?? params["type"]),
          description:
            params["description"] === undefined ? undefined : String(params["description"]),
        };
        this.entities.push(entity);
        return entity;
      }

      case "search_entities":
        return this.entities.slice(0, Number(params["limit"] ?? 10));

      case "add_fact": {
        const fact: StoredFact = {
          id: this.nextId("fact"),
          subject: String(params["subject"]),
          predicate: String(params["predicate"]),
          object: String(params["obj"]),
        };
        this.facts.push(fact);
        return fact;
      }

      case "record_step": {
        const step: StoredStep = {
          id: this.nextId("step"),
          conversation_id: String(params["conversation_id"]),
          reasoning: String(params["reasoning"]),
          action_taken: String(params["action_taken"]),
          result: params["result"] === undefined ? undefined : String(params["result"]),
          created_at: now,
        };
        this.steps.push(step);
        return step;
      }

      case "list_steps":
        return this.steps.filter((step) => step.conversation_id === params["conversation_id"]);

      default:
        throw new Error(`FakeNamsTransport: unimplemented method "${method}"`);
    }
  }
}
