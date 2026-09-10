# @neo4j-labs/agent-memory

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> **Now on npm.** Install with `npm install @neo4j-labs/agent-memory`
> ([package page](https://www.npmjs.com/package/@neo4j-labs/agent-memory)).
> Source lives alongside the Python SDK at
> [`neo4j-labs/agent-memory`](https://github.com/neo4j-labs/agent-memory)
> (relocated from `neo4j-labs/agent-memory-tck`).

> TypeScript client for the Neo4j Agent Memory Service — short-term,
> long-term, and reasoning memory for AI agents, backed by Neo4j.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## ✨ Features

- Three memory subclients in one client: **short-term** (conversations,
  messages, three-tier context), **long-term** (entities, search,
  relationships, graph view), and **reasoning** (steps, traces,
  provenance, tool calls).
- Zero-config construction — reads `MEMORY_API_KEY` from the
  environment and defaults to the hosted service.
- Works in Node 20+, Bun, Deno, Cloudflare Workers, and Vercel Edge.
- Five framework integrations: Vercel AI SDK middleware, MCP tools,
  LangChain JS, Mastra, and AWS Strands Agents.
- Built-in request logging, request-id correlation, and edge-friendly
  `fetch`-only transports.
- TCK Bronze conformance verified by the
  [agent-memory-tck](https://github.com/neo4j-labs/agent-memory-tck)
  cross-language test suite.

## 📦 Installation

```bash
npm install @neo4j-labs/agent-memory
```

Requires Node.js 20+.

## 🚀 Quick start

Get an API key from [memory.neo4jlabs.com](https://memory.neo4jlabs.com),
export it as `MEMORY_API_KEY`, then:

```ts
import { MemoryClient } from "@neo4j-labs/agent-memory";

const client = new MemoryClient();

const conv = await client.shortTerm.createConversation({ userId: "alice" });
await client.shortTerm.addMessage(conv.id, "user", "Hello!");

const entity = await client.longTerm.addEntity("Alice Johnson", "person", {
  description: "Software engineer working on graph memory.",
});

const ctx = await client.shortTerm.getContext(conv.id);
console.log(ctx.recentMessages, ctx.observations, ctx.reflections);
```

### On the edge

Edge runtimes (Cloudflare Workers, Vercel Edge) expose environment
variables via the request handler scope, not `process.env`. Pass the key
explicitly:

```ts
export default {
  async fetch(req: Request, env: { MEMORY_API_KEY: string }) {
    const client = new MemoryClient({ apiKey: env.MEMORY_API_KEY });
    // ...
  },
};
```

## 🧩 Integrations

All four ship as subpath exports. See each integration's
[example](./examples) and how-to guide for a runnable walkthrough.

| Integration | Import | Example |
|---|---|---|
| **Vercel AI SDK** | `@neo4j-labs/agent-memory/middleware/vercel-ai` | [`examples/vercel-ai`](./examples/vercel-ai) |
| **MCP tools** | `@neo4j-labs/agent-memory/mcp` | [`examples/mcp`](./examples/mcp) |
| **LangChain JS** | `@neo4j-labs/agent-memory/integrations/langchain` | [`examples/langchain`](./examples/langchain) |
| **Mastra** | `@neo4j-labs/agent-memory/integrations/mastra` | [`examples/mastra`](./examples/mastra) |
| **AWS Strands** | `@neo4j-labs/agent-memory/integrations/strands` | [`examples/strands`](./examples/strands) |

## 📖 Documentation

- [TypeScript SDK landing page](https://neo4j.com/labs/agent-memory/sdks/typescript)
- [Tutorial: First Agent Memory (TypeScript)](https://neo4j.com/labs/agent-memory/tutorials/first-agent-memory-typescript)
- [How-to guides](https://neo4j.com/labs/agent-memory/how-to/typescript) — authentication,
  edge deployment, error handling, observability, framework integrations
- [Concept: short-term vs long-term vs reasoning memory](https://neo4j.com/labs/agent-memory/explanation/memory-types)
- [Architecture overview](https://neo4j.com/labs/agent-memory/explanation/graph-architecture)
- [Troubleshooting](https://neo4j.com/labs/agent-memory/how-to/typescript/troubleshooting)

Full API reference (TypeDoc) is bundled with the docs site — see the
[TypeScript API reference](https://neo4j.com/labs/agent-memory/reference/typescript-api).

## 🔧 Configuration

The client accepts a small options bag:

```ts
new MemoryClient({
  endpoint: "https://memory.neo4jlabs.com/v1",  // default
  apiKey: "nams_...",                            // falls back to MEMORY_API_KEY env
  timeout: 30_000,                               // ms; default 30s
  headers: { "X-My-Trace": "..." },              // additional request headers
  logger: (event) => console.log(event),         // request/response/error events
});
```

`connect()` is optional — the first request acts as the implicit auth
check. Call it explicitly if you prefer fail-fast at startup.

## 🤝 Contributing

We welcome contributions. See the repo-root
[CONTRIBUTING.md](https://github.com/neo4j-labs/agent-memory/blob/main/CONTRIBUTING.md)
for the development setup, test commands, and PR conventions — there is a
dedicated "TypeScript contributions" section covering Node setup, vitest,
linting, and the TCK bridge.

This package lives alongside the Python SDK
([`neo4j-agent-memory`](https://pypi.org/project/neo4j-agent-memory/)) in
[`neo4j-labs/agent-memory`](https://github.com/neo4j-labs/agent-memory).
The two SDKs implement the same memory model and share the same backend
(NAMS). The cross-language behavioral contract is defined and certified
by the [`agent-memory-tck`](https://github.com/neo4j-labs/agent-memory-tck)
spec repo, which consumes this package from npm.

## 💬 Support

- [Neo4j Community Forum](https://community.neo4j.com) — questions and
  discussion (primary)
- [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) —
  bug reports and feature requests
- Security vulnerabilities: see
  [SECURITY.md](https://github.com/neo4j-labs/agent-memory/blob/main/SECURITY.md)

## 📝 License

Apache-2.0 — see [LICENSE](./LICENSE).
