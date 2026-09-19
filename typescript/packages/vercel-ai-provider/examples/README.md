# Provider package examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> **Neo4j Labs project.** Actively maintained but not officially supported.
> No SLAs, backward-compatibility guarantees or scheduled deprecation commitments;
> APIs may change without notice. Use the [Community Forum](https://community.neo4j.com).

Five scripts demonstrate provider, middleware, tools, hooks and mode selection;
a sixth file is a Next.js route template. These run against provider source and
a configured SDK artifact. They do not establish npm publication or live-service
behavior. Use Node 22+ for the current core SDK dependency, a NAMS test-workspace
key and an OpenAI key for the live model examples.

From the repository root, [build the source packages](../README.md#setup), then
remain in `typescript/packages/vercel-ai-provider/`. That procedure installs the
built in-tree core SDK without changing the package's declared release dependency.
The scripts import provider source; they still need those installed dependencies.

```bash
export MEMORY_API_KEY='nams_...'
export OPENAI_API_KEY='sk-...'
npx tsx examples/basic-chat.ts
```

| File | Purpose | Run from the provider package directory |
|---|---|---|
| [basic-chat.ts](./basic-chat.ts) | Provider wrapper; teach and recall using explicit scopes | `npx tsx examples/basic-chat.ts` |
| [middleware-chat.ts](./middleware-chat.ts) | Wrap an existing model | `npx tsx examples/middleware-chat.ts` |
| [tools-chat.ts](./tools-chat.ts) | Model-visible memory tools with a retrieval policy hook | `npx tsx examples/tools-chat.ts` |
| [hooks-chat.ts](./hooks-chat.ts) | Application hooks around model calls | `npx tsx examples/hooks-chat.ts` |
| [mode-switch-chat.ts](./mode-switch-chat.ts) | Choose among four modes with `NAMS_MODE` | `npx tsx examples/mode-switch-chat.ts` |
| [nextjs-chat-route.ts](./nextjs-chat-route.ts) | Route template; integrate with a configured Next.js application | Copy into the app's route and supply its surrounding request/UI code |

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
