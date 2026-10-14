# eve memory eval

Runs `@neo4j-labs/nams-ai-provider/eve` inside eve's own runtime, with no API
keys: a scripted `mockModel` stands in for the LLM, and a local fake of the
hosted NAMS REST API stands in for NAMS. Like hosted NAMS, the fake filters
`GET /conversations` and `POST /entities/search` on `userId` and rejects any
request field it does not know. It extracts nothing, so the eval adds the
entities NAMS would have extracted.

One eval, [`evals/memory.eval.ts`](evals/memory.eval.ts), checks that:

1. the model can call `nams__remember`,
2. capture writes the turn to a conversation whose `userId` is eve's scope key,
3. a new session's recall puts the saved note and this user's entities in
   front of the model, and not another user's,
4. every entity search carries the user's scope key,
5. `nams__search` finds the note, the earlier session and the user's
   entities, and
6. NAMS rejected no request.

The provider's `eve` peer range is open (`>=0.69.0`), so run it on each new
eve minor.

## Run

Node.js 24 or newer. The app installs the SDK and the provider from this
checkout, so build both first. `.npmrc` sets `install-links=true`, so npm
packs them instead of symlinking them and the app loads a single copy of eve.

```bash
cd typescript
npm ci && npm run build
cd packages/vercel-ai-provider
npm ci && npm run build
cd examples/eve-memory-eval
npm install
npm run typecheck
npx eve eval
```

Expected:

```
✓  memory  gates 17/17

Results: 1 passed (1 total)
```

Against `@neo4j-labs/agent-memory` 0.5.0 only 10 of the 17 checks pass. That
is the published release, and also this checkout's SDK source until the next
SDK release's fixes are merged. 0.5.0 sends `threshold` with every message search,
the fake rejects it as hosted NAMS does, and `nams__search` comes back empty.
It also drops the user filter from entity searches, so no entities are
recalled (and none of another user's either).

To try another eve version, change `eve` in `package.json`, move
`.eve/.workflow-data` aside (another eve version refuses to read it), then run
`npm install` and `npx eve eval` again.

Without the eval's setup, [`agent/memory/nams.ts`](agent/memory/nams.ts)
talks to hosted NAMS: set `MEMORY_API_KEY` and swap `mockModel` in
[`agent/agent.ts`](agent/agent.ts) for a real model to chat with it.
