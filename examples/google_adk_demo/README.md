# Google ADK Memory Demo

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> A real Google ADK loop — `LlmAgent` + `Runner` + `load_memory` — with `Neo4jMemoryService` as the `memory_service`. Two turns in two different ADK sessions; the second one remembers the first because the memory lives in Neo4j.

`Neo4jMemoryService` implements Google ADK's `BaseMemoryService`, so ADK's own
`load_memory` tool reads straight out of Neo4j. This example shows the whole
wiring in one file: a turn through the `Runner`, the ADK `Session` handed to
`add_session_to_memory()`, curated entities, preference learning, and recall
from a *fresh* ADK session that has no conversation history of its own.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Looking for more?** [`examples/google_cloud_integration/`](../google_cloud_integration/) covers the rest of the Google Cloud surface — Vertex AI embeddings, the MCP server, Cloud Run deployment.

> **Need a different LLM or embedding model?** Swap providers with a single string — `MemorySettings(llm=os.environ.get("MEMORY_LLM", "openai/gpt-5-mini"), embedding="BAAI/bge-small-en-v1.5")`. See [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model.html).

## What this demonstrates

| Step | What it shows |
|------|---------------|
| **Setup** | The resolved backend (`bolt` or hosted `nams`), embedder, and extractor are printed, so a degraded install is visible instead of silent. |
| **Turn 1** | `Runner(memory_service=Neo4jMemoryService(...))` + `LlmAgent(tools=[load_memory])`, then `add_session_to_memory(adk_session)` commits the turn to Neo4j. |
| **Entity curation** | `long_term.add_entity()` adds embeddings and descriptions to nodes the extractor created, which is what makes semantic entity recall work. |
| **Preference learning** | `MemoryIntegration(auto_preferences=True)` with `SessionStrategy.PER_DAY` runs the library's regex `PreferenceDetector` — no LLM call. |
| **Turn 2** | A brand-new ADK session; the model calls `load_memory`, which routes to `search_memory()` and answers out of Neo4j. |
| **Search** | `search_memory()` returns a `SearchMemoryResponse` — read `response.memories`, and each entry's `content` is a `google.genai.types.Content`. |
| **Session recall** | `get_memories_for_session()` returns the library's own `MemoryEntry` dataclass (`.content` is a plain string there). |

## Architecture

```
        ┌──────────────┐  new_message   ┌───────────────────────┐
 user ─▶│  ADK Runner  │───────────────▶│ LlmAgent (Gemini)     │
        │              │◀───────────────│ tools=[load_memory]   │
        └──────┬───────┘    events      └───────────┬───────────┘
               │                                    │ load_memory(query)
   InMemorySessionService                           ▼
   (ADK's own turn history)          ┌────────────────────────────┐
               │                     │    Neo4jMemoryService      │
               └─ add_session_to_────▶│ add_session_to_memory()   │
                  memory(session)    │ search_memory()            │
                                     └─────────────┬──────────────┘
                                                   │
                                           ┌───────▼────────┐
                                           │  MemoryClient  │
                                           │  short_term    │
                                           │  long_term     │
                                           │  reasoning     │
                                           └───────┬────────┘
                                                   ▼
                                      Neo4j (bolt)  or  hosted NAMS
```

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) — open in [Excalidraw](https://excalidraw.com) to edit.*

## Prerequisites

- Python 3.10+
- Neo4j 5.x (local Docker, Aura, or self-hosted) **or** a hosted NAMS API key
- `google-adk` 2.x — tested against 2.7.0 with `google-genai` 2.22.0
- Optional: a Gemini API key. Without one the demo drives the same `Runner`
  with a scripted stand-in model, so every step still executes.

```bash
# Docker Neo4j matching CI
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/test-password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5.26-community
```

## Setup

```bash
# The ADK adapter:
pip install "neo4j-agent-memory[google-adk]"

# Plus one embedding/extraction path — either hosted models:
export OPENAI_API_KEY=sk-...
# …or fully local (no API key at all):
pip install "neo4j-agent-memory[extraction,sentence-transformers]"
python -m spacy download en_core_web_sm

cp .env.example .env   # the demo loads it via python-dotenv if installed
```

Developing against the package source instead of a release:

```bash
cd /path/to/neo4j-agent-memory
uv pip install -e ".[google-adk,extraction,sentence-transformers]"
```

`[vertex-ai]` is **not** needed here — this demo uses no Vertex embeddings. See
[`../google_cloud_integration/`](../google_cloud_integration/) for that.

## Run

```bash
python demo.py              # full ADK Runner loop (scripted model without a key)
python demo.py --no-agent   # skip the model entirely; ingest a dict session
```

### Expected output (abridged, no-API-key path)

```
embedding: SentenceTransformersProvider
llm: None
extractor: ExtractionPipeline (SpacyEntityExtractor, GLiNEREntityExtractor)
backend: bolt

1. First turn — ADK Runner writing into Neo4j
  user: I'm working on Project Alpha with Sarah Chen and John Park. …
  agent: Noted — Project Alpha with Sarah Chen and John Park, …
  stored ADK session adk-demo-20260910164237-1 in Neo4j

2. Curating entities so they are searchable workspace-wide
  Sarah Chen (PERSON) — dedup: none
  Project Alpha (EVENT) — dedup: none

3. Preference learning (MemoryIntegration + PreferenceDetector)
  MemoryIntegration session (PER_DAY): demo-user-2026-09-10
  detected preference: [work] morning meetings

4. Second turn in a NEW ADK session — recall via load_memory
  user: Who am I working with on Project Alpha?
    → tool call: load_memory({'query': 'Project Alpha collaborators'})
    ← tool result from load_memory
  agent: From memory: Sarah Chen and John Park.

5. Direct search through the memory service
  query='Project Alpha deadline' (session=adk-demo-20260910164237-1)
    [message/user] I'm working on Project Alpha with Sarah Chen and John Park. …
    [entity/entity] Project Alpha: Internal project, deadline next Friday
```

With `GOOGLE_API_KEY` set, step 4 is a real Gemini turn and the wording of the
agent's answer will differ — the `load_memory` call and the recalled content are
the same.

## Minimal usage pattern

```python
import os

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import load_memory
from google.genai import types

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

APP, USER, SID = "my-app", "user-123", "session-1"

settings = MemorySettings(
    neo4j=Neo4jConfig(uri="bolt://localhost:7687", username="neo4j", password="password")
)

async with MemoryClient(settings) as client:
    session_service = InMemorySessionService()
    memory_service = Neo4jMemoryService(memory_client=client, user_id=USER)

    runner = Runner(
        app_name=APP,
        agent=LlmAgent(
            name="assistant",
            model=os.environ.get("ADK_MODEL", "gemini-2.5-flash"),
            instruction="Call load_memory to recall anything the user told you before.",
            tools=[load_memory],
        ),
        session_service=session_service,
        memory_service=memory_service,   # memory is a Runner service, not an agent field
    )

    await session_service.create_session(app_name=APP, user_id=USER, session_id=SID)
    async for event in runner.run_async(
        user_id=USER,
        session_id=SID,
        new_message=types.Content(role="user", parts=[types.Part(text="I work on Project Alpha")]),
    ):
        ...

    # Commit the finished ADK session to Neo4j
    await memory_service.add_session_to_memory(
        await session_service.get_session(app_name=APP, user_id=USER, session_id=SID)
    )

    # Reading results directly: search_memory returns a SearchMemoryResponse
    response = await memory_service.search_memory(query="Project Alpha", session_id=SID)
    for entry in response.memories:
        text = "".join(p.text or "" for p in entry.content.parts)
        kind = (entry.custom_metadata or {}).get("memory_type")
        print(f"[{kind}/{entry.author}] {text}")
```

### ADK gotchas

- `LlmAgent` has **no** `memory=` parameter. Memory is a `Runner`-level service:
  `Runner(memory_service=...)`.
- `Runner.run_async()` returns an `AsyncGenerator` — `async for event in
  runner.run_async(...)`, never `await`. Call `await runner.close()` when done.
- `InMemorySessionService.get_session()` / `create_session()` are coroutines and
  take keyword arguments only.
- `search_memory()` returns a `SearchMemoryResponse`, **not** a list. Iterate
  `response.memories`; each entry has `content` (a `types.Content`), `author`,
  `timestamp`, and `custom_metadata` (which carries `memory_type` and `score`).
- ADK authors model events with the *agent name*, not `"assistant"`. The adapter
  maps unknown authors onto `MessageRole.ASSISTANT` and keeps the original in the
  message metadata as `adk_author`.

## Backends

The demo resolves its backend from the environment and prints it:

| Environment | Backend | Notes |
|-------------|---------|-------|
| `MEMORY_API_KEY` set | `nams` | Hosted service; embeddings and extraction run server-side. The `NEO4J_*` variables are ignored. |
| `OPENAI_API_KEY` set | `bolt` | OpenAI embeddings + LLM extraction fallback. |
| neither | `bolt` | `llm=None`, local sentence-transformers embedder, local spaCy/GLiNER pipeline. |

## Known gaps

- **Message search is not session-scoped on bolt.** `search_memory(session_id=...)`
  passes the session through (hosted NAMS needs it), but `short_term.search_messages()`
  currently ignores the filter on the bolt backend, so message hits can come from
  other sessions. Entity and preference recall is workspace-wide by design.
- **`user_id` does not scope storage yet.** It is accepted by the service and the
  ADK `user_id`/`app_name` kwargs are honoured for contract parity, but writes are
  not yet tagged with a `:User` identity. Multi-tenant scoping lives on
  `MemorySettings(memory={"multi_tenant": True})` plus `user_identifier=` on the
  memory APIs — see the [multi-tenancy how-to](https://neo4j.com/labs/agent-memory/how-to/multi-tenancy.html).

## Going further

- **Companion example:** [`examples/google_cloud_integration/`](../google_cloud_integration/) — Vertex AI embeddings, MCP server, full pipeline, Cloud Run notes.
- **Production deployment:** [`deploy/cloudrun/README.md`](../../deploy/cloudrun/README.md).
- **ADK docs:** [google.github.io/adk-docs](https://google.github.io/adk-docs/).
- **Library how-to:** [Google ADK integration](https://neo4j.com/labs/agent-memory/how-to/integrations/google-adk.html).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev with google-adk 2.7.0 / google-genai 2.22.0 on 2026-09-10 — `python demo.py` and `python demo.py --no-agent` run end to end against Neo4j 5.26 with no API keys (scripted model, local embedder). The live-Gemini path was not exercised in this pass._
