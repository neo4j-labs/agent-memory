# Changelog

All notable changes to `@neo4j-labs/agent-memory` will be documented in
this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project follows [Semantic Versioning](https://semver.org).

This is a Neo4j Labs project under Beta status — breaking changes may
appear in minor versions with a callout in this file.

## [Unreleased]

### Changed

- `@strands-agents/sdk` devDependency raised to `^1.13.0` (TS memory landed in 1.6.0).

## 0.4.0 — NAMS alignment

Adds workspace addressing, a first-class ontology surface
(`client.ontology`), a `conversationId` alias on the short-term methods,
and an explicit `longTerm.waitForExtraction(...)` await for the
asynchronous NAMS extraction pipeline. First release cut from the
polyglot `neo4j-labs/agent-memory` repository, published under
namespaced `typescript-v*` tags.

### Added

- **Workspace addressing.** `MemoryClientOptions.workspaceId` (and the
  `MEMORY_WORKSPACE_ID` environment variable) is transmitted automatically as
  the `X-Workspace-Id` header on every request — required by header-scoped
  deployments (e.g. the development/staging service). An explicit
  `X-Workspace-Id` entry in `headers` wins; unset is harmless on production.
- **Ontology surface (`client.ontology`).** `list()`, `get()`, `getActive()`,
  `clone()`, `create()`, `update()`, `activate()`, `delete()` over the NAMS
  domain-ontology engine, with typed models (`OntologySummary`,
  `OntologyVersion`, `OntologyDocument`, …) and `permissive`/`strict`
  validation modes. `getActive()` surfaces the active version's
  `validationMode`.
- **`conversationId` alias** on short-term methods (`addMessage`,
  `getConversation`, `searchMessages`, `clearSession`) as an alias for
  `sessionId` (`sessionId` wins).
- **`longTerm.waitForExtraction(...)`** — await the asynchronous NAMS
  extraction pipeline explicitly (polls entity search for `expectedNames` /
  a `predicate`; returns a boolean).

### Changed

- Repository moved from
  [`neo4j-labs/agent-memory-tck`](https://github.com/neo4j-labs/agent-memory-tck)
  (source SHA `4603b91f`) to
  [`neo4j-labs/agent-memory`](https://github.com/neo4j-labs/agent-memory),
  alongside the Python SDK. No code changes — `package.json` repository
  and homepage fields point at the new location.
- Documentation is now served from
  [neo4j.com/labs/agent-memory/](https://neo4j.com/labs/agent-memory/)
  under the unified Antora site. TypeDoc API reference is published at
  [neo4j-labs.github.io/agent-memory/typescript/](https://neo4j-labs.github.io/agent-memory/typescript/).
- Release tags are now namespaced as `typescript-v*` (e.g.
  `typescript-v0.3.0`); the Python SDK uses `python-v*`.

### Fixed

- `npm run lint` now invokes only `tsc --noEmit`. The previous script
  chained `eslint src/` but `eslint` was never in `devDependencies` and
  no `.eslintrc*` / `eslint.config.*` ever existed — the second half
  silently failed on TCK CI and now blocks the new TypeScript CI. Adding
  eslint properly (config + rule choices + likely codebase touch-ups) is
  deferred to a follow-up PR.

## 0.3.0 — Beta launch

Beta launch release for the hosted-service-first TypeScript client.

### Added

- `MemoryClient` with `shortTerm`, `longTerm`, `reasoning`, `query`, and
  `auth` subclients
- Featured framework integrations:
  - `@neo4j-labs/agent-memory/middleware/vercel-ai` — Vercel AI SDK
    middleware with three-tier context injection and automatic
    persistence
  - `@neo4j-labs/agent-memory/mcp` — the 12 standard MCP tool
    definitions plus a dispatcher
  - `@neo4j-labs/agent-memory/integrations/langchain` —
    `Neo4jChatMessageHistory` and `Neo4jEntityRetriever` (duck-typed)
  - `@neo4j-labs/agent-memory/integrations/mastra` — `Neo4jMastraMemory`
    provider (duck-typed)
  - `@neo4j-labs/agent-memory/integrations/strands` — AWS Strands Agents
    integration: `Neo4jSessionStorage` (SnapshotStorage backend),
    `Neo4jConversationManager` (three-tier context injection layered on
    top of an inner manager), `registerReasoningHooks` + `connectMemoryToAgent`
    factory. Compatible with `@strands-agents/sdk@^1.2.0`.
- `@neo4j-labs/agent-memory/testing` — `BridgeTransport` for TCK
  conformance testing
- Zero-config construction: defaults endpoint to
  `https://memory.neo4jlabs.com/v1`; reads `MEMORY_API_KEY` from
  environment
- Lazy `connect()`: the first request acts as the implicit auth check;
  explicit `connect()` is supported for fail-fast startups
- Auto User-Agent header with caller override
- `requestId` propagated onto every `MemoryError` from the
  `x-request-id` (or equivalent) response header
- `logger` constructor option emitting typed `request` / `response` /
  `error` events
- Edge runtime support: Cloudflare Workers, Vercel Edge (with explicit
  `apiKey` pattern)
- TCK Bronze conformance via the polyglot test suite

### Breaking

- `BridgeTransport` is no longer exported from the package root. Import it
  from `@neo4j-labs/agent-memory/testing` instead.
- Deprecated `HttpTransport` / `HttpTransportOptions` aliases were removed.
