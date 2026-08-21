/**
 * AWS Strands + neo4j-agent-memory — long-term recall via MemoryStore.
 *
 * The store feeds the agent loop: MemoryManager searches it before each model
 * call and folds the results into the turn. Compare src/index.ts, which wires
 * transcript persistence and reasoning capture instead — the two are
 * independent and combine on one agent.
 *
 * Needs MEMORY_API_KEY and OPENAI_API_KEY.
 */

import { Agent, MemoryManager, InvocationTrigger } from "@strands-agents/sdk";
import { OpenAIModel } from "@strands-agents/sdk/models/openai";
import type { ContentBlock } from "@strands-agents/sdk";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jMemoryStore } from "@neo4j-labs/agent-memory/integrations/strands";

// The example has its own node_modules, so it and the file-linked client each
// resolve a separate copy of `@strands-agents/sdk`. Two copies means two
// declarations of the same types (ExtractionTrigger, MemoryStore), which
// TypeScript will not treat as interchangeable. See src/index.ts for the same
// workaround.
type StoreOptions = ConstructorParameters<typeof Neo4jMemoryStore>[0];
type ManagerConfig = ConstructorParameters<typeof MemoryManager>[0];

async function main() {
  const memory = new MemoryClient();

  // extraction is off by default and MemoryManager's add_memory tool is
  // opt-in, so a store left at both defaults recalls but never writes.
  // InvocationTrigger() extracts on every turn; `extraction: true` means
  // every fifth. Writes go through addMessages, which the service extracts
  // server-side — no extra model call.
  const store = new Neo4jMemoryStore({
    name: "graph",
    client: memory,
    userId: process.env.DEMO_USER_ID ?? "strands-demo-user",
    extraction: { trigger: new InvocationTrigger() } as unknown as StoreOptions["extraction"],
  });

  const agent = new Agent({
    model: new OpenAIModel({ modelId: "gpt-4o-mini" }),
    memoryManager: new MemoryManager({
      stores: [store as unknown as ManagerConfig["stores"][number]],
    }),
  });

  const first = await agent.invoke("Remember that I work at Acme Corp on the payments team.");
  process.stdout.write(`${flattenText(first.lastMessage.content)}\n\n`);

  // flush() awaits the write; NAMS then extracts entities in a background
  // pipeline, so the write returns before they are searchable. Without the
  // second await the recall below races that pipeline and normally loses.
  await agent.memoryManager?.flush();
  const extracted = await memory.longTerm.waitForExtraction({
    expectedNames: ["Acme Corp"],
    timeoutMs: 60_000,
  });
  if (!extracted) {
    process.stdout.write("Extraction has not caught up yet — recall may come back empty.\n\n");
  }

  const second = await agent.invoke("Where do I work?");
  process.stdout.write(`${flattenText(second.lastMessage.content)}\n`);

  await store.close();
}

main().catch((error: unknown) => {
  process.stderr.write(`${String(error)}\n`);
  process.exit(1);
});

function flattenText(blocks: ContentBlock[]): string {
  return blocks
    .map((block) => ("text" in block && typeof block.text === "string" ? block.text : ""))
    .filter((chunk) => chunk.length > 0)
    .join("");
}
