# Strands MemoryStore for Neo4j Agent Memory — Design

**Date**: 2026-08-19 · **Status**: approved, not yet implemented
**Verified against**: `strands-agents==1.52.0` (PyPI), `@strands-agents/sdk@1.13.0` (npm)

## Overview

`Neo4jMemoryStore` implements Strands' long-term-memory `MemoryStore` protocol in both
SDKs, so `MemoryManager(stores=[Neo4jMemoryStore(...)])` recalls from a Neo4j graph
across sessions. Requested by both Strands reviewers in
[harness-sdk#3871](https://github.com/strands-agents/harness-sdk/pull/3871).

It is an adapter: every protocol member has a backing primitive in the library already.

## Goals

- `MemoryStore` conformance in Python and TypeScript, same shape in both.
- `search()` over long-term memory: entities, preferences, facts.
- Server-side extraction via `add_messages` — no extra model call.
- Graph-native tools the manager cannot provide (`get_entity_graph`).
- Defined behaviour when paired with `Neo4jSessionManager`.

## Non-Goals (v1)

- No `MemoryManager` subclass — it is framework-owned and concrete.
- No buffered writes (see Write durability).
- No new NAMS capabilities; the store lives within NAMS's entity-only surface.
- No `searchFacts` in the TypeScript client — no REST endpoint behind it.
- No change to `Neo4jSessionManager` persistence behaviour beyond two guards.
- No TCK work: it certifies client conformance by tier and does not cover integrations.
- `search()` does not use `get_context()`, which blends short-term and reasoning memory.

## Verified contract

`MemoryStore` is a `Protocol` (Python) / `interface` (TypeScript). Required: `name`,
`description`, `max_search_results`, `writable`, `extraction`, `search()`. Optional:
`add()`, `add_messages()`, `initialize()`, `get_tools()`.

Manager behaviour that shapes this design:

| Fact | Source |
|---|---|
| Extraction mode follows the sinks a store defines: `add` only → client-side `ModelExtractor`; `add_messages` present → server-side, no model call | `memory/extraction/resolve_extraction_config.py` |
| `_has_method` inspects `type(store)`, so an inherited Protocol stub counts as not-implemented | `memory/types.py` |
| A writable store needs ≥1 sink; an extractor requires `add`; extraction without an extractor requires `add_messages` | `MemoryManager.__init__` |
| `search()` fans out with `return_exceptions=True`; per-store failures are logged, not fatal | `MemoryManager.search` |
| `add()` aggregates per-store failures into `AggregateMemoryError` | `MemoryManager.add` |
| `init_agent` calls `store.initialize()`, wires extraction triggers, registers injection middleware | `MemoryManager.init_agent` |
| Defaults: 3 search results per store, 5 injected entries, `IntervalTrigger(turns=5)` | `memory_manager.py` |
| Manager ships its own per-model-call injection: default on, trigger `"userTurn"`, folded into the last user message | `memory/types.py`, `injection/` |
| `Agent(session_manager=..., memory_manager=...)` are two first-class parameters; `agent.memory_manager` is public | `agent/agent.py:203-204` |
| `AddMessagesContext` carries only `sequence_numbers`, which reset every run — no session identity reaches the store | `memory/types.py` |

## Positioning mandate

Binding on implementation and docs.

| Job | Preferred construct | Supersedes |
|---|---|---|
| Recall into the agent loop | **`Neo4jMemoryStore` in a `MemoryManager`** | `search_context` from `context_graph_tools`; `Neo4jRetrievalConfig` injection |
| Transcript persistence + restore | `Neo4jSessionManager` | nothing; carries no memory framing |
| Deep graph queries the store lacks | `context_graph_tools` | nothing; factory retained |
| strands < 1.44 | `retrieval_config` + tools | documented as the pre-`MemoryStore` path |

In Strands' vocabulary: the session manager restores sessions, the memory store feeds the
agent loop.

## Architecture — Python

New module `src/neo4j_agent_memory/integrations/strands/memory_store.py`, exported from
the package `__init__` through the existing try/except ImportError guard. `_retrieval.py`
gains a `MemoryEntry`-shaped sibling to `_retrieve_context`; `_messages.py` is reused
unchanged.

Shape follows the vended stores (`strands.vended_memory_stores.bedrock_knowledge_base`,
`test_memory_store`): subclass the Protocol, config as a `TypedDict`.

```python
class Neo4jMemoryStore(MemoryStore):
    def __init__(self, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> None: ...

    @classmethod
    def for_nams(cls, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> Neo4jMemoryStore: ...
```

`for_nams` mirrors `Neo4jSessionManager.for_nams`, reading `MEMORY_API_KEY` /
`MEMORY_ENDPOINT` from the environment.

### Config

`Neo4jMemoryStoreConfig` extends `MemoryStoreConfig` with:

| Field | Default | Purpose |
|---|---|---|
| `client` \| `settings` | — | pre-connected `MemoryClient`, or settings the store builds one from |
| `conversation_id` | minted in `initialize()` | write sink target |
| `user_id` | `None` | scopes reads in multi-tenant mode |
| `include_entities` | `True` | search fan-out |
| `include_preferences` | `True` | auto-gated off on NAMS |
| `include_facts` | `True` | auto-gated off on NAMS |
| `min_score` | `0.2` | bolt only; NAMS ignores `threshold` |
| `graph_tools` | `True` | expose `get_tools()` |

Inherited defaults: `writable=True`, `extraction=False`, `max_search_results=None`
(defers to the manager's 3), `description` naming the graph.

### Protocol mapping

| Member | Maps to | Notes |
|---|---|---|
| `search` | concurrent `search_entities` / `search_preferences` / `search_facts` | reshape of `_retrieval.py` `_retrieve_context`. Per-kind failures isolated and logged; whole-search failures propagate. Limit precedence: `options["max_search_results"]` → `self.max_search_results` → manager default |
| `add` | default: message into the sink with extraction. `metadata["kind"]` ∈ `{preference, fact, entity}` routes to `add_preference` / `add_fact` / `add_entity` | `NotSupportedError` falls back to the sink, logged once. **Not** `long_term.add()`, which makes the whole string an entity *name* of type `OBJECT` (`memory/long_term.py:389-398`) |
| `add_messages` | `bulk_add_messages(sink_id, msgs, extract_entities=True)` | protocol alias forwarding kwargs to `add_messages_batch` (`memory/short_term.py:560-572`) |
| `initialize` | connect an owned client; mint the sink when `conversation_id` was omitted | idempotent |
| `get_tools` | `get_entity_graph`, `get_user_preferences` | bound to the store's own client, not the tools factory's cached clients. Excludes search/add, which would collide with `context_graph_tools`' `add_memory` and the manager's own tool of that name |

Tool availability is backend-gated; the differing depth is stated in the tool description
the model sees:

| Tool | bolt | NAMS / TypeScript |
|---|---|---|
| `get_entity_graph` | `get_related_entities`, configurable depth | `expand_graph` / `expandGraph` — 1 hop, keyed by node id, so the name resolves through `search_entities` first (`get_entity_by_name` unsupported on NAMS) |
| `get_user_preferences` | `get_preferences_for` | omitted — no preferences endpoint (`nams/long_term.py:459`) |

`MemoryEntry.content` is the formatted line (reusing `_format_entity` /
`_format_preference` / `_format_fact`); `metadata` carries:

| Key | Value |
|---|---|
| `kind` | `entity` \| `preference` \| `fact` |
| `id` | node id |
| `type` | `full_type` for entities, `category` for preferences |
| `score` | similarity, bolt only — `search_entities`, `search_preferences` and `search_facts` each set `metadata["similarity"]` (`memory/long_term.py:1059`, `:1126`, `:2065`); NAMS sets none, and an absent score is omitted rather than zeroed |

### Both sinks on one class

The class defines `add` **and** `add_messages`, so: extraction defaults to server-side
with no model call; `add` stays available for the manager's `add_memory` tool and
programmatic `MemoryManager.add`; an explicit `extraction={"extractor": ModelExtractor()}`
still validates. One class per SDK, no per-transport split.

### Scoping and the sink conversation

`AddMessagesContext` carries no session identity, so the store owns its scope — matching
the Bedrock KB precedent ("for per-tenant isolation, construct one store per scope").

- Writes go to a dedicated sink conversation. Its name is deterministic —
  `strands-memory-store/{user_id or "_"}/{name}` — so restarts reuse one sink instead of
  accumulating orphans. Resolution splits by backend, as
  `Neo4jSessionManager._aresolve_conversation` does:
  - **bolt**: the deterministic name *is* the conversation key, and the first write
    auto-creates the conversation (`add_message` and `add_messages_batch` both call
    `_ensure_conversation`). So resolution makes no backend call. Nothing is tagged: bolt's
    `CREATE_CONVERSATION` (`graph/queries.py`) has no metadata property, and
    `create_conversation` drops a `metadata` kwarg (`memory/short_term.py:521-524`).
  - **NAMS**: ids are server-minted and client session ids are dropped, so the sink is
    found by matching `_STORE_KEY` metadata (accepted at creation, unsettable afterwards)
    and the returned id is cached.
- Reads are conversation-independent; only `user_id` narrows them.
- Pointing the sink at the chat conversation duplicates `Message` nodes in the readable
  history: documented as unsupported, not guarded.

### Idempotency

`CREATE_MESSAGE` uses `CREATE` with an internally generated `$id` — no caller-supplied
id, no `MERGE` (`graph/queries.py:68-86`) — so message writes are not idempotent, while
Strands' extraction writes are at-least-once. Mitigation: an in-process set of
`(run_id, sequence_number)`, `run_id` per store instance. Retries occur within one
process, which is what the set covers. NAMS offers nothing better.

### Write durability

Awaited writes. `write_mode="buffered"` applies only to explicit
`client.buffered.submit(cypher, params)` calls; the memory layers do not route through it
(`memory/buffered.py:99`, `__init__.py:877`), so using the buffer would mean hand-writing
Cypher and bypassing embeddings, extraction and message linking. It is also bolt-only.
Durability stays where Strands puts it: background extraction plus
`MemoryManager.flush()`, and `wait_for_writes` on the add tool.

### Client ownership

As the session manager: a store built from `settings` owns its client and closes it via
`aclose()` / async context manager; a store handed a live `MemoryClient` never closes it.
The existing warning against sharing one client between the tools factory and the session
manager extends to the store.

## Coexistence with Neo4jSessionManager

The pairing is legitimate — two first-class `Agent` parameters, and transcript and
knowledge are different jobs. Two overlaps need handling.

Both guards live in `session_manager.py`, not the store: `initialize()` receives no
agent, while the session manager gets one at `AgentInitializedEvent` and
`agent.memory_manager` is public. The store therefore stays free of Strands internals.
Enumerating the manager's stores reads `MemoryManager._stores`, a private — one read, in
one file, pinned by a test that fails loudly on a strands upgrade.

### Guard 1 — double extraction (raises)

The store can only extract by re-writing turns the session manager already persisted:
`extract_entities_from_session` (extract in place) is bolt-only
(`memory/short_term.py:1111`) and cannot be the portable path. Compounding it, NAMS
`add_message` accepts only `{content, role}` and silently drops extraction kwargs
(`nams/short_term.py:287-289`), so on NAMS `Neo4jSessionManager(extract_entities=...)` is
a no-op and a sink conversation is extracted a second time regardless.

| Setup | Behaviour |
|---|---|
| Store alone | store owns extraction — the textbook Strands split |
| Store + session manager | store recall-only; session manager persists, bolt extracts in place, NAMS server-side |
| Store + session manager, `store.extraction` truthy | raises at construction, naming both one-line fixes |

Recommended paired configuration, which the docs lead with:

```python
Neo4jSessionManager(..., extract_entities=True)   # transcript + extraction
Neo4jMemoryStore(name="graph")                    # recall only (extraction=False default)
```

### Guard 2 — double injection (warns)

Manager injection is default-on and lands where `Neo4jRetrievalConfig` does — both fold
into the last user message on a user turn — so a current `retrieval_config` user adopting
the store gets two search fan-outs and two blocks.

`Neo4jRetrievalConfig` is kept and fully supported: it is the only injection path below
strands 1.44, and its sources are configured declaratively rather than decided by a
store. When the session manager detects a `MemoryManager` injecting from our store it
logs a warning naming the duplication and both ways out, once per session-manager
instance. No deprecation, no runtime disabling.

## Backend capability matrix

Source: `nams/long_term.py:1-27` and its `NotSupportedError` methods.

| Capability | bolt | NAMS |
|---|---|---|
| `search` entities | yes, with score | yes; no score, `threshold` ignored, singular `type` |
| `search` preferences / facts | yes | `NotSupportedError` → auto-gated off |
| `add` kind routing | entity / preference / fact | entity only; others fall back to the sink |
| `add_messages` | yes, `extract_entities=True` honoured | yes; extraction server-side and unconditional |
| `get_entity_graph` tool | yes, configurable depth | yes via `expand_graph`, 1 hop |
| `get_user_preferences` tool | yes | omitted |

## TypeScript parity

Same shape in TS idiom: one options object, `class Neo4jMemoryStore implements
MemoryStore`, type-only SDK imports so the published bundle keeps zero runtime
dependencies. `getTools()` reaches `tool()` through the existing lazy
`await import("@strands-agents/sdk")`.

| Divergence | Handling |
|---|---|
| `searchFacts` absent; `search_preferences`, `get_related_entities`, `add_preference`, `add_fact`, `add_relationship`, `get_entity_by_name` are `"unsupported"` on REST (`transport/rest.ts:171-177`) | TS store is entities-only for `search()`. `includeFacts` / `includePreferences` are absent from the TS options type — a compile error, not a silently ignored flag. `get_entity_graph` is available via `expandGraph` (1 hop, `transport/rest.ts:284`); `get_user_preferences` is not |
| `bulkAddMessages` caps at 100 per call (`short-term/index.ts:283`) | store chunks internally |
| No extraction flags on TS writes | none needed — NAMS extracts server-side |
| No bolt transport | `minScore` accepted, meaningful on Python/bolt only |

Those methods exist in the TS client because `BridgeTransport` is a generic
`POST {endpoint}/{snake_case_method}` forwarder used by the TCK against a Python
reference adapter: they work on the bridge, throw on REST. TS and Python agree on the
NAMS surface, so no live-API verification is needed.

TS guards attach in `Neo4jConversationManager.initAgent`, the only place an agent
reference arrives. `Neo4jSessionStorage` used without the conversation manager therefore
gets no guard.

## Error handling

| Failure | Behaviour |
|---|---|
| One search kind fails | logged, skipped; other kinds still return |
| Whole `search` fails | propagates; the manager logs and continues with other stores |
| `add` / `add_messages` fails | propagates; the manager aggregates into `AggregateMemoryError` |
| `NotSupportedError` on a routed `add` | falls back to the sink, logged once per store |
| `initialize` fails | propagates, aborting agent construction as Strands intends |
| Paired with store extraction on | `ValueError` at construction |

## Testing

| Layer | Coverage |
|---|---|
| Python unit | `search` fan-out with per-kind isolation; limit precedence; `MemoryEntry` metadata; `add` kind routing incl. `NotSupportedError` fallback; `add_messages` dedupe across a retried batch; sink minting idempotence; `get_tools`; client ownership; both guards |
| Python integration | bolt via docker-compose; NAMS gated on a key, skipped without |
| TS unit | mirrored test names against the existing msw/bridge setup, in `test/unit/strands/memory-store.test.ts` |
| Guards | extend `tests/unit/integrations/strands_fakes.py` with a fake agent exposing `memory_manager`; assert the private-attribute read fails loudly if strands moves it |
| Examples | `tests/examples/test_no_phantom_methods.py` covers the new example automatically |

Cross-language parity rests on mirrored test names and this spec's tables, not the TCK.

## Documentation requirements

1. Both guides lead their Quick Start with the store; `aws-strands.adoc` currently leads
   with tools + session manager and gets restructured.
2. Memory-tools section: `search_context` is superseded by the store for recall; the
   factory is retained for deep graph work.
3. `Neo4jRetrievalConfig` section: superseded for injection by `MemoryManager` injection,
   consistent with Guard 2.
4. Both guides state the split in Strands' vocabulary and carry the three deployment
   shapes plus the pairing rule.
5. "Memory" comes off the session-manager surface wherever it is not load-bearing.
6. TS guide states the store is entities-only, and why.

The guides were cross-linked in PR #181; the `MemoryStore` sections land in that
structure.

## Packaging

- `pyproject.toml`: `strands` extra `strands-agents>=0.1.0` → `>=1.44.0` (`MemoryStore`
  landed in 1.44.0; current release 1.52.0). Forces an upgrade on existing
  session-manager users — accepted.
- `typescript/package.json`: devDep `@strands-agents/sdk` `^1.2.0` → `^1.13.0` (TS memory
  landed in 1.6.0).
- CHANGELOG entries in both SDKs.
- Examples: `examples/strands-memory-store/` (Python, `llm=None` + local
  sentence-transformers, no API keys, wired into `example-tests` CI) and a memory-store
  variant under `typescript/examples/strands`.

## Sequencing

| PR | Contents |
|---|---|
| 1 | TS module split: `src/integrations/strands.ts` (846 lines) → `src/integrations/strands/` with `index.ts` re-exporting; no behaviour change. First, so the store lands on the final layout |
| 2 | Python store: implementation, tests, example, guide section, floor bump |
| 3 | TS store: implementation, tests, example, guide section, devDep bump |

Then a second Strands catalog entry (`integrationType: memory-store`), bundled with the
pending `maintainedBy: partner` and session-manager description retune — one YAML PR
against `strands-agents/harness-sdk`, needing explicit approval.

## Limitations (documented, accepted)

- Retried extraction batches dedupe in-process only; a restart mid-retry can duplicate
  messages in the sink conversation.
- Relevance scores are bolt-only.
- The TS store is entities-only, so `search()` is narrower than Python-on-bolt, and its
  `get_entity_graph` traverses one hop rather than a configurable depth.
- A store paired with the session manager writes turns to a sink separate from the
  readable history, so turn text exists twice in the graph. Entities converge via
  resolution/dedupe; the duplication is in messages, not knowledge.
- Guard 1 reads one private strands attribute (`MemoryManager._stores`).
- No TS guard when `Neo4jSessionStorage` is used without `Neo4jConversationManager`.
