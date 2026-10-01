/**
 * Hooks mode demo. The model gets no memory tools. `loadSession` restores the
 * transcript before each generation, and `onFinish` saves every turn after.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/hooks-chat.ts
 *
 * Expected output (wording varies):
 *
 *   ─── Turn 1 — teach it something
 *   user:      Hi! My name is Alex and I live in Oslo.
 *   assistant: Nice to meet you, Alex! …
 *
 *   ─── Turn 2 — fresh agent call, same session
 *   user:      What is my name, and what is the weather where I live?
 *   assistant: Your name is Alex. In Oslo it is currently sunny at 21°C.
 *
 * Turn 2 knows the name from the restored transcript. The get_weather call is
 * saved to NAMS as a tool record.
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs, tool } from 'ai';
import { z } from 'zod';
import { createNams } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-hooks-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

// One factory, so loadSession and onFinish use the same conversation.
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

  // Before: history plus the new message. The AI SDK takes prompt or
  // messages, not both, so the incoming ones are dropped.
  prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
    ...settings,
    messages: [
      ...(await session.loadSession(options)),
      { role: 'user' as const, content: options!.prompt },
    ],
    // Passes the scope to onFinish.
    runtimeContext: options,
  }),

  // After: save every turn once.
  onFinish: session.onFinish(),

  stopWhen: stepCountIs(5),
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
