# Hello Memory

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The front door: one file, thirteen lines of body, the whole round trip. Two messages in, one entity, one preference, the assembled context back out.

`main.py` carries a [PEP 723](https://peps.python.org/pep-0723/) header, so `uv` builds its environment for you — no checkout, no virtualenv, no `pip install`. It is the only example in this repository that works that way; every other one pins the library in a `requirements.txt` or `pyproject.toml`.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## What the script does

| Step | Call |
|---|---|
| Pick a backend | `MemorySettings(backend="nams")` when `MEMORY_API_KEY` is set, else `MemorySettings(backend="bolt", neo4j=…)` |
| Store the turn | `client.short_term.add_message(...)` ×2 |
| Remember a thing | `client.long_term.add_entity("Shellfish", "OBJECT")` |
| Remember a rule | `client.long_term.add_preference("diet", "Avoids shellfish")` |
| Read it back | `client.get_context("shellfish allergy", session_id=...)` |

Those five calls are the library. Everything else in [`examples/`](../) is a variation: a framework adapter around them, a bigger graph under them, or a production concern beside them.

## Prerequisites

- Python 3.10+ and [uv](https://docs.astral.sh/uv/) (0.11+, for `uv run --script`)
- **Either** a NAMS API key from <https://memory.neo4jlabs.com> (no database, no embedding key)
- **Or** a Neo4j 5.x instance and an embedding provider:

  ```bash
  docker run -d --name neo4j-hello -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/test-password \
    -e NEO4J_PLUGINS='["apoc"]' \
    neo4j:5.26-community
  ```

## Run

Hosted — one key, nothing to operate:

```bash
MEMORY_API_KEY=nams_xxxxxxxxxxxxxxxx uv run examples/hello-memory/main.py
```

Your own Neo4j, OpenAI embeddings:

```bash
OPENAI_API_KEY=sk-xxxx NEO4J_PASSWORD=test-password uv run examples/hello-memory/main.py
```

Your own Neo4j, **no API keys at all** (embeddings run locally, ~90 MB model on first use):

```bash
NEO4J_PASSWORD=test-password EMBEDDING=sentence-transformers/all-MiniLM-L6-v2 \
  uv run --with "neo4j-agent-memory[sentence-transformers]" examples/hello-memory/main.py
```

Or keep the variables in a file:

```bash
cp examples/hello-memory/.env.example examples/hello-memory/.env   # then edit it
uv run --env-file examples/hello-memory/.env examples/hello-memory/main.py
```

## Expected output

On the bolt backend against an empty database:

```
backend: bolt
## Conversation History
### Recent Conversation
**user**: I'm allergic to shellfish.
**assistant**: Noted — no shellfish.

### Relevant Past Messages
- [user] I'm allergic to shellfish. (relevance: 0.93)
- [assistant] Noted — no shellfish. (relevance: 0.80)

## Relevant Knowledge
### User Preferences
- [diet] Avoids shellfish

### Relevant Entities
- Shellfish (OBJECT): A food allergen.
```

On NAMS the first line reads `backend: nams`, the preference step is skipped (see below), and the context block is the server-assembled three-tier view — reflections and observations over recent messages.

Three things worth knowing about that output:

- **Relevance is a floor, not a ranking.** `get_context` is vector search with a similarity threshold (`MemorySettings.search`). `"shellfish allergy"` retrieves what the script stored; `"dinner plans"` retrieves nothing, because nothing stored is that close. That is the knob to turn first when a read comes back empty.
- **Re-running appends.** The session id is fixed, so a second run adds two more messages to the same conversation; the entity and the preference deduplicate instead.
- **The vector indexes are sized by your embedder.** Switching `EMBEDDING` between providers against the same database raises `EmbeddingDimensionMismatchError` rather than silently writing mismatched vectors.

## The two places the backends differ

The memory calls are identical on bolt and NAMS — that is what the backend-agnostic Protocol buys you. Exactly two differences surface in this file, and both are commented where they happen:

1. **Conversation ids.** NAMS mints them server-side, so the hosted path calls `create_conversation(...)` first and uses the id it returns. On bolt the session id is a name you choose and the first `add_message` creates the conversation.
2. **Preferences are bolt-only.** NAMS has no preferences endpoint yet, so `client.long_term.add_preference(...)` raises `NotSupportedError` there; the script guards it with `client.is_nams`.

One more difference is invisible here but worth knowing: on bolt `add_entity` returns `(entity, dedup_result)`, on NAMS just the entity. This example discards the return value, so it never has to care.

## Where to go next

- **The guided tour:** [`basic_usage.py`](../basic_usage.py) — the same three layers, plus geocoding, batch loading, and graph export.
- **Hosted backend in depth:** [`nams-quickstart/`](../nams-quickstart/) — server-side extraction, ontologies, graph expansion.
- **No API keys anywhere:** [`no_llm/`](../no_llm/) — local embedder *and* local extraction.
- **Docs:** [Getting started](https://neo4j.com/labs/agent-memory/getting-started) · [Bolt vs NAMS](https://neo4j.com/labs/agent-memory/explanation/backends)

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`) **and** the released 0.5.0 that the PEP 723 header resolves from PyPI; Python 3.12, uv 0.11.29, Neo4j 5.26-community, sentence-transformers 6.x embeddings on bolt, NAMS transport mocked (`tests/examples/test_hello_memory_example.py`) — 2026-09-10. Unlike the other examples, this one uses only released surface on purpose, so a stranger can run it before cloning anything; add `--with-editable .` to the `uv run` command to exercise the working tree instead._
