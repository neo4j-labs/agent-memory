/**
 * Lifecycle hooks mode: your code loads and saves the session transcript
 * around every generation (`prepare` / `onFinish`); nothing memory-related is
 * shown to the model. Lifecycle hooks add control around that. The four turns
 * below exercise all eight events. SessionStart and SessionEnd run once per
 * session; UserPromptSubmit, PreMemoryWrite and Stop run on every turn; the
 * three tool events run when their tool is called:
 *
 *   SessionStart         adds a note about the user
 *   UserPromptSubmit      redacts card numbers before the model sees them
 *   PreToolUse             denies the destructive tool
 *   PostToolUse              carries a note into the next turn
 *   PostToolUseFailure         retries a flaky tool once, then gives up
 *   PreMemoryWrite               redacts again, before it reaches the graph
 *   Stop                           logs what was saved
 *   SessionEnd                       runs on shutdown
 *
 * Run with:
 *
 *   MEMORY_API_KEY=nams_... OPENAI_API_KEY=sk-... npx tsx src/hooks-mode.ts
 */

import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createNams, type NamsHookConfig } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs, tool, type LanguageModel } from 'ai';
import { z } from 'zod';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-hooks-mode';
const modelId = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';
const CARD = /\b(?:\d[ -]*?){13,16}\b/g;
const redact = (text: string): string => text.replace(CARD, '[redacted card]');

/** Run the four-turn hooks demo. `buildModel` defaults to OpenAI; tests pass a mock model factory. */
export async function hooksModeDemo(buildModel: (id: string) => LanguageModel = openai): Promise<string[]> {
  const apiKey = process.env.MEMORY_API_KEY;
  if (!apiKey) {
    throw new Error('Set MEMORY_API_KEY before running this example. Get a free key at https://memory.neo4jlabs.com');
  }
  let flakyAttempts = 0;
  const hooks: NamsHookConfig = {
    SessionStart: [({ reason }) => ({ additionalContext: `Session ${reason}. The user is on the free plan.` })],
    UserPromptSubmit: [({ prompt }) => {
      const clean = redact(prompt);
      return clean === prompt ? undefined : { updatedPrompt: clean, systemMessage: 'Redacted a card number.' };
    }],
    PreToolUse: [{
      matcher: 'delete_account',
      hooks: [() => ({ permissionDecision: 'deny' as const, permissionDecisionReason: 'Account deletion is disabled in this demo.' })],
    }],
    PostToolUse: [{
      matcher: 'get_weather',
      hooks: [({ output }) => ({ additionalContext: `Last weather lookup: ${JSON.stringify(output)}` })],
    }],
    PostToolUseFailure: [{
      matcher: 'flaky_lookup',
      hooks: [({ attempt }) => (attempt === 1 ? { retry: true, systemMessage: 'flaky_lookup failed once, retrying.' } : undefined)],
    }],
    PreMemoryWrite: [({ turns }) => ({ updatedTurns: turns.map(t => ({ ...t, content: redact(t.content) })) })],
    Stop: [({ turns }) => { console.log(`   [hook] saved ${turns.length} turns`); }],
    SessionEnd: [({ reason }) => { console.log(`   [hook] session ended (${reason})`); }],
  };
  const nams = createNams({ apiKey, endpoint: process.env.MEMORY_ENDPOINT, workspaceId: process.env.MEMORY_WORKSPACE_ID });
  const session = nams.hooks({ userId, hooks }); // one instance: prepare + onFinish share a client
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
  const flaky_lookup = tool({
    description: 'Look up a fact. Fails the first time it is called in this process.',
    inputSchema: z.object({ topic: z.string() }),
    execute: async ({ topic }) => {
      flakyAttempts += 1;
      if (flakyAttempts === 1) throw new Error('transient lookup error');
      return { topic, fact: `${topic} is well documented` };
    },
  });
  const INSTRUCTIONS = 'You are a helpful assistant.';
  const agent = new ToolLoopAgent({
    model: buildModel(modelId),
    instructions: INSTRUCTIONS,
    tools: session.withHooks({ get_weather, delete_account, flaky_lookup }), // wrapping is what runs the tool hooks
    callOptionsSchema: z.object({ userId: z.string(), prompt: z.string(), memoryContext: z.string().optional() }),
    // Hook context goes in instructions -- the AI SDK rejects system messages in `messages`.
    prepareCall: ({ options, ...settings }) => ({
      ...settings,
      instructions: [INSTRUCTIONS, options?.memoryContext].filter(Boolean).join('\n\n'),
      runtimeContext: options,
    }),
    onFinish: session.onFinish(),
    stopWhen: stepCountIs(5),
  });
  async function turn(label: string, message: string): Promise<string> {
    console.log(`\n--- ${label}`);
    console.log(`user:      ${message}`);
    // prepare() runs SessionStart and UserPromptSubmit, then loads the history.
    const prepared = await session.prepare({ userId, prompt: message });
    if (prepared.blocked) {
      console.log(`blocked:   ${prepared.blockReason}`);
      return prepared.blockReason ?? '';
    }
    const { text } = await agent.generate({
      messages: prepared.messages,
      options: { userId, prompt: prepared.prompt, memoryContext: prepared.instructions },
    });
    console.log(`assistant: ${text}`);
    return text;
  }
  const answers = [
    await turn('Turn 1 -- a tool call the hooks allow', 'What is the weather in Oslo?'),
    await turn('Turn 2 -- a flaky tool the hooks retry', 'Look up a fact about graph databases for me.'),
    await turn('Turn 3 -- a tool call the hooks deny', 'Please delete my account, I confirm.'),
    await turn('Turn 4 -- sensitive input', 'My card is 4111 1111 1111 1111, remember it.'),
  ];
  await session.end({ userId, reason: 'demo_complete' });
  return answers;
}
// Run only when executed directly, not when a test imports this file.
const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(realpathSync(entry)).href) {
  hooksModeDemo().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
