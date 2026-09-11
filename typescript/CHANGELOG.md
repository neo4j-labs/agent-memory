# Changelog

All notable changes to `@neo4j-labs/agent-memory` will be documented in
this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project follows [Semantic Versioning](https://semver.org).

This is a Neo4j Labs project under Beta status — breaking changes may
appear in minor versions with a callout in this file.

## [Unreleased]

### Added

- **`registerMemoryTools`** (`@neo4j-labs/agent-memory/mcp/register`) — registers
  the 12-tool memory surface on a high-level MCP `McpServer` in one call.
  `McpServer.registerTool` validates arguments and therefore requires Zod input
  schemas, which the JSON Schema from `createMemoryTools()` cannot satisfy; this
  new subpath carries the Zod half, plus annotations, an optional `only`
  allow-list and an `onCall` audit hook that reports tool name, argument **keys**
  (never values), duration and outcome. Dispatch failures are returned as
  `isError: true` tool results rather than thrown protocol errors. `zod` and
  `@modelcontextprotocol/sdk` are optional peer dependencies, required only by
  this subpath.
- **MCP tool annotations.** `createMemoryTools()` entries now carry
  `annotations` (`readOnlyHint` / `idempotentHint` / `destructiveHint: false`),
  matching the Python MCP server in this repository. The split is exported as
  `READ_ONLY_MEMORY_TOOLS` and `memoryToolAnnotations(name)` so a self-hosted
  server can apply the same policy to tools it adds.
- **`wrapStream` on the Vercel AI middleware** — `streamText` responses are now
  persisted. The stream is piped through a transform that accumulates every
  `text-delta` and writes one assistant message when `finish` arrives (or when
  the consumer cancels early), fire-and-forget so the stream never waits on a
  memory write. Previously the middleware declared only `transformParams` and
  `wrapGenerate`, so switching from `generateText` to `streamText` silently lost
  all assistant-side persistence.
- **`Neo4jMastraMemory.getThreadById(threadId)`** — reads a thread back through
  `getConversationMetadata`, including the title `createThread` folds into
  conversation metadata.
- **Optional peer dependencies** declared for the type-only framework SDKs:
  `@ai-sdk/provider`, `@modelcontextprotocol/sdk`, `@opentelemetry/api`,
  `@strands-agents/sdk` and `zod`. They were previously devDependencies only,
  even though the published `.d.ts` files reference them.
- **`Neo4jMemoryStore`** (`@neo4j-labs/agent-memory/integrations/strands`) — a
  Strands `MemoryStore` for long-term recall inside the agent loop. Hand it to
  `MemoryManager({ stores: [...] })`. Recall is entities-only, and its
  `get_entity_graph` tool traverses one hop, because the hosted service exposes
  no preference/fact search and no multi-hop traversal.

### Changed

- **BREAKING (type-level): the Vercel AI middleware now implements specification
  v4.** `agentMemoryMiddleware` returns `LanguageModelV4Middleware` from
  `@ai-sdk/provider` 4.x — the specification `ai` 7.x uses — and declares
  `specificationVersion: "v4"`, which it previously did not declare at all.
  `AgentMemoryLanguageModelMiddleware` is now an alias of that type, so the
  `as unknown as` cast adopters needed at the `wrapLanguageModel` boundary can be
  deleted. Projects still on `ai` 4/5/6 must pin `@neo4j-labs/agent-memory`
  0.4.x.
- **BREAKING (behavioural): the Vercel AI middleware now reads and writes the
  real prompt shape.** `transformParams` previously treated
  `params.prompt` as `Array<{ role, content: string }>`. The provider
  specification gives `system` content as a string and `user`/`assistant`
  content as an array of content parts, so the persisted "user message" was
  `[object Object]`-shaped for any non-trivial prompt, and injected history was
  emitted in a shape providers reject. Input is now flattened from its `text`
  parts (a multi-part message joins its text and ignores files), reflections and
  observations are injected as one `system` message, and recent messages are
  replayed as `user`/`assistant` turns with `{ type: "text" }` parts.
- `@strands-agents/sdk` devDependency raised to `^1.16.0` (verified against
  1.17.0); `@ai-sdk/provider` to `^4.0.10`; `@modelcontextprotocol/sdk` to
  `^1.30.0`; `@types/node` to `^22`; `typescript` to `^5.9`; `typedoc` to
  `^0.28`; `vitest` to `^5`. `@mastra/core` `^1.66` added as a devDependency so
  the Mastra gap is type-checked. `engines.node` raised to `>=22` — Node 20 is
  EOL, and `ai` 7 requires 22+.
- `npm run lint` now type-checks `test/` and `conformance/` as well as `src/`
  (via the new `tsconfig.test.json`). Test files were previously outside every
  type-check, which is how the middleware's integration test came to assert the
  wrong prompt shape.
- **`Neo4jMastraMemory` is documented as what it is**: a thin
  thread-and-message-history adapter in Mastra's vocabulary, not a Mastra
  `Memory` or storage adapter. Mastra 1.x takes a custom backend as a storage
  adapter (a `memory` domain extending `MemoryStorage` from
  `@mastra/core/storage`), and two of its nine abstract methods
  (`listMessagesById`, `updateMessages`) have no NAMS equivalent yet. The README
  and how-to page no longer show `new Agent({ memory })`, which never worked.
- The MCP module documentation no longer claims the hosted NAMS MCP server
  exposes "the same tool surface" — the hosted server is larger and scope-gated;
  this subpath is the 12-tool self-hosted subset.
- The deprecated `memory.<verb>` MCP tool aliases are now documented as slated
  for removal in 1.0. The previous note promised removal in 0.3, which passed.
- `Neo4jConversationManager` throws at `initAgent` when paired with a
  `Neo4jMemoryStore` that has `extraction` enabled — both sides would ingest the
  same turns.

### Fixed

- **The Vercel AI middleware no longer re-persists the same user turn on every
  step of a tool loop.** `transformParams` runs once per model call, and in a
  multi-step `generateText` the same user message is still the most recent `user`
  entry in the prompt, so each step wrote it again. The middleware now remembers
  the last user text it wrote (and forgets it if the write failed, so the next
  call retries).
- **`Neo4jMastraMemory.createThread` no longer collapses every thread of a
  resource into one** on transports with no `createConversation`: the
  synthesized id is now `{resourceId}:{uuid}` rather than the bare
  `resourceId`. It also no longer writes an explicit `mastraTitle: undefined`
  key into conversation metadata when no title is given.
- **`connectMemoryToAgent` now asserts the public `ConversationManager` surface**
  before casting to it. Strands' abstract class declares a `protected` member,
  which makes structural assignment impossible from a class we cannot `extend`
  (it arrives through a dynamic import) — but a rename of `name` / `reduce` /
  `initAgent` now fails compilation instead of being swallowed by the cast.
- **`longTerm.expandGraph(...)` over REST.** Its payload was nested under a
  literal `body` key, so the hosted service rejected every call with
  `nodeId is required`. The method has been unusable against `/v1` since it
  landed.
- **`longTerm.addEntity` no longer returns a malformed Entity when the
  hosted service auto-merges the create onto an existing entity.** NAMS
  resolves-before-create: a sufficiently similar name responds
  `{id, resolution: "merged", merged_into, confidence}` with no
  `name`/`type`, which previously flowed into an Entity with `undefined`
  name and type. The client now follows up with `GET /entities/{id}` and
  returns the canonical merged-into entity. Fallback is limited to HTTP 404
  or empty/incomplete canonical responses with a valid merge ID; other errors
  propagate. Fallback entities use a lowercase hosted type and a client-generated
  ISO creation timestamp. The additive optional `Entity.metadata` preserves
  canonical metadata and exposes merge details in `nams_resolution`, including
  a `fallback` flag and separate `merge_confidence`; merge scores no longer
  populate entity confidence. Missing IDs and malformed canonical fields are
  rejected before conversion. Optional null fields are normalized to `undefined`
  on entities, inline relationship references, preferences, facts, and mentions.

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
  under the unified Antora site. TypeDoc API reference is bundled with that
  site — see the
  [TypeScript API reference](https://neo4j.com/labs/agent-memory/reference/typescript-api).
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
