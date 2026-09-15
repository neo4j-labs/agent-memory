/**
 * LangChain JS integration — exposes `MemoryClient` as a chat-message history
 * store and as a retriever-shaped object for entity look-up.
 *
 * Implementations are duck-typed against the LangChain JS interfaces so this
 * module has no LangChain dependency at compile time.
 *
 * @example
 * ```ts
 * import { MemoryClient } from "@neo4j-labs/agent-memory";
 * import { Neo4jChatMessageHistory, Neo4jEntityRetriever }
 *   from "@neo4j-labs/agent-memory/integrations/langchain";
 *
 * const client = new MemoryClient({ endpoint: "...", apiKey: "..." });
 * const history = new Neo4jChatMessageHistory(client, "conversation-id");
 * await history.addUserMessage("hello");
 * ```
 */

import type { MemoryClient } from "../client.js";

/** Plain message shape used by this adapter. @inline */
interface LangchainBaseMessage {
  type: "human" | "ai" | "system";
  content: string;
}

const TYPE_TO_ROLE: Record<LangchainBaseMessage["type"], "user" | "assistant" | "system"> = {
  human: "user",
  ai: "assistant",
  system: "system",
};

const ROLE_TO_TYPE: Record<"user" | "assistant" | "system", LangchainBaseMessage["type"]> = {
  user: "human",
  assistant: "ai",
  system: "system",
};

export class Neo4jChatMessageHistory {
  constructor(
    private readonly client: MemoryClient,
    private readonly conversationId: string,
  ) {}

  async getMessages(): Promise<LangchainBaseMessage[]> {
    const conv = await this.client.shortTerm.getConversation(this.conversationId);
    return conv.messages.map((m) => ({
      type: ROLE_TO_TYPE[m.role] ?? "human",
      content: m.content,
    }));
  }

  async addMessage(message: LangchainBaseMessage): Promise<void> {
    await this.client.shortTerm.addMessage(
      this.conversationId,
      TYPE_TO_ROLE[message.type] ?? "user",
      message.content,
    );
  }

  async addUserMessage(content: string): Promise<void> {
    await this.addMessage({ type: "human", content });
  }

  async addAIChatMessage(content: string): Promise<void> {
    await this.addMessage({ type: "ai", content });
  }

  async clear(): Promise<void> {
    await this.client.shortTerm.clearSession(this.conversationId);
  }
}

/** Plain document shape used by this adapter. @inline */
interface LangchainDocument {
  pageContent: string;
  metadata: Record<string, unknown>;
}

/**
 * Retriever-shaped wrapper over `client.longTerm.searchEntities` that returns
 * LangChain-compatible `Document` objects.
 */
export class Neo4jEntityRetriever {
  constructor(
    private readonly client: MemoryClient,
    private readonly options?: { type?: string; topK?: number },
  ) {}

  async invoke(query: string): Promise<LangchainDocument[]> {
    return this.getRelevantDocuments(query);
  }

  async getRelevantDocuments(query: string): Promise<LangchainDocument[]> {
    const entities = await this.client.longTerm.searchEntities(query, {
      type: this.options?.type,
      limit: this.options?.topK ?? 4,
    });
    return entities.map((e) => ({
      pageContent: `${e.name}${e.description ? ` — ${e.description}` : ""}`,
      metadata: {
        id: e.id,
        type: e.type,
        confidence: e.confidence,
        sourceStage: e.sourceStage,
        canonicalName: e.canonicalName,
      },
    }));
  }
}
