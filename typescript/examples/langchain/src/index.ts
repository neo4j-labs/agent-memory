/**
 * LangChain JS (v1) + Neo4j Agent Memory Service.
 *
 * A real `createAgent()` agent whose entire memory lives in a NAMS graph:
 * no checkpointer, no in-process message buffer. Three acts:
 *
 *   1. Two agent turns. The second turn only receives the new user message —
 *      everything it knows about the first turn comes back out of the graph
 *      through `namsMemoryMiddleware`.
 *   2. Entity recall. NAMS extracts entities from the persisted messages in the
 *      background; the script awaits that with `waitForExtraction()` and then
 *      queries them through `Neo4jEntityRetriever`.
 *   3. The graph, not the buffer: three-tier context counts, a preference
 *      recalled across conversations, and one `expandGraph()` hop.
 *
 * Run: `cp .env.example .env && npm install && npm start`
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import {
  Neo4jChatMessageHistory,
  Neo4jEntityRetriever,
} from "@neo4j-labs/agent-memory/integrations/langchain";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { pathToFileURL } from "node:url";
import { HumanMessage, createAgent } from "langchain";
import {
  createEntityLookupTool,
  entityName,
  namsMemoryMiddleware,
  toLangChainMessage,
} from "./nams-memory.js";

const SYSTEM_PROMPT =
  "You are a concise assistant with a long memory. When the user asks about " +
  "something they mentioned before, search your memory instead of guessing.";

const TURNS = [
  "I'm evaluating graph databases for a recommendation engine. Neo4j is top of my list.",
  "Which of the things I mentioned should I benchmark first?",
];

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Defaults to `ChatOpenAI` with `OPENAI_MODEL`. Tests pass a fake model. */
  model?: BaseChatModel;
  /** Defaults to `console.log`. */
  log?: (line: string) => void;
  /** How long to wait for background entity extraction. */
  extractionTimeoutMs?: number;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  conversationId: string;
  answers: string[];
  storedMessages: number;
  entityNames: string[];
  extractionReady: boolean;
}

async function resolveModel(model?: BaseChatModel): Promise<BaseChatModel> {
  if (model) return model;
  // Imported lazily so the fake-model path in tests needs no OpenAI package.
  const { ChatOpenAI } = await import("@langchain/openai");
  return new ChatOpenAI({ model: process.env.OPENAI_MODEL ?? "gpt-5-mini" });
}

export async function main(options: RunOptions = {}): Promise<RunResult> {
  const log = options.log ?? ((line: string) => console.log(line));
  // Fail fast and by name, rather than as a transport 401 ten lines later.
  if (!options.client && !process.env.MEMORY_API_KEY) {
    throw new Error(
      "Set MEMORY_API_KEY (see .env.example). Get a key at https://memory.neo4jlabs.com",
    );
  }
  if (!options.model && !process.env.OPENAI_API_KEY) {
    throw new Error("Set OPENAI_API_KEY (see .env.example), or pass a model to main().");
  }
  const memory = options.client ?? new MemoryClient();
  const closeClient = options.closeClient ?? options.client === undefined;

  try {
    const conversation = await memory.shortTerm.createConversation({
      userId: process.env.DEMO_USER_ID ?? "langchain-demo",
    });
    log(`conversation: ${conversation.id}`);

    // A preference written once is recalled in every later conversation — this
    // is the cross-session half of long-term memory.
    await memory.longTerm.addPreference(
      "database",
      "Prefers graph databases for relationship-heavy workloads",
      { context: "Stated while evaluating a recommendation engine" },
    );

    const history = new Neo4jChatMessageHistory(memory, conversation.id);
    const retriever = new Neo4jEntityRetriever(memory, { topK: 5 });

    const agent = createAgent({
      model: await resolveModel(options.model),
      systemPrompt: SYSTEM_PROMPT,
      tools: [createEntityLookupTool(retriever, log)],
      middleware: [
        namsMemoryMiddleware({ client: memory, conversationId: conversation.id, history, log }),
      ],
    });

    // --- Act 1: two turns, no checkpointer ---------------------------------
    const answers: string[] = [];
    for (const turn of TURNS) {
      log(`\nuser> ${turn}`);
      // Only the new message goes in. Prior turns come from the graph.
      const result = await agent.invoke({ messages: [new HumanMessage(turn)] });
      const answer = result.messages.at(-1)?.text ?? "";
      answers.push(answer);
      log(`agent> ${answer}`);
    }

    const stored = await history.getMessages();
    log(`\nNAMS holds ${stored.length} messages for this conversation:`);
    for (const message of stored) {
      const lc = toLangChainMessage(message);
      log(`  [${lc.getType()}] ${lc.text.slice(0, 72)}`);
    }

    // --- Act 2: entities extracted from those very messages ----------------
    log("\nWaiting for background entity extraction ...");
    const extractionReady = await memory.longTerm.waitForExtraction({
      query: "graph database",
      expectedNames: ["Neo4j"],
      timeoutMs: options.extractionTimeoutMs ?? 20_000,
    });
    if (!extractionReady) {
      log("  extraction did not finish in time — entity recall below may be empty");
    }

    const docs = await retriever.invoke("graph database");
    log(`Retriever returned ${docs.length} entities from the graph:`);
    const entityNames: string[] = [];
    for (const doc of docs) {
      entityNames.push(entityName(doc));
      log(`  ${entityName(doc)} (${String(doc.metadata.type)}) id=${String(doc.metadata.id)}`);
    }

    // --- Act 3: the graph, not the buffer ---------------------------------
    const context = await memory.shortTerm.getContext(conversation.id);
    log(
      `\nThree-tier context: ${context.reflections.length} reflections, ` +
        `${context.observations.length} observations, ` +
        `${context.recentMessages.length} recent messages`,
    );

    const preferences = await memory.longTerm.searchPreferences("database choice", { limit: 3 });
    log(`Preferences recalled across conversations: ${preferences.length}`);
    for (const preference of preferences) {
      log(`  [${preference.category}] ${preference.preference}`);
    }

    const firstId = docs[0]?.metadata.id;
    if (typeof firstId === "string" && firstId !== "") {
      const hop = await memory.longTerm.expandGraph(firstId);
      log(
        `One hop from ${entityName(docs[0]!)}: nodes=${hop.nodes.length} edges=${hop.edges.length}`,
      );
    }

    return {
      conversationId: conversation.id,
      answers,
      storedMessages: stored.length,
      entityNames,
      extractionReady,
    };
  } finally {
    if (closeClient) await memory.close();
  }
}

// Only run when executed directly, so tests can import `main`.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err: unknown) => {
    console.error(err);
    process.exit(1);
  });
}
