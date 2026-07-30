# @neo4j-labs/nams-ai-provider

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Community provider for the [Vercel AI SDK](https://sdk.vercel.ai) that adds persistent cross-session memory to any language model, backed by the [Neo4j Agent Memory Service (NAMS)](https://memory.neo4jlabs.com).

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

On every turn, NAMS automatically retrieves relevant memories from the user's history and injects them into the prompt — then persists the response so future sessions remember it. No Neo4j infrastructure to manage.

## What does it do?

Without this package, every chat session starts fresh — the model has no recollection of who the user is, what they've said before, or what decisions were made in prior conversations.

`@neo4j-labs/nams-ai-provider` wraps your existing AI model and transparently adds memory to every call:

1. **Before the model responds** — NAMS searches its memory store for facts, preferences, and past interactions relevant to the current message, then injects them into the prompt automatically.
2. **After the model responds** — NAMS persists the exchange (and optionally extracts entities into a Neo4j knowledge graph) so the next session can recall it.

The result: your AI remembers users across sessions without you changing your application logic.

```
User message
     │
     ▼
┌─────────────────────────────┐
│  NAMS: fetch relevant       │  ← searches long-term graph,
│  memories from Neo4j        │    past sessions, reasoning traces
└────────────┬────────────────┘
             │  memories injected into prompt
             ▼
┌─────────────────────────────┐
│  Your LLM (GPT, Claude…)    │  ← responds with full context
└────────────┬────────────────┘
             │  response
             ▼
┌─────────────────────────────┐
│  NAMS: persist & extract    │  ← stores turn, builds knowledge graph
└─────────────────────────────┘
```

## Setup

**1. Install the provider and its peer dependencies**

```bash
npm install @neo4j-labs/nams-ai-provider ai @ai-sdk/provider @neo4j-labs/agent-memory zod
```

<details>
<summary>Working from source (package not yet on npm)</summary>

```bash
# from the repo root
cd typescript/packages/vercel-ai-provider
npm install
npm run build
npm pack   # then `npm install ../path/to/neo4j-labs-nams-ai-provider-0.1.0.tgz` in your app
```

</details>

**2. Get a free API key** at [memory.neo4jlabs.com](https://memory.neo4jlabs.com)

```env
MEMORY_API_KEY=sk-nams-...
```

---

## Quick Start

Once installed and your API key is set, adding memory to your Vercel AI SDK app is a one-line model swap:

```ts
// Before: plain agent, no memory
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  stepCountIs,
} from 'ai';

const agent = new ToolLoopAgent({
  model:        openai('gpt-5.4-mini'),
  instructions: 'You are a helpful assistant.',
  stopWhen:     stepCountIs(10),
});

const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    const result = await agent.stream({ messages });
    writer.merge(result.toUIMessageStream());
  },
});

return createUIMessageStreamResponse({ stream });
```

```ts
// After: swap model → agent now remembers users across sessions
import { createNamsProvider } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  stepCountIs,
} from 'ai';

const nams = createNamsProvider({
  apiKey:       process.env.MEMORY_API_KEY!,
  baseProvider: openai,
  scope:        { userId: 'user-123' },  // identify the user
});

const agent = new ToolLoopAgent({
  model:        nams.languageModel('gpt-5.4-mini'),  // ← only change
  instructions: 'You are a helpful assistant.',
  stopWhen:     stepCountIs(10),
});

const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    const result = await agent.stream({ messages });
    writer.merge(result.toUIMessageStream());
  },
});

return createUIMessageStreamResponse({ stream });
```

**What happens automatically on every call:**
- Relevant memories for `user-123` are fetched and prepended to the prompt
- The model's response is saved back to memory for future sessions
- No other code changes needed

---

## Usage Modes

There are three ways to integrate NAMS depending on how much control you want:

| Mode | How it works | Best for |
|------|-------------|----------|
| **Provider** | Swap your model for a NAMS-wrapped one | Simplest integration, fully transparent |
| **Middleware** | Wrap an existing model instance | When you already have a model configured |
| **Tools** | Expose memory as explicit AI SDK tools (optionally merged with tools from an MCP server) | When you want the model to decide when to remember |

Switching modes is purely a code-level choice — all three use the same API key
and environment variables (see [Environment variables](#environment-variables)).
Middleware and tools modes can also be
[combined on one agent](#combining-tools-mode-with-middleware-hybrid) —
injected baseline context plus explicit memory tools.

> **How does this relate to `@neo4j-labs/agent-memory/middleware/vercel-ai`?**
> The core SDK ships a minimal middleware for the AI SDK 4.x-era
> `LanguageModelV1Middleware` shape that injects current-conversation context.
> This package targets AI SDK v7 / `LanguageModelV4` and adds a registrable
> `ProviderV4`, cross-session retrieval, optional graph extraction, explicit
> memory tools, and MCP tool merging. New projects should prefer this package.

---

## Provider Mode (ProviderV4)

Drop NAMS into any Vercel AI SDK project as a standard `ProviderV4`. Memory is fully transparent — no tools, no system prompt changes needed.

```ts
import { createNamsProvider } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  stepCountIs,
} from 'ai';

// One instance per user session
const nams = createNamsProvider({
  apiKey:       process.env.MEMORY_API_KEY!,
  baseProvider: openai,               // any @ai-sdk/* provider
  scope:        { userId: session.userId },
});

const agent = new ToolLoopAgent({
  model:        nams.languageModel('gpt-5.4-mini'),
  instructions: 'You are a helpful assistant.',
  stopWhen:     stepCountIs(1),       // no tools needed in provider mode
});

const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    const result = await agent.stream({ messages });
    writer.merge(result.toUIMessageStream());
  },
});

return createUIMessageStreamResponse({ stream });
```

Works with the provider registry:

```ts
import { createProviderRegistry as createRegistry } from 'ai';

const registry = createRegistry({
  nams: createNamsProvider({
    apiKey:       process.env.MEMORY_API_KEY!,
    baseProvider: openai,
    scope:        { userId },
  }),
});

const agent = new ToolLoopAgent({
  model:    registry.languageModel('nams:gpt-5.4-mini'),
  stopWhen: stepCountIs(1),
});
```

---

## Middleware Mode

Wrap any existing model instance with memory — useful when you already configure your model elsewhere and just want to decorate it:

```ts
import { createNams } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  stepCountIs,
} from 'ai';

const nams  = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const model = nams.wrap(openai('gpt-5.4-mini'), { userId: session.userId });

const agent = new ToolLoopAgent({ model, stopWhen: stepCountIs(1) });

const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    const result = await agent.stream({ messages });
    writer.merge(result.toUIMessageStream());
  },
});

return createUIMessageStreamResponse({ stream });
```

---

## Tools Mode

Expose `query_memory` and `store_memory` as explicit AI SDK tools. The model decides when to call them, and the calls are visible in your UI — useful for debugging or when you want the user to see memory activity:

```ts
import { createNams, enforceQueryMemory } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  stepCountIs,
} from 'ai';

const nams  = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const tools = nams.tools({ userId: session.userId });

const agent = new ToolLoopAgent({
  model:        openai('gpt-5.4-mini'),
  instructions:
    'Before answering, consult memory with query_memory. When the conversation ' +
    'contains facts or preferences worth remembering, call store_memory before ' +
    'giving your final answer.',
  tools,
  prepareStep:  enforceQueryMemory(),
  stopWhen:     stepCountIs(10),
});

const stream = createUIMessageStream({
  execute: async ({ writer }) => {
    const result = await agent.stream({ messages });
    writer.merge(result.toUIMessageStream());
  },
});

return createUIMessageStreamResponse({ stream });
```

Tool descriptions and instructions are advisory — models routinely skip
"bookkeeping" tool calls. `enforceQueryMemory()` makes retrieval mechanical
instead: it checks the executed tool calls each step and, until `query_memory`
has run, holds the model at `toolChoice: 'required'` — it can still call other
tools in any order, but cannot finish with a text-only answer before querying
memory. After `graceSteps` steps (default: 3) without a query, the next step
forces `query_memory` directly, so the loop can never exhaust its budget
without the query having run; `{ graceSteps: 0 }` forces it as the literal
first step. Keep `graceSteps` at least two below your `stopWhen` budget so the
forced query and the final answer both fit. There is no equivalent forcing
hook for `store_memory` (the loop ends when the model emits final text) — if
persistence must be guaranteed, check `steps` in `onFinish` and call
`store_memory.execute()` yourself, or use provider / middleware mode where
both directions happen unconditionally in code.

### Tools Mode with MCP (optional)

Still tools mode — not a separate mode. `toolsWithMcp()` connects to an MCP
server and merges its tools with the NAMS memory tools, so one agent can
remember *and* use external tooling. Requires the optional peer `@ai-sdk/mcp`
(`npm install @ai-sdk/mcp`) — it is loaded lazily, only when an MCP config is
passed.

```ts
import { createNams, enforceQueryMemory } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, stepCountIs } from 'ai';

const nams = createNams({ apiKey: process.env.MEMORY_API_KEY! });

const { tools, close } = await nams.toolsWithMcp(
  { userId: session.userId },
  {
    url:        'https://mcp.example.com/mcp',
    headers:    { Authorization: `Bearer ${token}` },
    toolPrefix: 'mcp_',
  },
);

const agent = new ToolLoopAgent({
  model:       openai('gpt-5.4-mini'),
  tools,       // query_memory + store_memory + mcp_* server tools
  prepareStep: enforceQueryMemory(),  // memory queried before answering, MCP tools in any order
  stopWhen:    stepCountIs(10),
});

try {
  const result = await agent.generate({ prompt: 'What did we decide last week?' });
  console.log(result.text);
} finally {
  await close(); // closes the MCP connection (no-op when MCP wasn't configured)
}
```

When the MCP config is omitted, `toolsWithMcp(scope)` behaves exactly like
`tools(scope)` with a no-op `close`.

### Combining tools mode with middleware (hybrid)

Middleware and tools modes are not mutually exclusive — one `createNams`
instance can serve both. The middleware-wrapped model injects baseline memory
context on every call, while the tools let the model run targeted searches and
explicit writes on top. This mirrors the "core memory injected each turn +
memory tool for on-demand search" pattern from the
[AI SDK custom memory tool guide](https://ai-sdk.dev/cookbook/guides/custom-memory-tool):

```ts
const nams  = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const scope = { userId: session.userId };

const agent = new ToolLoopAgent({
  model:    nams.wrap(openai('gpt-5.4-mini'), scope), // baseline context injected every call
  tools:    nams.tools(scope),                        // targeted search + explicit writes
  stopWhen: stepCountIs(10),
});
```

In this setup, skip `enforceQueryMemory()` — the middleware already guarantees
retrieval unconditionally in code, so forcing `query_memory` as well would
just spend an extra step re-fetching similar context. The enforcement hook is
for pure tools mode, where the tool call is the *only* retrieval path.

---

## Configuration

```ts
createNamsProvider({
  // Required
  apiKey:               string,
  baseProvider:         (modelId: string) => LanguageModelV4,
  scope:                { userId: string, conversationId?: string },

  // Optional
  endpoint?:            string,   // Default: https://memory.neo4jlabs.com/v1
  workspaceId?:         string,
  logger?:              NamsLogger, // warn/error sink for non-fatal errors. Default: console
  maxMemories?:         number,   // Max memories retrieved and injected into the prompt per turn (capped at 12). Default: 6
  persistInteractions?: boolean,  // Save each turn. Default: true
  extractionModel?:     LanguageModel, // Enables graph entity extraction
});
```

### Environment variables

No environment variable selects or changes the mode — provider, middleware,
and tools modes are chosen entirely in code, and all three read the same
variables:

| Variable | Required | Used for |
|----------|----------|----------|
| `MEMORY_API_KEY` | yes | NAMS API key — pass it as `apiKey` (the examples and snippets read it from the environment) |
| `OPENAI_API_KEY` | yes* | Read by `@ai-sdk/openai`; *swap for whichever `@ai-sdk/*` provider key your base model needs |
| `NAMS_DEMO_USER` | no | Overrides the demo `userId` in the [runnable examples](./examples) |
| `NAMS_DEMO_MODEL` | no | Overrides the demo model id (default: `gpt-5.4-mini`) in the [runnable examples](./examples) |

---

## Graph Extraction (optional)

Pass `extractionModel` to build a real Neo4j entity graph from memories instead of storing flat text:

```ts
const nams = createNamsProvider({
  apiKey:          process.env.MEMORY_API_KEY!,
  baseProvider:    openai,
  scope:           { userId },
  extractionModel: openai('gpt-5.4-mini'),
});
```

`"User is named Alex, works at TechCorp"` becomes `(Alex)-[:WORKS_AT]->(TechCorp)` in the graph.

> **Note:** relationship persistence depends on backend support. Where the
> NAMS API does not yet expose a relationship endpoint (the hosted REST API
> currently doesn't), extracted entities are stored and relationship writes
> are skipped with a warning — the graph gains edges automatically once the
> endpoint is available.

---

## Memory Sources

NAMS searches four sources in parallel per turn:

| Source | What it stores |
|--------|---------------|
| Long-term graph | Facts, preferences, patterns (Neo4j entities + relationships) |
| Current conversation | Messages in the active session (vector search) |
| Cross-session | Messages from past conversations for the same user |
| Reasoning traces | Prior step-by-step reasoning from agent runs |

---

## Development

```bash
npm install        # install dev dependencies
npm test           # run the vitest suite (provider, tools, ProviderV4 surface)
npm run typecheck  # tsc --noEmit
npm run build      # tsup → dist/
```

> **Note on conversation caching:** each provider / tools instance keeps its own
> conversation-id cache (scoped per `MemoryClient`). Create one instance per user
> session; instances never share cached conversation ids.

## Links

- [Neo4j Agent Memory Service](https://memory.neo4jlabs.com)
- [Vercel AI SDK community providers](https://ai-sdk.dev/providers/community-providers/custom-providers)
- [@neo4j-labs/agent-memory on npm](https://www.npmjs.com/package/@neo4j-labs/agent-memory)
- [Runnable examples](./examples)

## Support

- [Neo4j Community Forum](https://community.neo4j.com) — questions and
  discussion (primary)
- [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) —
  bug reports and feature requests

## License

Apache-2.0 — see [LICENSE](./LICENSE).
