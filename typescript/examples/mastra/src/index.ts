/**
 * Mastra + Neo4j Agent Memory Service.
 *
 * A real `@mastra/core` `Agent` whose conversation history lives in a NAMS
 * graph instead of a Mastra store. Three acts:
 *
 *   1. Two agent turns. Each turn is invoked with the history read back out of
 *      the graph plus the new user message — there is no in-process buffer and
 *      no Mastra storage adapter in the picture.
 *   2. The thread is a real NAMS conversation: read the title back, ask for
 *      three-tier context, and run a semantic search over its own messages.
 *   3. Cross-thread recall. A second thread for the *same* `resourceId` recalls
 *      a preference stored during the first one — the thing a thread-scoped
 *      store cannot do.
 *
 * `Neo4jMastraMemory` is a thin thread-and-message-history adapter, **not** a
 * Mastra storage adapter: it is never passed to `new Agent({ memory })`. See
 * "What the adapter covers — and what it does not" in the README, and the
 * type-level pins in `test/mastra.test.ts`, for the exact boundary.
 *
 * Run: `cp .env.example .env && npm install && npm start`
 */

import { Agent } from "@mastra/core/agent";
import type { MastraModelConfig } from "@mastra/core/llm";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jMastraMemory } from "@neo4j-labs/agent-memory/integrations/mastra";
import { pathToFileURL } from "node:url";
import { preferenceReminder, toMastraMessages } from "./nams-threads.js";

const INSTRUCTIONS =
  "You help users plan trips. Keep answers to two sentences and refer back to " +
  "what the user already told you instead of asking again.";

const FIRST_THREAD_TURNS = [
  "I'm planning a 7-day trip to Lisbon.",
  "Given what I told you, what should I book first?",
];

/** Asked in a brand-new thread, answerable only from cross-thread memory. */
const SECOND_THREAD_TURN = "Remind me what kind of trip I said I like.";

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Defaults to the model id in `MASTRA_MODEL`. Tests pass a stub model. */
  model?: MastraModelConfig;
  /** Defaults to `console.log`. */
  log?: (line: string) => void;
  /** Delete both threads when done. Defaults to `CLEANUP=1`. */
  cleanup?: boolean;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  threadId: string;
  secondThreadId: string;
  /** Title read back out of the graph by `getThreadById`. */
  threadTitle?: string;
  answers: string[];
  /** Roles of the messages NAMS holds for the first thread, in order. */
  storedRoles: string[];
  /** Messages the semantic search found inside the first thread. */
  searchHits: number;
  /** Preferences recalled in the second thread, written during the first. */
  recalledPreferences: string[];
  /** Threads NAMS lists for this resource (expects both). */
  threadsForResource: number;
  deleted: boolean;
}

function resolveModel(model?: MastraModelConfig): MastraModelConfig {
  if (model) return model;
  // Mastra's model router resolves "<provider>/<model>" against the provider's
  // API key in the environment; nothing is hard-coded to OpenAI.
  return process.env.MASTRA_MODEL ?? "openai/gpt-5-mini";
}

/**
 * Run one agent turn with the thread's stored history in front of it, then
 * persist both sides of the turn back to NAMS.
 */
async function turn(
  agent: Agent,
  memory: Neo4jMastraMemory,
  threadId: string,
  userText: string,
  log: (line: string) => void,
): Promise<string> {
  const history = await memory.getMessages(threadId);
  log(`\nuser> ${userText}`);
  log(`memory: replayed ${history.length} stored message(s) into the turn`);

  const result = await agent.generate([
    ...toMastraMessages(history),
    { role: "user", content: userText },
  ]);
  const answer = result.text;
  log(`agent> ${answer}`);

  // Both sides of the turn become real NAMS messages, so entity extraction,
  // observations and reflections all run over them server-side.
  await memory.saveMessage({ threadId, role: "user", content: userText });
  await memory.saveMessage({ threadId, role: "assistant", content: answer });
  return answer;
}

export async function main(options: RunOptions = {}): Promise<RunResult> {
  const log = options.log ?? ((line: string) => console.log(line));
  // Fail fast and by name, rather than as a transport 401 ten lines later.
  if (!options.client && !process.env.MEMORY_API_KEY) {
    throw new Error(
      "Set MEMORY_API_KEY (see .env.example). Get a key at https://memory.neo4jlabs.com",
    );
  }
  if (!options.model && !process.env.OPENAI_API_KEY && !process.env.MASTRA_MODEL) {
    throw new Error(
      "Set OPENAI_API_KEY (see .env.example), or MASTRA_MODEL for another provider, " +
        "or pass a model to main().",
    );
  }

  const client = options.client ?? new MemoryClient();
  const closeClient = options.closeClient ?? options.client === undefined;
  const cleanup = options.cleanup ?? process.env.CLEANUP === "1";
  const resourceId = process.env.DEMO_USER_ID ?? "mastra-demo-user";

  const memory = new Neo4jMastraMemory(client);
  const agent = new Agent({
    id: "scout",
    name: "scout",
    instructions: INSTRUCTIONS,
    model: resolveModel(options.model),
  });

  try {
    // --- Act 1: two turns, history held by the graph ------------------------
    const thread = await memory.createThread({
      resourceId,
      title: "Lisbon trip planning",
      metadata: { source: "mastra-example" },
    });
    // Thread ids are not printed: on the bridge transport the adapter derives
    // them from the resource id, which comes from the environment.
    log("first thread created for the demo resource");

    const answers: string[] = [];
    for (const text of FIRST_THREAD_TURNS) {
      answers.push(await turn(agent, memory, thread.id, text, log));
    }

    // A preference is resource-scoped, not thread-scoped — this is what the
    // second thread will recall below.
    await client.longTerm.addPreference("travel", "Prefers food and history trips", {
      context: "Stated while planning a 7-day Lisbon trip",
    });

    // --- Act 2: the thread is a real NAMS conversation ----------------------
    const stored = await memory.getMessages(thread.id);
    log(`\nNAMS holds ${stored.length} messages for this thread:`);
    for (const message of stored) {
      log(`  [${message.role}] ${message.content.slice(0, 72)}`);
    }

    const readBack = await memory.getThreadById(thread.id);
    log(`getThreadById -> title ${JSON.stringify(readBack?.title)}`);

    const context = await client.shortTerm.getContext(thread.id);
    log(
      `Three-tier context: ${context.reflections.length} reflections, ` +
        `${context.observations.length} observations, ` +
        `${context.recentMessages.length} recent messages`,
    );

    const hits = await client.shortTerm.searchMessages("where is the trip to", {
      sessionId: thread.id,
      limit: 3,
    });
    log(`Semantic search inside the thread matched ${hits.length} message(s)`);

    // --- Act 3: a second thread for the same resource ----------------------
    const secondThread = await memory.createThread({
      resourceId,
      title: "Follow-up",
    });
    log(`\nsecond thread created (same resource, distinct id: ${secondThread.id !== thread.id})`);

    const recalled = await client.longTerm.searchPreferences("trip style", { limit: 3 });
    log(`Preferences recalled in the new thread: ${recalled.length}`);
    for (const preference of recalled) {
      log(`  [${preference.category}] ${preference.preference}`);
    }

    // The new thread starts empty, so everything the agent can say about the
    // user's taste came from resource-scoped long-term memory.
    const primed = recalled.map((p) => preferenceReminder(p.category, p.preference));
    log(`\nuser> ${SECOND_THREAD_TURN}`);
    const followUp = await agent.generate([
      ...primed,
      { role: "user", content: SECOND_THREAD_TURN },
    ]);
    log(`agent> ${followUp.text}`);
    answers.push(followUp.text);
    await memory.saveMessage({
      threadId: secondThread.id,
      role: "user",
      content: SECOND_THREAD_TURN,
    });
    await memory.saveMessage({
      threadId: secondThread.id,
      role: "assistant",
      content: followUp.text,
    });

    const threads = await client.shortTerm.listConversations({ userId: resourceId });
    log(`NAMS lists ${threads.length} thread(s) for the demo resource`);

    if (cleanup) {
      await memory.deleteThread(thread.id);
      await memory.deleteThread(secondThread.id);
      log("\nCLEANUP=1 — both threads deleted");
    } else {
      log("\nRe-run with CLEANUP=1 to delete both threads on the way out.");
    }

    return {
      threadId: thread.id,
      secondThreadId: secondThread.id,
      ...(readBack?.title !== undefined ? { threadTitle: readBack.title } : {}),
      answers,
      storedRoles: stored.map((m) => m.role),
      searchHits: hits.length,
      recalledPreferences: recalled.map((p) => p.preference),
      threadsForResource: threads.length,
      deleted: cleanup,
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
