# AWS Strands example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Two runnable [AWS Strands](https://strandsagents.com) agents whose memory lives
in a [Neo4j Agent Memory Service](https://memory.neo4jlabs.com) graph: one wires
transcript persistence, context injection and reasoning capture
(`src/index.ts`), the other wires long-term recall through Strands'
`MemoryStore` (`src/memory-store.ts`).

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## `npm start` — session, context and reasoning (`src/index.ts`)

One `connectMemoryToAgent` call wires three orthogonal Strands extension points
at once:

- **`Neo4jSessionStorage`** — Strands' session snapshots persist into a NAMS
  conversation, so the transcript outlives the process. Real turns become
  `Message` graph nodes; the rest of the framework's state is stashed in
  synthetic marker messages you filter with `isSyntheticStrandsMessage`.
- **`Neo4jConversationManager`** — the conversation's reflections and
  observations are prepended before every model call.
- **`registerReasoningHooks`** — every invocation becomes a reasoning step and
  every tool call a tool-call node, so the run is auditable afterwards. A toy
  `lookup_fact` tool is included so the captured trace has tool calls in it.

**It resumes rather than restarts.** The script reuses the newest conversation
for `DEMO_USER_ID` (or the one named by `CONVERSATION_ID`) and prints which id to
re-run with. That is what makes recall across runs real: both session storage and
context injection are conversation-scoped, so a fresh conversation per run would
have nothing to recall.

**It reads back what it wrote.** The run ends by fetching — not describing — the
stored transcript, the reasoning trace with its tool calls, the three-tier
context counts, and the entities NAMS extracted from the conversation
(after `longTerm.waitForExtraction()`, because extraction is asynchronous).

## `npm run start:memory-store` — long-term recall (`src/memory-store.ts`)

`Neo4jMemoryStore` implements Strands' `MemoryStore`, so `MemoryManager` searches
the graph before each model call and folds the hits into the turn. This is the
preferred way to give a Strands agent memory that outlives one conversation.

Recall is **entities-only**: the hosted service exposes entity search but no
preference or fact search endpoint, so those knobs are absent from the options
type rather than silently ignored. Writes go through `addMessages`, which NAMS
extracts server-side — no extra model call. The two scripts are independent and
can be combined on one agent.

## Prerequisites

- Node.js 22+ (Node 20 is EOL)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
- An `OPENAI_API_KEY`, or `MODEL_PROVIDER=bedrock` with AWS credentials

## Run it

```bash
cp .env.example .env       # set MEMORY_API_KEY and OPENAI_API_KEY
npm install
npm start                  # session + context + reasoning
npm run start:memory-store # long-term recall via MemoryStore
```

`npm start` loads `.env` itself (`tsx --env-file-if-exists=.env`). A missing
`MEMORY_API_KEY` or provider key fails immediately with a named error rather
than a transport 401 three turns in.

## Expected output

```
Created conversation 0f4c… for strands-demo-user

user> I'm evaluating graph databases for a recommendation engine. Neo4j is top of my list.
agent> Neo4j stores relationships as first-class records …

user> Use the lookup_fact tool to find one more thing about Neo4j.
agent> Graphs model relationships natively, so multi-hop traversals stay cheap …

user> Summarize what we've discussed so far.
agent> You are evaluating Neo4j for a recommendation engine …

NAMS holds 8 conversation message(s) for this conversation (+4 synthetic Strands
state marker(s), filtered with isSyntheticStrandsMessage):
  [user] Use the lookup_fact tool to find one more thing about Neo4j.
  [assistant] Graphs model relationships natively …

Reasoning trace: 6 step(s), 2 tool call(s)
  [step] agent invocation started -> invoke_agent
  [tool] lookup_fact({"topic":"Neo4j"}) success

Three-tier context injected before each model call: 1 reflections, 1 observations, 6 recent messages
Entities extracted from the transcript: 2
  Neo4j (concept)
  recommendation engine (concept)

Re-run with CONVERSATION_ID=0f4c… (or the same DEMO_USER_ID=strands-demo-user) to see the agent recall this run.
```

Exact wording varies with the model; the structure does not. Reflection and
observation counts grow as NAMS processes the conversation in the background.

## Tests

```bash
npm test
```

Runs both scripts against a fake NAMS transport (`test/fake-nams.ts`) and a
scripted Strands model provider (`test/stub-model.ts`) — no API key, no network,
no Neo4j. The stub records every prompt it is handed, so the tests assert that
the injected context and the recalled entity actually reached the model, that
the reasoning trace carries a `lookup_fact` tool call, and that a second run
resumes the first conversation with its transcript restored from the graph.

## Using Amazon Bedrock instead of OpenAI

Strands' first-class pairing is AWS Bedrock. It is a runtime branch, not an
editing step — nothing to uncomment:

```bash
MODEL_PROVIDER=bedrock AWS_REGION=us-east-1 npm start
```

`src/index.ts` then builds `new BedrockModel({ modelId: ... })` from
`@strands-agents/sdk/models/bedrock` (a separate export entry from
`@strands-agents/sdk/models/anthropic`), defaulting to
`global.anthropic.claude-sonnet-4-6` and overridable with `BEDROCK_MODEL_ID`.
`@aws-sdk/client-bedrock-runtime` ships as a dependency of the Strands SDK, so
there is nothing extra to install; credentials come from the usual AWS chain.
Because both branches live in the source, CI type-checks the Bedrock path too.

## Notes on the dependencies

- `openai` is pinned at `^6.45`, not `^7`: `@strands-agents/sdk` 1.17 declares
  `openai@^6.45.0` as an optional peer, so installing 7.x fails `npm install`
  with `ERESOLVE`. Revisit when Strands widens that peer range.
- `.npmrc` sets `install-links=true`, so the `file:../..` SDK is installed as a
  real copy instead of a symlink. That leaves exactly one resolved
  `@strands-agents/sdk` in the tree, which is what lets `src/index.ts` spread
  `connectMemoryToAgent`'s result straight into `new Agent({ ... })` with no
  `as unknown as` casts. After editing `typescript/src`, rebuild the SDK
  (`npm --prefix ../.. run build`) and re-run `npm install` here.
- Because of that copy, `package-lock.json` inlines the SDK and its entry must
  keep `"resolved": "file:../.."`. Without it `npm ci` installs the *published*
  `@neo4j-labs/agent-memory` of the same version number instead of the local
  build, and the example type-checks against an older surface. If that happens,
  delete `package-lock.json` and `node_modules`, then run `npm install`.

## Known issue

`src/snapshot-compat.ts` works around a collision between the two surfaces:
`Neo4jConversationManager` injects context as plain objects, while Strands 1.17's
`takeSnapshot()` calls `msg.toJSON()` on every message, so the snapshot
`Neo4jSessionStorage` persists throws as soon as there is context to inject. The
fix belongs in the SDK's `conversation-manager.ts`; delete the shim and its call
in `src/index.ts` once that ships.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [How-to: Strands integration](https://neo4j.com/labs/agent-memory/how-to/typescript/strands)
- [Strands Agents SDK docs](https://strandsagents.com)

---

_Verified against @neo4j-labs/agent-memory 0.4.1 (in-tree; `Neo4jMemoryStore` and
`waitForExtraction` are not on npm yet), @strands-agents/sdk 1.17.0, openai
6.49.0, Node 22+ — 2026-09-10._
