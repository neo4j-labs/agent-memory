# Changelog

Releases before 0.3.0 predate this file; see the
[GitHub releases](https://github.com/neo4j-labs/agent-memory/releases) for those.

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
