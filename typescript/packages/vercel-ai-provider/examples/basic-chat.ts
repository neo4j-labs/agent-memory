// Provider mode: a new agent for the same user recalls a fact from an earlier session.
// Run: MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/basic-chat.ts

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, isStepCount } from 'ai';
import { createNamsProvider } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-basic-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

async function session(label: string, message: string): Promise<void> {
  // One provider per user session.
  const nams = createNamsProvider({
    apiKey: process.env.MEMORY_API_KEY!,
    baseProvider: openai,
    scope: { userId },
  });

  const agent = new ToolLoopAgent({
    model: nams.languageModel(model),
    instructions: 'You are a helpful assistant.',
    stopWhen: isStepCount(1), // no tools needed in provider mode
  });

  const { text } = await agent.generate({ prompt: message });

  console.log(`\n─── ${label}`);
  console.log(`user:      ${message}`);
  console.log(`assistant: ${text}`);
}

async function main(): Promise<void> {
  await session('Session 1 — teach it something', 'Hi! My name is Alex and I work at TechCorp on the graph platform team.');

  // A new agent has no history, so the answer must come from NAMS.
  await session('Session 2 — fresh session, same user', 'Where do I work, and what team am I on?');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
