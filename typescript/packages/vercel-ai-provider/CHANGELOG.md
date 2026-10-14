# Changelog

Releases before 0.3.0 predate this file; see the
[GitHub releases](https://github.com/neo4j-labs/agent-memory/releases) for those.

## Unreleased

Requires a `@neo4j-labs/agent-memory` release newer than 0.5.0, one with the
message-search and per-user-entity fixes. The `~0.5.0` peer range accepts a
0.5.x release, so the provider needs no change when the SDK ships.

### Added

- **eve memory provider.** `namsMemory()` from the new
  `@neo4j-labs/nams-ai-provider/eve` entry point backs an
  [eve](https://eve.dev) memory slot with NAMS, following eve's
  memory-provider contract. Recall returns one keyed record before each turn
  (and after compaction): notes saved with `remember`, NAMS summaries of the
  scope's earlier sessions, messages from them that match the user's message,
  and the entities NAMS extracted from the scope's conversations. Capture
  writes each completed turn to one NAMS conversation per eve session. The
  model gets `search` and `remember`, bound to the locked scope. Every
  conversation carries eve's `memory.scope.key` as its NAMS `userId`, entity
  searches send it too, and reads check it on every conversation and entity,
  in case the server returns other users'. The workspace's shared entities
  (no user) are read only with `workspaceGraph: true`. A replayed recall
  returns the same record; a replayed capture writes nothing, also after a
  restart. Tool callbacks are durable and keep no secrets.
- **`workspaceGraph`** on `NamsConfig`: also recall the workspace's shared
  entities in the AI SDK modes, as the eve provider's option of the same name
  does.
- `eve` (`>=0.69.0`) is a new optional peer dependency,
  needed only for the `/eve` entry point. The main entry point never loads it.
  `examples/eve-memory-eval` runs the provider inside eve's own runtime with a
  mock model and a local fake of NAMS; it passes on eve 0.69.0, 0.70.3,
  0.71.0, 0.75.1, 0.77.0, 0.78.0 and 0.79.0.

### Changed

- **BREAKING (behavioural): the AI SDK modes read and write entities per
  user.** The long-term search asks NAMS for `scope.userId`'s entities and
  keeps only the ones that carry that id, graph expansion asks for
  relationships within that user's memory, and `store_memory` and the graph
  extractor store entities with `userId`. Before, the search covered every
  entity in the workspace, other users' included. Entities stored by earlier
  versions carry no user, so they are no longer recalled; set
  `workspaceGraph: true` to keep recalling shared entities (never another
  user's). `findEntity`, `storeMemory` and `GraphExtractor` take an optional
  `userId` (a new last argument, `opts.userId`, and `scope.userId`); without
  it they behave as before.
- **The `@neo4j-labs/agent-memory` peer dependency is now `~0.5.0`.** The SDK
  fixes this release needs ship in a later 0.5.x, which the range accepts.
  0.5.0 itself installs but fails against hosted NAMS: it sends `threshold`
  with every message search, sends the conversation user filter as `user_id`,
  which the service ignores, and has no `userId` on entity search, entity
  reads or entity writes. 0.4.x is not supported either.
- **Name lookups are per user.** `findEntity` passes the type and `userId` to
  the SDK's name lookup and, when it returns another user's entity or another
  type, searches the user's entities instead of giving up. The SDK's REST
  transport has no name lookup yet, so nothing changes today; when an SDK
  release adds one, the provider uses it per user without a change.
- The README and `examples/` use AI SDK 7's `onEnd` option and `isStepCount`
  in place of the deprecated `onFinish` option and the `stepCountIs` alias.
  The package's own `session.onFinish()` and `ensureMemoryStored()` are
  unchanged. Pass the callback from `session.onFinish()` as `onEnd`; call
  the function from `ensureMemoryStored(tools)` inside your `onEnd`. The README
  used to pass it as the callback itself, which does not type-check against
  AI SDK 7, because `onEnd` must return nothing.

### Fixed

- **Conversation lookups are filtered by user.** Every AI SDK mode finds the
  user's conversation, and cross-session search finds their other
  conversations, with `listConversations({ userId })`. `@neo4j-labs/agent-memory`
  0.5.0 sent that filter as `user_id`, which hosted NAMS ignores. So without a
  `conversationId`, a turn could be read from and written to the workspace's
  newest conversation whoever it belonged to, and cross-session search read
  other users' conversations. The next SDK release sends `userId`, which NAMS
  applies on the server.
- **Searches no longer pass `threshold`.** Hosted NAMS never supported it, and
  since October 2026 it rejects unknown fields, so every message search failed
  and came back empty: recall's matching messages, `nams__search`, and the
  current and cross-session searches of the AI SDK modes. The SDK also added
  a default `threshold` of its own in 0.5.0; the next SDK release, which this
  release requires, no longer sends it.

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
