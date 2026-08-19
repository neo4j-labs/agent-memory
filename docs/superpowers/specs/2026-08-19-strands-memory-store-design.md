# Strands MemoryStore for Neo4j Agent Memory — Design

**Date**: 2026-08-19 · **Status**: approved, not yet implemented
**Verified against**: `strands-agents==1.52.0` (PyPI), `@strands-agents/sdk@1.13.0` (npm)

## Overview

`Neo4jMemoryStore` implements Strands' long-term-memory `MemoryStore` protocol in
both SDKs, so an agent constructed with
`MemoryManager(stores=[Neo4jMemoryStore(...)])` recalls from a Neo4j graph across
sessions. Both Strands reviewers asked for this unprompted
([harness-sdk#3871](https://github.com/strands-agents/harness-sdk/pull/3871)); it is the
blocking piece of the Strands relationship.

The store is an **adapter**, not new memory machinery. Every protocol member already
has a backing primitive in the library.

## Goals

- `MemoryStore` conformance in Python and TypeScript, with the same shape in both.
- Recall (`search`) over long-term memory: entities, preferences, facts.
- Server-side extraction via `add_messages` — no extra model call.
- Graph-native tools the manager cannot provide (`get_entity_graph`).
- Correct, documented behaviour when paired with `Neo4jSessionManager`.
- Establish the store as the preferred memory construct (see Positioning mandate).

## Non-Goals (v1)

- No `MemoryManager` subclass. It is framework-owned and concrete; we implement the
  store only.
- No buffered/fire-and-forget writes (see Write durability).
- No new NAMS capabilities. The store lives within the entity-only surface NAMS exposes.
- No `searchFacts` in the TypeScript client. No endpoint exists behind it on REST.
- No changes to `Neo4jSessionManager`'s persistence behaviour, beyond two guards.
- No TCK work. The TCK certifies client conformance by tier and does not cover
  integrations, so it cannot verify this integration's cross-language parity.

## Verified contract

`MemoryStore` is a `Protocol` (Python) / `interface` (TypeScript). Required: `name`,
`description`, `max_search_results`, `writable`, `extraction`, and `search()`.
Optional: `add()`, `add_messages()`, `initialize()`, `get_tools()`.

Manager behaviour that shapes this design:

| Fact | Source |
|---|---|
| Extraction mode is inferred from the sinks a store defines: `add` only → client-side `ModelExtractor`; `add_messages` present → server-side, no model call | `memory/extraction/resolve_extraction_config.py` |
| `_has_method` inspects `type(store)`, so an inherited Protocol stub counts as not-implemented | `memory/types.py` |
| A writable store must expose at least one sink; an extractor requires `add`; extraction without an extractor requires `add_messages` | `MemoryManager.__init__` validations |
| `search()` fans out with `return_exceptions=True`; per-store failures are logged, not fatal | `MemoryManager.search` |
| `add()` aggregates per-store failures into `AggregateMemoryError` | `MemoryManager.add` |
| `init_agent` calls `store.initialize()`, wires extraction triggers, registers injection middleware | `MemoryManager.init_agent` |
| Defaults: 3 search results per store, 5 injected entries, `IntervalTrigger(turns=5)` | `memory_manager.py` |
| `MemoryManager` ships **its own per-model-call injection**, default on, default trigger `"userTurn"`, folded into the last user message | `memory/types.py`, `injection/` |
| `Agent(session_manager=..., memory_manager=...)` are two first-class parameters; `agent.memory_manager` is public | `agent/agent.py:203-204` |
| `AddMessagesContext` carries only `sequence_numbers`, which reset every run — the store is handed no session identity | `memory/types.py` |

## Positioning mandate

Binding on the implementation and the docs.

| Job | Preferred construct | Supersedes |
|---|---|---|
| Recall into the agent loop | **`Neo4jMemoryStore` in a `MemoryManager`** | `search_context` from `context_graph_tools`; `Neo4jRetrievalConfig` injection |
| Transcript persistence + restore | `Neo4jSessionManager` | nothing — not superseded, carries no memory framing |
| Deep graph queries the store does not cover | `context_graph_tools` | nothing — factory retained |
| strands < 1.44 | `retrieval_config` + tools | documented as the pre-`MemoryStore` path |

In Strands' own vocabulary: the session manager restores sessions, the memory store
feeds the agent loop.

## Architecture — Python

New module `src/neo4j_agent_memory/integrations/strands/memory_store.py`, exported
from the package `__init__` through the existing try/except ImportError guard.
`_retrieval.py` gains a `MemoryEntry`-shaped sibling to `_retrieve_context`;
`_messages.py` is reused unchanged for message mapping.

Shape follows the vended stores (`strands.vended_memory_stores.bedrock_knowledge_base`,
`test_memory_store`): subclass the Protocol, config as a `TypedDict`.

```python
class Neo4jMemoryStore(MemoryStore):
    def __init__(self, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> None: ...

    @classmethod
    def for_nams(cls, **store_config: Unpack[Neo4jMemoryStoreConfig]) -> Neo4jMemoryStore: ...
```

`for_nams` mirrors `Neo4jSessionManager.for_nams` and reads `MEMORY_API_KEY` /
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

Inherited attribute defaults: `writable=True`, `extraction=False`,
`max_search_results=None` (defers to the manager's 3), `description` defaults to a
string naming the graph.

### Protocol mapping

| Member | Maps to | Notes |
|---|---|---|
| `search` | concurrent `search_entities` / `search_preferences` / `search_facts` | reshape of `_retrieval.py` `_retrieve_context`. Per-kind failures isolated and logged; whole-search failures propagate (the manager isolates per store). Limit precedence: `options["max_search_results"]` → `self.max_search_results` → manager default |
| `add` | default: message into the sink conversation with extraction. `metadata["kind"]` ∈ `{preference, fact, entity}` routes to `add_preference` / `add_fact` / `add_entity` | `NotSupportedError` falls back to the default sink and logs once. **Not** `long_term.add()`, which makes the whole string an entity *name* with type `OBJECT` (`memory/long_term.py:389-398`) |
| `add_messages` | `bulk_add_messages(sink_id, msgs, extract_entities=True)` | protocol alias forwarding kwargs to `add_messages_batch` (`memory/short_term.py:560-572`) |
| `initialize` | connect an owned client; mint the sink conversation when `conversation_id` was omitted and a sink is reachable | idempotent |
| `get_tools` | `get_entity_graph`, `get_user_preferences` | logic re-bound over the store's own client, not the tools factory's cached clients. Backend-gated, see below |

Tools are gated by what the backend actually exposes:

| Tool | bolt | NAMS / TypeScript |
|---|---|---|
| `get_entity_graph` | `get_related_entities`, configurable depth | `expand_graph` / `expandGraph` — **1 hop only**, keyed by node id, so the name is resolved through `search_entities` first (`get_entity_by_name` is unsupported on NAMS) |
| `get_user_preferences` | `get_preferences_for` | omitted — NAMS exposes no preferences endpoint (`nams/long_term.py:459`) |

Differing depth semantics are documented in the tool description the model sees, so the
tool never promises traversal the backend cannot do.

`MemoryEntry` mapping — `content` is the formatted line (reusing `_format_entity` /
`_format_preference` / `_format_fact`), `metadata` carries:

| Key | Value |
|---|---|
| `kind` | `entity` \| `preference` \| `fact` |
| `id` | node id |
| `type` | `full_type` for entities, `category` for preferences |
| `score` | similarity, **bolt only** — `search_entities` sets `entity.metadata["similarity"]`; NAMS returns no score |

### Both sinks on one class

The class defines `add` **and** `add_messages`. Consequences, by design:

- Default extraction is server-side, no model call.
- `add` stays available for the manager's `add_memory` tool and programmatic `MemoryManager.add`.
- An explicit `extraction={"extractor": ModelExtractor()}` still validates, because `add` exists.

This removes the need for a per-transport class split: one class per SDK.

### Scoping and the sink conversation

`AddMessagesContext` gives the store no session identity, so the store owns its scope
— matching the Bedrock KB precedent ("for per-tenant isolation, construct one store
per scope").

- Writes go to a **dedicated sink conversation**, minted in `initialize()` unless
  `conversation_id` is supplied. Its session id is **deterministic** —
  `strands-memory-store/{user_id or "_"}/{name}` — so `initialize()` is idempotent across
  process restarts and repeated runs reuse one sink instead of accumulating orphans.
  Where the backend supports conversation metadata at creation, the sink is also tagged
  (mirroring `_SESSION_KEY = "strands_session_id"` in the session manager); NAMS exposes
  no endpoint to set metadata after creation, so the tag is best-effort and the
  deterministic id is the contract.
- Reads are conversation-independent; only `user_id` narrows them.
- Pointing the sink at the chat conversation duplicates `Message` nodes inside the
  readable history. Documented as unsupported usage, not guarded.

### Idempotency

`CREATE_MESSAGE` uses `CREATE` with an internally generated `$id` — no caller-supplied
id, no `MERGE` (`graph/queries.py:68-86`), so message writes are not idempotent, while
Strands' extraction writes are explicitly at-least-once.

Mitigation: an in-process set of `(run_id, sequence_number)`, `run_id` minted per store
instance. Retries occur within one process, which is the case the set covers. No schema
change, no library change. NAMS offers nothing better.

### Write durability

Awaited writes. `write_mode="buffered"` applies **only** to explicit
`client.buffered.submit(cypher, params)` calls; the memory layers do not route through
it (`memory/buffered.py:99`, `__init__.py:877`), so using the buffer would mean
hand-writing Cypher and bypassing embeddings, extraction and message linking. It is
also bolt-only. Durability stays where Strands puts it: background extraction plus
`MemoryManager.flush()`, and `wait_for_writes` on the add tool. (Resolves A6.)

### Client ownership

Mirrors the session manager: a store built from `settings` owns its client and closes it
via `aclose()` / async context manager; a store handed a live `MemoryClient` never closes
it. The existing warning against sharing one client between the tools factory and the
session manager extends to the store.

## Coexistence with Neo4jSessionManager

The pairing is legitimate — Strands exposes both as separate `Agent` parameters, and
transcript and knowledge are different jobs. Two overlaps need handling.

**Both guards live in `session_manager.py`, not the store.** `initialize()` receives no
agent, so the store cannot see what else is attached; the session manager already gets
the agent at `AgentInitializedEvent`, and `agent.memory_manager` is public. The store
therefore stays free of coupling to Strands internals. Enumerating the manager's stores
reads `MemoryManager._stores`, a private — one read, in one file, pinned by a test that
fails loudly on a strands upgrade.

### Guard 1 — double extraction (raises)

The store can only extract by **re-writing** turns the session manager already
persisted; `extract_entities_from_session` (extract in place) is bolt-only
(`memory/short_term.py:1111`), so it cannot be the portable path.

Compounding this: NAMS `add_message` accepts only `{content, role}` and silently drops
extraction kwargs (`nams/short_term.py:287-289`) — NAMS extracts server-side whatever is
written, so on NAMS `Neo4jSessionManager(extract_entities=...)` is a no-op and a second
sink conversation is extracted a second time.

| Setup | Behaviour |
|---|---|
| Store alone (no `Neo4jSessionManager`) | store owns extraction — the textbook Strands split |
| Store + session manager | store defaults to recall-only; session manager persists, bolt extracts in place, NAMS extracts server-side |
| Store + session manager, `store.extraction` truthy | **raises at construction**, naming both one-line fixes |

Recommended paired configuration, and the one the docs lead with:

```python
Neo4jSessionManager(..., extract_entities=True)   # transcript + extraction
Neo4jMemoryStore(name="graph")                    # recall only (extraction=False default)
```

### Guard 2 — double injection (warns)

`MemoryManager` injection is default-on and lands in the same place as
`Neo4jRetrievalConfig` (both fold into the last user message on a user turn), so a
current `retrieval_config` user adopting the store gets memory injected twice — two
search fan-outs, two blocks.

`Neo4jRetrievalConfig` is **kept and fully supported**: it is the only injection path
below strands 1.44, and its sources are declaratively configured rather than decided by
a store. When the session manager detects an active `MemoryManager` injecting from our
store, it logs a warning naming the duplication and both ways out — once per session-manager
instance, not per turn. No
deprecation, no runtime disabling.

## Backend capability matrix

| Capability | bolt | NAMS |
|---|---|---|
| `search` entities | yes, with score | yes, no score, `threshold` ignored, singular `type` |
| `search` preferences / facts | yes | `NotSupportedError` → auto-gated off |
| `add` kind routing | entity / preference / fact | entity only; others fall back to the sink |
| `add_messages` | yes, `extract_entities=True` honoured | yes, extraction server-side and unconditional |
| `get_entity_graph` tool | yes, configurable depth | yes via `expand_graph`, 1 hop |
| `get_user_preferences` tool | yes | omitted — no preferences endpoint |

Source: `nams/long_term.py:1-27` and its `NotSupportedError` methods.

## TypeScript parity

Same shape, TS idiom: one options object, `class Neo4jMemoryStore implements MemoryStore`,
type-only SDK imports so the published bundle keeps zero runtime dependencies.
`getTools()` reaches `tool()` through the existing lazy `await import("@strands-agents/sdk")`.

| Divergence | Handling |
|---|---|
| No `searchFacts`; `search_preferences`, `get_related_entities`, `add_preference`, `add_fact`, `add_relationship`, `get_entity_by_name` are `"unsupported"` on REST (`transport/rest.ts:171-177`) | **TS store is entities-only** for `search()`. `includeFacts` / `includePreferences` are absent from the TS options type — a compile error, not a silently ignored flag. `get_entity_graph` **is** available via `expandGraph` (1 hop, `transport/rest.ts:284`); `get_user_preferences` is not |
| `bulkAddMessages` caps at 100 per call (`short-term/index.ts:283`) | store chunks internally |
| No extraction flags on TS writes | none needed — NAMS extracts server-side |
| No bolt transport | `minScore` accepted, meaningful on Python/bolt only |

Those methods exist in the TS client because `BridgeTransport` is a generic
`POST {endpoint}/{snake_case_method}` forwarder used by the TCK against a Python
reference adapter. They work on the bridge, throw on REST. TS and Python agree on the
NAMS surface, so no live-API verification is needed.

Guards in TS attach in `Neo4jConversationManager.initAgent`, the only place an agent
reference arrives. Honest asymmetry: `Neo4jSessionStorage` used **without** the
conversation manager gets no guard, because nothing hands it an agent.

## Error handling

| Failure | Behaviour |
|---|---|
| One search kind fails | logged, skipped; other kinds still return |
| Whole `search` fails | propagates; the manager logs and continues with other stores |
| `add` / `add_messages` fails | propagates; the manager aggregates into `AggregateMemoryError` |
| `NotSupportedError` on a routed `add` | falls back to the sink, logs once per store |
| `initialize` fails | propagates — aborts agent construction, as Strands intends |
| Paired with store extraction on | `ValueError` at construction |

## Testing

| Layer | Coverage |
|---|---|
| Python unit | `search` fan-out with per-kind isolation; limit precedence; `MemoryEntry` metadata; `add` kind routing incl. `NotSupportedError` fallback; `add_messages` dedupe across a retried batch; sink minting idempotence; `get_tools`; client ownership; both guards |
| Python integration | bolt via docker-compose; NAMS gated on a key, skipped without |
| TS unit | mirrored test names against the existing msw/bridge setup, in `test/unit/strands/memory-store.test.ts` |
| Guards | extend `tests/unit/integrations/strands_fakes.py` with a fake agent exposing `memory_manager`; assert the private-attribute read fails loudly if strands moves it |
| Examples | `tests/examples/test_no_phantom_methods.py` covers the new example automatically |

Cross-language parity is enforced by deliberately mirrored test names plus the tables in
this spec — not by the TCK, which does not cover integrations.

## Documentation requirements

1. Both guides lead their Quick Start with the store. `aws-strands.adoc` currently leads
   with tools + session manager and gets restructured.
2. Memory-tools section: explicit note that `search_context` is superseded by the store
   for recall; the factory is retained for deep graph work.
3. `Neo4jRetrievalConfig` section: same note for injection, pointing at `MemoryManager`
   injection, consistent with Guard 2.
4. One sentence stating the split in Strands' vocabulary (session manager restores
   sessions, memory store feeds the agent loop).
5. The word "memory" comes off the session-manager surface wherever it is not
   load-bearing.
6. The three deployment shapes and the pairing rule appear in both guides.
7. TS guide states plainly that the TS store is entities-only, and why.

Both guides were cross-linked in PR #181; the `MemoryStore` sections land in that structure.

## Packaging

- `pyproject.toml`: `strands` extra floor `strands-agents>=0.1.0` → `>=1.44.0`
  (`MemoryStore` landed in 1.44.0; current release 1.52.0). Forces an upgrade on existing
  session-manager users — accepted.
- `typescript/package.json`: devDep `@strands-agents/sdk` `^1.2.0` → `^1.13.0`
  (TS memory landed in 1.6.0).
- CHANGELOG entries in both SDKs.
- Examples: `examples/strands-memory-store/` (Python, `llm=None` + local
  sentence-transformers, no API keys, wired into `example-tests` CI) and a memory-store
  variant under `typescript/examples/strands`.

## Sequencing

| PR | Contents |
|---|---|
| 1 | TS module split: `src/integrations/strands.ts` (846 lines) → `src/integrations/strands/` with `index.ts` re-exporting; `exports` map retargeted; no behaviour change. Sequenced first so the store lands on the final layout |
| 2 | Python store: implementation, tests, example, guide section, floor bump |
| 3 | TS store: implementation, tests, example, guide section, devDep bump |

Afterwards, a second entry in the Strands integrations catalog
(`integrationType: memory-store`), bundled with the pending `maintainedBy: partner` and
session-manager description retune — one YAML PR against `strands-agents/harness-sdk`,
needing explicit approval. Context for the positioning ask:
[harness-sdk#3871](https://github.com/strands-agents/harness-sdk/pull/3871).

## Key design decisions (record)

| Decision | Outcome |
|---|---|
| one store class or two | **One class per SDK**, defining both sinks. `_has_method` fixes sinks per class in Python, but defining both gives server-side extraction by default and keeps `add` live |
| what `search()` maps to | LTM fan-out over entities / preferences / facts, configurable per kind, auto-gated by backend. Not `get_context()` — that blends short-term and reasoning |
| scoping | `user_id` scopes reads; `conversation_id` scopes writes; one store per scope |
| what `add()` writes | message into the sink with extraction by default; `metadata["kind"]` routes to a typed write |
| `get_tools()` | graph-only tools (`get_entity_graph` on both backends, `get_user_preferences` on bolt), on by default, `graph_tools=False` to suppress. Avoids the existing `add_memory` name collision between `context_graph_tools` and the manager's add tool |
| write durability | awaited; the buffered path is unusable without bypassing the message pipeline, and is bolt-only |
| TS parity | `Neo4jMemoryStore` from `@neo4j-labs/agent-memory/integrations/strands`, type-only SDK import, entities-only |
| which conversation `add_messages` uses | dedicated sink conversation, minted unless supplied |
| positioning | store is the preferred memory construct; session manager keeps transcript duties and gains no memory framing; two guards enforce the boundary at runtime |

## Limitations (documented, accepted)

- Retried extraction batches dedupe **in-process only**; a process restart mid-retry can
  duplicate messages in the sink conversation.
- Relevance scores are bolt-only. NAMS entity search returns none.
- TS store is entities-only, so `search()` results are narrower than Python-on-bolt, and
  its `get_entity_graph` traverses one hop rather than a configurable depth.
- A store paired with the session manager writes turns to a sink conversation separate
  from the readable chat history, so turn text exists twice in the graph. Entities
  converge via resolution/dedupe; the duplication is in messages, not knowledge.
- Guard 1 reads one private strands attribute (`MemoryManager._stores`).
- No guard in TS when `Neo4jSessionStorage` is used without `Neo4jConversationManager`.
