# NAMS AI provider examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Four runnable programs, one per integration mode of
[`@neo4j-labs/nams-ai-provider`](../../packages/vercel-ai-provider) — the
Vercel AI SDK community provider that adds persistent cross-session memory to
any language model, backed by the hosted
[Neo4j Agent Memory Service (NAMS)](https://memory.neo4jlabs.com). Each
program is also embedded verbatim on a documentation page; see
`test/docs-pages.test.ts`.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs, backward-compatibility guarantees,
> or scheduled deprecation commitments. APIs may change without notice. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## The programs

| File | Mode | Shows |
|---|---|---|
| `src/provider-mode.ts` | Provider | `createNamsProvider`, `scope`, `maxMemories`, `persistInteractions` — swap the model, memory is transparent |
| `src/middleware-mode.ts` | Middleware | `createNams().wrap(model, scope)` — wrap a model you already configured |
| `src/tools-mode.ts` | Tools | `createNams().tools`, `enforceQueryMemory`, `ensureMemoryStored` — the model decides when to read/write memory |
| `src/hooks-mode.ts` | Hooks | `createNams().hooks({ hooks })` — your code drives `prepare`/`onFinish`; lifecycle hooks cover all eight events |

Each program reads `MEMORY_API_KEY` and throws a clear error if it is unset,
reads the model id from `NAMS_DEMO_MODEL` (default `gpt-5.4-mini`), and prints
what it stored and what it recalled.

## Where this sits

| If you want… | Go to |
|---|---|
| The smallest correct middleware wiring against the SDK's own built-in middleware | [`typescript/examples/vercel-ai`](../vercel-ai) |
| A registrable `ProviderV4`, retrieval policies, explicit tools, MCP tool merging, lifecycle hooks | **this example** and [`@neo4j-labs/nams-ai-provider`](../../packages/vercel-ai-provider) |

## Prerequisites

- Node.js 22+ (the declared runtime floor)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
- An `OPENAI_API_KEY` (override the model with `NAMS_DEMO_MODEL`)

## Build the shared packages first

This is a source-checkout example. Its `file:../..` and
`file:../../packages/vercel-ai-provider` dependencies, and the shared
`../tsconfig.base.json`, require the repository layout. From the repository
root:

```bash
cd typescript
npm ci
npm run build
cd packages/vercel-ai-provider
npm ci
npm run build
cd ../../examples/nams-ai-provider
```

Run the commands below from `typescript/examples/nams-ai-provider/`. Build
**before** installing this example — package exports point at `typescript/dist/`
and `typescript/packages/vercel-ai-provider/dist/`, and npm does not build a
local dependency on installation. For standalone copies, follow the
[copy checklist](../README.md#copying-an-example) and verify the selected npm
artifacts supply every API used here.

## Run it

```bash
if [ ! -e .env ]; then
  (umask 077; set -C; cat .env.example > .env)
fi
chmod 600 .env
npm ci
```

Edit the private `.env` with `MEMORY_API_KEY` and `OPENAI_API_KEY`, then run
any of the four scripts:

```bash
npm run provider-mode
npm run middleware-mode
npm run tools-mode
npm run hooks-mode
```

Each script loads `.env` itself (`tsx --env-file-if-exists=.env`). A missing
`MEMORY_API_KEY` fails immediately with a named error rather than a transport
401.

## Expected output

Provider and middleware mode teach a fact in one session/turn and recall it in
a fresh one for the same user:

```
--- Session 1 -- teach it something
user:      Hi! My name is Alex and I work at TechCorp on the graph platform team.
assistant: Nice to meet you, Alex! ...

--- Session 2 -- fresh session, same user
user:      Where do I work, and what team am I on?
assistant: You work at TechCorp, on the graph platform team.
```

Tools mode prints each tool call as it happens, then the fallback-storage
outcome and the final answer. Hooks mode runs four turns: an allowed tool call,
a flaky tool the hooks retry, a denied tool call, and a card number the hooks
redact. The `Stop` hook prints a `[hook] saved N turns` line after every turn
and the `SessionEnd` hook prints one `[hook] session ended` line at the end.
The other hooks print nothing themselves; their `systemMessage` text goes to
the provider's logger as `[nams] ...` warnings. Exact wording varies with the
model; the structure does not.

## Tests

```bash
npm test
```

Two things are checked:

1. **Doc-page fidelity.** `test/docs-pages.test.ts` asserts each program is
   embedded byte-for-byte on its how-to page
   (`docs/modules/ROOT/pages/how-to/typescript/nams-ai-provider-<mode>-mode.adoc`).
   Whenever a program and its page drift apart, this half of the suite fails
   with a diff; re-embed the program on the page after editing it here.
2. **Offline behavior.** Each program runs against the offline hosted-service
   stub shared with `typescript/examples/vercel-ai`
   ([`../vercel-ai/test/docs-hosted-stub.ts`](../vercel-ai/test/docs-hosted-stub.ts))
   and the AI SDK's own `MockLanguageModelV4` from `ai/test` — no API key, no
   network, no Neo4j. The assertions are the ones that fail if memory stops
   working: the second session/turn's prompt (or its `query_memory` result)
   carries what the first one stored, and the stub recorded the underlying
   NAMS calls. Hooks mode checks the effect of each of the eight lifecycle
   events: the denied tool's blocked result, the retried tool's output, the
   carried-over context, the redacted prompt and transcript, and the `Stop` and
   `SessionEnd` log lines.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [`@neo4j-labs/nams-ai-provider`](../../packages/vercel-ai-provider) — the
  package these programs exercise, including its own runnable examples
- [`typescript/examples/vercel-ai`](../vercel-ai) — the SDK's own built-in
  middleware, for comparison
- [Vercel AI SDK docs](https://sdk.vercel.ai)

---

_Compatibility scope: this example targets the current source checkout and its committed package/lock files. Offline tests validate the exercised contracts; they do not establish published-package availability, a live model result, or deployed NAMS behavior. Use the runtime floor above; record the actual package/runtime versions when verifying a release or deployment._
