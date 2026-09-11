/**
 * Hooks mode demo — runtime-controlled (deterministic) session memory.
 *
 * No memory tools are exposed to the LLM. Instead the runtime restores the
 * transcript before every generation (`loadSession` inside `prepareCall`) and
 * persists every user, assistant, and tool turn after it (`onFinish`) —
 * exactly once per generation, regardless of what the model decides.
 * Run with:
 *
 *   MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... npx tsx examples/hooks-chat.ts
 *
 * Expected output (assistant wording will vary):
 *
 *   ─── Turn 1 — teach it something
 *   user:      Hi! My name is Alex and I live in Oslo.
 *   assistant: Nice to meet you, Alex! …
 *
 *   ─── Turn 2 — fresh agent call, same session
 *   user:      What is my name, and what is the weather where I live?
 *   assistant: Your name is Alex. In Oslo it is currently sunny at 21°C.
 *
 * Turn 2 knows the name because prepareCall replayed the turn-1 transcript,
 * and the get_weather tool call from this turn is persisted as an audit
 * record in NAMS — the runtime captured it, not the model.
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs, tool } from 'ai';
import { z } from 'zod';
import { createNams } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-hooks-chat';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

// One factory per process: loadSession and onFinish share a client, so both
// resolve the same conversation.
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

  // PRE hook: replace prompt/messages with restored history + the new turn.
  // The AI SDK enforces prompt XOR messages, so both incoming fields are
  // stripped before the rebuilt messages array goes in.
  prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
    ...settings,
    messages: [
      ...(await session.loadSession(options)),
      { role: 'user' as const, content: options!.prompt },
    ],
    // Per-call scope flows to the construction-time onFinish below.
    runtimeContext: options,
  }),

  // POST hook: persist every turn of the finished generation exactly once.
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
