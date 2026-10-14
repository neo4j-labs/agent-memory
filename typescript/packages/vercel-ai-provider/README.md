# @neo4j-labs/nams-ai-provider

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Community provider for the [Vercel AI SDK](https://ai-sdk.dev) that adds persistent cross-session memory to any language model, backed by the [Neo4j Agent Memory Service (NAMS)](https://memory.neo4jlabs.com) — a **knowledge graph**, not a flat vector store: what the model recalls includes the relationships between the things it remembers.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs, backward-compatibility guarantees,
> or scheduled deprecation commitments. APIs may change without notice. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

Provider and middleware mode read the user's memory from NAMS before each turn,
add it to the prompt, and save the user message and the answer afterwards. Tools
mode gives the model `query_memory` and `store_memory` instead, and hooks mode
restores and saves the whole transcript around each generation. A separate
`/eve` entry point is a memory provider for [eve](https://eve.dev). Everything
runs against hosted NAMS with an API key; there is no Neo4j database to run.

## What does it do?

Without this package, every chat session starts fresh — the model has no recollection of who the user is, what they've said before, or what decisions were made in prior conversations.

`@neo4j-labs/nams-ai-provider` wraps your existing AI model and transparently adds memory to every call:

1. **Before the model responds**, the package searches four sources in NAMS for the current message: the current conversation, this user's entities, the reasoning steps stored for the conversation, and the user's other recent conversations. It reads back the relationships of the best entity matches and adds everything to the last user message as one memory block.
2. **After the model responds**, the package saves the user message and the answer to the conversation. NAMS then extracts entities and relationships from them on the server, a few seconds later, so the next session can recall them.

**Whose memory is read.** `scope.userId` is the NAMS `userId` of every
conversation and entity this package writes. Conversation lookups send it to
NAMS, which filters on the server. Entity searches send it too, and the package
also drops any returned entity that belongs to another user. Entities that belong
to no user, such as ones your own ingestion wrote, are read only with
`workspaceGraph: true`. The package does not authenticate anyone: take `userId`
from your signed-in session, never from the request body.

**What happens when NAMS fails.** No mode fails the model call because of NAMS.
A failed read is logged with `logger.warn` and the turn runs without memory. A
failed write is logged with `logger.error` and the response is still returned.
In tools mode the tool returns `found: false` or `stored: false` to the model.
Writes are not retried. A streamed turn is saved when the stream ends, so a
client that stops reading early leaves that turn unsaved.

```
User message
     │
     ▼
┌─────────────────────────────┐
│  NAMS: fetch relevant       │  ← searches long-term graph, past
│  memories + their edges     │    sessions, traces; expands matches
└────────────┬────────────────┘
             │  memories injected into prompt
             ▼
┌─────────────────────────────┐
│  Your LLM (GPT, Claude…)    │  ← responds with full context
└────────────┬────────────────┘
             │  response
             ▼
┌─────────────────────────────┐
│  NAMS: persist the turn     │  ← stores turn; entities are extracted
│                             │    from it server-side
└─────────────────────────────┘
```

## Setup

**1. Install the provider and its peer dependencies**

Requires Node.js 22+ (24+ for the eve entry point). Install the provider with
the core SDK it calls NAMS through:

```bash
npm install @neo4j-labs/nams-ai-provider @neo4j-labs/agent-memory ai@^7 zod@^4
```

The peer dependencies are `ai` `^7.0.0`, `zod` `^3.25.76 || ^4.1.8` and
`@neo4j-labs/agent-memory` `~0.5.0`. This README describes the source, which
needs a provider release newer than 0.3.0 and an SDK release newer than 0.5.0.
SDK 0.5.0 installs but fails against hosted NAMS: it sends `threshold` with
every message search, which NAMS rejects with 400, and sends the conversation
user filter as `user_id`, which NAMS ignores. Two optional peers are needed only for one feature each:
`@ai-sdk/mcp` `^2.0.0` for [MCP tool merging](#tools-mode-with-mcp-optional),
and `eve` `>=0.69.0` for the [eve memory provider](#eve-memory-provider).

**2. Get a free API key** at [memory.neo4jlabs.com](https://memory.neo4jlabs.com)

```env
MEMORY_API_KEY=nams_...
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
  isStepCount,
} from 'ai';

const agent = new ToolLoopAgent({
  model:        openai('gpt-5.4-mini'),
  instructions: 'You are a helpful assistant.',
  stopWhen:     isStepCount(10),
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
  isStepCount,
} from 'ai';

const nams = createNamsProvider({
  apiKey:       process.env.MEMORY_API_KEY!,
  baseProvider: openai,
  scope:        { userId: 'user-123' },  // identify the user
});

const agent = new ToolLoopAgent({
  model:        nams.languageModel('gpt-5.4-mini'),  // ← only change
  instructions: 'You are a helpful assistant.',
  stopWhen:     isStepCount(10),
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
- Relevant memories for `user-123` — and the relationships around them — are fetched and prepended to the prompt
- The model's response is saved back to memory for future sessions
- No other code changes needed

---

## Usage Modes

There are four ways to integrate NAMS depending on how much control you want:

| Mode | How it works | Best for |
|------|-------------|----------|
| **Provider** | Swap your model for a NAMS-wrapped one | Simplest integration, fully transparent |
| **Middleware** | Wrap an existing model instance | When you already have a model configured |
| **Tools** | Expose memory as explicit AI SDK tools (optionally merged with tools from an MCP server) | When you want the model to decide when to remember |
| **Hooks** | The runtime reads/writes the session transcript around every generation via AI SDK hooks — nothing memory-related is shown to the LLM | Applications that explicitly coordinate transcript writes |

Building on [eve](https://eve.dev)? Use the [eve memory provider](#eve-memory-provider)
instead: eve owns the conversation history, so it takes a memory slot, not a
model wrapper.

Switching modes is purely a code-level choice — all four use the same API key
and environment variables (see [Environment variables](#environment-variables)).
To make it a deploy-time choice instead, see
[`examples/mode-switch-chat.ts`](examples/mode-switch-chat.ts): one agent whose
mode is selected by `NAMS_MODE=provider|middleware|tools|hooks`, all four
sharing the same memory backend so memories carry over between runs.
Middleware and tools modes can also be
[combined on one agent](#combining-tools-mode-with-middleware-hybrid) —
injected baseline context plus explicit memory tools.

### This package or the core SDK's middleware?

The core SDK ships its own AI SDK middleware,
`agentMemoryMiddleware` from `@neo4j-labs/agent-memory/middleware/vercel-ai`.
Both implement AI SDK 7's middleware specification v4. They read different
memory:

| | Core `agentMemoryMiddleware` | This package (middleware and provider mode) |
|---|---|---|
| Reads | One conversation: its NAMS reflections and observations as one system message, and its recent messages replayed as turns | A search of the current conversation, this user's entities and their relationships, the conversation's reasoning steps, and the user's other recent conversations, added to the last user message |
| Without `conversationId` | Creates a new conversation on the first call (`userId` defaults to `anonymous`) | Reuses the user's most recent conversation, or creates one |
| Writes | The user message before the call, the answer after it | The user message and the answer after the call |
| Streams | Saved without delaying the stream, also when the client stops reading | Saved when the stream ends; a stream the client stops reading is not saved |
| Failures | Ignored silently | Logged through `logger` |
| Also offers | Nothing else | `ProviderV4`, tools mode, hooks mode, MCP tool merging, the eve provider |

Use the core middleware to replay one conversation's history and summaries.
Use this package to recall across conversations and from the knowledge graph.

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
  isStepCount,
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
  stopWhen:     isStepCount(1),       // no tools needed in provider mode
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
  stopWhen: isStepCount(1),
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
  isStepCount,
} from 'ai';

const nams  = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const model = nams.wrap(openai('gpt-5.4-mini'), { userId: session.userId });

const agent = new ToolLoopAgent({ model, stopWhen: isStepCount(1) });

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
import { createNams, enforceQueryMemory, ensureMemoryStored } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  isStepCount,
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
  onEnd:        async (event) => { await ensureMemoryStored(tools)(event); },
  stopWhen:     isStepCount(10),
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
forced query and the final answer both fit.

### Requesting fallback persistence with `ensureMemoryStored()`

`prepareStep` cannot guarantee the *write* side: the loop ends when the model
emits final text, so there is no later step to force `store_memory` into.
`ensureMemoryStored(tools)` closes the gap after the loop instead. It returns a
function that takes the `onEnd` event and, if the model never called
`store_memory`, persists the turn itself. Call it from your `onEnd` callback:
AI SDK 7 types that callback as returning nothing, and this function returns
a result, so it cannot be passed as `onEnd` directly.

```ts
const tools = nams.tools({ userId: session.userId });

const agent = new ToolLoopAgent({
  model:       openai('gpt-5.4-mini'),
  tools,
  prepareStep: enforceQueryMemory(), // requests retrieval mid-loop
  onEnd: async (event) => {
    const outcome = await ensureMemoryStored(tools)(event); // stores the turn if the model did not
    if (!outcome.stored) console.log('not stored:', outcome.reason);
  },
  stopWhen:    isStepCount(10),
});
```

It returns `{ stored: true, input }` when it wrote, or `{ stored: false, reason }`
where `reason` is `'already-stored'`, `'nothing-to-store'`, or `'failed'` — a
failed write logs and reports rather than throwing into a response that has
already been generated.

By default it stores the assistant's final text as an **`interaction`**
(short-term conversation memory), matching what middleware mode records. It
deliberately does not store it as a `fact`: an agent's summary of what it
remembers is exactly the self-referential input that degrades recall, which is
why the graph extractor skips such entities. Pass `fallback` to decide for
yourself — including returning `null` to store nothing:

```ts
const storeTurn = ensureMemoryStored(tools, {
  // The onEnd event carries no user message; close over your own prompt.
  fallback: ({ text }) => ({ content: `User: ${prompt}\nAssistant: ${text}`, type: 'interaction' }),
});

// in the agent settings:
onEnd: async (event) => { await storeTurn(event); },
```

Pass the tool set object itself, not a copy — `ensureMemoryStored({ ...tools })`
throws immediately, because a spread loses the binding to the memory client.
In provider and middleware mode you need none of this: both directions happen
unconditionally in code.

### Tools Mode with MCP (optional)

Still tools mode — not a separate mode. `toolsWithMcp()` connects to an MCP
server and merges its tools with the NAMS memory tools, so one agent can
remember *and* use external tooling. Requires the optional peer `@ai-sdk/mcp`
(`npm install @ai-sdk/mcp`) — it is loaded lazily, only when an MCP config is
passed.

```ts
import { createNams, enforceQueryMemory } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, isStepCount } from 'ai';

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
  stopWhen:    isStepCount(10),
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
  stopWhen: isStepCount(10),
});
```

In this setup, skip `enforceQueryMemory()` — the middleware already attempts
retrieval unconditionally in code, so forcing `query_memory` as well would
just spend an extra step re-fetching similar context. The enforcement hook is
for pure tools mode, where the tool call is the *only* retrieval path.

---

## Hooks Mode (runtime-controlled) ✅ recommended for production session memory

Tools mode is LLM-controlled: the model decides when to query and store, which
is **not deterministic** — models sometimes skip writes (only part of a turn
gets persisted) or skip reads (the agent forgets earlier turns).
`ensureMemoryStored()` and `enforceQueryMemory()` narrow that gap, but the
transcript is still curated by the model.

Hooks mode removes the model from the loop entirely. Nothing memory-related
appears in the tool schema; instead the runtime restores the transcript before
every generation and persists it after. Every user, assistant, and tool turn
is captured **exactly once per generation**, regardless of what the LLM
decides.

```ts
import { createNams } from '@neo4j-labs/nams-ai-provider';
import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, isStepCount } from 'ai';
import { z } from 'zod';

const nams    = createNams({ apiKey: process.env.MEMORY_API_KEY! });
const session = nams.hooks();   // one instance: loadSession and onFinish() share a client

const agent = new ToolLoopAgent({
  model: openai('gpt-5.4-mini'),
  tools: { /* your domain tools — no memory tools needed */ },

  callOptionsSchema: z.object({
    userId: z.string(),
    conversationId: z.string().optional(),
    prompt: z.string(),
  }),

  // ── PRE hook: restore history and pass the scope to onEnd ──────────────────
  // Strip `prompt` / `messages` from the incoming settings — the AI SDK
  // enforces prompt XOR messages, and we're replacing them with the restored
  // transcript.
  prepareCall: async ({ options, prompt: _p, messages: _m, ...settings }) => ({
    ...settings,
    messages: [
      ...(await session.loadSession(options)),
      { role: 'user', content: options!.prompt },
    ],
    // Per-call scope flows to the construction-time onEnd callback below.
    runtimeContext: options,
  }),

  // ── POST hook: persist every turn exactly once ─────────────────────────────
  onEnd: session.onFinish(),

  stopWhen: isStepCount(10),
});

// Usage
await agent.generate({
  prompt: 'Hi! My name is Alex.',
  options: { userId: 'alice', prompt: 'Hi! My name is Alex.' },
});
```

**What the hooks do:**

| Hook | Method | When it runs | What it does |
|------|--------|--------------|--------------|
| Pre  | `session.loadSession({ userId, conversationId? })` | Inside `prepareCall`, before every LLM call | Reads prior turns from NAMS, returns `ModelMessage[]` you prepend to `messages`. Never creates a conversation; returns `[]` for a new user or on backend errors. |
| Post | `session.onFinish()` | After the full tool loop finishes | Persists the user prompt plus every assistant and tool turn across all steps, in order, in one bulk write. |

`loadSession()` restores user/assistant turns only. Tool turns are stored in
NAMS as metadata-tagged audit records (searchable, auditable) but are not
replayed as provider tool-result blocks, because OpenAI Responses and
Anthropic Messages reject orphan tool results without the matching prior
assistant tool call.

**Why `runtimeContext`?** The agent above sets `onEnd` once, when it is
constructed, so the callback cannot close over each call's `userId`,
`conversationId` and `prompt`. It reads them from the generation's
`runtimeContext`, which `prepareCall` sets. You can bind the scope per call
instead: AI SDK 7 also accepts `onEnd` on the call itself
(`agent.generate({ ..., onEnd: session.onFinish({ userId, prompt }) })`), and
one-shot `generateText` / `streamText` take it the same way:

```ts
const { text } = await generateText({
  model: openai('gpt-5.4-mini'),
  messages: [
    ...(await session.loadSession({ userId })),
    { role: 'user', content: prompt },
  ],
  onEnd: session.onFinish({ userId, prompt }),
});
```

When both are present, `runtimeContext` wins per-call and the closure scope
back-fills whatever it omits. Set `persistUserPrompt: false` if your API
handler already stores the user turn itself.

See [`examples/hooks-chat.ts`](examples/hooks-chat.ts) for the full runnable
example, or run it side by side with the other modes via
`NAMS_MODE=hooks npx tsx examples/mode-switch-chat.ts`.

> **Note:** Hooks mode replaces only the *session transcript* half of memory.
> Long-term memory (facts, preferences, patterns, the entity graph) is
> intentionally left to the other modes — it should be selective and
> content-dependent, not every-turn. Combine hooks with `nams.tools(scope)`
> on the same agent for model-driven long-term writes.

---

## Lifecycle Hooks

`loadSession()` and `onFinish()` cover the transcript. Lifecycle hooks cover
everything around it: gate a prompt, deny a tool call, rewrite tool arguments,
redact a memory write, add context for the next turn. The model will be
familiar from editor hook systems — events, matcher groups, and decision
fields — with plain functions in place of shell commands, so there is no stdin
JSON and no exit codes.

```ts
const session = nams.hooks({
  userId,
  hooks: {
    // A bare function matches every occurrence.
    SessionStart: [({ reason }) => ({ additionalContext: `Session ${reason}.` })],

    // A group adds a matcher: a plain name, `a|b`, or a regex.
    PreToolUse: [{
      matcher: 'delete_account|drop_table',
      hooks: [() => ({
        permissionDecision: 'deny',
        permissionDecisionReason: 'Destructive tools are disabled.',
      })],
    }],

    // Redact before anything reaches the graph.
    PreMemoryWrite: [({ turns }) => ({
      updatedTurns: turns.map(t => ({ ...t, content: redact(t.content) })),
    })],
  },
});
```

### Events

| Event | Fires | Matches on | Can |
|-------|-------|-----------|-----|
| `SessionStart` | First `prepare()` for a scope | `created` \| `resumed` | add context |
| `UserPromptSubmit` | Inside `prepare()`, before history loads | — | **block**, rewrite the prompt, add context |
| `PreToolUse` | Before a wrapped tool runs | tool name | **deny**, rewrite the arguments, add context |
| `PostToolUse` | After the tool returns | tool name | rewrite the result, add context |
| `PostToolUseFailure` | After the tool throws | tool name | ask for one retry, add context |
| `PreMemoryWrite` | Inside `onFinish()`, before saving | — | **block** the write, rewrite the turns |
| `Stop` | After the turn is saved | — | add context |
| `SessionEnd` | `session.end()` | your reason string | add context |

Every handler can also return `systemMessage`, which goes to `onSystemMessage`
or the logger.

### Wiring

| Method | Runs | Notes |
|--------|------|-------|
| `session.prepare({ userId, prompt })` | `SessionStart`, `UserPromptSubmit`, then `loadSession` | Returns `{ messages, instructions, prompt, blocked, blockReason }`. Check `blocked` before calling the model. |
| `session.withHooks(tools)` | `PreToolUse`, `PostToolUse`, `PostToolUseFailure` | Wraps an AI SDK tool set. Returns it untouched when no tool hooks are registered. |
| `session.onFinish()` | `PreMemoryWrite`, then `Stop` | Unchanged otherwise. |
| `session.end({ reason })` | `SessionEnd` | Also forgets the scope, so the next `prepare()` is a new session. |

Hook context reaches the model through `instructions`:

```ts
const prepared = await session.prepare({ userId, prompt });
if (prepared.blocked) return prepared.blockReason;

const { text } = await agent.generate({
  messages: prepared.messages,
  options: { userId, prompt: prepared.prompt, memoryContext: prepared.instructions },
});

// ...and in the agent, prepareCall folds it into the instructions:
prepareCall: ({ options, ...settings }) => ({
  ...settings,
  instructions: [MY_INSTRUCTIONS, options?.memoryContext].filter(Boolean).join('\n\n'),
  runtimeContext: options,
}),
```

### Rules

- **Matchers** omitted or `*` matches everything, plain
  names match exactly, `|` separates alternatives, anything else is a regular
  expression. A bad expression is reported at startup and never matches.
- **Rewrites chain.** Each handler sees the previous handler's replacement, and
  the final value is what the tool, the model, or the write actually gets.
- **The first deny wins** and skips the handlers after it.
- **A hook can never break a generation.** One that throws is logged and
  skipped; one that outruns its timeout, 30 seconds by default, is abandoned.
- **`additionalContext` comes back as `instructions`,** never as a message. The
  AI SDK rejects system messages inside `messages`, so `prepare()` hands you the
  text to append to your own instructions. Context from a tool hook is queued
  for the scope and arrives on the next `prepare()`, capped at the 20 most
  recent entries. Ignore the field and that context is dropped.
- **A denied tool returns `{ blocked: true, toolName, reason }`** to the model
  rather than throwing, so the loop continues and the model can explain itself.
- **`Stop` cannot block.** The generation is already finished when `onFinish()`
  runs. Use `UserPromptSubmit` or `PreToolUse` for control.

See [`examples/advanced-hooks-chat.ts`](examples/advanced-hooks-chat.ts) for a
runnable example covering every event.

---

## eve Memory Provider

[eve](https://eve.dev) owns an agent's memory slots: it locks who a slot's
memory belongs to, asks the slot's provider to recall context before each turn
and capture the turn after it, and gives the model the provider's tools.
`@neo4j-labs/nams-ai-provider/eve` implements eve's
[memory-provider contract](https://eve.dev/docs/memory/custom-provider) with
NAMS. It needs eve 0.69.0 or later and Node.js 24, and is tested on eve 0.69.0,
0.70.3, 0.71.0, 0.75.1, 0.77.0, 0.78.0 and 0.79.0. The entry point ships in the
first provider release after 0.3.0.

```bash
npm install @neo4j-labs/nams-ai-provider @neo4j-labs/agent-memory
```

```ts
// agent/memory/nams.ts
import { namsMemory } from '@neo4j-labs/nams-ai-provider/eve';
import { defineMemory } from 'eve/memory';
import { byPrincipal } from 'eve/memory/scope';

export default defineMemory({
  description: 'Durable facts and earlier sessions for the current user, from Neo4j Agent Memory.',
  provider: namsMemory(), // reads MEMORY_API_KEY (and MEMORY_WORKSPACE_ID if set)
  scope: byPrincipal,
});
```

| eve phase | What the provider does |
|---|---|
| Recall (`turn.started`, `compaction.completed`) | One record, id `nams-memory`: notes saved with `remember`, the summaries NAMS wrote about earlier sessions, messages from them that match the user's message, and the entities NAMS extracted from this user's conversations. Each turn's record replaces the last. |
| Capture (`turn.completed`) | The user's message and final answer, written to one NAMS conversation per eve session. NAMS extracts entities and writes summaries from it. |
| Tools | `nams__search` finds messages in earlier sessions and notes, and this user's entities; `nams__remember` saves a note. Both are bound to the locked scope. |

**Isolation.** Every conversation the provider writes carries eve's opaque
`memory.scope.key` as its NAMS `userId`, and every read starts from that
scope's own conversations. NAMS files the entities it extracts from those
conversations under the same `userId`, and the provider searches entities with
it. The provider also checks the `userId` of every conversation and entity it
reads, so a server that returns other users' data cannot leak it. The raw
principal id never reaches NAMS.

**Replays.** eve can replay a handler with the same `operationId`. Within one
process a replayed recall returns the record from the first call; after a
restart the record is read from NAMS again, so it differs if the scope's memory
changed in between, and eve then fails the turn. A replayed capture is detected
by comparing the end of the session's conversation with the turn, also after a
restart, and is not written twice. Each tool's `execute` is
an eve durable callback whose closure is the scope key, slot and session id:
eve's workflow bundler leaves it intact, and the client and API key never
enter durable state.

**Limits.** Entities written without a `userId`, for example by your own
ingestion, are shared by the whole workspace. They are recalled only with
`workspaceGraph: true`; other users' entities never are. Hosted NAMS cannot
delete a single message, so there is no `forget` tool yet. To erase one
scope's memory, call NAMS `DELETE /v1/memory?userId=<scope key>`.

**Requires an `@neo4j-labs/agent-memory` release newer than 0.5.0.** With
0.5.0 the provider loses most of its memory against hosted NAMS: every message
search sends `threshold`, which NAMS rejects with 400, so recall's matching
messages and `nams__search` come back empty; the conversation listing sends
`user_id`, which NAMS ignores, so the provider sees only the workspace's newest
200 conversations; and entity searches go out without `userId` and come back
without owners, so no entities are recalled. Saved notes and session summaries
still work.

```ts
namsMemory({
  apiKey?:            string,  // Default: MEMORY_API_KEY
  workspaceId?:       string,  // Default: MEMORY_WORKSPACE_ID
  endpoint?:          string,  // Default: https://memory.neo4jlabs.com/v1
  logger?:            NamsLogger,
  crossSessionLimit?: number,  // Earlier sessions read per recall. 0 = off. Default: 3
  maxMemories?:       number,  // Max lines in the recalled record. Default: 8
  capture?:           boolean, // Write completed turns to NAMS. Default: true
  tools?:             boolean, // Offer search and remember. Default: true
  workspaceGraph?:    boolean, // Also recall shared entities (no user). Default: false
});
```

A rejected (401) or missing API key fails the turn; any other NAMS error
during recall, a 403 included, is logged, and the turn goes on with a record
that says memory is unavailable.

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
  crossSessionLimit?:   number,   // Other recent conversations each lookup also searches, 2 requests each. 0 = off. Default: 5
  graphExpansionLimit?: number,   // Matched entities whose relationships are read back, 1 request each. 0 = off. Default: 2
  workspaceGraph?:      boolean,  // Also recall shared entities (no user). Default: false
});
```

`crossSessionLimit`, `graphExpansionLimit` and `workspaceGraph` also apply to
`createNams` (middleware, tools, and hooks modes).

`extractionModel` / `extractionOptions` live on `createNams` and apply to tools
mode only — see [Graph Extraction](#graph-extraction-tools-mode).


### Environment variables

No environment variable selects or changes the mode — provider, middleware,
tools, and hooks modes are chosen entirely in code, and all four read the same
variables:

| Variable | Required | Used for |
|----------|----------|----------|
| `MEMORY_API_KEY` | yes | NAMS API key — pass it as `apiKey` (the examples and snippets read it from the environment; the eve provider reads it when `apiKey` is not passed) |
| `MEMORY_WORKSPACE_ID` | no | NAMS workspace, read by the core SDK when `workspaceId` is not passed |
| `OPENAI_API_KEY` | yes* | Read by `@ai-sdk/openai`; *swap for whichever `@ai-sdk/*` provider key your base model needs |
| `NAMS_DEMO_USER` | no | Overrides the demo `userId` in the [runnable examples](./examples) |
| `NAMS_DEMO_MODEL` | no | Overrides the demo model id (default: `gpt-5.4-mini`) in the [runnable examples](./examples) |

---

## Graph Extraction (tools mode)

Pass `extractionModel` to build a real Neo4j entity graph from long-term
memories instead of storing flat text:

```ts
const nams = createNams({
  apiKey:          process.env.MEMORY_API_KEY!,
  extractionModel: openai('gpt-5.4-mini'),
});

const tools = nams.tools({ userId });
```

A `store_memory` write of type `fact`, `user_preference`, or `pattern` turns
`"User is named Alex, works at TechCorp"` into `(Alex)-[:WORKS_AT]->(TechCorp)`
instead of one sentence-shaped node.

> **Scope:** tools mode only. Middleware, provider, and hooks mode persist
> turns as short-term messages, which NAMS extracts server-side — extracting
> again from the client would just duplicate that work. Setting
> `extractionModel` and using another mode logs a warning.

Extracted entities are stored in the user's memory (`userId` on the write). An
extracted entity that this user already has under the same name and type is
reused rather than created again, so every memory about "Alex" adds its edges
to the same Alex node. Hosted NAMS has no lookup-by-name route, so the match is
made through entity search; because that search is nearest-neighbour, only an
exact name or canonical name is treated as the same entity, and "Alexandra"
stays a separate node.

> **Note:** hosted NAMS does not accept relationship *writes* from a client
> today — `add_relationship` has no REST route, so client-extracted edges are
> skipped with a single warning while the entities themselves are stored. The
> edges the model reads back (see [Graph Retrieval](#graph-retrieval)) are the
> ones NAMS builds server-side from persisted turns. Against a backend that
> does accept them — a self-hosted bridge — the extractor writes its edges
> directly.

Extraction skips **self-referential** entities: when the agent answers "what do
you remember about me?" and stores its own answer, extraction would otherwise
mint entities *about remembering* (`long-term memories [Concept]`,
`profile details [Object]`). Those meta-entities are the closest match for the
next such question, so they outrank the real facts and every ask degrades the
next one. Skipped entities are logged at `warn`.

```ts
extractionOptions: {
  // "preferences" is a real entity here; drop raw identifiers instead.
  skipEntity: (e) => /^[a-z0-9_]+$/.test(e.name),
}
```

---

## Memory Sources

The package searches four sources in NAMS in parallel, once per turn, then expands the
entities that matched into a fifth. In middleware and provider modes, later
steps of a tool loop reuse the turn's memories instead of searching again.

| Source | What it stores |
|--------|---------------|
| Long-term graph | Facts, preferences, patterns: the Neo4j entities in this user's memory |
| Graph | The relationships around the entities that matched — see below |
| Current conversation | Messages in the active session |
| Cross-session | Messages from the user's 5 most recent other conversations (`crossSessionLimit`) |
| Reasoning steps | Steps your app recorded for the current conversation with the core SDK's `client.reasoning` API, where the action is `direct response`. Other conversations' steps come in through cross-session. This package reads them but never writes them. |

Entities are per user. NAMS files the entities it extracts from a conversation
under the conversation's `userId`, and `store_memory` writes them with it too, so
the long-term search asks NAMS for `scope.userId`'s entities and keeps only the
ones that carry that id. Graph expansion asks for relationships within the same
user's memory. `workspaceGraph: true` adds shared entities, the ones with no user
(for example a catalog your own code wrote without a `userId`); they are not
expanded, since their edges can lead into other users' memory. Another user's
entities are never recalled.

NAMS uses vector search where the workspace has embeddings, and a text match
otherwise. For a text match, a question that finds nothing is retried with its
longest words.

The sources are merged by **taking turns**, not by concatenating: with
`maxMemories: 6`, five entity matches cannot fill the prompt and leave one slot
for everything else. Each source keeps the order the backend returned it in.

### Graph Retrieval

This is the part a vector memory cannot do. Entity search returns entities
without their edges, so after the search the top matches are expanded — one
request each — and their stored relationships are added to the prompt as
triples:

```
Relevant long-term memory about this user (use it to personalise your answer):
1. [long-term] Alex — The user
2. [graph] (Alex)-[WORKS_AT]->(TechCorp)
3. [graph] (Alex)-[USES]->(Neovim)
4. [conversation] I need something that runs in the terminal
A [graph] line is a stored relationship, written (subject)-[RELATIONSHIP]->(object).
```

The model is told what the notation means only when a triple is actually
present. In tools mode the same triples come back from `query_memory` as hits
with `source: 'graph'`.

So a question like *"who else works where I work?"* is answerable from one
turn's context: the edge is in the prompt, not left sitting in the database
behind a similarity score.

| Setting | Effect |
|---------|--------|
| `graphExpansionLimit: 2` | Default. The two best entity matches are expanded. |
| `graphExpansionLimit: 0` | No graph reads. Entities stay flat text. |
| `graphExpansionLimit: 4` | Wider graph context, at four extra requests per turn. |

At most five relationships are taken from any one entity, so a hub node cannot
crowd out the rest of the prompt. An edge whose target has no stored name is
dropped rather than shown as a bare id, and an entity that cannot be read is
logged and skipped — the other sources still answer.

---

## Development

```bash
npm ci             # install dev dependencies
npm test           # vitest: provider, middleware, tools, hooks, extraction and eve
npm run typecheck  # tsc --noEmit
npm run build      # tsup → dist/
```

The unit tests replace NAMS with a fake client. To run the eve provider inside
eve's own runtime, use [`examples/eve-memory-eval`](examples/eve-memory-eval),
which installs the SDK and this package from the checkout.

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
