# Next.js memory chat

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

The flagship TypeScript demo: a deployable **Next.js 16** App Router travel
assistant whose entire memory — transcript, entities, reasoning trail — lives in
the hosted [Neo4j Agent Memory Service](https://memory.neo4jlabs.com). One
`MEMORY_API_KEY` and one `OPENAI_API_KEY` is the whole setup. No Neo4j to
operate, no vector store, no database migrations.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs, backward-compatibility guarantees,
> or scheduled deprecation commitments. APIs may change without notice. For questions and support, please use the
> [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **Memory is one wrapped model.** `lib/handlers.ts` wraps `openai(…)` in a
  `createNamsProvider(...)` from `@neo4j-labs/nams-ai-provider` and calls
  `streamText`. After that line there is no memory-specific code in the
  request path: retrieval, user-turn persistence and assistant-turn
  persistence (streamed included) are the provider's job. Before each call the
  provider searches with the new question — messages in this conversation,
  matching entities and their graph relationships, and messages in the
  user's other conversations — and also reads back reasoning steps recorded
  with `actionTaken: 'direct response'` in this and the user's recent
  conversations, which are not matched against the question (this app's own
  steps use `generate_answer`, so the provider skips them). It prepends at
  most `maxMemories` (default 6) of those hits. It does not replay recent
  turns, observations or reflections.
  The SDK's own `agentMemoryMiddleware` (see
  [How-to: Vercel AI SDK](https://neo4j.com/labs/agent-memory/how-to/typescript/vercel-ai))
  does replay a conversation's reflections, observations and recent messages;
  use it instead when that continuity matters more than cross-session,
  graph-expanded search.
- **The browser is not the source of truth.** The route forwards *only the newest
  user turn* to the model; anything earlier reaches the model only when the
  provider's retrieval brings it back from the graph. Reload the tab, or open the
  same `/c/<id>` URL on another device, and the thread is intact — the panel
  rehydrates from `shortTerm.getContext`.
- **The memory rail is the argument.** Entities appear on the right from the
  traveller's own sentences. Double-click one and
  `longTerm.expandGraph(nodeId, loadedIds)` fetches *only* the neighbours not
  already on the canvas, so the visualisation grows lazily instead of re-sending
  a subgraph you are already looking at. `expandGraph` only makes sense with a
  UI; this is its home.
- **Asynchronous extraction is made visible, not hidden.** NAMS writes a message
  immediately and extracts its entities in a background pipeline. The badge shows
  that window: the rail awaits `longTerm.waitForExtraction` with a predicate
  ("resolve when an entity I have not seen appears") and only *then* refetches
  the graph. That beats a hopeful `setTimeout`.
- **"Why did it answer that?"** The chat route records one reasoning step per
  answered turn in its `onEnd` callback; the trace drawer reads them back with
  `reasoning.getTraceByConversation`. An audit trail without inventing a store
  for it.
- **Conversation id in the URL.** `/c/<conversationId>` is the only handle on a
  thread, and it is shareable and resumable because every message behind it is in
  NAMS.

## Screenshots

[SCREENSHOT PLACEHOLDER] `docs/images/nextjs-memory-chat-rail.png` — the chat on
the left after three turns, the rail on the right showing 1 observation, 6 recent
messages, the green "new entities" badge, and an eight-node graph.

[SCREENSHOT PLACEHOLDER] `docs/images/nextjs-memory-chat-expand.png` — the same
graph immediately after double-clicking **Kyoto**, with the newly fetched
neighbours attached.

[SCREENSHOT PLACEHOLDER] `docs/images/nextjs-memory-chat-trace.png` — the
reasoning-trace drawer open, one step per answered turn.

## Architecture

```
app/
  page.tsx                     mint a conversation → redirect to /c/<id>
  c/[conversationId]/page.tsx  the app (Next 16: params is a promise)
  api/chat/route.ts            createNamsProvider + streamText
  api/conversation/route.ts    shortTerm.createConversation
  api/memory/context/route.ts  shortTerm.getContext        → the three tiers
  api/memory/graph/route.ts    GET  longTerm.getEntityGraph
                               POST longTerm.expandGraph(nodeId, loadedIds)
  api/memory/extraction/route  longTerm.waitForExtraction(predicate)
  api/memory/trace/route.ts    reasoning.getTraceByConversation
components/
  Workspace, ChatPanel, MemoryRail, GraphView, ExtractionBadge, TraceDrawer
lib/
  memory.ts    the MemoryClient, the provider config, the model, the user id (server-only)
  handlers.ts  every route's behaviour as (deps, Request) => Response
  types.ts     the DTOs shared by the routes and the components
```

Route files are four-line wrappers; the behaviour lives in `lib/handlers.ts` as
plain `(deps, Request) => Response` functions. That split is what lets
`test/routes.test.ts` exercise the real handlers against a mocked service without
booting Next.

## Prerequisites

- **Node.js 22+** — `ai` 7 is ESM-only and requires it; Node 20 is EOL.
- A **`MEMORY_API_KEY`** from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
  (keys are prefixed `nams_`).
- An **`OPENAI_API_KEY`**. The model id comes from `OPENAI_MODEL` and defaults to
  `gpt-5-mini`.
- Optionally a **`MEMORY_WORKSPACE_ID`** to scope the conversations and entities
  to one workspace.

## Streaming persistence boundary

`createNamsProvider`'s stream wrapper awaits the assistant-turn write as part of
closing the response stream, so a fully-drained client stream implies the write
was attempted — not skipped, unlike a plain fire-and-forget write. It is still
best-effort: a failed write is logged and swallowed, not surfaced to the caller,
and nothing in the response tells the client whether it succeeded. For
application-owned writes whose completion and errors the server observes, follow
the [Cloudflare lifecycle example](../cloudflare-agents-edge/README.md) and adapt
its runtime hook to your deployment. That example awaits the write in
`streamText`'s `onEnd` and logs a failure on the server; the client still is not
told. If you move persistence into your own code, pass
`persistInteractions: false` to `createNamsProvider`. That flag turns off the
provider's writes for **both** the user turn and the assistant turn, so your
explicit write must store both. (The Cloudflare example uses the SDK
middleware's `persistResponses: false`, which turns off only the assistant
write.)

## Build the shared packages first

This is a source-checkout example. Its `file:../..` and
`file:../../packages/vercel-ai-provider` dependencies require the repository
layout. In an application (not this checkout) these are two separate npm
installs — `@neo4j-labs/nams-ai-provider@0.3.0` (the provider `lib/handlers.ts`
wraps the chat model in) and `@neo4j-labs/agent-memory@0.5.0` (the SDK the
memory rail's routes call directly). This example links both to the packages in
this repository instead. The memory rail's routes therefore run against the
in-tree SDK. The provider link is a symlink, so the provider loads
`@neo4j-labs/agent-memory` from its own `node_modules`: the npm release it
installs as a development dependency, not the in-tree SDK. From the repository
root:

```bash
cd typescript
npm ci
npm run build
cd packages/vercel-ai-provider
npm ci
npm run build
cd ../../examples/nextjs-memory-chat
```

Run the commands below from `typescript/examples/nextjs-memory-chat/`. Build **both
packages before** installing this example; their exports point at
`typescript/dist/` and `typescript/packages/vercel-ai-provider/dist/`, and npm
does not build local `file:` dependencies on installation. For standalone
copies, follow the [copy checklist](../README.md#copying-an-example), install
`@neo4j-labs/nams-ai-provider@0.3.0` and `@neo4j-labs/agent-memory@0.5.0` from
npm instead of the `file:` links, and verify the selected artifacts supply
every API used here.

## Run it

```bash
cp .env.example .env.local     # set MEMORY_API_KEY and OPENAI_API_KEY
npm ci
npm run dev                    # http://localhost:3000
```

Opening `/` creates a conversation and redirects to `/c/<id>`. Ask the three
suggested questions in order — the third ("What did I say about transport?") can
only be answered from memory, because the request that carries it contains
nothing but that sentence.

A one-liner, if you would rather not write a file:

```bash
MEMORY_API_KEY=nams_xxxxxxxxxxxx OPENAI_API_KEY=sk-xxxx npm run dev
```

### Seed a populated rail (optional)

An empty canvas is a poor first impression for a graph-memory demo. The seed
script writes a prior trip-planning conversation (15 messages, 8 entities), waits
for extraction, and prints the URL to open:

```bash
MEMORY_API_KEY=nams_xxxxxxxxxxxx npm run seed
```

It needs no model key — extraction and embeddings run server-side.

### Deploy

```bash
npx vercel deploy
```

Set `MEMORY_API_KEY`, `OPENAI_API_KEY` and (optionally) `MEMORY_WORKSPACE_ID` as
project environment variables. Nothing else changes: there is no database to
provision, and Turbopack is the default builder (this example adds no custom
webpack config, which Next 16 would reject).

## Expected output

`npm run seed`:

```
Created conversation 6b21f0ac-… for traveller@example.com
Wrote 15 messages
  Tokyo (LOCATION)
  Kyoto (LOCATION)
  Kanazawa (LOCATION)
  Osaka (LOCATION)
  Nikko (LOCATION)
  Japan Railways (ORGANIZATION)
  Shigetsu (ORGANIZATION)
  Japan Rail Pass (OBJECT)
Extraction caught up.
Graph now holds 11 node(s), 4 edge(s)

Open http://localhost:3000/c/6b21f0ac-…
```

In the browser, after the three suggested turns:

- the rail reads **0 reflections · 1 observation · 6 recent messages** — what
  NAMS stores for the conversation, not what the next model call receives (that
  is the provider's search hits for the new question);
- the badge turns green (**new entities**) and reports how many entities matched
  the last turn;
- the graph holds the places and organisations from your own sentences, and
  double-clicking one attaches its neighbours;
- the trace drawer lists one `generate_answer` step per turn.

Reflection and observation counts climb as NAMS processes the conversation in the
background. Exact model wording varies; the structure does not.

## Tests

```bash
npm test          # vitest
npm run lint      # eslint . && tsc --noEmit
npm run build     # next build (Turbopack)
```

`test/routes.test.ts` runs every route handler with **no API key and no
network**:

- NAMS is a mock REST service served by [msw](https://mswjs.io)
  (`test/nams-server.ts`), driven through the *real* `MemoryClient`,
  `RestTransport` **and** `createNamsProvider` from `@neo4j-labs/nams-ai-provider`
  — so a wrong URL, verb or body shape fails the suite, and any endpoint the
  example starts calling without a handler answers 501 rather than passing
  silently;
- the model is the AI SDK's own `MockLanguageModelV4` from `ai/test`, which
  records every call it receives.

The assertions are the ones that fail if memory stops working: both sides of a
turn are persisted, the prompt carries memory the request never sent —
conversation history retrieved by the provider's search, plus a matching
entity's graph relationship — `expandGraph` receives the accumulated
`loadedIds` (and the delta excludes what is already loaded), the extraction
probe waits for an entity it has not seen before the rail refetches, and the
answered turn lands in the reasoning trace. A source-level check also asserts
no `node:` imports reach `app/`, `components/` or `lib/`, so the app stays
edge-deployable.

## Where this sits

| If you want… | Go to |
|---|---|
| The full app — rail, graph expansion, trace drawer, deploy | **this example** |
| The smallest correct middleware wiring, in one file | [`../vercel-ai`](../vercel-ai) |
| A provider wrapper, memory tools and `enforceQueryMemory` | [`../../packages/vercel-ai-provider`](../../packages/vercel-ai-provider) |
| Options, streaming semantics, edge-runtime notes | [How-to: Vercel AI SDK](https://neo4j.com/labs/agent-memory/how-to/typescript/vercel-ai) |
| The same app explained step by step | [How-to: Build a Next.js memory chat app](https://neo4j.com/labs/agent-memory/how-to/typescript/nextjs-memory-chat) |

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [TypeScript SDK reference](https://neo4j.com/labs/agent-memory/sdks/typescript)
- [Vercel AI SDK docs](https://sdk.vercel.ai)
- [Neo4j Visualization Library](https://neo4j.com/docs/nvl/current/)

---

_Compatibility scope: this example targets the current source checkout and its committed package/lock files. Offline tests validate the exercised contracts; they do not establish published-package availability, a live model result, or deployed NAMS behavior. Use the runtime floor above; record the actual package/runtime versions when verifying a release or deployment._
