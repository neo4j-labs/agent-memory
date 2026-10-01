/**
 * Tools mode: the model decides when to read and write memory, and each
 * memory call shows up as a visible tool call. `enforceQueryMemory()` makes
 * sure `query_memory` runs before the model can give a final answer.
 * `ensureMemoryStored()` persists the turn if the model never called
 * `store_memory` itself. Turn 1 teaches a preference; turn 2 uses a fresh
 * tool set for the same user, so the only way to answer is `query_memory`.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=nams_... OPENAI_API_KEY=sk-... npx tsx src/tools-mode.ts
 *
 * Expected output (arguments, steps and wording vary with the model). Each
 * ensureMemoryStored line is printed by onFinish inside agent.generate(), so
 * it appears above the header of its turn:
 *
 *   ensureMemoryStored: already-stored
 *
 *   --- Turn 1 -- teach it something
 *   step 0 [enforced: some tool required]
 *     tool call: query_memory({"query":"editor preferences","limit":5})
 *   step 1 [unconstrained]
 *     tool call: store_memory({"content":"User uses Neovim and prefers short answers","type":"user_preference","confidence":0.9,"tags":[]})
 *   step 2 [unconstrained]
 *   assistant: Got it -- short answers, and I'll remember you use Neovim.
 *   ensureMemoryStored: persisted the turn
 *
 *   --- Turn 2 -- fresh tool set, same user
 *   step 0 [enforced: some tool required]
 *     tool call: query_memory({"query":"editor preferences","limit":5})
 *   step 1 [unconstrained]
 *   assistant: You use Neovim, and you like short answers.
 */

import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createNams, enforceQueryMemory, ensureMemoryStored } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs, type LanguageModel } from 'ai';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-tools-mode';
const modelId = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

/**
 * Run the teach-then-recall demo. `buildModel` defaults to OpenAI; tests pass
 * a mock model factory here so the demo can run without a real API call.
 */
export async function toolsModeDemo(
  buildModel: (id: string) => LanguageModel = openai,
): Promise<{ taught: string; recalled: string }> {
  const apiKey = process.env.MEMORY_API_KEY;
  if (!apiKey) {
    throw new Error(
      'Set MEMORY_API_KEY before running this example. Get a free key at https://memory.neo4jlabs.com',
    );
  }

  const nams = createNams({
    apiKey,
    endpoint: process.env.MEMORY_ENDPOINT,
    workspaceId: process.env.MEMORY_WORKSPACE_ID,
  });

  async function turn(label: string, prompt: string, instructions: string): Promise<string> {
    // A new tool set each turn -- store_memory/query_memory read and write
    // the same NAMS-side conversation for this user, found by userId.
    const tools = nams.tools({ userId });

    const agent = new ToolLoopAgent({
      model: buildModel(modelId),
      instructions,
      tools,
      prepareStep: enforceQueryMemory(), // holds the model at query_memory until it has queried
      onFinish: async event => {
        // Falls back to persisting the answer if store_memory was never called.
        const outcome = await ensureMemoryStored(tools)(event);
        console.log(`ensureMemoryStored: ${outcome.stored ? 'persisted the turn' : outcome.reason}`);
      },
      stopWhen: stepCountIs(6),
    });

    const result = await agent.generate({ prompt });

    console.log(`\n--- ${label}`);
    let queried = false;
    result.steps.forEach((step, i) => {
      console.log(`step ${i} [${queried ? 'unconstrained' : 'enforced: some tool required'}]`);
      for (const call of step.toolCalls) {
        queried ||= call.toolName === 'query_memory';
        console.log(`  tool call: ${call.toolName}(${JSON.stringify(call.input).slice(0, 120)})`);
      }
    });
    console.log(`assistant: ${result.text}`);
    return result.text;
  }

  const taught = await turn(
    'Turn 1 -- teach it something',
    'I prefer very short answers, and I use Neovim. Got any editor tips for me?',
    'Consult memory with query_memory before answering. When the conversation ' +
      'contains facts or preferences worth remembering, call store_memory before ' +
      'giving your final answer.',
  );

  const recalled = await turn(
    'Turn 2 -- fresh tool set, same user',
    'What editor do I use, and how do I like my answers?',
    'Consult memory with query_memory before answering.',
  );

  return { taught, recalled };
}

// Run only when executed directly, not when a test imports this file.
const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(realpathSync(entry)).href) {
  toolsModeDemo().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
