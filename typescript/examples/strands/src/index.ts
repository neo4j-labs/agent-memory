/**
 * AWS Strands + Neo4j Agent Memory Service — transcript persistence, context
 * injection and reasoning capture on one agent.
 *
 * Three integration surfaces, wired by the single `connectMemoryToAgent`
 * factory:
 *
 *   1. `Neo4jSessionStorage`       — Strands' session snapshots persist into a
 *                                    NAMS conversation, so the transcript
 *                                    survives the process.
 *   2. `Neo4jConversationManager`  — three-tier context (reflections +
 *                                    observations) is prepended before every
 *                                    model call.
 *   3. `registerReasoningHooks`    — each invocation and each tool call lands in
 *                                    the graph as a reasoning step / tool call.
 *
 * The script resumes the conversation it created last time instead of starting a
 * fresh one, then reads back what the graph now holds: the stored transcript,
 * the reasoning trace (with its `lookup_fact` tool calls), the context the
 * manager injected, and the entities NAMS extracted server-side.
 *
 * Run: `cp .env.example .env && npm install && npm start`
 * Long-term recall through Strands' `MemoryStore` lives in `memory-store.ts`.
 */

import { Agent, tool, type ContentBlock } from "@strands-agents/sdk";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import {
  connectMemoryToAgent,
  isSyntheticStrandsMessage,
} from "@neo4j-labs/agent-memory/integrations/strands";
import { pathToFileURL } from "node:url";
import { z } from "zod";
import { ensureSnapshotSafeMessages } from "./snapshot-compat.js";

const SYSTEM_PROMPT =
  "You are a helpful assistant who explains things concisely. Call the " +
  "lookup_fact tool when the user asks you to look something up, and refer " +
  "back to what the user already told you instead of asking again.";

/** Marks the conversations this script owns, so a re-run resumes its own. */
const CONVERSATION_SOURCE = "strands-example";

const TURNS = [
  "I'm evaluating graph databases for a recommendation engine. Neo4j is top of my list.",
  "Use the lookup_fact tool to find one more thing about Neo4j.",
  "Summarize what we've discussed so far.",
];

/** Whatever `new Agent({ model })` accepts — a provider instance or a Bedrock id. */
type AgentModel = NonNullable<NonNullable<ConstructorParameters<typeof Agent>[0]>["model"]>;

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Defaults to the provider named by `MODEL_PROVIDER`. Tests pass a stub model. */
  model?: AgentModel;
  /** Defaults to `console.log`. */
  log?: (line: string) => void;
  /** Conversation to resume. Defaults to `CONVERSATION_ID`, then this example's newest. */
  conversationId?: string;
  /** Defaults to `DEMO_USER_ID`. */
  userId?: string;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  conversationId: string;
  /** True when this run continued an existing conversation. */
  resumed: boolean;
  answers: string[];
  /** Real conversation messages NAMS holds (synthetic state markers excluded). */
  storedMessages: number;
  /** Synthetic Strands state markers on the same conversation. */
  syntheticMessages: number;
  /** Reasoning captured by the hooks during this run. */
  trace: { steps: number; toolCalls: number; toolNames: string[] };
  /** Three-tier context available for injection after the run. */
  context: { reflections: number; observations: number; recentMessages: number };
  /** Entities NAMS extracted from the transcript. */
  entities: string[];
}

type ModelProvider = "openai" | "bedrock";

function modelProvider(): ModelProvider {
  const raw = (process.env.MODEL_PROVIDER ?? "openai").toLowerCase();
  if (raw !== "openai" && raw !== "bedrock") {
    throw new Error(`MODEL_PROVIDER must be "openai" or "bedrock" (got "${raw}")`);
  }
  return raw;
}

/**
 * Both providers are resolved here so CI type-checks the Bedrock path too — it
 * is Strands' first-class pairing, and a README snippet is not type-checked.
 * The imports are dynamic so the OpenAI path never loads the AWS SDK.
 */
async function resolveModel(provider: ModelProvider): Promise<AgentModel> {
  if (provider === "bedrock") {
    // `@aws-sdk/client-bedrock-runtime` ships as a dependency of the Strands
    // SDK, so there is nothing extra to install; credentials come from the
    // usual AWS chain.
    const { BedrockModel } = await import("@strands-agents/sdk/models/bedrock");
    return new BedrockModel({
      modelId: process.env.BEDROCK_MODEL_ID ?? "global.anthropic.claude-sonnet-4-6",
    });
  }
  const { OpenAIModel } = await import("@strands-agents/sdk/models/openai");
  return new OpenAIModel({ modelId: process.env.OPENAI_MODEL ?? "gpt-5-mini" });
}

/** Fail by name, up front, rather than as a provider 401 three turns in. */
function requireEnv(keys: string[]): void {
  for (const key of keys) {
    if (!process.env[key]) throw new Error(`Set ${key} — see .env.example`);
  }
}

/** A toy tool, included so the captured reasoning trace has tool calls in it. */
const lookupTool = tool({
  name: "lookup_fact",
  description: "Look up a fact about a topic.",
  inputSchema: z.object({ topic: z.string().describe("Topic to look up") }),
  callback: ({ topic }) => `Fact about ${topic}: graphs model relationships natively.`,
});

export async function main(options: RunOptions = {}): Promise<RunResult> {
  const log = options.log ?? ((line: string) => console.log(line));
  const provider = modelProvider();
  if (!options.client) requireEnv(["MEMORY_API_KEY"]);
  if (!options.model) {
    requireEnv(provider === "bedrock" ? ["AWS_REGION"] : ["OPENAI_API_KEY"]);
  }

  const memory = options.client ?? new MemoryClient();
  const closeClient = options.closeClient ?? options.client === undefined;
  const userId = options.userId ?? process.env.DEMO_USER_ID ?? "strands-demo-user";

  try {
    // 1. Resume the conversation this example wrote last time. Session storage
    //    and context injection are both conversation-scoped, so recall across
    //    runs needs the same conversation id — a fresh one every run would make
    //    "re-run to see recall" impossible.
    //    The scan is filtered on the metadata this script writes, so it never
    //    picks up a conversation another script owns — `memory-store.ts` creates
    //    its own sink conversation under the same user id.
    const requested = options.conversationId ?? process.env.CONVERSATION_ID;
    const existingId =
      requested ??
      (await memory.shortTerm.listConversations({ userId, limit: 25 })).find(
        (candidate) => candidate.metadata?.source === CONVERSATION_SOURCE,
      )?.id;
    const conv = existingId
      ? await memory.shortTerm.getConversationMetadata(existingId)
      : await memory.shortTerm.createConversation({
          userId,
          metadata: { source: CONVERSATION_SOURCE },
        });
    const resumed = existingId !== undefined;
    log(
      `${resumed ? "Resumed" : "Created"} conversation ${conv.id} for ${redactIdentifier(userId)}`,
    );

    // 2. One factory call returns both halves of the Agent config. No casts:
    //    the example resolves a single copy of @strands-agents/sdk (see
    //    .npmrc), so the SDK's nominal classes are the ones the Agent expects.
    const wiring = await connectMemoryToAgent(memory, { conversationId: conv.id });

    const agent = new Agent({
      systemPrompt: SYSTEM_PROMPT,
      model: options.model ?? (await resolveModel(provider)),
      tools: [lookupTool],
      // This script prints the turns itself.
      printer: false,
      ...wiring,
    });

    // Works around an SDK/Strands-1.17 collision between context injection and
    // session snapshots. Delete the call and the module once the SDK fix lands —
    // see src/snapshot-compat.ts.
    await ensureSnapshotSafeMessages(agent);

    // 3. Drive the dialogue. Every turn writes a snapshot through
    //    Neo4jSessionStorage and a reasoning step through the hooks.
    const answers: string[] = [];
    for (const userMessage of TURNS) {
      log(`\nuser> ${userMessage}`);
      let text: string;
      try {
        const result = await agent.invoke(userMessage);
        text = flattenText(result.lastMessage.content) || "(no text response)";
      } catch (error) {
        throw new Error(
          `The ${provider} model call failed: ${describe(error)}. ` +
            (provider === "bedrock"
              ? "Check AWS_REGION, your AWS credentials and that BEDROCK_MODEL_ID is enabled in that region."
              : "Check OPENAI_API_KEY and OPENAI_MODEL, or set MODEL_PROVIDER=bedrock to use Amazon Bedrock instead."),
          { cause: error },
        );
      }
      log(`agent> ${text}`);
      answers.push(text);
    }

    // 4. Read back what the graph now holds — the payoff, fetched rather than
    //    described. None of this is kept in process memory.
    const stored = await memory.shortTerm.getConversation(conv.id);
    const real = stored.messages.filter((message) => !isSyntheticStrandsMessage(message));
    const synthetic = stored.messages.length - real.length;
    log(
      `\nNAMS holds ${real.length} conversation message(s) for this conversation ` +
        `(+${synthetic} synthetic Strands state marker(s), filtered with ` +
        `isSyntheticStrandsMessage):`,
    );
    for (const message of real.slice(-6)) {
      log(`  [${message.role}] ${message.content.slice(0, 72)}`);
    }

    const trace = await memory.reasoning.getTraceByConversation(conv.id);
    log(
      `\nReasoning trace: ${trace.steps.length} step(s), ` +
        `${trace.toolCalls.length} tool call(s)`,
    );
    for (const step of trace.steps.slice(-6)) {
      log(`  [step] ${step.reasoning} -> ${step.actionTaken}`);
    }
    for (const call of trace.toolCalls.slice(-6)) {
      log(`  [tool] ${call.toolName}(${JSON.stringify(call.arguments)}) ${call.status}`);
    }

    const context = await memory.shortTerm.getContext(conv.id);
    log(
      `\nThree-tier context injected before each model call: ` +
        `${context.reflections.length} reflections, ` +
        `${context.observations.length} observations, ` +
        `${context.recentMessages.length} recent messages`,
    );

    // Extraction runs server-side after the write, so poll instead of sleeping.
    const caughtUp = await memory.longTerm.waitForExtraction({
      query: "Neo4j",
      timeoutMs: 20_000,
    });
    if (!caughtUp) log("Extraction has not caught up yet — entity search may be empty.");
    const entities = await memory.longTerm.searchEntities("graph database", { limit: 5 });
    log(`Entities extracted from the transcript: ${entities.length}`);
    for (const entity of entities) {
      log(`  ${entity.name} (${entity.type})`);
    }

    log(
      `\nRe-run with CONVERSATION_ID=${conv.id} (or the same ` +
        `DEMO_USER_ID=${userId}) to see the agent recall this run.`,
    );

    return {
      conversationId: conv.id,
      resumed,
      answers,
      storedMessages: real.length,
      syntheticMessages: synthetic,
      trace: {
        steps: trace.steps.length,
        toolCalls: trace.toolCalls.length,
        toolNames: trace.toolCalls.map((call) => call.toolName),
      },
      context: {
        reflections: context.reflections.length,
        observations: context.observations.length,
        recentMessages: context.recentMessages.length,
      },
      entities: entities.map((entity) => entity.name),
    };
  } finally {
    if (closeClient) await memory.close();
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function redactIdentifier(value: string): string {
  if (value.length <= 4) return "****";
  return `${value.slice(0, 2)}****${value.slice(-2)}`;
}

function flattenText(blocks: ContentBlock[]): string {
  return blocks
    .map((block) => ("text" in block && typeof block.text === "string" ? block.text : ""))
    .filter((chunk) => chunk.length > 0)
    .join("");
}

// Only run when executed directly, so tests can import `main`.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error: unknown) => {
    console.error(error);
    process.exit(1);
  });
}
