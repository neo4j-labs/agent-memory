# Vercel AI SDK example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

The minimal wiring for `agentMemoryMiddleware` — the
[Vercel AI SDK](https://sdk.vercel.ai) (`ai` 7) language-model middleware
shipped by `@neo4j-labs/agent-memory` — against the hosted
[Neo4j Agent Memory Service](https://memory.neo4jlabs.com).

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **One wrapped model is the whole integration.** `wrapLanguageModel({ model, middleware: agentMemoryMiddleware(client, { conversationId }) })`
  — after that, every `generateText` / `streamText` call in the file is plain AI
  SDK code with no memory-specific lines.
- **Nothing is held in process.** Turn two's prompt contains turn one only
  because the middleware read it back out of the graph (specification-v4
  `transformParams`), and the assistant turns are persisted for it by
  `wrapGenerate` and `wrapStream`.
- **Streaming is persisted too.** Turn three uses `streamText` and prints tokens
  as they arrive; `wrapStream` accumulates the deltas and writes one assistant
  message when the stream finishes.
- **Re-runs resume.** The script resolves a conversation (from
  `CONVERSATION_ID`, else the newest one it created for this user) instead of
  always creating one, so a second run continues the first.
- **It prints what the graph actually holds** — the reflection, observation and
  recent-message counts that will be injected on the next call, a preference
  recalled across sessions, and the entities NAMS extracted in the background.

## Where this sits

| If you want… | Go to |
|---|---|
| The smallest correct middleware wiring | **this example** |
| A provider wrapper, memory tools, `enforceQueryMemory`, a Next.js route handler | [`typescript/packages/vercel-ai-provider`](../../packages/vercel-ai-provider) (`@neo4j-labs/nams-ai-provider`) and its [four examples](../../packages/vercel-ai-provider/examples) |
| Options, streaming semantics and edge-runtime notes | [How-to: Vercel AI SDK](https://neo4j.com/labs/agent-memory/how-to/typescript/vercel-ai) |

## Prerequisites

- Node.js 22+ (`ai` 7 requires it; Node 20 is EOL)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
- An `OPENAI_API_KEY` (override the model with `OPENAI_MODEL`)

## Run it

```bash
cp .env.example .env       # set MEMORY_API_KEY and OPENAI_API_KEY
npm install
npm start
```

`npm start` loads `.env` itself (`tsx --env-file-if-exists=.env`). A missing key
fails immediately with a named error rather than a transport 401.

Run it again to see cross-session recall — either bare (it finds the
conversation it created last time) or with the id it printed:

```bash
CONVERSATION_ID=<printed id> npm start
```

## Expected output

```
Created conversation 9f3c1b7e-… for vercel-ai-demo-user

[user] Hi! I'm building a recommendation engine for board games. I love euro-style games.
[assistant] Nice — euro-style games give you dense, low-luck signal to model …

[user] What kind of dataset would you use?
[assistant] Start from a BoardGameGeek ratings dump: user ratings, weights …

[user] Given what I just told you about my preferences, suggest a starter project.
[assistant] Build an item-item recommender over euro-style titles first …

Injected on the next call: 0 reflection(s), 1 observation(s), 6 recent message(s)
Preferences recalled across sessions: 1
  [games] Prefers euro-style board games with low randomness
Entities extracted from the conversation: 2
  Euro-style board games (OBJECT)
  BoardGameGeek (ORGANIZATION)

Re-run with CONVERSATION_ID=9f3c1b7e-… to continue this conversation.
```

A second run opens with `Resumed conversation 9f3c1b7e-…` and the recent-message
count keeps climbing. Exact wording varies with the model; the structure does
not. Reflection and observation counts grow as NAMS processes the conversation
in the background, and entity extraction is asynchronous — the script waits for
it with `longTerm.waitForExtraction` rather than racing a fixed delay.

## Tests

```bash
npm test
```

Runs the whole script against a fake NAMS transport (`test/fake-nams.ts`) and
the AI SDK's own `MockLanguageModelV4` from `ai/test` — no API key, no network,
no Neo4j. The mock records every call it receives, so the test asserts that turn
two's prompt carried turn one's text (which the script never passed in), that
the streamed turn was persisted as one assistant message, and that a second run
resumes the first conversation.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [How-to: Vercel AI SDK](https://neo4j.com/labs/agent-memory/how-to/typescript/vercel-ai)
- [`@neo4j-labs/nams-ai-provider`](../../packages/vercel-ai-provider) — provider,
  memory tools and a Next.js route handler built on the same service
- [Vercel AI SDK docs](https://sdk.vercel.ai)

---

_Verified against @neo4j-labs/agent-memory 0.5.0-dev (in-tree), ai 7.0.97,
@ai-sdk/openai 4.0.65, @ai-sdk/provider 4.0.13, Node 22+ — 2026-09-10._
