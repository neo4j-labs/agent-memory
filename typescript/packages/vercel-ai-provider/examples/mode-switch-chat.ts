/**
 * Mode-switch demo — all four integration modes behind one env var, so the
 * deployment (not the code) decides how memory attaches:
 *
 *   NAMS_MODE=provider    memory wraps the provider          (transparent)
 *   NAMS_MODE=middleware  memory wraps the model instance    (transparent)
 *   NAMS_MODE=tools       memory is query_memory/store_memory (model-driven)
 *   NAMS_MODE=hooks       memory brackets the generation      (runtime-driven)
 *
 * Run with:
 *
 *   NAMS_MODE=hooks MEMORY_API_KEY=sk-nams-... OPENAI_API_KEY=sk-... \
 *     npx tsx examples/mode-switch-chat.ts
 *
 * Every mode runs the same two turns — teach a fact, then recall it — and all
 * four share one API key and one memory backend, so you can switch modes
 * between runs and the memories carry over. What changes is *where* the
 * retrieve-and-persist cycle happens: inside the model call (provider,
 * middleware), inside the agent loop as visible tool calls (tools), or around
 * the whole generation with nothing shown to the model (hooks).
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';
import { z } from 'zod';
import {
  createNams,
  createNamsProvider,
  enforceQueryMemory,
  ensureMemoryStored,
} from '../src/index';

const MODES = ['provider', 'middleware', 'tools', 'hooks'] as const;
type Mode = (typeof MODES)[number];

const mode = (process.env.NAMS_MODE ?? 'provider') as Mode;
if (!MODES.includes(mode)) {
  console.error(`NAMS_MODE must be one of: ${MODES.join(' | ')} (got "${process.env.NAMS_MODE}")`);
  process.exit(1);
}

const apiKey = process.env.MEMORY_API_KEY!;
const userId = process.env.NAMS_DEMO_USER ?? `demo-user-mode-switch`;
const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

/** One shape per mode: a send() the turn runner below can call uniformly. */
interface Chat {
  send: (message: string) => Promise<string>;
}

function buildChat(): Chat {
  const scope = { userId };

  switch (mode) {
    case 'provider': {
      // Memory as a ProviderV4 — the swap lands at the model constructor.
      const nams = createNamsProvider({ apiKey, baseProvider: openai, scope });
      const agent = new ToolLoopAgent({
        model: nams.languageModel(model),
        instructions: 'You are a helpful assistant.',
        stopWhen: stepCountIs(1),
      });
      return { send: async (message) => (await agent.generate({ prompt: message })).text };
    }

    case 'middleware': {
      // Memory as middleware — same guarantee, wraps an already-resolved model.
      const nams = createNams({ apiKey });
      const agent = new ToolLoopAgent({
        model: nams.wrap(openai(model), scope),
        instructions: 'You are a helpful assistant.',
        stopWhen: stepCountIs(1),
      });
      return { send: async (message) => (await agent.generate({ prompt: message })).text };
    }

    case 'tools': {
      // Memory as tools — the model drives, with guards on read and write.
      const tools = createNams({ apiKey }).tools(scope);
      const agent = new ToolLoopAgent({
        model: openai(model),
        instructions:
          'Consult memory with query_memory before answering. When the conversation ' +
          'contains facts or preferences worth remembering, call store_memory before ' +
          'giving your final answer.',
        tools,
        prepareStep: enforceQueryMemory(),
        onFinish: async (event) => { await ensureMemoryStored(tools)(event); },
        stopWhen: stepCountIs(6),
      });
      return { send: async (message) => (await agent.generate({ prompt: message })).text };
    }

    case 'hooks': {
      // Memory as generation lifecycle hooks — the runtime reads and writes
      // the transcript around every generation; the model sees no memory
      // surface at all.
      const session = createNams({ apiKey }).hooks(scope);
      const agent = new ToolLoopAgent({
        model: openai(model),
        instructions: 'You are a helpful assistant.',
        callOptionsSchema: z.object({ userId: z.string(), prompt: z.string() }),
        prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
          ...settings,
          messages: [
            ...(await session.loadSession(options)),
            { role: 'user' as const, content: options!.prompt },
          ],
          runtimeContext: options,
        }),
        onFinish: session.onFinish(),
        stopWhen: stepCountIs(6),
      });
      return {
        send: async (message) =>
          (await agent.generate({ prompt: message, options: { userId, prompt: message } })).text,
      };
    }
  }
}

async function main(): Promise<void> {
  console.log(`mode: ${mode}\n`);
  const chat = buildChat();

  const turns = [
    ['Turn 1 — teach it something', 'My favourite database is Neo4j.'],
    ['Turn 2 — recall', 'What is my favourite database?'],
  ] as const;

  for (const [label, message] of turns) {
    const text = await chat.send(message);
    console.log(`─── ${label}`);
    console.log(`user:      ${message}`);
    console.log(`assistant: ${text}\n`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
