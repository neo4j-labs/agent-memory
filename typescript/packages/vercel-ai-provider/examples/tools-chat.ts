/**
 * Tools mode demo. The model decides when to query and store memory, and each
 * memory call shows up as a tool call. `enforceQueryMemory()` makes sure
 * `query_memory` runs before the final answer.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/tools-chat.ts
 *
 * Expected output (arguments and wording vary):
 *
 *   step 0 [enforced: some tool required]
 *     tool call: query_memory({"query":"user editor preferences","limit":5})
 *   step 1 [unconstrained]
 *     tool call: store_memory({"content":"User prefers very short answers","type":"user_preference","confidence":0.9,…
 *     tool call: store_memory({"content":"User uses Neovim as their editor","type":"fact","confidence":0.9,"tags":["e…
 *   step 2 [unconstrained]
 *
 *   ensureMemoryStored: already-stored
 *
 *   assistant: Try Telescope for fuzzy finding and Harpoon for quick file
 *   switching.
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';
import { createNams, enforceQueryMemory, ensureMemoryStored } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-tools-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

async function main(): Promise<void> {
  const nams = createNams({ apiKey: process.env.MEMORY_API_KEY! });
  const tools = nams.tools({ userId });

  const agent = new ToolLoopAgent({
    model: openai(model),
    instructions:
      'Consult memory with query_memory before answering. When the conversation ' +
      'contains facts or preferences worth remembering, call store_memory before ' +
      'giving your final answer.',
    tools,
    prepareStep: enforceQueryMemory(),
    onFinish: async (event) => {
      const outcome = await ensureMemoryStored(tools)(event);
      console.log(
        `\nensureMemoryStored: ${outcome.stored ? 'persisted the turn' : outcome.reason}`,
      );
    },
    stopWhen: stepCountIs(6),
  });

  const result = await agent.generate({
    prompt: 'I prefer very short answers, and I use Neovim. Got any editor tips for me?',
  });

  let queried = false;
  result.steps.forEach((step, i) => {
    console.log(`step ${i} [${queried ? 'unconstrained' : 'enforced: some tool required'}]`);
    for (const call of step.toolCalls) {
      queried ||= call.toolName === 'query_memory';
      console.log(`  tool call: ${call.toolName}(${JSON.stringify(call.input).slice(0, 120)})`);
    }
  });
  console.log(`\nassistant: ${result.text}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
