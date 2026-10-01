/**
 * Lifecycle hooks demo. Each stage of the turn runs a hook:
 *
 *   SessionStart       adds a note about the user
 *   UserPromptSubmit   redacts card numbers before the model sees them
 *   PreToolUse         denies the destructive tool
 *   PostToolUse        carries a note into the next turn
 *   PreMemoryWrite     redacts again, before anything reaches the graph
 *   Stop               logs what was saved
 *   SessionEnd         runs on shutdown
 *
 * Run from the package root, with MEMORY_API_KEY and OPENAI_API_KEY set:
 *
 *   npx tsx --env-file=.env examples/advanced-hooks-chat.ts
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs, tool } from 'ai';
import { z } from 'zod';
import { createNams } from '../src/index';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-advanced-hooks';
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

const CARD = /\b(?:\d[ -]*?){13,16}\b/g;
const redact = (text: string): string => text.replace(CARD, '[redacted card]');

const nams = createNams({ apiKey: process.env.MEMORY_API_KEY! });

const session = nams.hooks({
  userId,
  hooks: {
    SessionStart: [
      ({ reason }) => ({ additionalContext: `Session ${reason}. The user is on the free plan.` }),
    ],

    UserPromptSubmit: [
      ({ prompt }) => {
        const clean = redact(prompt);
        return clean === prompt ? undefined : { updatedPrompt: clean, systemMessage: 'Redacted a card number.' };
      },
    ],

    PreToolUse: [
      {
        matcher: 'delete_account',
        hooks: [
          () => ({
            permissionDecision: 'deny' as const,
            permissionDecisionReason: 'Account deletion is disabled in this demo.',
          }),
        ],
      },
    ],

    PostToolUse: [
      {
        matcher: 'get_weather',
        hooks: [({ output }) => ({ additionalContext: `Last weather lookup: ${JSON.stringify(output)}` })],
      },
    ],

    PreMemoryWrite: [
      ({ turns }) => ({ updatedTurns: turns.map(t => ({ ...t, content: redact(t.content) })) }),
    ],

    Stop: [({ turns }) => { console.log(`   [hook] saved ${turns.length} turns`); }],

    SessionEnd: [({ reason }) => { console.log(`   [hook] session ended (${reason})`); }],
  },
});

const get_weather = tool({
  description: 'Current weather for a city',
  inputSchema: z.object({ city: z.string() }),
  execute: async ({ city }) => ({ city, condition: 'sunny', tempC: 21 }),
});

const delete_account = tool({
  description: 'Permanently delete the user account',
  inputSchema: z.object({ confirm: z.boolean() }),
  execute: async () => ({ deleted: true }),
});

const INSTRUCTIONS = 'You are a helpful assistant.';

const agent = new ToolLoopAgent({
  model: openai(model),
  instructions: INSTRUCTIONS,

  // Wrapping the tool set is what makes the tool hooks run.
  tools: session.withHooks({ get_weather, delete_account }),

  callOptionsSchema: z.object({
    userId: z.string(),
    prompt: z.string(),
    memoryContext: z.string().optional(),
  }),

  // Hook context goes in instructions. The AI SDK rejects system messages in `messages`.
  prepareCall: ({ options, ...settings }) => ({
    ...settings,
    instructions: [INSTRUCTIONS, options?.memoryContext].filter(Boolean).join('\n\n'),
    runtimeContext: options,
  }),

  onFinish: session.onFinish(),

  stopWhen: stepCountIs(5),
});

async function turn(label: string, message: string): Promise<void> {
  console.log(`\n─── ${label}`);
  console.log(`user:      ${message}`);

  // prepare() runs SessionStart and UserPromptSubmit, then loads the history.
  const prepared = await session.prepare({ userId, prompt: message });

  if (prepared.blocked) {
    console.log(`blocked:   ${prepared.blockReason}`);
    return;
  }

  const { text } = await agent.generate({
    messages: prepared.messages,
    options: { userId, prompt: prepared.prompt, memoryContext: prepared.instructions },
  });

  console.log(`assistant: ${text}`);
}

async function main(): Promise<void> {
  await turn('Turn 1 — a tool call the hooks allow', 'What is the weather in Oslo?');
  await turn('Turn 2 — a tool call the hooks deny', 'Please delete my account, I confirm.');
  await turn('Turn 3 — sensitive input', 'My card is 4111 1111 1111 1111, remember it.');
  await session.end({ userId, reason: 'demo_complete' });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
