/**
 * The reusable half of this example: a LangChain v1 middleware and a tool that
 * put a NAMS graph behind a `createAgent()` agent.
 *
 * Two things happen here, and they are the only glue an app needs:
 *
 * 1. `namsMemoryMiddleware()` — `wrapModelCall` prepends the conversation's
 *    three-tier context (reflections, observations, recent messages) plus any
 *    recalled preferences to the system message, and `afterAgent` writes the
 *    turn's messages back to the graph through `Neo4jChatMessageHistory`.
 *    The agent therefore needs no checkpointer: the graph *is* the store.
 * 2. `createEntityLookupTool()` — wraps `Neo4jEntityRetriever` as a tool so the
 *    model can query entities NAMS extracted from past conversations.
 *
 * `toLangChainMessage` / `fromLangChainMessage` / `toLangChainDocument` are the
 * whole adapter contract: the SDK classes speak plain `{ type, content }` and
 * `{ pageContent, metadata }` objects, and these three functions lift them into
 * `@langchain/core` classes. See the README for the SDK issue tracking making
 * the adapters return those classes directly.
 */

import type { MemoryClient } from "@neo4j-labs/agent-memory";
import type {
  Neo4jChatMessageHistory,
  Neo4jEntityRetriever,
} from "@neo4j-labs/agent-memory/integrations/langchain";
import {
  AIMessage,
  type BaseMessage,
  Document,
  HumanMessage,
  SystemMessage,
  createMiddleware,
  tool,
} from "langchain";
import { z } from "zod";

/** The message shape `Neo4jChatMessageHistory` reads and writes. */
export interface AdapterMessage {
  type: "human" | "ai" | "system";
  content: string;
}

/** The document shape `Neo4jEntityRetriever` returns. */
export interface AdapterDocument {
  pageContent: string;
  metadata: Record<string, unknown>;
}

/** Lift a stored message into a `@langchain/core` message instance. */
export function toLangChainMessage(message: AdapterMessage): BaseMessage {
  switch (message.type) {
    case "ai":
      return new AIMessage(message.content);
    case "system":
      return new SystemMessage(message.content);
    default:
      return new HumanMessage(message.content);
  }
}

/**
 * Reduce a LangChain message to the shape the history adapter stores.
 *
 * Returns `null` for messages that should not be persisted: tool results,
 * assistant turns that only request a tool call, empty messages, and injected
 * system messages (the graph is the source of those, not their sink).
 */
export function fromLangChainMessage(message: BaseMessage): AdapterMessage | null {
  const content = message.text.trim();
  if (content === "") return null;
  if (AIMessage.isInstance(message)) {
    if ((message.tool_calls?.length ?? 0) > 0) return null;
    return { type: "ai", content };
  }
  if (message.getType() === "human") return { type: "human", content };
  return null;
}

/** Lift a retriever result into a `@langchain/core` `Document`. */
export function toLangChainDocument(doc: AdapterDocument): Document {
  return new Document({ pageContent: doc.pageContent, metadata: doc.metadata });
}

/**
 * Name of an entity-shaped document. `metadata.name` is not populated by the
 * SDK adapter today (see README), so fall back to the canonical name and then
 * to the leading segment of `pageContent` (`"<name> — <description>"`).
 */
export function entityName(doc: AdapterDocument | Document): string {
  const metadata = doc.metadata as { name?: unknown; canonicalName?: unknown };
  for (const candidate of [metadata.name, metadata.canonicalName]) {
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate;
  }
  return doc.pageContent.split(" — ")[0] ?? doc.pageContent;
}

export interface NamsMemoryOptions {
  client: MemoryClient;
  conversationId: string;
  /** History adapter bound to `conversationId`. */
  history: Neo4jChatMessageHistory;
  /** How many recalled preferences to inject. Set to 0 to skip the lookup. */
  preferenceLimit?: number;
  /** Called with each line of memory bookkeeping; defaults to no output. */
  log?: (line: string) => void;
}

/**
 * Build the memory block injected into the system message.
 *
 * Exported so tests and the demo script can show exactly what the model sees.
 */
export async function buildMemoryBlock(
  options: NamsMemoryOptions,
  query: string,
): Promise<string> {
  const { client, conversationId, preferenceLimit = 3 } = options;
  const context = await client.shortTerm.getContext(conversationId);
  const preferences =
    preferenceLimit > 0 && query.trim() !== ""
      ? await client.longTerm.searchPreferences(query, { limit: preferenceLimit })
      : [];

  const sections: string[] = [];
  if (context.reflections.length > 0) {
    sections.push(
      `Reflections:\n${context.reflections.map((r) => `- ${r.content}`).join("\n")}`,
    );
  }
  if (context.observations.length > 0) {
    sections.push(
      `Observations:\n${context.observations.map((o) => `- ${o.content}`).join("\n")}`,
    );
  }
  if (context.recentMessages.length > 0) {
    sections.push(
      `Earlier in this conversation:\n${context.recentMessages
        .map((m) => `- ${m.role}: ${m.content}`)
        .join("\n")}`,
    );
  }
  if (preferences.length > 0) {
    sections.push(
      `Known user preferences:\n${preferences
        .map((p) => `- [${p.category}] ${p.preference}`)
        .join("\n")}`,
    );
  }
  if (sections.length === 0) return "";
  return ["## Memory (Neo4j Agent Memory Service)", ...sections].join("\n\n");
}

/**
 * LangChain v1 middleware that reads from and writes to NAMS.
 *
 * `wrapModelCall` injects graph memory into the system message; `afterAgent`
 * persists the turn. Both are no-ops on failure paths you care about only in
 * production, so the demo keeps them deliberately small.
 */
export function namsMemoryMiddleware(options: NamsMemoryOptions) {
  const log = options.log ?? (() => {});
  const persisted = new Set<string>();
  let autoKey = 0;

  return createMiddleware({
    name: "NamsMemoryMiddleware",

    wrapModelCall: async (request, handler) => {
      const lastHuman = [...request.messages].reverse().find((m) => m.getType() === "human");
      const block = await buildMemoryBlock(options, lastHuman?.text ?? "");
      if (block === "") {
        log("memory: nothing recalled yet (first turn)");
        return handler(request);
      }
      log(`memory: injected ${block.split("\n").length} lines of graph context`);
      const base = request.systemMessage.text.trim();
      return handler({
        ...request,
        systemMessage: new SystemMessage(base === "" ? block : `${base}\n\n${block}`),
      });
    },

    afterAgent: async (state) => {
      for (const message of state.messages) {
        const key = message.id ?? `auto-${(autoKey += 1)}`;
        if (persisted.has(key)) continue;
        const stored = fromLangChainMessage(message);
        if (stored === null) continue;
        await options.history.addMessage(stored);
        persisted.add(key);
        log(`memory: persisted ${stored.type} message (${stored.content.length} chars)`);
      }
    },
  });
}

/**
 * Expose `Neo4jEntityRetriever` as an agent tool so the model can look up
 * entities NAMS extracted from earlier conversations.
 */
export function createEntityLookupTool(
  retriever: Neo4jEntityRetriever,
  log: (line: string) => void = () => {},
) {
  return tool(
    async ({ query }) => {
      const docs = (await retriever.invoke(query)).map(toLangChainDocument);
      log(`tool search_memory_entities(${JSON.stringify(query)}) -> ${docs.length} entities`);
      if (docs.length === 0) return "No entities in memory match that query.";
      return docs
        .map((d) => `- ${entityName(d)} (${String(d.metadata.type)}): ${d.pageContent}`)
        .join("\n");
    },
    {
      name: "search_memory_entities",
      description:
        "Search the long-term memory graph for entities (people, organizations, " +
        "concepts, places) the user has discussed before. Use it before claiming " +
        "you do not know something about the user.",
      schema: z.object({
        query: z.string().describe("What to look for, e.g. 'graph database'"),
      }),
    },
  );
}
