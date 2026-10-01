# Provider package examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> **Neo4j Labs project.** Actively maintained but not officially supported.
> No SLAs, backward-compatibility guarantees or scheduled deprecation commitments;
> APIs may change without notice. Use the [Community Forum](https://community.neo4j.com).

One runnable example per integration mode — provider, middleware, tools, and
hooks — plus a mode switcher and a Next.js route template. All examples need a
free NAMS API key from [memory.neo4jlabs.com](https://memory.neo4jlabs.com) and
an OpenAI key (swap in any `@ai-sdk/*` provider if you prefer). No other
environment changes are needed to switch modes — mode selection is code-level.
All examples need Node 22 or newer.

```bash
export MEMORY_API_KEY='nams_...'
export OPENAI_API_KEY='sk-...'
npx tsx examples/basic-chat.ts
```

| Example | Mode | Run |
|---------|------|-----|
| [`basic-chat.ts`](./basic-chat.ts) | Provider (transparent memory) — teaches a fact in session 1, recalls it in session 2 | `npx tsx examples/basic-chat.ts` |
| [`middleware-chat.ts`](./middleware-chat.ts) | Middleware — wraps an existing model instance; a fresh model in turn 2 recalls turn 1 | `npx tsx examples/middleware-chat.ts` |
| [`tools-chat.ts`](./tools-chat.ts) | Tools (model-driven) — `query_memory` / `store_memory` visible as tool calls; retrieval guaranteed via `enforceQueryMemory()` | `npx tsx examples/tools-chat.ts` |
| [`hooks-chat.ts`](./hooks-chat.ts) | Hooks (runtime-controlled) — the transcript is restored and saved around every generation, with no memory tools | `npx tsx examples/hooks-chat.ts` |
| [`advanced-hooks-chat.ts`](./advanced-hooks-chat.ts) | Lifecycle hooks — blocks a destructive tool, redacts a card number, carries context between turns | `npx tsx examples/advanced-hooks-chat.ts` |
| [`mode-switch-chat.ts`](./mode-switch-chat.ts) | The same conversation in whichever mode you pick | `NAMS_MODE=hooks npx tsx examples/mode-switch-chat.ts` |
| [`nextjs-chat-route.ts`](./nextjs-chat-route.ts) | Provider inside a Next.js App Router endpoint — copy into `app/api/chat/route.ts` | n/a (template) |

Expected-output comments illustrate a successful live run, not fixed model
wording or guaranteed service extraction. Inspect stored conversation ids and
readback before concluding that a response was persisted. Avoid logging keys.

Run `npm test` and `npm run typecheck` from this directory for the package's
offline checks. The package test configuration and typecheck scope define what
those checks cover; a script's illustrative output is not a live test result.

For a standalone application, import `@neo4j-labs/nams-ai-provider` from a verified
artifact, satisfy its peers and use its supported runtime. See the
[main README](../README.md) for mode boundaries and the core middleware comparison.

Report issues in [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues).
