/**
 * Middleware mode: keep the model you already configure elsewhere and add
 * memory with `createNams().wrap()`. Turn 1 teaches the agent a fact. Turn 2
 * uses a brand-new model instance for the same user, so the answer must come
 * back out of NAMS.
 *
 * Run with:
 *
 *   MEMORY_API_KEY=nams_... OPENAI_API_KEY=sk-... npx tsx src/middleware-mode.ts
 *
 * Expected output (wording varies):
 *
 *   --- Turn 1 -- teach it something
 *   user:      My favourite programming language is Rust.
 *   assistant: Great choice! Rust is loved for its safety and performance. ...
 *
 *   --- Turn 2 -- fresh model instance, same user
 *   user:      What is my favourite programming language?
 *   assistant: Your favourite programming language is Rust.
 */

import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createNams, type NamsProviderOptions } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';

const userId = process.env.NAMS_DEMO_USER ?? 'demo-user-middleware-mode';
const modelId = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

/**
 * Run the teach-then-recall demo. `buildModel` defaults to OpenAI; tests pass
 * a mock model factory here so the demo can run without a real API call.
 */
export async function middlewareModeDemo(
  buildModel: NamsProviderOptions['baseProvider'] = openai,
): Promise<{ taught: string; recalled: string }> {
  const rawApiKey = process.env.MEMORY_API_KEY;
  if (!rawApiKey) {
    throw new Error(
      'Set MEMORY_API_KEY before running this example. Get a free key at https://memory.neo4jlabs.com',
    );
  }
  const apiKey: string = rawApiKey; // narrowed here so the closure below sees `string`, not `string | undefined`

  async function turn(label: string, message: string): Promise<string> {
    // A new model each turn -- memory comes from NAMS, not from local state.
    const nams = createNams({
      apiKey,
      endpoint: process.env.MEMORY_ENDPOINT,
      workspaceId: process.env.MEMORY_WORKSPACE_ID,
    });
    const wrappedModel = nams.wrap(buildModel(modelId), { userId });

    const agent = new ToolLoopAgent({
      model: wrappedModel,
      instructions: 'You are a helpful assistant.',
      stopWhen: stepCountIs(1), // no tools needed in middleware mode
    });

    const { text } = await agent.generate({ prompt: message });

    console.log(`\n--- ${label}`);
    console.log(`user:      ${message}`);
    console.log(`assistant: ${text}`);
    return text;
  }

  const taught = await turn('Turn 1 -- teach it something', 'My favourite programming language is Rust.');
  console.log('Stored: both sides of turn 1, saved to NAMS by the wrapped model.');

  const recalled = await turn('Turn 2 -- fresh model instance, same user', 'What is my favourite programming language?');
  console.log('Recalled: NAMS retrieved turn 1 and injected it before the model saw turn 2.');

  return { taught, recalled };
}

// Run only when executed directly, not when a test imports this file.
const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(realpathSync(entry)).href) {
  middlewareModeDemo().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
