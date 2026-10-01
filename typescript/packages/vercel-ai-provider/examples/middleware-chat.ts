/**
 * Middleware mode demo. Keep your model and add memory with `createNams().wrap()`.
 * MEMORY_API_KEY is for NAMS. OPENAI_API_KEY is for the OpenAI model.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/middleware-chat.ts
 *
 * Expected output (wording varies):
 *
 *   ─── Turn 1 — teach it something
 *   user:      My favourite programming language is Rust.
 *   assistant: Great choice! Rust is loved for its safety and performance. …
 *
 *   ─── Turn 2 — fresh model instance, same user
 *   user:      What is my favourite programming language?
 *   assistant: Your favourite programming language is Rust.
 *
 * Turn 2 uses a new model instance, so the answer comes from NAMS memory.
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';
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
    stopWhen: stepCountIs(1), // no tools needed in middleware mode
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
