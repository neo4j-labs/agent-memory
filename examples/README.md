# `neo4j-agent-memory` Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Runnable examples for [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory) — from a thirteen-line round trip to full-stack apps with Next.js frontends and multi-agent orchestration.

> ⚠️ **Neo4j Labs Project**
>
> These examples are part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. They are actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## How to choose an example

| If you want to… | Start here |
|---|---|
| Run the smallest possible round trip — one file, no checkout, no virtualenv | [`hello-memory/`](#hello-memory) |
| Read a guided tour of the whole API surface | [`basic_usage.py`](#basic-usage) |
| Run against the hosted service with just an API key (no Neo4j) | [`nams-quickstart/`](#hosted-backend-nams) |
| Share one memory graph across a whole team, with no agent code | [`claude-code-team-memory/`](#hosted-backend-nams) |
| Evolve a typed domain model and re-type an existing graph | [`ontology-lifecycle/`](#hosted-backend-nams) |
| Put memory behind an HTTP API | [`nams-fastapi/`](#hosted-backend-nams) |
| Lay the library on top of a graph you already have in production | [`existing-graph/`](#existing-graph) |
| Stop blocking the user-visible response on Neo4j writes | [`buffered-writes/`](#buffered-writes) |
| Wire 1-hop "what touched this entity?" audit queries | [`audit-trail/`](#audit-trail) |
| Gate CI on memory quality like any other regression metric | [`eval-harness/`](#eval-harness) |
| Run with no LLM at all (air-gapped, offline, deterministic) | [`no_llm/`](#run-without-an-llm) |
| Tune entity extraction for a specific domain | [`domain-schemas/`](#domain-schemas) |
| Resolve duplicate entities | [`entity_resolution.py`](#entity-resolution) |
| Enrich entities with Wikipedia/Diffbot data | [`enrichment_example.py`](#enrichment) |
| Use it from a framework | [`langchain_agent.py`](#langchain), [`pydantic_ai_agent.py`](#pydantic-ai), [`google_adk_demo/`](#google-adk-demo), [`microsoft_agent_retail_assistant/`](#microsoft-agent-retail-assistant) |
| Persist a Strands agent's conversation automatically (no tool calls) | [`strands-session-manager/`](#strands-session-manager) |
| Give a Strands agent cross-session recall from a graph | [`strands-memory-store/`](#strands-memory-store) |
| Wire it to Google Cloud (Vertex AI, ADK, MCP) | [`google_cloud_integration/`](#google-cloud-integration) |
| See a full-stack reference app | [`full-stack-chat-agent/`](#full-stack-chat-agent), [`lennys-memory/`](#lennys-podcast-memory-explorer) |
| See a multi-agent compliance workflow | [`financial-services-advisor/`](#financial-services-advisor) |
| Write the memory layer in TypeScript instead | [`../typescript/examples/`](#typescript-examples) |

---

## Hosted backend (NAMS)

Run against the hosted [NAMS](https://memory.neo4jlabs.com) service — no Neo4j to operate and no LLM/embedding key required (extraction and embeddings run server-side). Set `MEMORY_API_KEY` and the backend auto-selects NAMS.

| Example | Description |
|---|---|
| [`nams-quickstart/`](nams-quickstart/) | Minimal end-to-end flow over the unified `MemoryClient` — a conversation, messages, an entity, a reasoning trace, `wait_for_extraction()`, and a read-only `client.query.cypher` round-trip. The same script body runs on bolt by flipping `backend`, so it doubles as the bolt-vs-NAMS diff. |
| [`nams-fastapi/`](nams-fastapi/) | NAMS-backed memory inside a FastAPI service: one lifespan-managed client, per-user scoping from the authenticated request, the library's error taxonomy mapped onto HTTP status codes, a `/chat` route that reads assembled context before answering, and a `/search` route. |
| [`nams-langchain/`](nams-langchain/) | The same `create_agent` + middleware agent as [`langchain_agent.py`](#langchain), against hosted NAMS — server-side extraction and embeddings, no Neo4j to run. |
| [`ontology-lifecycle/`](ontology-lifecycle/) | The full NAMS ontology lifecycle: import an Arrows diagram into a typed schema, activate it, ingest under it, then rename a type and migrate the already-extracted entities — `import_`, `diff`, `migrate` + `get_migration` polling, with a `query.cypher` read-back. Hosted-only; the bolt twin is [`existing-graph/`](#existing-graph). |
| [`claude-code-team-memory/`](claude-code-team-memory/) | Shared memory for Claude Code, Claude Desktop and Cursor with **no agent code** — ready-to-copy `.mcp.json` / `claude_desktop_config.json` / `.cursor/mcp.json` files wiring the hosted NAMS MCP server (47 scope-gated tools, OAuth) and the self-hosted `mcp serve` (6 or 16 tools) side by side, plus `provision_keys.py` (one rotatable `client.auth` key per developer), `seed_workspace.py` (`bulk_add_messages` → await extraction → read back) and `doctor.py` (key, config validity, tool surface, reachability, extraction status). |

```bash
cp examples/.env.example examples/.env   # then set MEMORY_API_KEY=nams_...
uv run python examples/nams-quickstart/main.py
```

See [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams) and [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends) for the trade-offs.

---

## Standalone scripts

Single-file demos. Run the top-level scripts with `uv run python examples/<name>.py`; `hello-memory/` is a one-file directory because it carries a PEP 723 header.

### Hello memory

[`hello-memory/`](hello-memory/) — the front door. One file, thirteen lines of body: two messages in, one entity, one preference, the assembled context back out. `main.py` carries a [PEP 723](https://peps.python.org/pep-0723/) header, so `uv run examples/hello-memory/main.py` builds its own environment — no checkout, no virtualenv, no `pip install`. Runs on hosted NAMS with `MEMORY_API_KEY`, or on bolt with `NEO4J_URI`.

### Basic usage

`basic_usage.py` — the guided tour: twelve numbered sections across all three memory types (short-term conversation, long-term entities/preferences/facts, reasoning traces), plus geocoding, batch loading, consolidation and graph export. Read [`hello-memory/`](#hello-memory) first if you want the shortest possible round trip, and [`nams-quickstart/`](#hosted-backend-nams) for the hosted path.

### Entity resolution

`entity_resolution.py` — six sections over the resolver strategies: exact, fuzzy, semantic (embedder-backed), and composite chaining, including the type-aware guarantee that a `PERSON` is never merged into a `LOCATION`. No Neo4j required — runs purely in-memory. Optional extras: `[fuzzy]` for the RapidFuzz stage, `[sentence-transformers]` for the semantic stage (both sections self-skip when the extra is absent).

### Enrichment

`enrichment_example.py` — fetch additional entity data from Wikipedia (free) and Diffbot (API key) and merge it onto your nodes. Demos direct provider use, caching, composite providers, and end-to-end Neo4j integration. Enriched fields land in `entity.metadata` (see the conventions note below). Needs `httpx`, which the `[nams]` extra already installs.

### LangChain

`langchain_agent.py` — a LangChain 1.x `create_agent` agent with memory: `Neo4jMemoryMiddleware` for context injection and turn persistence, a reasoning-trace middleware, and `Neo4jMemoryRetriever` as a retriever tool. Runs with no API key (scripted model + local embedder). Requires the `[langchain-agents]` extra; add `[openai]` plus `langchain-openai` for a real model turn.

### Pydantic AI

`pydantic_ai_agent.py` — `MemoryDependency`, `create_memory_tools()` and `record_agent_trace()` wired into a PydanticAI 2.x agent: two real turns where the agent calls the memory tools, the exchange persisted with `save_interaction()`, each run recorded as a reasoning trace and read back, and the second turn seeing the first. Requires the `[pydantic-ai]` extra; with `OPENAI_API_KEY` it runs on `OpenAIChatModel` (the same model instance also drives memory extraction), and without a key it runs offline on `pydantic_ai.models.test.TestModel` plus the `[sentence-transformers]` extra.

---

## Directory examples — v0.2 features

These four examples cover the v0.2 feature drop. Each is self-contained, runs with no LLM and a local embedder, and pairs with a how-to in `docs/`.

### Existing graph

[`existing-graph/`](existing-graph/) — adopt a pre-existing Neo4j graph (`:Person`, `:Movie`, `:Genre` …) as long-term memory entities via `client.schema.adopt_existing_graph(...)`. Idempotent. Configures `SchemaModel.CUSTOM` so library writes target your domain types instead of POLE+O. Four scripts: `seed.py` (behind a `--reset` safety flag), `adopt.py`, `memory_io.py`, `retrieve.py`.

### Buffered writes

[`buffered-writes/`](buffered-writes/) — `MemorySettings.memory.write_mode = "buffered"`, `client.buffered.submit(...)`, `client.flush()`, `client.write_errors`, and a back-pressure section that drops `max_pending` to 4 so the bounded queue is observable. The agent's response to the user is *not* blocked on Neo4j round-trips.

### Audit trail

[`audit-trail/`](audit-trail/) — explicit `:TOUCHED` edges from `ReasoningStep` → `Entity`, an `@on_tool_call_recorded` hook for domain-specific inference, and `TraceOutcome` with indexable `error_kind`. Headline payoff: a one-hop `MATCH (e)<-[:TOUCHED]-(s)` audit query. Bolt only — `:TOUCHED` edges are a Cypher-level feature.

### Eval harness

[`eval-harness/`](eval-harness/) — labelled regression cases for memory quality. `RetrievalCase`, `AuditCase` and `PreferenceCase` over a two-tenant fixture, run via `client.eval.run(suite, dimensions=[...])`, plus a `ci_gate.py` that exits non-zero below a threshold and writes a JSON report you can upload as a CI artifact.

---

## Directory examples — runtime + tooling

### Run without an LLM

[`no_llm/`](no_llm/) — `llm=None`, `backend="bolt"`, sentence-transformers embedder, spaCy + GLiNER extractor with the LLM fallback disabled. Exercises all three memory layers locally, runs a consolidation dry run, and fails fast at construction time when a local model is missing, so you never get a surprise API call.

### Domain schemas

[`domain-schemas/`](domain-schemas/) — eight ready-made GLiNER2 schemas (POLE+O, podcast, news, scientific, business, entertainment, medical, legal), a recipe for your own, and shared sample documents under `samples/`. One runner: `uv run python examples/domain-schemas/run.py --schema <name>` (the eight per-domain scripts remain as thin deprecated wrappers).

---

## Directory examples — framework integrations

### Strands session manager

[`strands-session-manager/`](strands-session-manager/) — `Neo4jSessionManager` on `Agent(session_manager=...)`: every turn persisted and restored automatically, opt-in `<user_context>` injection from long-term memory, per-analyst `user_id=` scoping, tool calls mirrored into reasoning memory, and the shared-brain pattern (N agents, one graph). Runs with no LLM or API key.

### Strands memory store

[`strands-memory-store/`](strands-memory-store/) — `Neo4jMemoryStore` on `MemoryManager(stores=[...])`: entity/preference/fact recall fed into the agent loop, plus graph-native tools a `MemoryManager` cannot provide. Complementary to the session manager. Also runs with no API key.

### Google ADK demo

[`google_adk_demo/`](google_adk_demo/) — a real ADK `Runner` loop (`LlmAgent` + `load_memory`) backed by `Neo4jMemoryService`; two turns across two sessions prove cross-session recall; runs with no API keys via a scripted model and a local embedder.

### Google Cloud integration

[`google_cloud_integration/`](google_cloud_integration/) — Vertex AI embeddings (`gemini-embedding-001`), ADK memory, the FastMCP 4 server (6 core / 16 extended tools), a reasoning-trace `:TOUCHED` audit query, buffered writes for Cloud Run, and a consolidation dry run. Use this if you have already chosen GCP.

### Microsoft Agent retail assistant

[`microsoft_agent_retail_assistant/`](microsoft_agent_retail_assistant/) — full-stack retail shopping assistant on the Microsoft Agent Framework 1.x GA. `Neo4jContextProvider`, a working entity-deduplication review queue, reasoning audit edges (`:TOUCHED`), GDS-backed recommendations, a shopper switcher that exercises the preference write path, and a memory graph visualization.

---

## Directory examples — full-stack reference apps

### Full-stack chat agent

[`full-stack-chat-agent/`](full-stack-chat-agent/) — FastAPI + PydanticAI 2.x + Next.js over two Neo4j graphs (memory plus a seeded local news graph); SSE with live tool events, reasoning traces with `:TOUCHED` audit edges, entity extraction switched by `EXTRACTION_MODE`. Bolt only (it uses `client.get_graph()`). Great middle-weight example; the frontend has its own README, lint/typecheck/test scripts and a Node 22 floor.

### Lenny's Podcast Memory Explorer

[`lennys-memory/`](lennys-memory/) — the flagship Python demo. A podcast knowledge graph with a 28-tool PydanticAI agent, Wikipedia-enriched entity cards, geospatial map view, NVL graph view and automatic preference learning. The real transcript corpus is not shipped — `make load-sample` runs against the synthetic fixtures in `data/samples/`. **[Live demo →](https://lennys-memory.vercel.app)**

### Financial Services Advisor

[`financial-services-advisor/`](financial-services-advisor/) — multi-agent KYC/AML compliance investigations. Same architecture implemented twice: AWS Strands + Bedrock, and Google ADK + Gemini on Python 3.12 with the `[google-adk,vertex-ai,litellm]` extras. A supervisor agent orchestrates four specialists (KYC, AML, Relationship, Compliance), all backed by real Cypher queries against Neo4j.

---

## TypeScript examples

The TypeScript SDK [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory) ships its own gallery at [`../typescript/examples/`](../typescript/examples/) — nine examples covering the Vercel AI SDK middleware, a Next.js 16 App Router flagship app, an MCP server, LangChain JS, Mastra, Strands, the ontology lifecycle, a Cloudflare Worker on `fetch` alone, and an agentic-commerce assistant. See [`../typescript/examples/README.md`](../typescript/examples/README.md) for the index and the TypeScript contributing checklist.

---

## Conventions across examples

- **Async-only.** Every memory operation is a coroutine. From a script, wrap your entry point in `asyncio.run(...)`. From a notebook, prefix calls with `await`.
- **`MemoryClient` lifecycle.** Use `async with MemoryClient(settings) as client:` (the recommended pattern), or `await client.connect()` / `await client.close()`. There is no `initialize()` method.
- **Environment loading.** Single-file examples do `from _env import NEO4J_URI, NEO4J_PASSWORD, ...`; [`examples/_env.py`](_env.py) loads `examples/.env` (copy [`examples/.env.example`](.env.example)) and falls back to a tiny parser when `python-dotenv` is absent. Directory examples with their own `.env.example` load that file instead.
- **Model ids come from the environment.** Never hard-code a chat or embedding model id. Read it from an env var with a current default — `OPENAI_MODEL` (default `gpt-5-mini`), `GEMINI_MODEL` (default `gemini-2.5-flash`), `VERTEX_EMBEDDING_MODEL` (default `gemini-embedding-001`), `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`) — so an example survives the next retirement.
- **`add_entity` returns a tuple.** Since v0.1.1, `await client.long_term.add_entity(...)` returns `(entity, dedup_result)`. Discard the result with `_, _ = await ...` or unpack to inspect the dedup outcome.
- **POLE+O entity types are strings.** Use `"PERSON"`, `"ORGANIZATION"`, `"LOCATION"`, `"EVENT"`, `"OBJECT"` — not the legacy `EntityType` enum.
- **Custom entity types are uppercase.** `add_entity` normalises types, but the `:TOUCHED` MERGE stores `type` verbatim, so `EntityRef(type="Client")` creates a second `:Entity` node that `add_entity` can never match. Write `EntityRef(type="CLIENT")`.
- **Message roles are plain strings.** Pass `"user"` / `"assistant"` / `"system"` to `add_message`. A `MessageRole` enum member is also accepted, and `MessageRole` is what reads back off a `Message`, so compare with `msg.role.value`.
- **Enrichment lands in `entity.metadata`.** Wikipedia/Diffbot fields (`enriched_description`, `wikipedia_url`, `wikidata_id`, `image_url`, `enriched_at`) are entries in the `metadata` dict, not attributes on `Entity`. Read them as `(entity.metadata or {}).get("wikipedia_url")`.
- **Vertex AI embedding models.** Use `gemini-embedding-001` (the default — 3072 native dimensions, truncated to 768 by `VertexAIEmbedder` so existing vector indexes keep working), `text-embedding-005`, or `text-multilingual-embedding-002`. `text-embedding-004` (shut down 2026-01-14) and the `textembedding-gecko*` family (shut down 2025-04-09) are retired and now raise an `EmbeddingError` naming the replacement. Read the id from `VERTEX_EMBEDDING_MODEL` rather than hard-coding it.
- **Google ADK.** Memory is a `Runner`-level service — `Runner(memory_service=...)` plus the `load_memory` tool. `LlmAgent` has no `memory=` field. `Runner.run_async()` returns an `AsyncGenerator`, so iterate it with `async for event in runner.run_async(...)`; don't `await` it. `search_memory()` returns a `SearchMemoryResponse` — iterate `response.memories`.
- **Multi-file example directories.** A directory example whose scripts import each other adds its own directory to `sys.path` at the top of each entrypoint (`sys.path.insert(0, str(Path(__file__).parent))`), which is why `examples/**/*.py` carries an `E402` ruff exemption.
- **PEP 723 is allowed in `hello-memory/` only.** Every other example declares its dependencies in a `requirements.txt` or `pyproject.toml` so the pin is reviewable and Dependabot can see it.
- **Local development uv source.** Backend `pyproject.toml` files pin `neo4j-agent-memory[...]>=0.5.0,<0.7` and add a `[tool.uv.sources]` entry pointing at the repo root, relative to the backend directory (`{ path = "../../..", editable = true }` — one more `..` for a backend nested two levels down). The git URL line is commented out and used for production.

## Running the test suite for examples

Every example has a smoke-test module under [`../tests/examples/`](../tests/examples/). Two entry points:

```bash
# Quick (no Neo4j — structure, imports, manifests, phantom methods)
uv run pytest tests/examples -m "syntax or imports"

# Full (needs Neo4j; testcontainers will start one if available)
uv run pytest tests/examples
```

Equivalent `make` targets: `make test-examples-quick` and `make test-examples`. The exact set CI runs is defined in [`.github/workflows/ci-python.yml`](../.github/workflows/ci-python.yml) (`example-tests-quick` with no database, `example-tests` with a Neo4j service) — that file is the source of truth. `tests/examples/test_examples_registry.py` fails if an example directory has no test module, no README footer, or no index row in this file.

## Contributing a new example

1. Add a directory under `examples/` (or a single `.py` for a script).
2. Pin the library as `neo4j-agent-memory[...]>=0.5.0,<0.7` in `requirements.txt` or `pyproject.toml`, and read every model id from an environment variable with a current default.
3. Include a README following the [Neo4j Labs guidelines](https://github.com/neo4j-labs) — Labs badge, status badge, community support badge, disclaimer, prerequisites, run steps, expected output, support section, and a "verified against" footer naming the library version, the framework versions you tested, and the date.
4. Add a smoke test under `tests/examples/`. Mirror an existing one such as [`tests/examples/test_buffered_writes_example.py`](../tests/examples/test_buffered_writes_example.py) for the structure, and mark the classes that need a database with `@pytest.mark.requires_neo4j`.
5. Register the test in `.github/workflows/ci-python.yml` under `example-tests-quick` if it needs no Neo4j (`example-tests` picks up the whole directory automatically).
6. Add a row to the index above — `tests/examples/test_examples_registry.py` enforces it.

### Contributing a TypeScript example

1. Add a directory under `typescript/examples/<name>/` with `"@neo4j-labs/agent-memory": "file:../.."`, `"engines": {"node": ">=22"}`, and caret-pinned frameworks.
2. Extend [`typescript/examples/tsconfig.base.json`](../typescript/examples/tsconfig.base.json) rather than re-declaring compiler options.
3. Add `lint` (`tsc --noEmit`) and `test` scripts; keep the test suite offline (no API key).
4. Add the directory to the `type-check-examples` matrix in `.github/workflows/ci-typescript.yml`.
5. Add a row to [`typescript/examples/README.md`](../typescript/examples/README.md).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

## License

Apache 2.0 — see the main `neo4j-agent-memory` repository for details.

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (in-tree; NAMS hosted-backend support shipped in v0.4.0, workspace addressing and the ontology surface in v0.5.0) and [`@neo4j-labs/agent-memory`](https://www.npmjs.com/package/@neo4j-labs/agent-memory) 0.4.1 on npm, on 2026-09-10. Examples pinning unreleased surface say so in their own footers._
