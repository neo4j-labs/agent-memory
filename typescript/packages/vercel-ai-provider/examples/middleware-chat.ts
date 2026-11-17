// Middleware mode: keep your model and add memory with `createNams().wrap()`.
// Run: MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/middleware-chat.ts

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, isStepCount } from 'ai';
import { createNams } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-middleware-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

async function turn(label: string, message: string): Promise<void> {
  // A new model each turn. Memory comes from NAMS.
  const nams = createNams({ apiKey: process.env.MEMORY_API_KEY! });
  const wrappedModel = nams.wrap(openai(model), { userId });

  const agent = new ToolLoopAgent({
    model: wrappedModel,
    instructions: 'You are a helpful assistant.',
    stopWhen: isStepCount(1), // no tools needed in middleware mode
  });

  const { text } = await agent.generate({ prompt: message });

  console.log(`\n─── ${label}`);
  console.log(`user:      ${message}`);
  console.log(`assistant: ${text}`);
}

async function main(): Promise<void> {
  await turn('Turn 1 — teach it something', 'My favourite programming language is Rust.');
  await turn('Turn 2 — fresh model instance, same user', 'What is my favourite programming language?');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
