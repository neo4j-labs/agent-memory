# Strands SessionManager for Neo4j Agent Memory — Design

**Date:** 2026-06-05
**Status:** Approved design, pending implementation plan
**Scope:** Python SDK only (Strands Agents is Python-only)

## Overview

Provide Neo4j Agent Memory (NAMS-hosted or self-hosted bolt) as a Strands
`SessionManager`, the same way AWS Bedrock AgentCore Memory provides
`AgentCoreMemorySessionManager`. Today's Strands integration
(`context_graph_tools()` / `nams_context_graph_tools()`) is *pull-based* —
the agent must decide to call a tool. A SessionManager is *push-based*:
every message is persisted automatically, conversation history is restored
on agent restart, and (opt-in) relevant long-term memories are injected
into each user turn.

Researched against `strands-agents` 1.42.0 and the verified NAMS REST API
(`https://memory.neo4jlabs.com/openapi.json`, June 2026).

## Goals

1. `Neo4jSessionManager` usable as `Agent(session_manager=...)`, backed by
   any `MemoryClient` — bolt or NAMS.
2. Automatic conversation persistence + restore (Strands' 4 core hooks:
   `initialize`, `append_message`, `sync_agent`, `redact_latest_message`).
3. Opt-in per-turn long-term memory injection (AgentCore's
   `retrieval_config` pattern).
4. **No Strands-specific node types in the graph.** Strands `Session` maps
   to the existing `Conversation`; Strands messages map to `Message`.
   The graph stays memory-shaped, not runtime-snapshot-shaped.
5. Document and test the "shared brain" pattern: N agents, N session
   managers, one shared graph (see the Medium article "When Your Agents
   Share a Brain").

## Non-Goals (v1)

- Strands Graph/Swarm orchestration persistence (`sync_multi_agent` /
  `initialize_multi_agent`) — base-class defaults inherited, untested.
- Bidirectional-streaming agent hooks.
- Byte-faithful runtime snapshotting (tool-use blocks, `agent.state`
  key-values). That's `FileSessionManager`'s job; this is memory-grade
  persistence. See Limitations.
- TypeScript SDK parity (no Strands TS SDK exists).

## Architecture

Option chosen: **direct `SessionManager` implementation** (no
`SessionRepository` layer).

```
src/neo4j_agent_memory/integrations/strands/
├── tools.py                 # existing — unchanged
├── config.py                # existing + shared NAMS settings-from-env helpers
├── session_manager.py       # NEW: Neo4jSessionManager(SessionManager) —
│                            #      lifecycle, hooks, write-behind buffer
├── _messages.py             # NEW: Strands ↔ Memory message mapping (pure)
└── _retrieval.py            # NEW: Neo4jRetrievalConfig + long-term search
                             #      + context-block formatting
# AsyncBridge (background event loop) lives in integrations/base.py, shared.
```

`Neo4jSessionManager` subclasses Strands' `SessionManager` ABC directly
and implements the 4 core methods against a `MemoryClient` (identical on
bolt and NAMS, since both backends expose the same `short_term` API):

- `initialize(agent)` — resolve/create the conversation, load history,
  populate `agent.messages`.
- `append_message(message, agent)` — write-behind buffer →
  `add_message`.
- `redact_latest_message(...)` — rewrite the buffer (bolt in-place
  fallback after flush).
- `sync_agent(agent)` — no-op (agent state is not persisted; the buffer flush runs on the `AfterInvocationEvent` hook; see Data mapping).

Hook wiring (AgentInitializedEvent → initialize, MessageAddedEvent →
append, AfterInvocationEvent → sync, redaction event → redact) is
inherited from the `SessionManager` base class; we extend
`register_hooks()` with the buffer-flush and retrieval-injection
callbacks.

### Why not Strands' repository split

An earlier revision of this design subclassed `RepositorySessionManager`
+ `SessionRepository` (the File/S3/AgentCore layering). The
no-Strands-specific-data constraint hollowed that contract out:
`create_agent`/`update_agent` would be no-ops, `read_agent` synthetic,
and `create_message`/`update_message` would buffer rather than persist —
a stateful "repository" that violates the CRUD contract it advertises.
Exposing such a `Neo4jSessionRepository` publicly would invite misuse in
users' own `RepositorySessionManager` wiring, and the inherited
lifecycle logic mostly manages state we deliberately don't persist. A
direct implementation is smaller, honest, and only couples us to the
public 4-method ABC. The conversation-mapping helpers and `_AsyncBridge`
stay private and independently unit-testable.

### Constructor

```python
Neo4jSessionManager(
    session_id: str,
    memory_client: MemoryClient | None = None,    # bring your own (bolt or NAMS)
    settings: MemorySettings | None = None,        # or let us construct one
    user_id: str | None = None,                    # optional multi-tenant scoping
    retrieval_config: Neo4jRetrievalConfig | None = None,  # opt-in LTM injection
    extract_entities: bool = True,                 # extraction on stored messages (bolt;
                                                   # NAMS extracts server-side regardless)
    record_tool_calls: bool = False,               # mirror toolUse/toolResult blocks into
                                                   # reasoning memory (audit graph)
    request_timeout: float = 30.0,                 # sync→async bridge timeout
)
```

Exactly one of `memory_client` / `settings` must be provided. A client
constructed from `settings` is owned by the manager (`close()` closes
it); an injected client is left open.

Convenience constructor mirroring `nams_context_graph_tools()` env-var
conventions:

```python
Neo4jSessionManager.for_nams(
    session_id: str,
    endpoint: str | None = None,      # MEMORY_ENDPOINT, default https://memory.neo4jlabs.com/v1
    api_key: str | None = None,       # MEMORY_API_KEY
    transport_mode: str = "auto",
    **kwargs,                          # user_id, retrieval_config, ...
)
```

Sets `validate_on_connect=False` like the NAMS tools do.

### Sync↔async bridge

Strands hooks are synchronous; `MemoryClient` is async; the manager is
long-lived. The existing `_run_async` helper in `tools.py` creates a new
event loop per call, which breaks persistent transports (httpx/bolt
transports are loop-bound). The manager's internal `_AsyncBridge`
therefore owns a **dedicated background event-loop thread**, started
lazily on first use. Every backend call submits its coroutine via
`asyncio.run_coroutine_threadsafe(...)` and blocks on
`future.result(timeout=request_timeout)`. One loop, one open transport,
for the manager's lifetime.

## Coexistence with the existing Strands integration

- `context_graph_tools()` and `nams_context_graph_tools()` are
  **unchanged and complementary** (pull-based tools vs push-based session
  manager) — same posture as AgentCore, which ships both.
- Docs reposition `add_memory` as "store extra-conversational facts"
  (conversation capture is now automatic) and `search_context` as the
  explicit/deep-query escape hatch when retrieval injection is on.
- **Transports are intentionally separate**: tools keep their own cached
  clients with per-call `async with client` open/close; the session
  manager holds its own persistent client. Sharing a client would let a
  tool's `async with` close the manager's transport mid-session. Two
  transports per process is correct and cheap.
- The shared-brain pattern becomes the flagship doc example: each agent
  gets its own `Neo4jSessionManager` (own `session_id`), all pointing at
  the same workspace/DB; conversations flow into one graph, entities are
  extracted, and the shared tools let any agent query what the others
  learned.

## Data mapping

Verified NAMS API constraints that shape this design:

- Messages accept **only `{content, role}`** — no per-message metadata,
  no update endpoint, no delete endpoint (`delete_message` raises
  `NotSupportedError` in the SDK).
- Conversations: NAMS issues the conversation UUID; `metadata` + `userId`
  are set **at creation only** (no conversation update endpoint).

| Strands record | Mapping |
|---|---|
| `Session` | **One `Conversation`.** Created via `create_conversation(metadata={"strands_session_id": ..., "session_type": ...}, user_id=...)`. On NAMS, `initialize` resolves Strands session_id → conversation UUID by scanning `list_conversations` metadata (cached in-memory after first hit). On bolt, the Strands session_id is used directly as the conversation session_id. |
| `SessionMessage` | **One `Message`** — `role` + concatenated text blocks as `content`. This is what gets embedded and extracted (server-side on NAMS, pipeline on bolt) and feeds the shared brain. Restore ordering is by `createdAt` (positional). |
| toolUse / toolResult content blocks | **Not stored as messages.** When `record_tool_calls=True`, mirrored to reasoning memory (`POST /v1/reasoning/tool-calls`, conversation-scoped) as write-only enrichment for the audit graph. Off by default. Never used for restore. |
| `SessionAgent` (`agent.state` KV, conversation-manager state) | **Not persisted** — there is nowhere to put it without inventing Strands-specific nodes. `sync_agent` is a no-op (the buffer is flushed by the `AfterInvocationEvent` hook). |

`initialize` (restore) yields the conversational text history plus the
shared long-term graph. It does **not** yield exact tool-use blocks
(precedent: AgentCore's `filter_restored_tool_context=True`), `agent.state`
key-values, or conversation-manager window state (re-initializes to
defaults).

### Redaction without an update endpoint — write-behind buffer

The manager holds the **latest message in a one-slot buffer**, flushed
when:

1. the next message arrives,
2. `AfterInvocationEvent` fires,
3. `close()` is called.

Strands calls `redact_latest_message` during the same invocation the
guardrail fires, so redaction **rewrites the buffer before the original
ever reaches the backend** — privacy-correct on both backends, one
portable code path. The buffer stores a **deep copy** taken at append
time so later in-memory mutations (notably retrieval injection) cannot
leak into the graph on flush.

Trade-off: a crash before flush loses at most the final message of the
in-flight turn.

## Long-term memory retrieval injection (opt-in)

Enabled by passing a `Neo4jRetrievalConfig`; without it the manager is
persistence-only.

```python
@dataclass
class Neo4jRetrievalConfig:
    top_k: int = 10                    # max results per memory kind
    min_score: float = 0.2             # similarity floor
    include_entities: bool = True
    include_preferences: bool = True
    include_facts: bool = False
    context_tag: str = "user_context"  # wrapper tag, AgentCore-compatible default
```

Mechanism (mirrors AgentCore):

1. `Neo4jSessionManager.register_hooks()` calls `super().register_hooks()`
   first, then always registers an `AfterInvocationEvent` callback for the
   write-behind buffer flush, and — only when `retrieval_config` is set —
   one extra `MessageAddedEvent` callback. Strands fires hooks in
   registration order, so **persistence always runs before injection** —
   the stored message is the user's original, never the augmented one.
2. The callback fires only for **user-role messages containing text**.
3. It uses the message text as the query; runs `search_entities` /
   `search_preferences` / `search_facts` concurrently on the background
   loop (searches are workspace/database-scoped; `user_id` scopes writes, not
   searches — the search APIs take no user filter).
4. Results below `min_score` are dropped; survivors are formatted and
   **prepended to the message's first text block in-memory**:

   ```
   <user_context>
   Relevant memory:
   - [entity] Acme Corp (ORGANIZATION) — customer since 2024, owner: Jane Doe
   - [preference] communication: prefers concise answers
   </user_context>
   {original user text}
   ```

5. If nothing clears the floor, nothing is injected (no empty tags).

Retrieval failures are logged at `WARNING` and skipped — a memory lookup
must never break the agent's turn. Documented cost: 1–3 backend
round-trips of latency per user turn plus injected tokens; that's why it
is opt-in.

## Error handling

- **Persistence failures raise**, wrapped as Strands' `SessionException`
  with the original as `__cause__` (matches `FileSessionManager` /
  `S3SessionManager`). Silent loss of conversation history is the one
  unacceptable failure mode.
- **Retrieval injection failures degrade** (log + skip).
- **Late redaction** (after the buffer flushed — shouldn't happen in
  Strands' lifecycle, handled defensively): bolt deletes the stored message
  and re-adds the redacted text (fresh timestamp — it moves to the end of
  restored history); NAMS logs a clear `WARNING` that redaction couldn't be
  applied server-side. Documented limitation.
- **Bridge timeouts:** every sync→async call blocks with
  `future.result(timeout=request_timeout)` (default 30s) so a hung
  backend raises instead of deadlocking the agent thread.

## Lifecycle

`close()` = flush buffer → close the `MemoryClient` (only if constructed
from `settings`, or if the manager performed the connect on an injected
client) → stop the background loop thread. Sync context-manager
support (`with Neo4jSessionManager(...) as sm:`). The
`AfterInvocationEvent` flush means even without `close()`, at most the
final in-flight message is at risk.

## Packaging

- No new required dependency; `strands-agents` stays optional behind the
  same try/except-ImportError guard as `tools.py`.
- New public exports from `integrations/strands/__init__.py`:
  `Neo4jSessionManager`, `Neo4jRetrievalConfig`.

## Testing

- **Unit** (`tests/unit/integrations/strands/`), skipped when
  `strands-agents` isn't installed:
  - Conversation-mapping round-trips (Strands messages ↔ stored
    messages) against a fake in-memory `MemoryClient`.
  - NAMS-mode semantics: conversation-UUID resolution via metadata scan,
    positional restore ordering.
  - Buffer flush/redact paths, including deep-copy isolation.
  - Hook registration order (persistence before injection).
  - Injection formatting, skip-on-empty, skip-on-error.
  - `_AsyncBridge`: lazy start, timeout, clean shutdown.
- **Integration** (`tests/integration/`): manager against Docker Neo4j
  (bolt), driving SessionManager methods directly, plus one optional
  end-to-end with a real `Agent` and a stub model.
- **Example**: `examples/strands-session-manager/` — shared-brain demo
  (two agents, two session managers, one graph), runnable with
  `llm=None` + local sentence-transformers on bolt; NAMS variant via
  `MEMORY_ENDPOINT` / `MEMORY_API_KEY`. Gets a `tests/examples/` smoke
  test; the phantom-method guard covers it automatically.

## Documentation

Update `docs/how-to/integrations/strands.adoc`:

- Session-manager quickstart (NAMS path first, bolt second).
- Retrieval injection configuration.
- Tools vs session manager guidance (when to use which; transports stay
  separate).
- The shared-brain multi-agent pattern.
- Limitations (below).
- CHANGELOG entry.

## Limitations (documented, accepted)

1. `agent.state` key-values and conversation-manager window state do not
   survive restarts (no Strands-specific storage in the graph).
2. Restored history is text-only; tool-use blocks are not replayed
   (optionally captured in reasoning memory for audit instead).
3. Redaction after buffer flush cannot be applied server-side on NAMS
   (warning logged); in-lifecycle redaction is fully supported via the
   write-behind buffer.
4. One agent per session manager instance (AgentCore has the same
   posture); multiple agents = multiple session managers sharing one
   graph (the shared-brain pattern).
5. A crash before flush loses at most the final in-flight message.

## Key design decisions (record)

| Decision | Choice | Why |
|---|---|---|
| Backend scope | Backend-agnostic (`MemoryClient`) | Bolt and NAMS share the memory-layer API; self-hosted users get it free |
| LTM injection | Opt-in via `Neo4jRetrievalConfig` | AgentCore-familiar; latency/token cost shouldn't be default |
| Topologies | Core 4 methods + shared-brain docs | Shared brain needs no Graph/Swarm hooks; smallest correct surface |
| Architecture | Direct `SessionManager` subclass | Repository split was revisited and dropped: with no agent-state persistence and a write-behind buffer, the `SessionRepository` CRUD contract can't be honored honestly; direct implementation is smaller and only couples to the public 4-method ABC |
| Graph shape | Session ↔ Conversation, no Strands-specific nodes | Keep the graph memory-shaped; NAMS API has no per-message metadata anyway |
| Redaction | Write-behind one-slot buffer | No message update/delete on NAMS; privacy-correct before flush |
