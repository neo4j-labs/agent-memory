/**
 * Provider mode: swap your model for a NAMS-wrapped one and memory comes for
 * free on every call -- no tools, no system-prompt changes. Session 1 teaches
 * the agent a fact. Session 2 is a brand-new agent for the same user, so the
 * only way it can answer is by recalling what NAMS stored.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=nams_... OPENAI_API_KEY=sk-... npx tsx src/provider-mode.ts
 *
 * Expected output (wording varies):
 *
 *   --- Session 1 -- teach it something
 *   user:      Hi! My name is Alex and I work at TechCorp on the graph platform team.
 *   assistant: Nice to meet you, Alex! How can I help you today? ...
 *
 *   --- Session 2 -- fresh session, same user
 *   user:      Where do I work, and what team am I on?
 *   assistant: You work at TechCorp, on the graph platform team.
 */

import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createNamsProvider, type NamsProviderOptions } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-provider-mode';
const modelId = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

/**
 * Run the teach-then-recall demo. `baseProvider` defaults to OpenAI; tests
 * pass a mock model here so the demo can run without a real API call.
 */
export async function providerModeDemo(
  baseProvider: NamsProviderOptions['baseProvider'] = openai,
): Promise<{ taught: string; recalled: string }> {
  const rawApiKey = process.env.MEMORY_API_KEY;
  if (!rawApiKey) {
    throw new Error(
      'Set MEMORY_API_KEY before running this example. Get a free key at https://memory.neo4jlabs.com',
    );
  }
  const apiKey: string = rawApiKey; // narrowed here so the closure below sees `string`, not `string | undefined`

  async function session(label: string, message: string): Promise<string> {
    // One provider per user session. `maxMemories` caps how many memories
    // are added to the prompt each turn (the package itself caps it at 12);
    // `persistInteractions` (default: true) saves the turn back to NAMS.
    const nams = createNamsProvider({
      apiKey,
      baseProvider,
      scope: { userId },
      endpoint: process.env.MEMORY_ENDPOINT,
      workspaceId: process.env.MEMORY_WORKSPACE_ID,
      maxMemories: 6,
      persistInteractions: true,
    });

    const agent = new ToolLoopAgent({
      model: nams.languageModel(modelId),
      instructions: 'You are a helpful assistant.',
      stopWhen: stepCountIs(1), // no tools needed in provider mode
    });

    const { text } = await agent.generate({ prompt: message });

    console.log(`\n--- ${label}`);
    console.log(`user:      ${message}`);
    console.log(`assistant: ${text}`);
    return text;
  }

  const taught = await session(
    'Session 1 -- teach it something',
    'Hi! My name is Alex and I work at TechCorp on the graph platform team.',
  );
  console.log('Stored: the user message and the assistant reply, saved to NAMS automatically.');

  // A new agent has no history, so the answer must come from NAMS.
  const recalled = await session('Session 2 -- fresh session, same user', 'Where do I work, and what team am I on?');
  console.log('Recalled: NAMS retrieved the session 1 fact and injected it into this prompt.');

  return { taught, recalled };
}

// Run only when executed directly, not when a test imports this file.
const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(realpathSync(entry)).href) {
  providerModeDemo().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
