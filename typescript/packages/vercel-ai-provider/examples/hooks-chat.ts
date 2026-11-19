// Hooks mode: `loadSession` restores the transcript before each call, `onEnd()` saves it after.
// Run: MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/hooks-chat.ts

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, isStepCount, tool } from 'ai';
import { z } from 'zod';
import { createNams } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-hooks-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

// One factory, so loadSession() and onEnd() use the same conversation.
const nams = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const session = nams.hooks({ userId });

const get_weather = tool({
  description: 'Current weather for a city',
  inputSchema: z.object({ city: z.string() }),
  execute: async ({ city }) => ({ city, condition: 'sunny', tempC: 21 }),
});

const agent = new ToolLoopAgent({
  model: openai(model),
  instructions: 'You are a helpful assistant.',
  tools: { get_weather },

  callOptionsSchema: z.object({
    userId: z.string(),
    prompt: z.string(),
  }),

  // History plus the new message. The AI SDK takes prompt or messages, not both.
  prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
    ...settings,
    messages: [
      ...(await session.loadSession(options)),
      { role: 'user' as const, content: options!.prompt },
    ],
    // Passes the scope to the onEnd callback.
    runtimeContext: options,
  }),

  // After: save every turn once.
  onEnd: session.onEnd(),

  stopWhen: isStepCount(5),
});

async function turn(label: string, message: string): Promise<void> {
  const { text } = await agent.generate({
    prompt: message,
    options: { userId, prompt: message },
  });

  console.log(`\n─── ${label}`);
  console.log(`user:      ${message}`);
  console.log(`assistant: ${text}`);
}

async function main(): Promise<void> {
  await turn('Turn 1 — teach it something', 'Hi! My name is Alex and I live in Oslo.');
  await turn('Turn 2 — fresh agent call, same session', 'What is my name, and what is the weather where I live?');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
