# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[0.1.0]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.1.0
[0.0.5]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.5
[0.0.4]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.4
[0.0.3]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.3
[0.0.2]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.2
[0.0.1]: https://github.com/neo4j-labs/agent-memory/releases/tag/v0.0.1
