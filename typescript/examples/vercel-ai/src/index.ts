/**
 * Vercel AI SDK + Neo4j Agent Memory Service — the minimal middleware wiring.
 *
 * `agentMemoryMiddleware` is an AI SDK specification-v4 language-model
 * middleware (`ai` 7.x). Wrap any model with it and every call gains memory:
 *
 *   - three-tier context (reflections + observations + recent messages) is
 *     prepended to the prompt,
 *   - the user's turn is persisted before generation,
 *   - the assistant's turn is persisted after it — from `generateText` via
 *     `wrapGenerate`, and for streams through an application-owned, awaited write.
 *
 * The script is deliberately small: one wrapped model, three turns (the third
 * streamed), then an epilogue that prints what the graph actually holds —
 * injected context counts, messages recalled from the same conversation, and the
 * entities NAMS extracted in the background. Nothing is kept in process: turn
 * two knows about turn one only because the middleware read it back out of
 * Neo4j.
 *
 * Re-run with the printed `CONVERSATION_ID` to resume the same conversation
 * and watch the assistant pick up where it left off.
 *
 * Run: `cp .env.example .env && npm install && npm start`
 */

import { openai } from "@ai-sdk/openai";
import type { LanguageModelV4 } from "@ai-sdk/provider";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
import { generateText, streamText, wrapLanguageModel } from "ai";
import { pathToFileURL } from "node:url";

/** Marks the conversations this example creates, so a re-run can find them. */
const SOURCE = "vercel-ai-example";

const GENERATED_TURNS = [
  "Hi! I'm building a recommendation engine for board games. I love euro-style games.",
  "What kind of dataset would you use?",
];

/** Streamed, with the application awaiting the assembled assistant write. */
const STREAMED_TURN = "Given what I just told you about my preferences, suggest a starter project.";

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Defaults to `openai(OPENAI_MODEL ?? "gpt-5-mini")`. Tests pass a mock. */
  model?: LanguageModelV4;
  /** Conversation to resume. Defaults to `CONVERSATION_ID`. */
  conversationId?: string;
  /** Owner of the conversation. Defaults to `DEMO_USER_ID`. */
  userId?: string;
  /** Line output. Defaults to `console.log`. */
  log?: (line: string) => void;
  /** Raw (unterminated) output, used for streamed tokens. */
  write?: (chunk: string) => void;
  /** How long to wait for background entity extraction. Default 15s. */
  extractionTimeoutMs?: number;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  conversationId: string;
  /** True when the run continued an existing conversation. */
  resumed: boolean;
  /** Answers to the two `generateText` turns. */
  answers: string[];
  /** Answer to the streamed turn, assembled from its deltas. */
  streamedAnswer: string;
  /** What `shortTerm.getContext` held after the turns — i.e. what gets injected. */
  context: { reflections: number; observations: number; recentMessages: number };
  recalledMessages: string[];
  extractedEntities: string[];
}

function requireEnv(...keys: string[]): void {
  for (const key of keys) {
    if (!process.env[key]) throw new Error(`Set ${key} — see .env.example`);
  }
}

/**
 * Resume the conversation named by `CONVERSATION_ID`, else the newest one this
 * example created for this user, else create one. Always creating would make
 * "re-run to see recall" impossible.
 */
async function resolveConversation(
  client: MemoryClient,
  userId: string,
  requested: string | undefined,
): Promise<{ id: string; resumed: boolean }> {
  if (requested) {
    const conv = await client.shortTerm.getConversationMetadata(requested);
    return { id: conv.id, resumed: true };
  }

  const mine = (await client.shortTerm.listConversations({ userId })).filter(
    (c) => c.metadata?.source === SOURCE,
  );
  mine.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  if (mine.length > 0 && mine[0] !== undefined) return { id: mine[0].id, resumed: true };

  const conv = await client.shortTerm.createConversation({
    userId,
    metadata: { source: SOURCE },
  });
  return { id: conv.id, resumed: false };
}

export async function main(options: RunOptions = {}): Promise<RunResult> {
  if (options.client === undefined) requireEnv("MEMORY_API_KEY");
  if (options.model === undefined) requireEnv("OPENAI_API_KEY");

  const log = options.log ?? ((line: string) => console.log(line));
  const write = options.write ?? ((chunk: string) => void process.stdout.write(chunk));
  const closeClient = options.closeClient ?? options.client === undefined;
  const client = options.client ?? new MemoryClient();

  try {
    const userId = options.userId ?? process.env.DEMO_USER_ID ?? "vercel-ai-demo-user";
    const { id: conversationId, resumed } = await resolveConversation(
      client,
      userId,
      options.conversationId ?? process.env.CONVERSATION_ID,
    );
    log(
      resumed
        ? `Resumed conversation ${conversationId} for the demo user`
        : `Created conversation ${conversationId} for the demo user`,
    );

    // The whole integration: one wrapped model. Everything below is a plain
    // AI SDK call with no memory-specific code.
    const model = wrapLanguageModel({
      model: options.model ?? openai(process.env.OPENAI_MODEL ?? "gpt-5-mini"),
      middleware: agentMemoryMiddleware(client, { conversationId, userId }),
    });

    const answers: string[] = [];
    const turns = resumed
      ? ["What kind of games did I say I enjoy?", "What project were we discussing?"]
      : GENERATED_TURNS;
    for (const prompt of turns) {
      log(`\n[user] ${prompt}`);
      const { text } = await generateText({ model, prompt });
      log(`[assistant] ${text}`);
      answers.push(text);
    }

    // Own the streaming write so closing the CLI cannot race background persistence.
    const streamingModel = wrapLanguageModel({
      model: options.model ?? openai(process.env.OPENAI_MODEL ?? "gpt-5-mini"),
      middleware: agentMemoryMiddleware(client, { conversationId, persistResponses: false }),
    });
    log(`\n[user] ${STREAMED_TURN}`);
    write("[assistant] ");
    let streamedAnswer = "";
    let streamError: unknown;
    const streamed = streamText({
      model: streamingModel, prompt: STREAMED_TURN,
      onError: ({ error }) => { streamError = error; },
    });
    for await (const chunk of streamed.textStream) {
      streamedAnswer += chunk;
      write(chunk);
    }
    if (streamError !== undefined) throw streamError;
    await client.shortTerm.addMessage(conversationId, "assistant", streamedAnswer);
    write("\n");

    // Epilogue: what the graph holds — i.e. what the next call will inject.
    const ctx = await client.shortTerm.getContext(conversationId);
    log(
      `\nInjected on the next call: ${ctx.reflections.length} reflection(s), ` +
        `${ctx.observations.length} observation(s), ${ctx.recentMessages.length} recent message(s)`,
    );

    const recalled = ctx.recentMessages.filter((message) => message.role === "user");
    log(`User messages in this conversation's context: ${recalled.length}`);

    // Entity extraction is asynchronous, so wait for it rather than racing it.
    const extracted = await client.longTerm.waitForExtraction({
      query: "board games",
      expectedNames: ["Euro-style board games"],
      timeoutMs: options.extractionTimeoutMs ?? 15_000,
    });
    const entities = extracted
      ? await client.longTerm.searchEntities("board games", { limit: 5 })
      : [];
    log(`Matching workspace entities: ${entities.length}`);
    for (const e of entities) log(`  ${e.name} (${e.type})`);

    log(`\nRe-run with CONVERSATION_ID=${conversationId} to continue this conversation.`);

    return {
      conversationId,
      resumed,
      answers,
      streamedAnswer,
      context: {
        reflections: ctx.reflections.length,
        observations: ctx.observations.length,
        recentMessages: ctx.recentMessages.length,
      },
      recalledMessages: recalled.map((message) => message.content),
      extractedEntities: entities.map((e) => e.name),
    };
  } finally {
    if (closeClient) await client.close();
  }
}

// Only run when executed directly, so tests can import `main`.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err: unknown) => {
    console.error(err);
    process.exit(1);
  });
}
