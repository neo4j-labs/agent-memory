# NAMS + FastAPI

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The production wiring for a memory-backed HTTP service: one lifespan-managed `MemoryClient`, an authenticated tenant id, a typed error taxonomy, and a `/chat` route that actually *reads* memory back.

Backed by the hosted **Neo4j Agent Memory Service (NAMS)** — no Neo4j to operate, no embedding or extraction key.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Routes

| Route | What it does |
|---|---|
| `POST /chat` | One turn: read assembled context, store the user message, generate a reply (stub), store the reply. |
| `GET /conversations` | The caller's conversations — how a returning client recovers its id. |
| `GET /conversations/{id}` | Message count. Costs two NAMS reads, which is why it is *not* in `/chat`. |
| `GET /conversations/{id}/search` | Semantic recall inside one conversation. |
| `GET /health` | Liveness. No I/O; 503 when the transport is closed. |
| `GET /ready` | Readiness. One cheap authenticated NAMS call. |

## Tenancy: what NAMS scopes, and what you must

| Boundary | Who enforces it |
|---|---|
| Workspace | **NAMS**, from your API key (or the `X-Workspace-Id` header on header-scoped deployments — set `MEMORY_WORKSPACE_ID`). |
| Conversation owner | **NAMS**, from the `userId` sent by `create_conversation(..., user_identifier=...)`. |
| Per-user isolation of *requests* | **Your application.** A conversation id is not a credential, so this app derives the tenant key from a verified bearer token and re-checks ownership via `list_conversations(user_identifier=...)` on every request. |

One thing the SDK does **not** do: `short_term.add_message()` accepts only `{content, role}` on NAMS and silently drops `user_identifier`. Passing it there looks like scoping and does nothing, so this example does not — the conversation carries ownership instead.

## Prerequisites

- Python 3.10+
- A NAMS API key from <https://memory.neo4jlabs.com>
- No model key: the agent is a deterministic stub, marked with the one comment you replace

## Setup

```bash
uv pip install -r requirements.txt
cp .env.example .env
# edit .env: set MEMORY_API_KEY
```

`main.py` loads this directory's `.env` itself (the process environment always wins, so a container's injected config is never overridden).

## Run

```bash
# The README's curl uses the dev header mode; drop this for bearer-token auth.
ALLOW_INSECURE_USER_HEADER=1 uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/docs> for the generated OpenAPI UI.

### Two authentication modes

| Mode | How to call it | When |
|---|---|---|
| Bearer token (default) | `-H 'Authorization: Bearer demo-alice'` | Always. `verify_token()` is the stub you replace with your JWT/session verification; it returns the subject (`alice`). |
| `X-User-Id` header | `-H 'X-User-Id: alice'`, and start the server with `ALLOW_INSECURE_USER_HEADER=1` | Local demos only. Unauthenticated, logged as a warning on every request, and rejected with 401 unless the flag is set. |

## Try it

```bash
# 1. Liveness
curl -s http://127.0.0.1:8000/health

# 2. First turn — omit conversation_id; the service creates one under your user id
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: alice' \
  -d '{"message": "Hello there!"}'

# 3. Second turn — send back the conversation_id from step 2
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: alice' \
  -d '{"message": "Remember: I prefer dark mode.", "conversation_id": "<id from step 2>"}'

# 4. What is stored, and recall inside the conversation
curl -s -H 'X-User-Id: alice' http://127.0.0.1:8000/conversations/<id>
curl -s -H 'X-User-Id: alice' 'http://127.0.0.1:8000/conversations/<id>/search?q=dark+mode'
```

## Expected output

```json
// 1. GET /health
{"status": "ok"}

// 2. POST /chat — no context yet, so context_used is false
{"conversation_id": "00000000-0000-0000-0000-0000000000aa",
 "reply": "(no memory yet) echo: Hello there!",
 "user_message_id": "fab34bd7-93b6-4166-8fe9-9148f81e7e4c",
 "context_used": false}

// 3. POST /chat — the second turn reads turn 1 back out of memory
{"conversation_id": "00000000-0000-0000-0000-0000000000aa",
 "reply": "(with memory) echo: Remember: I prefer dark mode.",
 "user_message_id": "7a820071-0da8-41c9-ab59-dcf9dad091ad",
 "context_used": true}

// 4. GET /conversations/{id} — two turns, user + assistant each
{"conversation_id": "00000000-0000-0000-0000-0000000000aa", "message_count": 4}

// 4. GET /conversations/{id}/search?q=dark+mode
[{"message_id": "7a820071-0da8-41c9-ab59-dcf9dad091ad",
  "role": "user",
  "content": "Remember: I prefer dark mode."},
 {"message_id": "556832c0-0037-44d1-862a-5dbeec965b4c",
  "role": "assistant",
  "content": "(with memory) echo: Remember: I prefer dark mode."}]
```

Ids differ per run; `conversation_id` is minted by NAMS. `reply` comes from the
`generate_reply()` stub — replace it with your agent call and pass `context`
into the prompt.

## Error taxonomy

App-level handlers, not a per-route `try/except`, so the read paths are covered
too. The backend's message is logged server-side and never echoed to the caller
(it can carry endpoint and configuration detail).

| Library exception | HTTP | Response |
|---|---|---|
| `RateLimitError` | 429 | `Retry-After` passed through from the server |
| `ValidationError` | 400 | `{"detail": ..., "errors": exc.details}` — the caller's problem, so the details help |
| `NotFoundError` | 404 | `{"detail": "Not found"}` |
| `NotSupportedError` | 501 | names the method and backend |
| `AuthenticationError` | 500 | *our* API key is bad — a config error, never the caller's 401 |
| `TransportError` / `MemoryError` | 502 | `{"detail": "Memory backend unavailable"}` |

Import the library's `ValidationError` under an alias (`as MemoryValidationError`): the bare name collides with `pydantic.ValidationError`, which FastAPI also handles.

## Production notes

* **One client per process.** `MemoryClient` owns an HTTP connection pool and is safe to share across concurrent requests. Open it in `lifespan`, close it in `finally` — never per request.
* **Fail fast at boot.** A missing `MEMORY_API_KEY` raises during startup, and `validate_on_connect` (default on) spends one request verifying credentials and reachability, so bad config never reaches the load balancer's healthy set. Set `MEMORY_VALIDATE_ON_CONNECT=0` where cold-start latency is user-visible.
* **Retries are built in.** 429 honours `Retry-After`; 5xx and network errors use exponential backoff (`MEMORY_MAX_RETRIES`, default 3). No app-side retry needed — and note that `max_retries=0` also means no `Retry-After` to pass on.
* **Graceful shutdown.** The lifespan closes the pool when uvicorn receives `SIGTERM`. Don't bypass it with custom signal handling.
* **Tunables** (`.env.example`): `MEMORY_TIMEOUT`, `MEMORY_MAX_RETRIES`, `MEMORY_VALIDATE_ON_CONNECT`, `MEMORY_WORKSPACE_ID`, `MEMORY_ENDPOINT`.
* **Observability.** The NAMS transport emits HTTP-semantic-convention spans (`http.method`, `http.url`, `http.status_code`, `nams.method`, `nams.protocol`) **only when a tracer is injected into the transport**. `MemorySettings` has no `tracer` field, and `MemoryClient` does not pass one, so on this code path no NAMS span is emitted today. Instrument the FastAPI app itself (e.g. `opentelemetry-instrumentation-fastapi`); for the library side, the only injection point is building the backend yourself with `NamsBackend.from_config(settings.nams, tracer=...)`. See [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams).
* **Bolt-only accessors raise.** `client.users`, `client.buffered`, `client.consolidation` and `schema.adopt_existing_graph()` are not available on NAMS; they raise `NotSupportedError`, which this app maps to 501.

### Hand-rolled session ids vs `MemoryIntegration`

This example derives conversation ownership itself because a service needs the
ownership check. If you only want per-user or per-day session ids, the library
ships that logic:

```python
from neo4j_agent_memory import MemoryIntegration, SessionStrategy

memory = MemoryIntegration(
    session_strategy=SessionStrategy.PER_DAY,  # "{user_id}-YYYY-MM-DD"
    user_id="alice",
    auto_preferences=False,  # required on NAMS: the background detector calls
                             # long_term.add_preference(), which raises
                             # NotSupportedError there (logged once per turn)
)
```

## Going further

- **Hosted backend:** [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams) and [`examples/nams-quickstart/`](../nams-quickstart/)
- **Trade-offs:** [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends)
- **Framework version of the same wiring:** [`examples/nams-langchain/`](../nams-langchain/)

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`), `fastapi` 0.141.1 and `uvicorn` 0.52.4 (the versions `uv pip compile` resolves from `requirements.txt`), with the app driven over ASGI and the NAMS transport mocked (`tests/examples/test_nams_fastapi_example.py`). uvicorn itself is not exercised by the tests. Dated 2026-09-10._
