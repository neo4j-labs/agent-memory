# Cloudflare Workers agent on the edge

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

A memory-augmented chat agent that runs **entirely inside a Cloudflare Worker** —
no Neo4j driver, no bolt socket, no Node built-ins. The whole memory layer is the
hosted [Neo4j Agent Memory Service](https://memory.neo4jlabs.com) reached over
`fetch`, which is the only thing an edge isolate can reach a database with.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use the
> [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **Why a hosted backend exists.** A Worker isolate cannot open a TCP connection,
  so `bolt://` is not an option — not slow, *impossible*. Hosted NAMS is HTTPS, so
  the same `MemoryClient` you use on Node works unchanged at the edge.
- **The SDK is genuinely edge-safe, and this proves it twice.** `npm run build`
  bundles for `workerd` with **no `nodejs_compat` flag** (see `wrangler.jsonc`),
  and `npm test` runs the suite *inside* `workerd` via
  `@cloudflare/vitest-pool-workers`. A static `node:` import anywhere in the
  dependency graph fails both. That makes this example a regression test as much
  as a demo — it is the canary behind
  [How-to: deploy to edge runtimes](https://neo4j.com/labs/agent-memory/how-to/typescript/edge-runtime).
- **Bindings, not `process.env`.** Workers populate secrets on the `env` argument
  of `fetch(request, env, ctx)` — there is nothing at module scope. Every client
  here is built per request from `env`, and a missing secret fails with a message
  naming `wrangler secret put`, not a NAMS 401 you have to reverse-engineer.
- **`waitUntil`, not fire-and-forget.** `agentMemoryMiddleware` persists the
  assistant turn without awaiting it, which is right on a long-lived Node process
  and a coin flip on Workers. This example sets `persistResponses: false`, writes
  the turn in `streamText`'s `onEnd`, and hands that promise to
  `ctx.waitUntil` — the one genuinely edge-specific change in the file, and the
  one the test suite asserts.
- **Memory costs are measured, not guessed.** Each response carries a
  `Server-Timing: nams;dur=…;desc="N requests"` header, summed from the SDK's own
  `logger` events. You can see what context assembly costs per turn without
  instrumenting anything.
- **Reasoning is recorded too.** Every turn writes a reasoning step and its tool
  call, so `GET /memory` answers "why did it say that" for a deployment you cannot
  attach a debugger to.

## Routes

| Route | What it does |
|---|---|
| `POST /chat` | One memory-augmented turn, streamed. Body `{ message, conversationId? }`; the id comes back on `X-Conversation-Id`. |
| `GET /memory?conversationId=…` | The three-tier context that will be injected next turn, plus the conversation's reasoning trace. |
| `GET /graph` | The entity graph NAMS extracted in the background (`longTerm.getEntityGraph`). |
| `GET /graph?entity=Kyoto` | That entity's 1-hop neighbourhood (`longTerm.expandGraph`) — the delta-shaped call a graph view uses on node expand. |

Note on scope: the hosted graph endpoints are **workspace**-scoped, not
conversation-scoped. To ask "which conversations mentioned this entity", use
`longTerm.getEntityHistory(entityId)`.

## Where this sits

| If you want… | Go to |
|---|---|
| The smallest correct middleware wiring, on Node | [`../vercel-ai`](../vercel-ai) |
| A full Next.js app with a memory rail and a graph view | [`../nextjs-memory-chat`](../nextjs-memory-chat) |
| This, but on the edge, with the runtime's constraints made explicit | **this example** |

## Prerequisites

- Node.js 22+ (to run `wrangler` and the test suite; the Worker itself runs on
  `workerd`)
- A Cloudflare account for `wrangler deploy` — **not** needed for `wrangler dev`,
  `npm test` or `npm run build`, which are all local
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
  (starts with `nams_`)
- An `OPENAI_API_KEY` (override the model with the `OPENAI_MODEL` var in
  `wrangler.jsonc`)

## Run it

```bash
npm install
cp .dev.vars.example .dev.vars    # then put your real keys in .dev.vars
npm run dev                       # wrangler dev on http://localhost:8787
```

`.dev.vars` is this Worker's `.env`: `wrangler dev` loads it and exposes each
entry on `env`. It is gitignored — never commit real values.

Then, in another terminal:

```bash
# First turn — read the conversation id off the response headers
curl -i -N http://localhost:8787/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"I am planning five days in Kyoto in April. I hate crowds."}'

# Second turn — pass the id back; send no history
curl -N http://localhost:8787/chat \
  -H 'Content-Type: application/json' \
  -d '{"conversationId":"<id from X-Conversation-Id>","message":"Where should I stay?"}'

# What the next turn will see, and why the last one answered as it did
curl -s "http://localhost:8787/memory?conversationId=<id>" | jq

# What NAMS extracted from the conversation in the background
curl -s http://localhost:8787/graph | jq
curl -s 'http://localhost:8787/graph?entity=Kyoto' | jq
```

The second `curl` is the point of the example: the request body carries one
sentence and no history, and the answer still knows about April, five days and
the crowds — because the Worker read them back out of the graph.

### Deploy it

Secrets are never written to `wrangler.jsonc` (its contents are committed
plaintext). Set them out-of-band:

```bash
npx wrangler secret put MEMORY_API_KEY        # nams_…
npx wrangler secret put OPENAI_API_KEY
npx wrangler secret put MEMORY_WORKSPACE_ID   # optional, multi-workspace keys only
npx wrangler deploy
```

To run against the live service from a shell instead of a browser, the same key
is all you need:

```bash
MEMORY_API_KEY=nams_… npx wrangler dev        # or put it in .dev.vars
```

## Expected output

First turn (`curl -i -N`):

```
HTTP/1.1 200 OK
content-type: text/plain; charset=utf-8
x-conversation-id: 0b4e7f21-3c9a-4d16-9f2e-5a8c1d7b6e03
server-timing: nams;dur=118;desc="2 requests"

April in Kyoto is peak sakura season, so "five days without crowds" is mostly a
scheduling problem rather than a destination one …
```

Second turn, with only `{"conversationId": …, "message":"Where should I stay?"}`
in the body:

```
Given the five days in April and how you feel about crowds, I'd base you north
of the centre rather than in Gion …
```

`GET /memory?conversationId=…`:

```json
{
  "conversationId": "0b4e7f21-3c9a-4d16-9f2e-5a8c1d7b6e03",
  "willBeInjectedNextTurn": {
    "reflections": [],
    "observations": ["Traveller is planning five days in Kyoto in April and dislikes crowds."],
    "recentMessages": [
      { "role": "user", "content": "I am planning five days in Kyoto in April. I hate crowds." },
      { "role": "assistant", "content": "April in Kyoto is peak sakura season …" }
    ]
  },
  "reasoning": {
    "steps": [
      {
        "id": "…",
        "reasoning": "Answered from the conversation's stored context rather than a prompt the client had to re-send.",
        "actionTaken": "generate_reply"
      }
    ],
    "toolCalls": [{ "toolName": "generate_reply", "status": "success", "durationMs": 2143 }]
  }
}
```

Exact wording varies with the model; the structure does not. Reflections and
observations appear as NAMS processes the conversation in the background, and
entity extraction is asynchronous — `GET /graph` may be empty on the first call
and populated a few seconds later.

### What memory costs per turn

The `Server-Timing` header is summed from the SDK's `logger` events, so it is
measured rather than estimated. A turn makes these NAMS round-trips:

| Turn | NAMS requests during the response | NAMS requests after it (`waitUntil`) |
|---|---|---|
| First in a conversation | 3 — create conversation, read context, read flat history (the context is empty, so the middleware falls back once) | 3 — assistant message, reasoning step, tool call |
| Every later turn | 1 — read context | 3 — assistant message, reasoning step, tool call |

Only the first column is on the user's critical path. To build the latency table
for *your* colo and workspace, read the header:

```bash
curl -s -o /dev/null -D - http://localhost:8787/chat \
  -H 'Content-Type: application/json' \
  -d '{"conversationId":"<id>","message":"and in May?"}' | grep -i server-timing
```

## Build proof

```bash
npm run build     # npx wrangler deploy --dry-run --outdir dist
```

This is the edge assertion, not a formality: it bundles `src/index.ts` for
`workerd` with `compatibility_flags: []`, so a static Node built-in anywhere in
the graph is a build failure rather than a production surprise. Current output:

```
Total Upload: 1873.82 KiB / gzip: 311.91 KiB
```

Most of that is the AI SDK, not the memory client. To check the bundle yourself:

```bash
grep -E '(from|import)[[:space:]]*\(?"node:' dist/index.js   # → no matches
```

(You will find a handful of `node:` strings passed to guarded dynamic loaders
inside `ai`'s optional telemetry paths. Those are never resolved at build time
and never reached at runtime here, which is why the bundle loads in an isolate
with no `nodejs_compat` flag.)

## Tests

```bash
npm test          # vitest, running inside workerd
```

19 tests, no API key, no network, no Neo4j. They run in the real Workers runtime
via `@cloudflare/vitest-pool-workers`, so loading the SDK at all is part of the
assertion. `test/fake-nams.ts` stubs `globalThis.fetch` and answers the hosted
REST API's own routes — deliberately *not* a fake `Transport`, because the
transport is the thing under test. The model is the AI SDK's own
`MockLanguageModelV4`.

The assertions that fail if memory stops working:

- turn two's prompt carries turn one's text and NAMS's observation, neither of
  which the request body contained;
- nothing at all is written to memory before the handler returns — so the single
  `waitUntil` promise is what makes the turn durable;
- the exact NAMS request trace, each call bearing `Authorization: Bearer nams_…`;
- no `node:` import and no Node global anywhere under `src/`.

Two version notes, both deliberate:

- this example pins `vitest` **4.x** because `@cloudflare/vitest-pool-workers`
  0.22 requires it (the other examples here are on 5.x; each has its own
  `node_modules`, so they do not collide);
- `wrangler.jsonc` pins `compatibility_date` to a date *older* than today,
  because the `workerd` build bundled with the test pool refuses a date it does
  not recognise. Any date the test runtime accepts is also valid in production.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [How-to: deploy to edge runtimes](https://neo4j.com/labs/agent-memory/how-to/typescript/edge-runtime)
- [How-to: Vercel AI SDK](https://neo4j.com/labs/agent-memory/how-to/typescript/vercel-ai)
- [Cloudflare Workers docs](https://developers.cloudflare.com/workers/)
- [Workers Vitest integration](https://developers.cloudflare.com/workers/testing/vitest-integration/)

---

_Verified against @neo4j-labs/agent-memory 0.6.0-dev (in-tree), ai 7.0.97,
@ai-sdk/openai 4.0.65, @ai-sdk/provider 4.0.13, wrangler 4.131.0,
@cloudflare/workers-types 5.20260911.1, @cloudflare/vitest-pool-workers 0.22.0,
vitest 4.1.11, TypeScript 5.9.3, Node 22+ — 2026-09-10._
