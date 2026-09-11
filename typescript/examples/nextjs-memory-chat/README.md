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
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use the
> [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **Memory is one wrapped model.** `app/api/chat/route.ts` wraps
  `openai(…)` with `agentMemoryMiddleware` and calls `streamText`. After that
  line there is no memory-specific code in the request path: context injection,
  user-turn persistence and assistant-turn persistence (streamed included) are
  the middleware's job.
- **The browser is not the source of truth.** The route forwards *only the newest
  user turn* to the model; the history the model sees comes back out of the graph.
  Reload the tab, or open the same `/c/<id>` URL on another device, and the
  thread is intact — the panel rehydrates from `shortTerm.getContext`.
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
  api/chat/route.ts            wrapLanguageModel + agentMemoryMiddleware + streamText
  api/conversation/route.ts    shortTerm.createConversation
  api/memory/context/route.ts  shortTerm.getContext        → the three tiers
  api/memory/graph/route.ts    GET  longTerm.getEntityGraph
                               POST longTerm.expandGraph(nodeId, loadedIds)
  api/memory/extraction/route  longTerm.waitForExtraction(predicate)
  api/memory/trace/route.ts    reasoning.getTraceByConversation
components/
  Workspace, ChatPanel, MemoryRail, GraphView, ExtractionBadge, TraceDrawer
lib/
  memory.ts    the MemoryClient, the model, the user id (server-only)
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

## Run it

```bash
cp .env.example .env.local     # set MEMORY_API_KEY and OPENAI_API_KEY
npm install
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

- the rail reads **0 reflections · 1 observation · 6 recent messages** — exactly
  what the next model call will have prepended to it;
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
  (`test/nams-server.ts`), driven through the *real* `MemoryClient` and
  `RestTransport` — so a wrong URL, verb or body shape fails the suite, and any
  endpoint the example starts calling without a handler answers 501 rather than
  passing silently;
- the model is the AI SDK's own `MockLanguageModelV4` from `ai/test`, which
  records every call it receives.

The assertions are the ones that fail if memory stops working: both sides of a
turn are persisted, the prompt carries history the request never sent,
`expandGraph` receives the accumulated `loadedIds` (and the delta excludes what
is already loaded), the extraction probe waits for an entity it has not seen
before the rail refetches, and the answered turn lands in the reasoning trace. A
source-level check also asserts no `node:` imports reach `app/`, `components/` or
`lib/`, so the app stays edge-deployable.

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

_Verified against @neo4j-labs/agent-memory 0.6.0-dev (in-tree `file:../..`),
next 16.3.4, react 19.3.0, ai 7.0.97, @ai-sdk/openai 4.0.65, @ai-sdk/react
4.0.100, @chakra-ui/react 3.37.0, @neo4j-nvl/react 1.2.2, msw 2.15.0, vitest
5.0.0, Node 22+ — 2026-09-10._
