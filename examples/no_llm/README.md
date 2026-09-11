# Run Without an LLM

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> No LLM provider, no API key, no outbound inference calls — local embeddings, local NER, and all three memory layers still working.

This example wires `MemorySettings` for environments where you can't (or don't want to) call an LLM. The key is `llm=None` plus a non-LLM extractor, a local embedder, and a pinned backend.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## When to use this

- Offline or restricted deployments where outbound API calls aren't allowed.
- Cost-sensitive workloads where every LLM call counts.
- Deterministic test environments where you want zero variability from a remote model.
- Bootstrapping a new project before you've decided on an LLM vendor.

## What this demonstrates

- **`llm=None`** — explicit opt-out. Validated at construction time.
- **`backend="bolt"`** — pinned. Without it, a `MEMORY_API_KEY` in your environment resolves to the hosted service, where extraction and embeddings run server-side — the opposite of what this example is showing.
- **Provider-string shorthand for local embeddings** — `"sentence-transformers/all-MiniLM-L6-v2"` resolves to a local `SentenceTransformersProvider` via `from_provider`. No embeddings API call.
- **`ExtractorType.PIPELINE` with `enable_llm_fallback=False`** — multi-stage spaCy + GLiNER pipeline, no LLM rescue.
- **Configuration-time validation** — pair `llm=None` with an extractor that needs an LLM and `MemorySettings` raises a `ValidationError` naming both fields, rather than failing later at runtime.
- **All three memory layers, locally** — short-term messages with local NER, long-term preferences/facts/entities with local embeddings, a reasoning trace with a structured `TraceOutcome`, and a `consolidation.dedupe_entities(dry_run=True)` hygiene pass (pure Cypher).
- **Fail-fast on a broken local stack** — the script checks for spaCy, the spaCy model, GLiNER and sentence-transformers up front, then asserts the pipeline actually extracted something. A missing model otherwise degrades silently to zero entities.

```python
settings = MemorySettings(
    backend="bolt",                                    # ignore MEMORY_API_KEY
    neo4j=Neo4jConfig(...),
    llm=None,                                          # explicit opt-out
    embedding="sentence-transformers/all-MiniLM-L6-v2", # local embeddings
    extraction=ExtractionConfig(
        extractor_type=ExtractorType.PIPELINE,
        enable_spacy=True,
        enable_gliner=True,
        enable_llm_fallback=False,            # required when llm=None
    ),
)
```

This example is **bolt-only by design**. On the hosted backend (NAMS) extraction and embeddings are server-side, so there is no LLM or embedding provider to configure in the first place — see [Use NAMS](https://neo4j.com/labs/agent-memory/how-to/use-nams).

## What works without an LLM

| Capability | Without an LLM |
|---|---|
| Messages, conversations, sequential linking | ✅ works |
| Embeddings + vector search (messages, entities, preferences, facts, traces) | ✅ local sentence-transformers |
| Entity extraction and POLE+O typing | ✅ spaCy + GLiNER |
| Preferences, facts, curated entities, entity resolution/dedup | ✅ works (embedding + fuzzy matching) |
| Reasoning traces, steps, tool calls, similar-trace retrieval | ✅ works |
| Consolidation (`dedupe_entities`, preference supersedence, archival) | ✅ pure Cypher + stored embeddings |
| Conversation summaries | ✅ extractive fallback (pass `summarizer=` for an LLM summary) |
| Relation extraction inside `ExtractorType.PIPELINE` | ❌ needs the LLM stage — for a local option use `GLiNERWithRelationsExtractor` (GLiREL) |
| Schema-guided extraction of attributes the local NER misses | ❌ needs `enable_llm_fallback=True` |
| Generating answers for your user | ❌ that's your agent's job — the library never calls an LLM on your behalf |

## Prerequisites

```bash
pip install "neo4j-agent-memory[extraction,sentence-transformers]"
python -m spacy download en_core_web_sm
```

First run downloads model weights once: sentence-transformers `all-MiniLM-L6-v2` (~90 MB) and the GLiNER model (~500 MB). Both are cached under `~/.cache/huggingface` afterwards.

A running Neo4j 5.x. From the repository root, `make neo4j-start` launches one (password `test-password`) — the defaults in this example point at it. To change the connection, copy the tracked template and edit it:

```bash
cp examples/.env.example examples/.env
```

or export `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` directly.

## Run

```bash
make neo4j-start                       # once, from the repository root
uv run python examples/no_llm/main.py  # safe to re-run
```

Re-running is idempotent: the script clears its own conversation first, and entities, preferences and facts MERGE on identity.

## Expected output

```
llm configured: None
embedding provider: SentenceTransformersProvider (sentence-transformers/all-MiniLM-L6-v2)
extractor: ExtractionPipeline
  extracted locally: John Smith (PERSON)
  extracted locally: Acme Corp (ORGANIZATION:COMPANY)
  extracted locally: New York (LOCATION:CITY)

backend: bolt
  in the graph: Acme Corp ['Entity', 'Organization', 'Company']
  in the graph: John Smith ['Entity', 'Person']
  in the graph: New York ['Entity', 'Location', 'City']

  preference: [food] Prefers Italian
  fact: John Smith -WORKS_AT-> Acme Corp
  semantic entity search: ['John Smith (PERSON)']

## Conversation History
### Recent Conversation
**user**: John Smith works at Acme Corp in New York.
**assistant**: Got it — I'll remember John and Acme Corp.

## Relevant Knowledge

### Relevant Entities
- John Smith (PERSON): Works at Acme Corp in New York

## Similar Past Tasks
### Similar Past Tasks

**Task**: Answer a question about John
- Similarity: 0.91
- Outcome: Answered from local memory — no LLM call
- Success: Yes

dedupe candidates (dry run): 0
```

No `extracted locally:` lines means the local stack is degraded — the script exits with the missing install step instead of printing an empty-looking context. Every repeat run adds one more entry under **Similar Past Tasks**: `clear_session()` removes the conversation and its messages, not reasoning traces.

## Truly offline

Nothing here calls an inference API, but the first run does download model weights from Hugging Face. To run with no network at all, warm the caches once on a connected machine:

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
python -m spacy download en_core_web_sm
python -c "from gliner import GLiNER; GLiNER.from_pretrained('gliner-community/gliner_medium-v2.5')"
```

Copy `~/.cache/huggingface` (and the installed `en_core_web_sm` package) to the target machine, then run with the offline switches set:

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
uv run python examples/no_llm/main.py
```

Neo4j stays wherever you put it — a local container is fine.

## Going further

- **How-to guide:** [Run without an LLM](https://neo4j.com/labs/agent-memory/how-to/running-without-an-llm) — the same configuration, explained.
- **Reference:** [Extractors](https://neo4j.com/labs/agent-memory/reference/extractors) — the extractor menu, when to add LLM rescue, GLiNER schema choices.
- **Bring your own model:** [Bring Your Own Model](https://neo4j.com/labs/agent-memory/how-to/bring-your-own-model) — provider strings for when you do want an LLM.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `neo4j-agent-memory` v0.5.0 with sentence-transformers 6.0.1, gliner 0.2.24, spacy 3.8.11 (`en_core_web_sm`), and Neo4j 5.26 on 2026-09-10._
