# eve memory eval

Runs `@neo4j-labs/nams-ai-provider/eve` inside eve's own runtime, with no API
keys: a scripted `mockModel` stands in for the LLM, and a local fake of the
hosted NAMS REST API stands in for NAMS. Like hosted NAMS, the fake filters
`GET /conversations`, `POST /messages/search` and `POST /entities/search` on
`userId` and rejects any request field it does not know. It extracts nothing, so the eval adds the
entities NAMS would have extracted.

One eval, [`evals/memory.eval.ts`](evals/memory.eval.ts), checks that:

1. the model can call `nams__remember`,
2. capture writes the turn to a conversation whose `userId` is eve's scope key,
   each message tagged with the eve operation that wrote it,
3. a new session's recall puts the saved note, this user's entities and the
   relationships around them in front of the model, and not another user's,
4. every entity search, and every search across the user's conversations,
   carries the user's scope key,
5. `nams__search` finds the note, the earlier session, the user's entities
   and their relationships,
6. `nams__forget` deletes the note and the user's entity, not another user's,
   and a new session's record has neither, and
7. NAMS rejected no request.

The provider's `eve` peer range (`>=0.84.0 <1.0.0 || ^1.0.0-rc.0`) admits every
eve 1.x release, so run it on each new one before relying on it.

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
✓  memory  gates 28/28

Results: 1 passed (1 total)
```

The provider needs `@neo4j-labs/agent-memory` 0.6.0. Against 0.5.x only 18 of
the 28 checks pass: 0.5.x sends `threshold` with every message search, the fake
rejects it as hosted NAMS does, and `nams__search` comes back empty. It also
drops the user filter from entity searches, so no entities are recalled (and
none of another user's either).

SDK 0.6.0 cannot search a user's messages in one request, so the provider
searches each conversation and the eval log reports `searches across the
user's conversations: 0`. With an SDK that supports
`searchMessages(query, { userId })`, the same 28 checks pass and that count is
above 0.

The fake accepts `purge` on a conversation delete but does not need it, so
`nams__forget` passes on SDK 0.6.0 too; the log's `forget requests` line shows
`?purge=extracted` only with an SDK whose `deleteConversation` takes `purge`.

To try another eve version, change `eve` in `package.json`, move
`.eve/.workflow-data` aside (another eve version refuses to read it), then run
`npm install` and `npx eve eval` again.

After rebuilding the provider or the SDK, delete `package-lock.json` and
`node_modules/@neo4j-labs` before `npm install`. The lock (not saved in git)
records the copies npm made, and `npm install` keeps them, so the eval would
run against the earlier build.

Without the eval's setup, [`agent/memory/nams.ts`](agent/memory/nams.ts)
talks to hosted NAMS: set `MEMORY_API_KEY` and swap `mockModel` in
[`agent/agent.ts`](agent/agent.ts) for a real model to chat with it.
