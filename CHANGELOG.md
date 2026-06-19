# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Ontology `import` / `diff` / `migrate` on `client.ontology`.** Closes the gap
  where these shipped server-side + REST but weren't reachable from the SDK.
  `import_(content=/url=, format=)` converts an external graph/ontology document
  (Arrows / Neo4j Data Importer / RDF / GraphQL / Cypher / LinkML / native) into a
  non-persisted draft; `diff(id, from_revision, to_revision)` returns a structural
  diff; `migrate(id, ...)` enqueues an async label-rename job and `get_migration(job_id)`
  polls it. New models: `OntologyImportResult`, `ImportWarning`, `OntologyDiff`,
  `MigrationJob`. Mirrored in the TypeScript SDK (`OntologyClient.import/diff/migrate/getMigration`).
- **`client.auth` — API-key management (NAMS).** New Python accessor mirroring the
  TypeScript `AuthClient`: `list_api_keys`, `create_api_key`, `reveal_api_key`,
  `rotate_api_key`, `revoke_api_key`, and `refresh_access_token`, with `ApiKey` /
  `AccessTokenPair` models. On bolt it is a `NotSupportedError` sentinel. Also adds
  `rotateApiKey` to the TypeScript `AuthClient` (was missing).
- **`short_term.get_extraction_status(...)`** — the authoritative per-conversation
  extraction rollup (`ExtractionStatus` with `is_complete` / `pending_count`),
  backed by `GET /v1/conversations/{id}/extraction-status`.
- **`long_term.expand_graph(node_id, loaded_ids=...)`** — 1-hop entity-neighborhood
  expansion (`POST /v1/graph/expand`) for graph visualization; mirrored in the
  TypeScript SDK as `longTerm.expandGraph(nodeId, loadedIds)`.

### Changed

- **Fixed: `LongTermMemory.add()` now returns `Entity`** (per its `BaseMemory[Entity]`
  contract) instead of a `tuple[Entity, DeduplicationResult]`. The tuple return was a
  latent contract violation surfaced by the Phase 1 typing pass; no in-tree caller
  destructured it. Use `add_entity(...)` (unchanged) if you need the
  `(Entity, DeduplicationResult)` pair. (Type-safety effort, spec §6 Phase 1.)
- **Memory timestamps are now timezone-aware (UTC).** Default timestamps in the
  `memory/` layer (`Conversation.created_at`, `ConversationSummary.generated_at`,
  `ReasoningTrace.started_at`, and `_to_python_datetime` fallbacks) switched from
  naive `datetime.utcnow()` to `datetime.now(timezone.utc)`. Reads already produced
  aware datetimes; this makes the write side consistent. (Type-safety effort, spec §6 Phase 1.)
- **Fixed: GLiNER entity spans with `end_pos == 0`.** In `extraction/gliner_extractor.py`
  the span fallback changed from `entity.end_pos or len(name)` to an explicit
  `is not None` check, so a zero-width span at offset 0 is preserved instead of being
  replaced by the name length. Real extracted entities never have `end_pos == 0`, so
  this has no observable effect in practice. (Type-safety effort, spec §6 Phase 1.)
- **BREAKING: `InstructorProvider.__init__` no longer accepts `async_client`.**
  The parameter (default `True`) was removed during the Phase 1 typing pass.
  `async_client=False` produced a synchronous `instructor.Instructor`, which
  crashed at call time because the provider always `await`s `self._client.create(...)`
  — the sync path never worked. The provider is now unconditionally async. Callers
  that explicitly passed `async_client=True` (the working default) must drop the
  argument; `async_client=False` was already non-functional. (Type-safety effort,
  spec §6 Phase 1.)
- **`long_term.wait_for_extraction(...)` now uses the authoritative extraction-status
  endpoint** when a `session_id` / `conversation_id` is supplied (polls until no
  message is pending), instead of only the workspace-scoped entity-search heuristic.
  When entity-level assertions (`expected_names` / `predicate` / `query`) are also
  given, it waits for the conversation to finish *then* confirms via search. Fully
  backward-compatible: the search-only path is unchanged when no conversation id is
  passed.

> **Docs note:** when this ships, flip the "REST-only / no SDK method" notes in
> `reference/rest-api.adoc`, `reference/ontology-api.adoc`, and
> `reference/authentication.adoc`, and the Python↔TS parity note in
> `reference/typescript-api.adoc`, to reflect the new SDK surface.

## [0.5.0] - 2026-05-30

The NAMS-alignment release. Adds workspace addressing, a first-class
ontology surface (`client.ontology`), `conversation_id`/`type` parameter
aliases across the NAMS methods, and an explicit
`long_term.wait_for_extraction(...)` await for the asynchronous NAMS
extraction pipeline. Also makes the Google ADK `Neo4jMemoryService`
NAMS-compatible (#130). This is the first release cut from the polyglot
repository, published under namespaced `python-v*` tags.

### Added

- **Workspace addressing for NAMS.** `NamsConfig.workspace_id` (and the
  `MEMORY_WORKSPACE_ID` environment variable) is transmitted automatically
  as the `X-Workspace-Id` header on every request. This is required by
  deployments that scope by header (e.g. the development/staging service)
  rather than encoding the workspace in the API key (production, unchanged).
  An explicit `X-Workspace-Id` entry in `headers` still wins. The
  `validate_on_connect` probe sends the header so misconfiguration fails fast.
- **Ontology surface (`client.ontology`).** Drive the NAMS domain-ontology
  engine from the SDK: `list()`, `get()`, `get_active()`, `clone()`,
  `create()`, `update()`, `activate()`, `delete()` over the ~28 system
  templates, versioned revisions, and `permissive`/`strict` validation modes.
  Returns typed Pydantic models (`OntologySummary`, `OntologyVersion`,
  `OntologyDocument`, `EntityTypeDef`, …); `get_active()` surfaces the active
  version's `validation_mode`. On the bolt backend the accessor raises
  `NotSupportedError` (define a custom schema with `SchemaModel.CUSTOM`).
- **`conversation_id` alias** accepted across NAMS short-term methods
  (`add_message`, `get_conversation`, `search_messages`, `clear_session`,
  `create_conversation`, `bulk_add_messages`, …) as an alias for `session_id`.
  `session_id` wins when both are supplied; a clear error names both when
  neither is. `entity_type` gains `type`/`label` aliases on `add_entity`.
- **`long_term.wait_for_extraction(...)`** — await the asynchronous NAMS
  extraction pipeline explicitly (polls entity search for `expected_names` /
  a `predicate`; returns a boolean, no raise). A no-op returning `True` on
  bolt, where extraction is synchronous.

### Fixed

- **Google ADK `Neo4jMemoryService` is now NAMS-compatible (#130).**
  `search_memory()` is conversation-scoped on NAMS (it threads the active
  session id and skips message search gracefully when none is known, instead
  of crashing with the unscoped-search `ValueError`); tool events
  (`FunctionCall`/`FunctionResponse`) are filtered out of the entity pipeline
  rather than stringified into it, so they no longer produce empty knowledge
  graphs. Documentation parameter names are reconciled (`session_id` /
  `conversation_id`, `entity_type` / `type`). `MemoryClient` gains `backend`
  and `is_nams` properties.

### Repository

- The repository at `neo4j-labs/agent-memory` is now polyglot. A
  TypeScript SDK (`@neo4j-labs/agent-memory`, in `typescript/`) ships
  alongside the Python SDK with the same memory model and the same NAMS
  backend. _For the TypeScript SDK changelog see
  [`typescript/CHANGELOG.md`](typescript/CHANGELOG.md)._
- Python release tags are now namespaced as `python-v*` (e.g.
  `python-v0.4.1`) so they cannot collide with TypeScript tags
  (`typescript-v*`). The publish workflow has been renamed from
  `release.yml` to `publish-python.yml`.
- The CI workflow has been renamed from `ci.yml` to `ci-python.yml` and
  is now path-filtered. TypeScript-only PRs no longer trigger the
  Python test matrix.

## [0.4.0] - 2026-05-17

The "hosted backend" release. Headline feature is **NAMS (Neo4j Agent Memory Service) backend support** — write once against `MemoryClient`, choose between local bolt-to-Neo4j or the hosted REST service at config time. The change is purely additive — existing v0.3.x code keeps working unchanged on bolt.

### Added

- **NAMS backend** — `MemorySettings(backend="nams", nams=NamsConfig(api_key=...))` routes every `client.short_term`, `client.long_term`, `client.reasoning`, and `client.query` call to the hosted REST API at `https://memory.neo4jlabs.com/v1` (default endpoint, overridable). SPEC tier conformance targets Bronze, Silver, Gold, and Platinum.
- **`neo4j_agent_memory.nams` package** — new public surface:
  - `NamsConfig` — endpoint, api_key (`SecretStr`), timeout, retries, headers, `validate_on_connect`, `transport_mode`.
  - `AuthProvider` Protocol + `StaticApiKeyAuth` (`Authorization: Bearer {api_key}`).
  - `HttpTransport` — httpx-based async client with retry policy (429 honors `Retry-After`, 5xx + network errors retried with exponential backoff), structured error mapping, and OpenTelemetry-style span attributes.
  - Auto-protocol detection: `/v\d+`-shaped endpoints → REST; otherwise TCK bridge protocol (`POST /{snake_case_method}`).
  - `NamsBackend` — composition root that owns the transport + three memory implementations + Cypher accessor. Used by `MemoryClient.connect()` when `backend == "nams"`.
- **`neo4j_agent_memory.core.protocols`** — three new `@runtime_checkable` Protocols (`ShortTermProtocol`, `LongTermProtocol`, `ReasoningProtocol`) plus `CypherQueryProtocol`. Existing `ShortTermMemory`/`LongTermMemory`/`ReasoningMemory` bolt classes structurally satisfy them; new `NamsShortTermMemory`/`NamsLongTermMemory`/`NamsReasoningMemory` implement them over HTTP.
- **`client.query.cypher(query, params)`** — unified read-only Cypher accessor. Works on both backends. On bolt, forwards to `Neo4jClient.execute_read`; on NAMS, forwards to `POST /v1/query` (Platinum). Read-only enforcement uses a shared `is_read_only_query` validator.
- **Platinum-tier methods** on `client.long_term` and `client.short_term`:
  - `set_entity_feedback(entity_id, feedback, user_identifier=...)`
  - `get_entity_history(entity_id, limit=...)`
  - `get_entity_provenance(entity_id)` (Gold; available on bolt and NAMS)
  - `bulk_add_messages(session_id, messages)`
  - `get_observations(session_id, limit=...)`
  - `get_reflections(session_id, limit=...)`
  - `create_conversation(session_id, ...)`
  - `list_conversations(user_identifier=..., limit=...)`

  NAMS implements them via REST; bolt is missing the Platinum surface and raises `NotSupportedError` at call time.
- **New exceptions** in `neo4j_agent_memory.core.exceptions`:
  - `TransportError(ConnectionError)` — HTTP transport failures (5xx, network).
  - `AuthenticationError(MemoryError)` — 401/403.
  - `NotSupportedError(MemoryError)` — structured with `backend`, `method`, `workaround`.
  - `RateLimitError(MemoryError)` — 429, carries `retry_after`.
  - `ValidationError(MemoryError)` — 400, carries `details`.

  Existing `except ConnectionError` blocks still catch transport failures (since `TransportError` is a subclass).
- **NAMS-flavored framework helpers**:
  - `integrations.pydantic_ai.nams_memory_tools(memory)` — base tools + Platinum tools (`set_entity_feedback`, `get_entity_history`, `get_entity_provenance`, `cypher_query`) as Pydantic AI tools.
  - `integrations.strands.nams_context_graph_tools(endpoint=..., api_key=...)` — Strands `@tool` functions for NAMS Platinum operations. Auto-reads `MEMORY_API_KEY`/`MEMORY_ENDPOINT` from env.
- **MCP server**:
  - Four new Platinum tools (`memory_set_entity_feedback`, `memory_get_entity_history`, `memory_get_entity_provenance`, `memory_get_reflections`) registered automatically when the underlying `MemoryClient` uses `backend == "nams"`.
  - `register_tools(mcp, *, profile, register_platinum)` parameter.
- **CLI** — `neo4j-agent-memory mcp serve` accepts new flags:
  - `--backend {bolt,nams}` (env: `NAM_BACKEND`)
  - `--api-key` (env: `MEMORY_API_KEY`)
  - `--endpoint` (env: `MEMORY_ENDPOINT`)
- **Environment variables** — `MEMORY_API_KEY` and `MEMORY_ENDPOINT` are recognized by `MemorySettings`. When `MEMORY_API_KEY` is set and `backend` is unspecified, the backend defaults to NAMS (otherwise bolt — preserving the historical default).
- **Documentation**:
  - `explanation/backends.adoc` — bolt vs NAMS trade-offs and feature matrix.
  - `how-to/use-nams.adoc` — first-time NAMS setup.
  - `how-to/migrate-to-nams.adoc` — porting bolt code; full `NotSupportedError` table.
  - `tutorials/nams-quickstart.adoc` — 5-minute walkthrough.
- **Examples**:
  - `examples/nams-quickstart/` — minimal end-to-end NAMS usage.
  - `examples/nams-langchain/` — LangChain `ConversationChain` backed by NAMS.
  - `examples/nams-fastapi/` — FastAPI app with lifespan-managed NAMS client.
- **Tests** — `tests/unit/nams/` (12 test files, respx-based, 320+ tests). `tests/integration/nams/` scaffold for TCK conformance suite (Bronze/Silver/Gold/Platinum) once the TCK reference Docker image is available.

### Changed

- **`MemoryClient.connect()`** dispatches on `MemorySettings.backend`. When unset, NAMS if `MEMORY_API_KEY` is in the environment, otherwise bolt.
- **`client.graph`** raises `NotSupportedError` on NAMS — use `client.query.cypher()` for portable read-only queries. On bolt, returns a deprecation proxy that emits a one-time `DeprecationWarning` on `execute_read` calls; `execute_write` continues to work without deprecation.
- **`client.users`, `client.buffered`, `client.consolidation`** — on NAMS, accessors return a `_NamsUnsupported` sentinel that raises `NotSupportedError` with workaround hints on any method call.
- **`client.schema`** — on NAMS, returns a `_NamsUnsupported` sentinel (`adopt_existing_graph` and other schema operations are server-managed by NAMS).
- **`client.get_stats`, `client.get_graph`, `client.get_locations`** — raise `NotSupportedError` on NAMS (rely on bolt-specific Cypher; use `client.query.cypher()` with a custom query).
- **MCP `graph_query` tool, `_get_entity_neighbors` helper, `memory://graph/stats` resource** — migrated from `client.graph.execute_read` to `client.query.cypher`. Now portable across both backends.
- **Strands `context_graph_tools` internal Cypher**, **AgentCore `HybridMemoryProvider._enrich_with_relationships`**, and **`EvalMemory._eval_audit`** — same migration. All work on both backends.

### Deprecated

- **`client.graph.execute_read`** — use `client.query.cypher` for portable read-only queries. Emits one-time `DeprecationWarning` per process. Scheduled for removal in **v0.6.0**.

### Migration

Existing v0.3.x users:

* Stay on bolt → **no code changes required**.
* Switch to NAMS → set one env var:
  ```bash
  export MEMORY_API_KEY=nams_xxxxx
  ```
  `MemoryClient()` now talks to NAMS. Methods that don't apply (geocoding, enrichment, deduplication knobs) emit a single `UserWarning` at `connect()` time listing the inactive layers.
* For custom Cypher:
  ```python
  # v0.3 (deprecated, still works on bolt)
  results = await client.graph.execute_read("MATCH (n:Entity) RETURN n LIMIT 10")
  # v0.4 (works on both backends)
  results = await client.query.cypher("MATCH (n:Entity) RETURN n LIMIT 10")
  ```

See `docs/.../how-to/migrate-to-nams.adoc` for the full migration cookbook and `NotSupportedError` reference table.

### Backward compatibility

* Every public class/function from v0.3.x is importable unchanged.
* All existing examples and integration tests pass on bolt without modification.
* The SPEC-aligned method names (`add_message`, `add_entity`, `start_trace`, ...) were already present in v0.3 — they were the source for the SPEC.
* `BaseMemory[T]` ABC stays as the bolt base class; the new Protocols supplement it without replacing it.

## [0.3.0] - 2026-05-12

The "bring your own model" release. Headline feature is **pluggable LLM and embedding providers** — Anthropic, Bedrock, Vertex AI, local sentence-transformers, and 100+ others via LiteLLM now slot in alongside OpenAI through a single Protocol. Existing v0.2.x code keeps working with a one-time `DeprecationWarning`; the legacy `EmbeddingConfig`/`LLMConfig` types are removed in v0.5.0.

### Added

- **`neo4j_agent_memory.llm` package** — new public surface for the Provider Protocol. Three `@runtime_checkable` Protocols:
  - `LLMProvider` — chat completions (Bronze tier).
  - `StructuredExtractor` — validated Pydantic outputs (Silver tier).
  - `EmbeddingProvider` — text embeddings (Bronze tier).
  Plus types (`ChatMessage`, `Completion`, `Usage`), a provider-agnostic exception hierarchy (`ProviderError`, `ProviderRateLimitError`, `ProviderTimeoutError`, `ProviderAuthError`, `ProviderInvalidRequestError`, `ProviderServiceError`, `StructuredExtractionError`, `EmbeddingDimensionMismatchError`), a defaults lookup (`EMBEDDING_DIMENSIONS`, `lookup_embedding_dimensions`), the schema-aligned retry helper (`schema_aligned_extract`), and the factory (`from_provider`).
- **Native adapters** — `OpenAIProvider`, `OpenAIEmbeddingProvider`, `AnthropicProvider` (with optional Anthropic prompt-caching via `cache_system=True`), `BedrockProvider`, `BedrockEmbeddingProvider`, `SentenceTransformersProvider`, `VertexAIEmbeddingProvider`, `InstructorProvider`.
- **Universal adapter** — `LiteLLMProvider` and `LiteLLMEmbeddingProvider` cover 100+ providers (Cohere, Voyage, Groq, Together, Mistral, Ollama, OpenRouter, ...).
- **`from_provider("provider/model", ...)`** string-shorthand factory with native-first resolution: when both a native adapter and LiteLLM are installed, the native adapter wins. Override with `prefer_litellm=True`. Unknown-extra errors include an actionable install hint.
- **Vector index dimension validation** — `SchemaManager.validate_vector_index_dimensions(expected)` runs in `MemoryClient.connect()`. A mismatch between the configured embedder's `dimensions` and an existing Neo4j vector index raises `EmbeddingDimensionMismatchError` listing every offending index and pointing at the migration runbook (`how-to/migrate-embedding-model.adoc`).
- **`MCPserve --llm` / `--embedding`** CLI flags (plus `--llm-api-key`, `--llm-api-base`, `--embedding-dimensions`) and matching `NAM_LLM` / `NAM_EMBEDDING` / `NAM_LLM_API_KEY` env vars on `neo4j-agent-memory mcp serve`.
- **LLM-driven session reflections** — `MemoryObserver(llm_provider=...)` uses the configured provider to summarize older messages when the token threshold trips. Falls back to keyword extraction on missing provider or provider error.
- **LLM-driven conversation summaries** — `ShortTermMemory(default_llm_provider=...)` wires the configured provider into `get_conversation_summary()` so the method works without an explicit `summarizer=` callable. A module-level `_llm_summarizer(provider)` helper builds the equivalent callable for user code.
- **Framework pass-through helpers** — `llm_provider_from_langchain`, `_pydantic_ai`, `_llamaindex`, `_crewai`, `_openai_agents`, `_microsoft_agent`, `_google_adk`, and `_strands` translate a framework-native model object into an `LLMProvider`. Lets users avoid double-declaring their model.
- **New installation extras** — `[litellm]` (universal fallback) and `[instructor]` (structured-output power user). `[all]` now bundles `litellm`; `[full]` adds `instructor`.
- **New tests** — `tests/unit/llm/` (foundations + contract harness + canned providers), `tests/unit/test_legacy_compat.py` (deprecation + v0.2 backward compat), `tests/unit/test_passthrough.py`, `tests/unit/test_observer_llm.py`, `tests/unit/test_short_term_summarizer.py`, `tests/unit/test_index_validate.py`.

### Changed

- **`MemorySettings.embedding` and `MemorySettings.llm`** are now union types. They accept:
  - the legacy `EmbeddingConfig` / `LLMConfig` (emits one `DeprecationWarning` per construction when user-explicit; planned removal in v0.5.0),
  - a provider-string shorthand resolved via `from_provider` (`"openai/text-embedding-3-small"`, `"anthropic/claude-3-5-sonnet-latest"`),
  - a fully-constructed Provider instance (the new canonical shape).
  Dict input is still coerced to legacy configs for v0.2 compatibility.
- **`MemoryClient.connect()`** sizes vector indexes from the resolved embedder's `dimensions`, not from the legacy `EmbeddingConfig.dimensions` field — works correctly when a Provider instance is configured.
- **`LLMEntityExtractor`** accepts an injected `provider: LLMProvider | StructuredExtractor`. The legacy `model=` / `api_key=` constructor signature continues to work and now constructs the provider internally via `from_provider`. When the provider also implements `StructuredExtractor`, the extractor uses `complete_structured` with a `LLMExtractionPayload` Pydantic model for native-quality structured outputs.
- **Strands integration** routes `embedding_provider` strings through `from_provider` instead of mapping to the legacy enum.

### Deprecated

- **`EmbeddingConfig` and `LLMConfig`** as values for `MemorySettings.embedding` / `MemorySettings.llm`. Passing either at construction time emits a single `DeprecationWarning` pointing at the migration guide. **Removal planned for v0.5.0.** The classes themselves remain importable through v0.5.0 for type-import usage in user code.

### Migration

Three patterns are common. Pick the one closest to your existing code.

**v0.2.x — legacy explicit configs (still works, one warning per construction):**

```python
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig, EmbeddingProvider, LLMConfig, LLMProvider,
)

settings = MemorySettings(
    neo4j={"password": "p"},
    embedding=EmbeddingConfig(
        provider=EmbeddingProvider.OPENAI,
        model="text-embedding-3-small",
    ),
    llm=LLMConfig(provider=LLMProvider.OPENAI, model="gpt-4o-mini"),
)
```

**v0.3 — provider-string shorthand (recommended for most users):**

```python
from neo4j_agent_memory import MemoryClient, MemorySettings

settings = MemorySettings(
    neo4j={"password": "p"},
    embedding="openai/text-embedding-3-small",
    llm="anthropic/claude-3-5-sonnet-latest",
)
```

**v0.3 — explicit Provider instance (full control, e.g. local vLLM/Ollama):**

```python
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.llm.adapters.litellm import LiteLLMProvider

settings = MemorySettings(
    neo4j={"password": "p"},
    embedding="BAAI/bge-small-en-v1.5",
    llm=LiteLLMProvider(
        "ollama/llama3.2",
        api_base="http://localhost:11434",
    ),
)
```

See `docs/.../how-to/migrate-to-providers.adoc` for the full migration cookbook, including embedding-dimension migration when changing models.

### Backward compatibility

- All v0.2.x example code runs unmodified, emitting exactly one `DeprecationWarning` per `MemoryClient` construction. Verified by `tests/unit/test_legacy_compat.py`.
- The legacy `Embedder` Protocol in `neo4j_agent_memory.embeddings.base` remains; a new internal `_ProviderToEmbedderAdapter` bridges new Provider instances back to the old API for downstream memory layers that have not migrated.
- `extra="forbid"` on `MemorySettings` is preserved; field-name typos still raise.

## [0.2.1] - 2026-05-05

### Fixed

- **`MemorySettings(...)` no longer raises `extra_forbidden` for unrelated `.env` keys.** pydantic-settings 2.x leaks `.env` keys outside the configured `env_prefix` into the validation payload, which collided with `MemorySettings`'s `extra="forbid"` (used to catch code-level typos). Common symptoms: instantiating `MemorySettings(neo4j={...})` failed with `extra_forbidden` on `neo4j_uri` / `neo4j_password` / `openai_api_key` whenever the user's `.env` contained the unprefixed equivalents (e.g. `NEO4J_URI=...` from a Docker setup, or `OPENAI_API_KEY=...` for an unrelated tool). `MemorySettings.settings_customise_sources` now wraps the dotenv source to drop keys that aren't top-level model fields. `NAM_*`-prefixed nested loads (e.g. `NAM_NEO4J__URI`) are unchanged, and the typo guard for kwargs (`MemorySettings(schema=...)`) still raises. New `TestDotEnvFiltering` regression tests in `tests/unit/test_config.py`.

## [0.2.0] - 2026-05-04

The v0.2 feature drop. Headline feature is **adopting an existing Neo4j graph** as long-term memory; the rest are production-readiness primitives.

### Added

- **Adopt an existing graph** — `client.schema.adopt_existing_graph(label_to_type=..., name_property_per_label=...)` attaches the `:Entity` super-label and the library's required `id`/`type`/`name` properties to nodes in your existing domain graph. Idempotent. After adoption, library writes (entity extraction, MENTIONS edges, relation writes) link to your existing nodes instead of creating duplicates. New how-to: `docs/.../how-to/adopt-existing-graph.adoc`. New example: `examples/existing-graph/`.
- **Multi-tenancy** — `MemorySettings.memory.multi_tenant=True` plus a `user_identifier=` kwarg on short-term, long-term, and reasoning APIs scopes reads and writes per tenant. New `client.users` (`UserMemory`) layer for first-class `:User` identity. New how-to: `docs/.../how-to/multi-tenancy.adoc`.
- **Buffered (fire-and-forget) writes** — `MemorySettings.memory.write_mode = "buffered"` plus `client.buffered.submit(query, params)`, `client.flush()`, `client.wait_for_pending()`, and `client.write_errors`. Decouples user-visible latency from Neo4j round-trips. New how-to: `docs/.../how-to/buffered-writes.adoc`. New example: `examples/buffered-writes/`.
- **Consolidation primitives** — `client.consolidation` exposes `dedupe_entities()`, `summarize_long_traces()`, `detect_superseded_preferences()`, and `archive_expired_conversations()`. All default to `dry_run=True`. New how-to: `docs/.../how-to/consolidation.adoc`.
- **Evaluation harness** — `client.eval.run(EvalSuite(...))` for labelled regression tests over memory quality (recall@k for retrieval, audit-coverage of `:TOUCHED` paths, preference fidelity). New how-to: `docs/.../how-to/evaluation.adoc`. New example: `examples/eval-harness/`.
- **Audit-trail / TOUCHED edges** — `record_tool_call(touched_entities=[...])`, `@client.reasoning.on_tool_call_recorded` hook for domain-specific inference, `TraceOutcome` with indexable `error_kind`. Headline payoff: a one-hop `MATCH (e)<-[:TOUCHED]-(s)` audit query. New how-to: `docs/.../how-to/audit-reasoning.adoc`. New example: `examples/audit-trail/`.
- **Privacy & encryption** — `core.encryption` helper plus `docs/.../how-to/privacy-and-audit.adoc`.
- **Schema objects reference** — declarative constraints/indexes documented at `docs/.../reference/schema-objects.adoc`.
- **Glossary page** — `docs/.../glossary.adoc`.
- **README async-only callout** — explicit guidance that every memory operation is a coroutine.
- **Generic phantom-method guard** — `tests/examples/test_no_phantom_methods.py` cross-references every `client.<layer>.<method>(` call in `examples/` against the actual class API. Catches silent breakage when an example calls a method that doesn't exist (typically renamed or never landed).
- **Smoke test for `enrichment_example.py`** — `tests/examples/test_enrichment_example.py`.

### Changed

- All 14 example READMEs migrated to the Neo4j Labs branding template (Labs badge, status badge, community-supported badge, disclaimer block, support section, "verified against" footer). New top-level `examples/README.md` index.

### Fixed (during the v0.2 examples-review pass)

- `examples/google_cloud_integration/adk_memory_service.py` — printed code-example showed deprecated `await memory_client.initialize()`; corrected to `await memory_client.connect()`.
- `examples/enrichment_example.py` — called phantom `client.long_term.get_entity(entity.id)` (no such public method); fixed to `get_entity_by_name(entity.name)`.
- `examples/basic_usage.py` — called phantom `client.long_term.get_entity_coordinates()`; fixed to `get_location_coordinates()`. Also tightened `add_entity()` callers to consistently demonstrate the v0.1.1+ tuple return.
- `examples/langchain_agent.py` — same `add_entity()` consistency fix.
- `examples/lennys-memory/scripts/load_transcripts.py` — called phantom `client.short_term.get_messages()`; fixed to use `get_conversation()` and check `.messages`. The previous call was wrapped in `except Exception: pass`, so it silently failed at runtime, defeating the dedup check on transcript loads.
- `examples/lennys-memory/backend/src/api/routes/threads.py` — called phantom `client.short_term.delete_conversation()`; fixed to `clear_session()`. Also wrapped in `except Exception: pass`, so the thread-delete endpoint was silently returning success without actually deleting from Neo4j.

## [0.1.2] - 2026-04-29

### Added

- **Optional LLM (`llm=None`)**: `MemorySettings.llm` is now `Optional[LLMConfig]`. Pass `llm=None` to construct a fully working `MemoryClient` without any LLM provider — useful for air-gapped environments, deployments without an `OPENAI_API_KEY`, and deterministic local-only extraction. A new `examples/no_llm/` example and a "Run Without an LLM" how-to guide demonstrate the spaCy/GLiNER-only setup.

### Changed

- **Validator on `MemorySettings`**: setting `llm=None` together with extraction settings that require an LLM (`extractor_type=ExtractorType.LLM`, or `extractor_type=PIPELINE` with `enable_llm_fallback=True`) now raises a `ValidationError` at construction time, naming both fields and suggesting the minimal fix. Omitting the `llm` field entirely preserves the historical default of auto-filling an `LLMConfig` when an LLM stage is enabled, so existing code is unaffected.

## [0.1.1] - 2026-04-23

### Added

- **Fact and Preference Deduplication on Creation** (PR [#97](https://github.com/neo4j-labs/agent-memory/pull/97)): `add_fact()` and `add_preference()` now check for existing entries with matching subject/predicate (or category/preference) and >0.95 embedding similarity. When a duplicate is found, the existing record is returned with `metadata["deduplicated"] = True`, and confidence is updated when the new value is higher.
- **Metadata on `memory_add_fact` MCP Tool** (PR [#103](https://github.com/AhmedHamadto/agent-memory/pull/103)): Exposed the `metadata` parameter on the `memory_add_fact` MCP tool, matching the existing `memory_add_entity` interface and the underlying `LongTermMemory.add_fact()` API.
- **AWS Strands Multi-Agent Financial Services Example** (PR [#99](https://github.com/neo4j-labs/agent-memory/pull/99)): Aligned the AWS and GCP financial services examples with shared entity extraction, visualization, and persistent investigation patterns.

### Fixed

- **Google ADK `BaseMemoryService` Inheritance** (PR [#106](https://github.com/neo4j-labs/agent-memory/pull/106), PR [#107](https://github.com/neo4j-labs/agent-memory/pull/107)): `Neo4jMemoryService` now inherits from `google.adk.memory.BaseMemoryService` for proper ADK compatibility, with stricter package detection and updated method signatures (`search_memory` return type).
- **LlamaIndex Remote Timeout** (PR [#102](https://github.com/neo4j-labs/agent-memory/pull/102)): Adjusted timeout handling in the LlamaIndex integration.

### New Contributors

- [@AhmedHamadto](https://github.com/AhmedHamadto) made their first contribution in PR [#97](https://github.com/neo4j-labs/agent-memory/pull/97) and PR [#103](https://github.com/neo4j-labs/agent-memory/pull/103)
- [@kaustubh-darekar](https://github.com/kaustubh-darekar) made their first contribution in PR [#106](https://github.com/neo4j-labs/agent-memory/pull/106) and PR [#107](https://github.com/neo4j-labs/agent-memory/pull/107)

## [0.1.0] - 2026-04-02

### Added

- **MCP Server Enhancements** (PR #80): Major expansion of the MCP server with tool profiles, observational memory, and preference detection
  - **Tool Profiles**: `core` (6 tools) and `extended` (16 tools) profiles to control context overhead
  - **MemoryIntegration Layer**: High-level convenience wrapper with session strategies (`per_conversation`, `per_day`, `persistent`), auto-extraction, and preference detection — shared by MCP server and applications
  - **Observational Memory**: `MemoryObserver` tracks accumulated context per session and generates keyword-based reflections when token thresholds are exceeded
  - **Automatic Preference Detection**: Pattern-based `PreferenceDetector` identifies user preferences from messages with zero-latency, zero-cost regex patterns
  - **Server Instructions**: LLM guidance sent during MCP initialization to direct tool usage patterns
  - **Extended MCP Tools**: 10 additional tools including conversation history, session listing, entity details, graph export, relationship creation, reasoning traces, observations, and read-only Cypher queries
  - **MCP Tool Annotations**: All tools annotated with `readOnlyHint`, `destructiveHint`, `idempotentHint` for client introspection
  - **CLI MCP Command**: `neo4j-agent-memory mcp serve` with `--profile`, `--session-strategy`, `--user-id`, `--observation-threshold`, and `--no-auto-preferences` flags
  - **MCPB Manifest**: `.mcpb` manifest for Claude Desktop extension directory (`deploy/mcpb/`)
- **Documentation**: MCP server tutorial, MCP tools reference, create-context-graph how-to guide

### Fixed

- Fixed `session_id` parameter usage in `_detect_and_store_preferences` context field
- Corrected CLI flag names in Google Cloud documentation (`--uri`/`--password` not `--neo4j-uri`/`--neo4j-password`)

## [0.0.5] - 2026-03-07

### Added

- **FastMCP Migration** (PR #67): Rewrote MCP server using FastMCP v2, replacing the low-level `mcp` SDK
  - Decorator-based `@mcp.tool()` API for all 6 memory tools (search, store, entity lookup, conversation history, graph query, reasoning traces)
  - **MCP Resources**: 4 new resource endpoints (`memory://conversations/{session_id}`, `memory://entities/{entity_name}`, `memory://preferences/{category}`, `memory://graph/stats`)
  - **MCP Prompts**: 3 guided workflow prompts (`memory_search_guide`, `entity_analysis`, `conversation_summary`)
  - Lifespan-based server initialization with `create_mcp_server()` factory function
  - Shared `get_client()` context helper for accessing `MemoryClient` from tool/resource handlers
  - Read-only query validation for `graph_query` tool to prevent write operations
  - Backward-compatible `Neo4jMemoryMCPServer` wrapper preserved
- **MCP Test Suite**: Comprehensive unit tests for tools, resources, prompts, and server initialization using FastMCP's native `Client`

### Changed

- **Managed Transactions** (PR #71): `execute_read()` and `execute_write()` in `Neo4jClient` now use Neo4j managed transactions with `@unit_of_work` decorator
  - Automatic retry on transient failures
  - Query metadata tagging with `neo4j-agent-memory` version for server-side tracking
  - Better resource cleanup via driver-managed connection lifecycle
- **MCP Dependency**: Changed from `mcp>=1.0.0` to `fastmcp>=2.0.0,<3` in optional dependencies

### New Contributors

- [@MuddyBootsCode](https://github.com/MuddyBootsCode) made their first contribution in PR [#67](https://github.com/neo4j-labs/agent-memory/pull/67)
- [@darrellwarde](https://github.com/darrellwarde) made their first contribution in PR [#71](https://github.com/neo4j-labs/agent-memory/pull/71)

## [0.0.4] - 2026-02-25

### Added

- **Microsoft Agent Framework Integration** (Preview): Complete integration with Microsoft's Agent Framework (`agent-framework>=1.0.0b260212`)
  - `Neo4jMicrosoftMemory` main memory class with context retrieval, message storage, and search
  - `Neo4jContextProvider` for automatic context injection via Agent Framework hooks
  - `Neo4jChatMessageStore` implementing the `ChatMessageStore` protocol for persistent conversation history
  - `create_memory_tools()` generating `FunctionTool` instances for memory search, store, entity lookup, and preferences
  - `record_agent_trace()` for recording reasoning traces from Agent Framework runs
  - `GDSIntegration` with Graph Data Science algorithms (PageRank, shortest path, node similarity) and Cypher fallbacks
  - `GDSConfig` for configuring GDS algorithm parameters
- **MemoryClient.graph Property**: Exposes underlying `Neo4jClient` for custom Cypher queries and domain-specific services
- **Location Query Enhancements**: `get_locations()`, `search_locations_near()`, and `search_locations_in_bounding_box()` methods on long-term memory
- **Graph Export Improvements**: Filtering by memory types, session_id, and date ranges
- **New Example Application**: Google Cloud Financial Advisor — multi-agent compliance demo with AML, KYC, relationship, and compliance specialist agents using Google ADK, Vertex AI, and Neo4j
  - `Neo4jDomainService` pattern wrapping `MemoryClient.graph` for custom domain queries
  - Domain data loading for sanctions, PEP, and alerts data
- **Documentation**: Framework comparison guide updated for all 7 integrations, Microsoft Agent Framework how-to and tutorial guides
- **Test Coverage**: 55+ Microsoft Agent Framework tests, 82 financial advisor tests, 26 example validation tests

### Changed

- Framework comparison documentation expanded from 6 to 7 integrations
- README.md updated with Microsoft Agent Framework integration example

### Fixed

- Microsoft Agent Framework `FunctionTool` assertions in tests updated for object-based API (`.name` instead of dict subscript)
- Ruff linting fixes for import sorting and duplicate set items

## [0.0.3] - 2026-02-18

### Added

- **AWS Integration**: Comprehensive Amazon Web Services ecosystem support
  - AWS Strands Agents integration with 4 context graph tools (search, entity graph, add memory, user preferences)
  - Amazon Bedrock embeddings (Titan Embed v2/v1, Cohere English/Multilingual v3) with batch support
  - AWS Bedrock AgentCore `MemoryProvider` for native AgentCore memory persistence
  - `HybridMemoryProvider` with intelligent routing strategies (auto, explicit, short-term-first, long-term-first)
- **Google Cloud Integration**: Comprehensive Google Cloud ecosystem support
  - Vertex AI embeddings (`text-embedding-004`, gecko models) with async non-blocking I/O
  - Google ADK `MemoryService` for native ADK agent memory persistence
- **MCP Server**: Model Context Protocol server with 6 tools (memory search, store, entity lookup, conversation history, graph query, reasoning traces)
  - Supports stdio and SSE transports, CLI command: `neo4j-agent-memory mcp serve`
- **Cloud Run Deployment**: Production-ready Dockerfile, Cloud Build config, and Terraform templates
- **New Example Applications**:
  - Google Cloud Financial Advisor: Full-stack multi-agent compliance demo with AML, KYC, relationship, and compliance agents (FastAPI + React/TypeScript)
  - AWS Financial Services Advisor: Strands Agents multi-agent demo with Bedrock LLM and embeddings
  - Google ADK demo: Session storage with entity extraction and memory search
- **Documentation**: Antora-based docs restructuring, Strands Agent quickstart tutorial, Google Cloud and AWS integration guides

### Changed

- Centralized all Cypher queries into `graph/queries.py` module for maintainability
- Short-term memory now auto-links messages sequentially (`FIRST_MESSAGE`/`NEXT_MESSAGE` relationships)
- Optional dependency stubs now raise `ImportError` with install instructions instead of returning `None`

### Fixed

- MCP handler event dispatch fixes
- Entity type parameter error and APOC fallback handling
- Cypher query fixes for entity search, tool calls, and relationship extraction
- Lenny's Memory demo: improved initial loading speed, graph view, tool call result cards, mobile responsiveness, and entity enrichment

## [0.0.2] - 2026-01-29

### Added

- **Agent Framework Integrations**: Improved integration APIs for multiple AI frameworks
  - OpenAI Agents integration improvements
  - LangChain, Pydantic AI, LlamaIndex, and CrewAI support
  - Async handler context improvements
- **Reasoning Trace Search**: Fixed reasoning trace visibility in demo app search tools with improved exposure control for sensitive data
- **Documentation Improvements**: Comprehensive documentation restructuring using the Diataxis framework (tutorials, how-to guides, reference, explanation)
- **New Example Applications**:
  - Lenny's Podcast Memory Explorer demo with 299 episodes, 19 specialized tools, and interactive graph visualization
  - Full-Stack Chat Agent with FastAPI backend and Next.js frontend
  - Financial Services Advisor domain-specific example
  - Microsoft Agent Retail Assistant example
  - 8 domain schema examples (POLEO, podcast, news, scientific, business, entertainment, medical, legal)

### Changed

- Entity types now support string-based POLE+O classification with dynamic Neo4j label creation
- Improved deduplication configuration with auto-merge thresholds
- Enhanced provenance tracking for entity creation
- Refactored `procedural.*` memory abstraction to `reasoning.*` top level APIs

### Fixed

- Tracing API fixes for string/enum value support
- String serialization fixes in async handlers

## [0.0.1] - 2026-01-22

### Added

- Initial release of Neo4j Agent Memory
- **Three-Layer Memory Architecture**:
  - Short-Term Memory: Conversation history with temporal context and session management
  - Long-Term Memory: Entity and fact storage using POLE+O data model (Person, Object, Location, Event, Organization)
  - Reasoning Memory: Tool usage tracking and reasoning traces
- **Entity Extraction Pipeline**:
  - Multi-stage extraction with spaCy, GLiNER, and LLM fallback
  - Merge strategies: union, intersection, confidence-based, cascade, first-success
  - Batch and streaming extraction support
  - GLiNER2 domain schemas
  - GLiREL relation extraction
- **Entity Resolution & Deduplication**:
  - Multiple strategies: exact, fuzzy (RapidFuzz), semantic (embeddings), composite
  - Automatic deduplication on ingest
  - Duplicate review workflow with SAME_AS relationships
- **Vector + Graph Search**:
  - Semantic similarity search with embeddings
  - Graph traversal for relationship queries
  - Neo4j vector indexes (requires Neo4j 5.11+)
  - Metadata filtering with MongoDB-style syntax
- **Entity Enrichment**:
  - Wikipedia and Diffbot data enrichment
  - Background enrichment service
  - Geocoding with spatial indexing
- **Observability**:
  - OpenTelemetry integration
  - Opik tracing support
- **CLI Tool**: Command-line interface for entity extraction and schema management
- **Schema Persistence**: Store and version custom entity schemas in Neo4j

[0.5.0]: https://github.com/neo4j-labs/agent-memory/releases/tag/python-v0.5.0
[0.4.0]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.4.0
[0.1.0]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.1.0
[0.0.5]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.5
[0.0.4]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.4
[0.0.3]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.3
[0.0.2]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.2
[0.0.1]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.1
