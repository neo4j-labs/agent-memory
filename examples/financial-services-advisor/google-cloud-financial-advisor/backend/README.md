# Financial Advisor backend (Google ADK + Neo4j Agent Memory)

FastAPI service behind the Google Cloud Financial Advisor example. Setup,
credentials and the product tour live in [the app README](../README.md); this
page is the module map for someone working in this directory.

## Run it

```bash
uv sync                                    # needs Python >=3.12
uv run uvicorn src.main:app --reload --port 8000
uv run pytest                              # 42 offline tests, no Neo4j needed
uv run ruff check src/ tests/
```

Configuration comes from `../.env` (preferred) or `./.env`; see
[`../.env.example`](../.env.example). `NEO4J_PASSWORD` is the only required
variable — without Gemini credentials the app still starts, and the startup log
says that entity extraction is disabled.

## Module map

| Path | What lives there |
|---|---|
| `src/main.py` | FastAPI app, `.env` + google-genai env bootstrap, lifespan that connects `MemoryClient` and builds `Neo4jDomainService` |
| `src/config.py` | Nested pydantic-settings (`VERTEX_AI_*`, `NEO4J_*`, `MEMORY_*`) and the Gemini credential resolution |
| `src/agents/_base.py` | `create_specialist_agent` + `bind_tool` — one factory for all four specialists |
| `src/agents/supervisor.py` | The `LlmAgent` with `sub_agents=[…]`, `load_memory`, and model threading |
| `src/agents/{kyc,aml,relationship,compliance}_agent.py` | Per-specialist tool lists and write tools |
| `src/tools/*.py` | Domain tool functions; each takes `neo4j_service=` (hidden from the LLM by `bind_tool`) |
| `src/services/memory_service.py` | `MemoryClient` construction (Vertex AI embeddings, Gemini extractor, dedup) and the typed helpers the routes use |
| `src/services/neo4j_service.py` | All domain Cypher. Reads via `client.query.cypher`, writes via `client.graph.execute_write` |
| `src/services/adk_events.py` | **The only place that knows the ADK `Event` shape**: `consume_run` / `collect_run` |
| `src/services/trace_writer.py` | Audit-grade reasoning traces (`INITIATED_BY`, `TOUCHED`, `TraceOutcome`) |
| `src/api/routes/` | `chat` (SSE + JSON), `customers`, `alerts`, `investigations`, `graph`, `traces` |
| `src/models/` | Pydantic request/response models |
| `tests/` | Offline FastAPI `TestClient` smoke tests plus unit tests for the event consumer, trace writer and agent wiring |

## Where the interesting code is

* **SSE streaming** — `src/api/routes/chat.py::chat_stream`. Events are produced
  by `services/adk_events.consume_run` and mapped to SSE frames there.
* **Reasoning traces** — `src/services/trace_writer.py`. The trace is written
  *during* the run, so a crash leaves a partial trace rather than nothing.
* **The audit query** — `Neo4jDomainService.get_entity_audit_trail`, exposed as
  `GET /api/graph/audit-trail/{entity_name}`.
* **Hybrid data access** — `src/services/neo4j_service.py`: domain data and
  agent memory share one driver because the service takes the `MemoryClient`.

## Container

`make docker-build` (from the app root) re-exports
`requirements-docker.txt` and builds the image. The Dockerfile deliberately does
not use `uv sync`: `pyproject.toml` points `neo4j-agent-memory` at an editable
path outside the build context.
