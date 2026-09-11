# `@neo4j-labs/agent-memory` Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Runnable examples for each integration shipped with
[`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory).
Each one is a self-contained Node project you can copy, install and run
against the hosted [Neo4j Agent Memory Service (NAMS)](https://memory.neo4jlabs.com).

> ⚠️ **Neo4j Labs Project**
>
> These examples are part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. They are actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Prerequisites

- **Node.js 22+** — every example declares `"engines": {"node": ">=22"}`.
  `eve-commerce-agent/` needs **Node 24+** (the `eve` framework requires it).
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com).
  Each example's own test suite runs offline without one.

## The examples

| Folder | What it shows |
|---|---|
| [`nextjs-memory-chat/`](./nextjs-memory-chat) | **Flagship full-stack demo.** Next.js 16 App Router travel assistant on hosted NAMS: `agentMemoryMiddleware` in the route handler, a live entity-graph rail with lazy `expandGraph` on double-click, an extraction-status badge, and a reasoning-trace drawer |
| [`vercel-ai/`](./vercel-ai) | The minimal middleware snippet — memory-augmented chat through the Vercel AI SDK in one script |
| [`mcp/`](./mcp) | An MCP server you run yourself, exposing the SDK's 12 memory tools with Zod-validated inputs and read/write annotations, plus a 13th read-only Cypher console behind an allow-list |
| [`langchain/`](./langchain) | Chat-history and entity-retriever shapes for LangChain JS, plus a NAMS-backed memory module |
| [`mastra/`](./mastra) | Wrapping the client as a Mastra-compatible memory provider, with NAMS threads |
| [`strands/`](./strands) | AWS Strands agent with session persistence, three-tier context, reasoning capture, and a `MemoryStore` script |
| [`ontology-lifecycle/`](./ontology-lifecycle) | The NAMS ontology lifecycle through `OntologyClient` — import an Arrows diagram, activate it, ingest, diff two revisions, then migrate the graph onto a renamed type |
| [`cloudflare-agents-edge/`](./cloudflare-agents-edge) | A Cloudflare Worker with no driver and no Node built-ins — memory over `fetch`, streamed turns persisted with `ctx.waitUntil`, tests that run inside `workerd` |
| [`eve-commerce-agent/`](./eve-commerce-agent) | Agentic commerce on Vercel's [eve](https://eve.dev) framework: a NAMS-backed eve **memory provider** gives a shopping assistant a shopper profile that survives the session, plus a reasoning trace per tool call and a human-approved checkout (Node 24+) |

The Python gallery — twenty-five examples including the bolt backend, the
framework integrations and four full-stack apps — is at
[`../../examples/`](../../examples/).

## Running an example

```bash
cd nextjs-memory-chat
cp .env.example .env       # then edit .env with your MEMORY_API_KEY
npm install
npm run dev                # or `npm start` for the single-script examples
```

Each example's own README has the full step-by-step.

## Notes

- These examples use `"@neo4j-labs/agent-memory": "file:../.."` to
  depend on the in-tree SDK during development — so contributors can
  iterate on both without an npm publish round-trip. When using one of
  these examples as a template for your own project, replace the
  `file:` path with the published version:
  ```
  "@neo4j-labs/agent-memory": "^0.4.1"
  ```
- **Lockfile policy.** Example lockfiles are committed where they exist, and
  CI installs with `npm ci` when it finds one and `npm install` otherwise. A
  committed lockfile buys reproducibility; an example without one acts as an
  early-warning drift detector against its frameworks' latest releases. Do not
  delete a committed lockfile to "float" an example — say so in the example's
  README instead.
- Every example is **type-checked in CI** by the `type-check-examples`
  matrix in [`.github/workflows/ci-typescript.yml`](../../.github/workflows/ci-typescript.yml),
  against a freshly built `typescript/dist/` — drift between an example and
  the SDK's public API fails the PR that introduces it. The same job runs each
  example's `npm test` (all suites are offline). Runtime execution
  (`npm start`, `npm run dev`) still needs the API keys you provide locally.
- `make ts-test-examples` from the repository root mirrors that matrix.

## Contributing a TypeScript example

1. Create `typescript/examples/<name>/` with
   `"@neo4j-labs/agent-memory": "file:../.."`,
   `"engines": {"node": ">=22"}`, and caret-pinned frameworks at their
   current major.
2. Extend [`tsconfig.base.json`](./tsconfig.base.json) instead of
   re-declaring compiler options, and override only what your runtime needs
   (a Next.js or Workers example needs its own `module`/`jsx` settings).
3. Ship `lint` (`tsc --noEmit`) and `test` scripts; keep the suite offline so
   it runs with no API key.
4. Write a README with the Labs badges, the disclaimer, prerequisites, run
   steps, expected output, a support section and a "verified against" footer.
5. Add the directory to the `type-check-examples` matrix in
   `.github/workflows/ci-typescript.yml`, and add a row to the table above.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [TypeScript SDK documentation](https://neo4j.com/labs/agent-memory/sdks/typescript)

## License

Apache 2.0 — see the main `neo4j-agent-memory` repository for details.

---

_Verified against [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory) 0.4.1 (the in-tree `typescript/package.json` version and the current npm release) on Node 22 and 24, on 2026-09-10._
