# eve memory eval

Runs `@neo4j-labs/nams-ai-provider/eve` inside eve's own runtime, with no API
keys: a scripted `mockModel` stands in for the LLM, and a local fake of the
hosted NAMS REST API stands in for NAMS. Like hosted NAMS, the fake filters
`GET /conversations` on `userId` and rejects any request field it does not
know.

One eval, [`evals/memory.eval.ts`](evals/memory.eval.ts), checks that:

1. the model can call `nams__remember`,
2. capture writes the turn to a conversation whose `userId` is eve's scope key,
3. a new session's recall puts the saved note in front of the model,
4. `nams__search` finds the note and the earlier session, and
5. NAMS rejected no request.

Run it before widening the provider's `eve` peer range to a new eve minor.

## Run

Node.js 24 or newer. Build the provider first; `.npmrc` sets
`install-links=true`, so npm packs it instead of symlinking it and the app
loads a single copy of eve.

```bash
cd typescript/packages/vercel-ai-provider
npm ci && npm run build
cd examples/eve-memory-eval
npm install
npm run typecheck
npx eve eval
```

Expected:

```
✓  memory  gates 11/11

Results: 1 passed (1 total)
```

With `@neo4j-labs/agent-memory` 0.5.0 it fails 8/11: that version sends
`threshold` with every message search, the fake rejects it as hosted NAMS
does, and `nams__search` comes back empty. It passes with the SDK release
that drops the field.

To try another eve version, change `eve` in `package.json`, then run
`npm install` and `npx eve eval` again.

Without the eval's setup, [`agent/memory/nams.ts`](agent/memory/nams.ts)
talks to hosted NAMS: set `MEMORY_API_KEY` and swap `mockModel` in
[`agent/agent.ts`](agent/agent.ts) for a real model to chat with it.
