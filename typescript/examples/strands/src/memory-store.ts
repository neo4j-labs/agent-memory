/**
 * AWS Strands + Neo4j Agent Memory Service — long-term recall via `MemoryStore`.
 *
 * `Neo4jMemoryStore` implements Strands' `MemoryStore`, so `MemoryManager`
 * searches the graph before each model call and folds the hits into the turn.
 * This is the preferred way to give a Strands agent memory that outlives one
 * conversation.
 *
 * Compare `index.ts`, which wires transcript persistence, context injection and
 * reasoning capture instead — the two are independent and combine on one agent.
 *
 * Recall is **entities-only**: the hosted service exposes entity search but no
 * preference or fact search endpoint, so those knobs are absent from the options
 * type rather than silently ignored.
 *
 * Run: `cp .env.example .env && npm install && npm run start:memory-store`
 */

import { Agent, InvocationTrigger, MemoryManager, type ContentBlock } from "@strands-agents/sdk";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jMemoryStore } from "@neo4j-labs/agent-memory/integrations/strands";
import { pathToFileURL } from "node:url";

/** Whatever `new Agent({ model })` accepts — a provider instance or a Bedrock id. */
type AgentModel = NonNullable<NonNullable<ConstructorParameters<typeof Agent>[0]>["model"]>;

const WRITE_TURN = "Remember that I work at Acme Corp on the payments team.";
const RECALL_TURN = "Where do I work?";

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Defaults to the provider named by `MODEL_PROVIDER`. Tests pass a stub model. */
  model?: AgentModel;
  /** Defaults to `console.log`. */
  log?: (line: string) => void;
  /** Defaults to `DEMO_USER_ID`. */
  userId?: string;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  answers: string[];
  /** Whether extraction caught up before the recall turn. */
  extracted: boolean;
  /** Entries the store returned for the recall query. */
  recalled: string[];
}

type ModelProvider = "openai" | "bedrock";

function modelProvider(): ModelProvider {
  const raw = (process.env.MODEL_PROVIDER ?? "openai").toLowerCase();
  if (raw !== "openai" && raw !== "bedrock") {
    throw new Error(`MODEL_PROVIDER must be "openai" or "bedrock" (got "${raw}")`);
  }
  return raw;
}

async function resolveModel(provider: ModelProvider): Promise<AgentModel> {
  if (provider === "bedrock") {
    const { BedrockModel } = await import("@strands-agents/sdk/models/bedrock");
    return new BedrockModel({
      modelId: process.env.BEDROCK_MODEL_ID ?? "global.anthropic.claude-sonnet-4-6",
    });
  }
  const { OpenAIModel } = await import("@strands-agents/sdk/models/openai");
  return new OpenAIModel({ modelId: process.env.OPENAI_MODEL ?? "gpt-5-mini" });
}

function requireEnv(keys: string[]): void {
  for (const key of keys) {
    if (!process.env[key]) throw new Error(`Set ${key} — see .env.example`);
  }
}

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
    // Extraction is off by default and MemoryManager's add_memory tool is
    // opt-in, so a store left at both defaults recalls but never writes.
    // InvocationTrigger() extracts on every turn; `extraction: true` means every
    // fifth. Writes go through addMessages, which the service extracts
    // server-side — no extra model call.
    const store = new Neo4jMemoryStore({
      name: "graph",
      client: memory,
      userId,
      extraction: { trigger: new InvocationTrigger() },
    });

    const agent = new Agent({
      model: options.model ?? (await resolveModel(provider)),
      memoryManager: new MemoryManager({ stores: [store] }),
      printer: false,
    });

    const answers: string[] = [];
    log(`user> ${WRITE_TURN}`);
    const first = await agent.invoke(WRITE_TURN);
    const firstText = flattenText(first.lastMessage.content);
    log(`agent> ${firstText}\n`);
    answers.push(firstText);

    // flush() awaits the write; NAMS then extracts entities in a background
    // pipeline, so the write returns before they are searchable. Without the
    // second await the recall below races that pipeline and normally loses.
    await agent.memoryManager?.flush();
    const extracted = await memory.longTerm.waitForExtraction({
      expectedNames: ["Acme Corp"],
      timeoutMs: 60_000,
    });
    if (!extracted) {
      log("Extraction has not caught up yet — recall may come back empty.\n");
    }

    // What the manager will hand the model on the next turn, fetched directly so
    // the recall is visible rather than implied.
    const recalled = await store.search("where does the user work");
    log(`Store recall for the next turn: ${recalled.length} entr(ies)`);
    for (const entry of recalled) {
      log(`  ${entry.content}`);
    }

    log(`\nuser> ${RECALL_TURN}`);
    const second = await agent.invoke(RECALL_TURN);
    const secondText = flattenText(second.lastMessage.content);
    log(`agent> ${secondText}`);
    answers.push(secondText);

    // The store borrows the client (we passed `client`), so close() leaves it
    // open and the `finally` below owns it.
    await store.close();

    return { answers, extracted, recalled: recalled.map((entry) => entry.content) };
  } finally {
    if (closeClient) await memory.close();
  }
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
    process.stderr.write(`${String(error)}\n`);
    process.exit(1);
  });
}
