/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service.
 *
 * `MemoryClient` accepts any `Transport`, so both example scripts run in CI with
 * no API key and no network. The fake keeps conversations, reasoning steps and
 * tool calls per conversation, which is what makes the assertions meaningful:
 * the transcript, the trace and the injected context all have to come back out
 * of this store rather than out of the agent's process memory.
 *
 * Entity extraction is modelled the way NAMS behaves — asynchronously. Entities
 * only appear from the second `search_entities` call onward, so a test fails if
 * `waitForExtraction()` stops being awaited.
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

interface StoredStep {
  id: string;
  conversation_id: string;
  reasoning: string;
  action_taken: string;
  result?: string;
  created_at: string;
}

interface StoredToolCall {
  id: string;
  step_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  status: string;
}

export interface FakeNamsOptions {
  /** Seed conversations so a run can resume one. */
  conversations?: Array<{ id: string; userId: string; metadata?: Record<string, unknown> }>;
  /**
   * Entities `search_entities` returns once extraction has "caught up".
   * Defaults to one Neo4j concept.
   */
  entities?: Array<{ name: string; type: string }>;
  /** Searches to answer with an empty list before entities appear. Default 1. */
  extractionDelayPolls?: number;
}

export class FakeNamsTransport implements Transport {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  readonly conversations = new Map<string, StoredConversation>();
  readonly steps: StoredStep[] = [];
  readonly toolCalls: StoredToolCall[] = [];
  connected = false;
  closed = false;

  private counter = 0;
  private entitySearches = 0;
  private readonly entities: Array<{ name: string; type: string }>;
  private readonly extractionDelayPolls: number;

  constructor(options: FakeNamsOptions = {}) {
    this.entities = options.entities ?? [
      { name: "Neo4j", type: "concept" },
      { name: "recommendation engine", type: "concept" },
    ];
    this.extractionDelayPolls = options.extractionDelayPolls ?? 1;
    for (const seed of options.conversations ?? []) {
      this.conversations.set(seed.id, {
        id: seed.id,
        user_id: seed.userId,
        metadata: seed.metadata ?? {},
        messages: [],
      });
    }
  }

  /** Real (non-synthetic) message contents for one conversation. */
  messagesFor(conversationId: string): StoredMessage[] {
    return this.conversations.get(conversationId)?.messages ?? [];
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async close(): Promise<void> {
    this.closed = true;
  }

  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    this.calls.push({ method, params });
    return this.handle(method, params) as T;
  }

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

      case "list_conversations":
        return [...this.conversations.values()]
          .filter((c) => params.userId === undefined || c.user_id === params.userId)
          .slice(0, typeof params.limit === "number" ? params.limit : undefined)
          .map((c) => ({ id: c.id, user_id: c.user_id, metadata: c.metadata, created_at: now }));

      case "get_conversation_metadata": {
        const conv = this.conversation(params);
        return { id: conv.id, user_id: conv.user_id, metadata: conv.metadata, created_at: now };
      }

      case "get_conversation": {
        const conv = this.conversation(params);
        return { id: conv.id, session_id: conv.id, messages: conv.messages, created_at: now };
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

      case "bulk_add_messages": {
        const conv = this.conversation(params);
        const incoming = (params.messages ?? []) as Array<{ role: string; content: string }>;
        return incoming.map((m) => {
          const message: StoredMessage = {
            id: this.nextId("msg"),
            role: m.role,
            content: m.content,
            created_at: now,
          };
          conv.messages.push(message);
          return message;
        });
      }

      case "get_context": {
        const conv = this.conversation(params);
        // NAMS derives reflections and observations from the messages it already
        // holds, so a resumed conversation has context to inject and a brand-new
        // one does not.
        return {
          reflections:
            conv.messages.length >= 6
              ? [
                  {
                    id: "refl-1",
                    conversation_id: conv.id,
                    content: "User is evaluating Neo4j for a recommendation engine.",
                    created_at: now,
                  },
                ]
              : [],
          observations:
            conv.messages.length >= 2
              ? [
                  {
                    id: "obs-1",
                    conversation_id: conv.id,
                    content: "User mentioned graph databases.",
                    created_at: now,
                  },
                ]
              : [],
          recent_messages: conv.messages.slice(-6),
        };
      }

      case "record_step": {
        const step: StoredStep = {
          id: this.nextId("step"),
          conversation_id: String(params.conversation_id),
          reasoning: String(params.reasoning),
          action_taken: String(params.action_taken),
          result: params.result === undefined ? undefined : String(params.result),
          created_at: now,
        };
        this.steps.push(step);
        return step;
      }

      case "record_tool_call": {
        const call: StoredToolCall = {
          id: this.nextId("tool"),
          step_id: String(params.step_id),
          tool_name: String(params.tool_name),
          arguments: (params.arguments as Record<string, unknown>) ?? {},
          result: params.result,
          status: String(params.status ?? "success"),
        };
        this.toolCalls.push(call);
        return call;
      }

      case "get_trace_by_conversation": {
        const conversationId = String(params.conversation_id);
        const steps = this.steps.filter((s) => s.conversation_id === conversationId);
        const stepIds = new Set(steps.map((s) => s.id));
        return {
          conversation_id: conversationId,
          steps,
          tool_calls: this.toolCalls.filter((c) => stepIds.has(c.step_id)),
        };
      }

      case "search_entities": {
        this.entitySearches += 1;
        if (this.entitySearches <= this.extractionDelayPolls) return [];
        const limit = typeof params.limit === "number" ? params.limit : 10;
        return this.entities.slice(0, limit).map((entity, index) => ({
          id: `entity-${index + 1}`,
          name: entity.name,
          type: entity.type,
          created_at: now,
        }));
      }

      case "delete_conversation": {
        const conv = this.conversation(params);
        this.conversations.delete(conv.id);
        return null;
      }

      default:
        throw new Error(`FakeNamsTransport: unhandled method "${method}"`);
    }
  }
}
