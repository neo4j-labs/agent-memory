# Changelog

Releases before 0.3.0 predate this file; see the
[GitHub releases](https://github.com/neo4j-labs/agent-memory/releases) for those.

## 0.5.1 — unreleased

### Added

- **eve: `nams__forget`.** Deletes a saved note, or one of the user's entities
  by name. Hosted NAMS cannot delete one message, so the notes conversation
  that holds the note is rewritten without it and the old one is deleted with
  `purge: "extracted"`; an SDK without `purge` (0.6.0) deletes the note but
  keeps what NAMS extracted from it. Notes match word for word, ignoring case
  and spacing, or by the shortened line the record showed. A notes
  conversation of 200 or more notes is not rewritten, since only its newest
  200 can be read. Note writes in one scope run one at a time, so a `remember`
  in the same step is not lost to the rewrite. Shared and other users'
  entities are never deleted. Turn it off with `forget: false`.
- **eve: relationships in recall and search.** The memory record adds the
  relationships around the user's two best-matching entities as
  `(subject)-[RELATIONSHIP]->(object)` lines, read with the scope key as
  `userId`, and `nams__search` returns them as `relationships`. Before, eve
  agents got entities only.
- **eve: cancellation.** Recall, capture and tool calls send their NAMS
  requests through `client.withSignal(abortSignal)` when the SDK has it, so a
  cancelled turn or tool call cancels them; with SDK 0.6.0 they run to
  completion as before.
- **eve: `maxCaptureChars`.** eve neither truncates nor expires memory and
  leaves size and retention to the provider. Each captured user message and
  answer is now cut at `maxCaptureChars` (default 16,000 characters), ending
  in `…`. The README and the eve how-to state the bounds (captured message,
  recalled line, recalled record) and that NAMS keeps memory until it is
  deleted, with the three ways to delete it.
- **Hooks mode: `onEnd()`.** `session.onEnd()` builds the callback for the AI
  SDK 7 `onEnd` slot, with the types `OnEndScope`, `NamsOnEndEvent` and
  `NamsOnEndCallback`. It is the same callback `onFinish()` built.

### Deprecated

- Hooks mode's `onFinish()`, `OnFinishScope`, `NamsOnFinishEvent` and
  `NamsOnFinishCallback`, after AI SDK 7 renamed `onFinish` to `onEnd`. They
  remain as aliases of `onEnd()` and the `OnEnd*` types.

### Fixed

- **eve peer range: 1.x in, 2.0 out.** The optional `eve` peer is now
  `>=0.84.0 <1.0.0 || ^1.0.0-rc.0`. 0.5.0's `>=0.84.0` did not match the eve
  1.0 release candidates (npm skips prereleases), so an eve 1.0 RC project
  failed with `ERESOLVE`, and it had no upper bound, so it also admitted any
  future eve 2.0, whose memory contract this release has not been tested
  against. The eve runtime eval passes 28/28 on eve 0.84.1, 0.91.0 and 1.0.1.
- **eve: a repeated exchange is no longer dropped as a replay.** Capture
  recognised a replayed operation by comparing the end of the session's
  conversation with the turn's text, so when a user sent the same message
  again in the next turn and got the same answer, that turn was not saved.
  Each captured message now carries the turn's `operationId` as metadata
  (`eveOperation`), and a replay is recognised by that id, as eve's provider
  contract asks. Messages saved by provider 0.5.0 or earlier carry no id and
  are still compared by text.
- **Provider and middleware modes frame recalled memory as untrusted data.**
  The memory block put stored text straight into the user's message under
  "use it to personalise your answer", so a stored message could read as an
  instruction. The block is now wrapped in `<nams_memory>`, says the memory is
  data, not instructions, and gives each memory one line of at most 400
  characters with `&` and `<` escaped, as the eve record already did. The
  tools-mode `query_memory` description also says results are stored data.
- **Graph relationships keep their direction.** Provider, middleware and tools
  modes wrote every relationship as `(entity)-[TYPE]->(other)`, so an edge that
  points at the matched entity reached the model reversed (a match on TechCorp
  produced `(TechCorp)-[WORKS_AT]->(Alex)`). Edges with `direction: "in"` are
  now written from the other end. A relationship NAMS stored as `RELATED_TO` is
  named by its `predicate`, the name extraction asked for, when there is one.
- **Install commands name the current release.** The README and the provider's
  docs pages installed 0.4.0, whose `/eve` entry point fails to import on eve
  0.84.0 or later.
- The examples and docs pages use AI SDK 7's `onEnd` and `isStepCount` in place
  of the deprecated `onFinish` and the `stepCountIs` alias.
- **eve: saved notes no longer crowd out the rest of recall.** Notes filled up
  to `maxMemories` lines before anything else, so a user with eight notes saw
  nothing from earlier sessions or the knowledge graph. Notes and other memory
  now each get half the lines, and either side uses what the other leaves.
  Recall also searches the notes for the user's message and lists the matches
  first, so a relevant note older than the newest 20 is found.
- **eve: memory survives a slot rename.** Recall, capture and the tools kept
  only conversations whose stored `slot` matched the current slot name. eve's
  scope key already hashes the memory namespace, so with an explicit
  `namespace` a renamed slot kept its key but lost its memory. The slot name is
  no longer compared; two slots that share a namespace share memory, as eve
  defines it.

### Changed

- **eve: recall and `nams__search` search all of the user's earlier
  sessions.** Recall searched only the `crossSessionLimit` (3) newest sessions,
  and `nams__search` the 10 newest, one request each. With a
  `@neo4j-labs/agent-memory` release that supports
  `searchMessages(query, { userId })`, one request now searches every
  conversation stored under the scope key, and each match's conversation is
  checked to be this scope's. With SDK 0.6.0, which cannot search by user, the
  provider searches conversation by conversation as before, without a warning.
  `crossSessionLimit` still sets how many earlier sessions' summaries recall
  reads; `0` still leaves earlier sessions out. A scope with nothing saved yet
  makes no message search.
- `examples/eve-memory-eval`: the fake NAMS server answers
  `POST /v1/messages/search` and `GET /v1/conversations/{id}`, and an 18th
  check confirms every search across the user's conversations carries the
  scope key. 18/18 on eve 1.0.0-rc.13, with SDK 0.6.0 and with an SDK that
  searches by user.
- **eve: `>` is no longer escaped in the memory record.** `<` and `&` still
  are, so a stored line cannot open or close a tag, and relationship lines keep
  their `->` arrows.
- `examples/eve-memory-eval` also checks relationships in recall and search,
  and `nams__forget` deleting the note and the user's entity: 27/27 on eve
  1.0.1, with SDK 0.6.0 and with an SDK that has `withSignal` and
  `deleteConversation({ purge })`.
- `examples/eve-memory-eval`: the fake NAMS server keeps message metadata
  (string values only, as hosted NAMS) and a 28th check confirms every
  captured message carries its eve operation id. 28/28 on eve 0.84.1, 0.91.0
  and 1.0.1 with SDK 0.6.0; 18/28 with SDK 0.5.0. The example depends on eve
  `~1.0.1`.
- Development dependencies: eve `~1.0.1`, which needs `ai` 7.0.172 or later,
  so `ai` `^7.0.173`, `@ai-sdk/openai` `^4.0.118` and `@ai-sdk/mcp` `^2.0.99`,
  the releases that share one `@ai-sdk/provider` (4.0.38). The
  `nextjs-memory-chat` example's lock moved to the same set, since it links
  the provider and must resolve the same `LanguageModelV4` type.
- The README and the provider's docs pages link the runnable examples in
  [neo4j-agent-integrations](https://github.com/neo4j-labs/neo4j-agent-integrations/tree/main/vercel-agent).
- **Recalled entities carry their type.** Each entity the model sees is now one
  line, `name (type) — description`, with the type NAMS stored for it. With an
  ontology active on the workspace that is the ontology's label, so the model
  reads `TK-2210 (ticket)` instead of a bare `TK-2210`. This covers the eve
  recall record and `nams__search`, the memory block in provider and middleware
  modes, and the `content` of tools-mode `query_memory` hits, which keep their
  `type` field. An entity without a type keeps the old line. Search ranking is
  unchanged.

## 0.5.0 — 2026-10-27

### Breaking

- **eve 0.84.0 or later.** eve 0.84.0 removed `defineDurableCallback` and
  compaction recall, and rejects a memory provider that registers
  `recall["compaction.completed"]`. Provider 0.4.0 used both, so its `/eve`
  entry point fails to import on eve 0.84.0 (`does not provide an export named
  'defineDurableCallback'`). The tools are now plain functions, recall runs on
  `turn.started` only, and the optional `eve` peer is `>=0.84.0`. Stay on 0.4.0
  for eve 0.69.0 to 0.83.x.

### Changed

- `examples/eve-memory-eval` runs on eve 0.84.0, whose eval turns expose the
  answer as `reply` instead of `message`. All 17 checks pass.

## 0.4.0 — 2026-10-22

Requires `@neo4j-labs/agent-memory` 0.6.0, the release with the message-search
and per-user-entity fixes. The peer range is `~0.6.0`.

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
- **The `@neo4j-labs/agent-memory` peer dependency is now `~0.6.0`.** The SDK
  fixes this release needs ship in 0.6.0, which the range accepts. 0.5.x is
  not supported: 0.5.0 installs but fails against hosted NAMS — it sends
  `threshold` with every message search, sends the conversation user filter
  as `user_id`, which the service ignores, and has no `userId` on entity
  search, entity reads or entity writes. 0.4.x is not supported either.
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
