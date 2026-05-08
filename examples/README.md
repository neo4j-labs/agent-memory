# `neo4j-agent-memory` Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Runnable examples for [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory) — from one-file demos to full-stack apps with Next.js frontends and multi-agent orchestration.

> ⚠️ **Neo4j Labs Project**
>
> These examples are part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. They are actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## How to choose an example

| If you want to… | Start here |
|---|---|
| See the smallest possible memory hello-world | [`basic_usage.py`](#basic-usage) |
| Lay the library on top of a graph you already have in production | [`existing-graph/`](#existing-graph) |
| Stop blocking the user-visible response on Neo4j writes | [`buffered-writes/`](#buffered-writes) |
| Wire 1-hop "what touched this entity?" audit queries | [`audit-trail/`](#audit-trail) |
| Score memory quality like any other regression metric | [`eval-harness/`](#eval-harness) |
| Run with no LLM at all (air-gapped, offline, deterministic) | [`no_llm/`](#run-without-an-llm) |
| Use it from a framework | [`langchain_agent.py`](#langchain), [`pydantic_ai_agent.py`](#pydantic-ai), [`google_adk_demo/`](#google-adk-demo), [`microsoft_agent_retail_assistant/`](#microsoft-agent-retail-assistant) |
| See a full-stack reference app | [`full-stack-chat-agent/`](#full-stack-chat-agent), [`lennys-memory/`](#lennys-podcast-memory-explorer) |
| See a multi-agent compliance workflow | [`financial-services-advisor/`](#financial-services-advisor) |
| Tune entity extraction for a specific domain | [`domain-schemas/`](#domain-schemas) |
| Wire it to Google Cloud (Vertex AI, ADK, MCP) | [`google_cloud_integration/`](#google-cloud-integration) |
| Resolve duplicate entities | [`entity_resolution.py`](#entity-resolution) |
| Enrich entities with Wikipedia/Diffbot data | [`enrichment_example.py`](#enrichment) |

---

## Standalone scripts

Single-file demos. Run them directly with `uv run python examples/<name>.py`.

### Basic usage

`basic_usage.py` — the canonical hello-world. Walks through all three memory types (short-term conversation, long-term entities/preferences/facts, reasoning traces), plus geocoding, batch loading, and graph export. Good first read.

### Entity resolution

`entity_resolution.py` — exact, fuzzy, semantic, and composite resolution strategies for matching new entity references against existing ones. No Neo4j required — runs purely in-memory.

### Enrichment

`enrichment_example.py` — fetch additional entity data from Wikipedia (free) and Diffbot (API key) and merge it onto your nodes. Demos direct provider use, caching, composite providers, and end-to-end Neo4j integration.

### LangChain

`langchain_agent.py` — `Neo4jAgentMemory` and `Neo4jMemoryRetriever` for use with LangChain agents. Requires the `[langchain,openai]` extras.

### Pydantic AI

`pydantic_ai_agent.py` — `MemoryDependency` and `create_memory_tools()` for use with PydanticAI agents. Requires the `[pydantic-ai,openai]` extras.

---

## Directory examples — v0.2 features

These four examples cover the v0.2 feature drop. Each is self-contained, runs with no LLM and a local embedder, and pairs with a how-to in `docs/`.

### Existing graph

[`existing-graph/`](existing-graph/) — adopt a pre-existing Neo4j graph (`:Person`, `:Movie`, `:Genre` …) as long-term memory entities via `client.schema.adopt_existing_graph(...)`. Idempotent. Configures `SchemaModel.CUSTOM` so library writes target your domain types instead of POLE+O.

### Buffered writes

[`buffered-writes/`](buffered-writes/) — `MemorySettings.memory.write_mode = "buffered"`, `client.buffered.submit(...)`, `client.flush()`, `client.write_errors`. The agent's response to the user is *not* blocked on Neo4j round-trips.

### Audit trail

[`audit-trail/`](audit-trail/) — explicit `:TOUCHED` edges from `ReasoningStep` → `Entity`, an `@on_tool_call_recorded` hook for domain-specific inference, and `TraceOutcome` with indexable `error_kind`. Headline payoff: a one-hop `MATCH (e)<-[:TOUCHED]-(s)` audit query.

### Eval harness

[`eval-harness/`](eval-harness/) — labelled regression cases for memory quality. `EvalSuite` of `AuditCase` and `PreferenceCase`, run via `client.eval.run(suite)`, returns a structured `EvalReport` you can diff between commits.

---

## Directory examples — runtime + tooling

### Run without an LLM

[`no_llm/`](no_llm/) — `llm=None`, sentence-transformers embedder, spaCy + GLiNER extractor with the LLM fallback disabled. Validated at construction time so you never get a surprise API call.

### Domain schemas

[`domain-schemas/`](domain-schemas/) — eight ready-made GLiNER2 schemas (POLE+O, podcast, news, scientific, business, entertainment, medical, legal) and a recipe for your own. One script per domain.

---

## Directory examples — framework integrations

### Google ADK demo

[`google_adk_demo/`](google_adk_demo/) — drop-in `Neo4jMemoryService` for Google's Agent Development Kit. Stores conversation sessions, semantic search across all three memory types, automatic entity extraction.

### Google Cloud integration

[`google_cloud_integration/`](google_cloud_integration/) — comprehensive Google Cloud surface: Vertex AI embeddings, ADK integration, MCP server, full pipeline, Cloud Run deployment notes. Use this if you've already chosen GCP.

### Microsoft Agent retail assistant

[`microsoft_agent_retail_assistant/`](microsoft_agent_retail_assistant/) — full-stack retail shopping assistant on the Microsoft Agent Framework. `Neo4jContextProvider`, `DeduplicationConfig`, GDS-backed recommendations, memory graph visualization.

---

## Directory examples — full-stack reference apps

### Full-stack chat agent

[`full-stack-chat-agent/`](full-stack-chat-agent/) — small PydanticAI + Next.js news-research assistant. All three memory types, SSE streaming, interactive memory graph view. Great middle-weight example.

### Lenny's Podcast Memory Explorer

[`lennys-memory/`](lennys-memory/) — the flagship demo. 299 podcast episodes loaded into a knowledge graph with a 19-tool PydanticAI agent, Wikipedia-enriched entity cards, geospatial map view, NVL graph view, automatic preference learning. **[Live demo →](https://lennys-memory.vercel.app)**

### Financial Services Advisor

[`financial-services-advisor/`](financial-services-advisor/) — multi-agent KYC/AML compliance investigations. Same architecture implemented twice: AWS Strands + Bedrock and Google ADK + Gemini. Supervisor agent orchestrates four specialists (KYC, AML, Relationship, Compliance), all backed by real Cypher queries against Neo4j.

---

## Conventions across examples

- **Async-only.** Every memory operation is a coroutine. From a script, wrap your entry point in `asyncio.run(...)`. From a notebook, prefix calls with `await`.
- **`MemoryClient` lifecycle.** Use `async with MemoryClient(settings) as client:` (the recommended pattern), or `await client.connect()` / `await client.close()`. There is no `initialize()` method.
- **`add_entity` returns a tuple.** Since v0.1.1, `await client.long_term.add_entity(...)` returns `(entity, dedup_result)`. Discard the result with `_, _ = await ...` or unpack to inspect the dedup outcome.
- **POLE+O entity types are strings.** Use `"PERSON"`, `"ORGANIZATION"`, `"LOCATION"`, `"EVENT"`, `"OBJECT"` — not the legacy `EntityType` enum.
- **Vertex AI embedding models.** Use `text-embedding-004`, `textembedding-gecko@003`, `@002`, `@001`, or `gecko-multilingual@001`. Other model names (e.g. `text-embedding-005`) do not exist.
- **Google ADK.** `Runner.run_async()` returns an `AsyncGenerator` — iterate with `async for event in runner.run_async(...)`. Don't `await` it.
- **Local development uv source.** Backend `pyproject.toml` files use `[tool.uv.sources]` with `neo4j-agent-memory = { path = "../../..", editable = true }`. The git URL line is commented out and used for production.

## Running the test suite for examples

Two pytest entry points exercise the examples:

```bash
# Quick (no Neo4j, validates structure + imports)
uv run pytest tests/examples/test_*_quick* tests/examples/test_no_phantom_methods.py

# Full (needs Neo4j; testcontainers will start one if available)
uv run pytest tests/examples
```

The quick suite catches the most common drift — missing imports, renamed APIs, phantom method calls — without needing a database. CI runs both as `example-tests-quick` and `example-tests`.

## Contributing a new example

1. Add a directory under `examples/` (or a single `.py` for a script).
2. Include a README following the [Neo4j Labs guidelines](https://github.com/neo4j-labs) — Labs badge, status badge, community support badge, disclaimer, support section, "verified against" footer.
3. Add a smoke test under `tests/examples/`. Mirror an existing one such as [`tests/examples/test_buffered_writes_example.py`](../tests/examples/test_buffered_writes_example.py) for the structure.
4. Register the test in `.github/workflows/ci.yml` under `example-tests-quick` (no Neo4j) or `example-tests` (with Neo4j).
5. Add a row to the index above.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

## License

Apache 2.0 — see the main `neo4j-agent-memory` repository for details.

---

_This index reflects the state of the `adopt-existing-graph` branch as of 2026-05-03 (current package version v0.1.2; v0.2 surface in development)._
