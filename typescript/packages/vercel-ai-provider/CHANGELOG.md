# Changelog

Releases before 0.3.0 predate this file; see the
[GitHub releases](https://github.com/neo4j-labs/agent-memory/releases) for those.

### Added

- **eve memory provider.** `namsMemory()` from the new
  `@neo4j-labs/nams-ai-provider/eve` entry point backs an
  [eve](https://eve.dev) memory slot with NAMS, following eve's
  memory-provider contract. Recall returns one keyed record before each turn
  (and after compaction): notes saved with `remember`, NAMS summaries of the
  scope's earlier sessions, and messages from them that match the user's
  message. Capture writes each completed turn to one NAMS conversation per eve
  session. The model gets `search` and `remember`, bound to the locked scope.
  Every conversation carries eve's `memory.scope.key` as its NAMS `userId`,
  and reads also check it, in case the server returns other users'. The
  workspace-wide entity graph is read only with `workspaceGraph: true`. A
  replayed recall returns the same record; a replayed capture writes nothing,
  also after a restart. Tool callbacks are durable and keep no secrets.
- `eve` (`~0.69.0 || ~0.70.0 || ~0.71.0`) is a new optional peer dependency,
  needed only for the `/eve` entry point. The main entry point never loads it.
  `examples/eve-memory-eval` runs the provider inside eve's own runtime with a
  mock model and a local fake of NAMS; it passes on eve 0.69.0, 0.70.3 and
  0.71.0.

### Fixed

- **Searches no longer pass `threshold`.** Hosted NAMS never supported it, and
  since October 2026 it rejects unknown fields, so every message search failed
  and came back empty: recall's matching messages, `nams__search`, and the
  current and cross-session searches of the AI SDK modes.
  `@neo4j-labs/agent-memory` 0.5.0 still adds a default `threshold` of its
  own, so hosted message search works again with the SDK release that drops
  it.

## 0.3.0

### Added

- **Graph retrieval.** After the entity search, the best matches are expanded
  and their stored relationships are added to the prompt as triples —
  `(Alex)-[WORKS_AT]->(TechCorp)` — with `source: 'graph'`. Entity search
  returns entities without their edges, so until now the graph was never read
  back. Configured with `graphExpansionLimit` (default: 2 entities, one
  request each; `0` turns it off). At most five relationships are taken from
  any one entity, and an edge whose target has no stored name is dropped.
- **Hooks mode**: `createNams().hooks()` with `loadSession()`, `prepare()`,
  `withHooks()`, `onFinish()` and `end()`, plus lifecycle hooks for
  `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
  `PostToolUseFailure`, `PreMemoryWrite`, `Stop` and `SessionEnd`.

### Fixed

- **Retrieval no longer ranks by entity confidence.** Hits were sorted by a
  score that measures how sure extraction was, not how well the entity matches
  the query. It discarded the backend's relevance order and put every entity
  ahead of the current conversation, so a small `maxMemories` could leave no
  room for the conversation at all. Sources are now merged by taking turns,
  and each keeps the order the backend returned it in.
- **Extracted entities are no longer duplicated on every write.** Reuse relied
  on `getEntityByName`, which hosted NAMS does not implement, so the lookup
  always missed and each memory about "Alex" created another Alex. The name is
  now resolved through entity search, matching an exact name or canonical name
  only — a nearest-neighbour hit like "Alexandra" stays its own entity.

### Changed

- **Breaking:** `engines.node` is now `>=22` (was `>=20`).
- **Breaking:** `extractionModel` / `extractionOptions` are no longer accepted
  by `createNamsProvider` or `createNamsMemory`. They only ever applied to
  tools mode, and setting them elsewhere now warns.
- The `@ai-sdk/provider` peer dependency is gone; model types come from `ai`,
  so they always match the caller's version.
- The `@neo4j-labs/agent-memory` peer range is `~0.4.0`.
