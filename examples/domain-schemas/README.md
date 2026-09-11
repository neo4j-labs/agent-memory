# Domain Schema Examples

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Domain-tuned entity extraction with GLiNER — eight ready-made schemas from POLE+O investigations to medical records, plus a recipe for your own.

GLiNER supports **domain schemas**: named sets of entity types, each with a natural-language description the model reads when deciding whether a span matches. Describing `drug` and `symptom` gives the model far more context than a generic `OBJECT` label on the same clinical note, with the same weights — which is typically where the precision gain on domain-specific spans comes from.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

> **Synthetic data.** Every corpus in `samples/` is invented — names, quotes, figures, case numbers, trial results. A few real technology, library and hardware names are kept so the schemas have recognisable spans to find. Nothing here is attributable to a real person or company, and nothing is medical, legal or investment advice.

## Architecture

![Domain Schemas Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## What's here

One runner, eight schemas, one corpus module per schema:

```
domain-schemas/
├── run.py                  # the runner: python run.py --schema <name>
├── samples/
│   ├── base.py             # SampleSet / Document / Highlight
│   ├── poleo.py            # one corpus per schema, kept verbatim
│   ├── podcast.py
│   └── ... news, scientific, business, entertainment, medical, legal
└── <old_name>.py           # thin wrappers kept one release (see below)
```

| Schema | Corpus | Demos it showcases by default |
|---|---|---|
| `poleo` | Fraud investigation reports (POLE+O model) | GLiREL relations |
| `podcast` | Tech podcast transcript excerpts | Native batch inference |
| `news` | News articles (regulation, climate, disaster) | GLiREL relations |
| `scientific` | ML paper abstracts | Streaming extraction |
| `business` | Earnings call, market analysis, M&A announcement | — |
| `entertainment` | Film review, TV preview, awards coverage | — |
| `medical` | Clinical case, trial summary, genomic report | — |
| `legal` | Court opinion, enforcement action, arbitration award | — |

Every demo is a flag, so any schema can exercise any of them: `--relations`, `--batch`, `--streaming` (or `--extract-only` for none).

## Prerequisites

```bash
# From the repository root, with uv (recommended)
uv sync --all-extras

# Or with pip
pip install "neo4j-agent-memory[gliner,sentence-transformers]"
```

- GLiNER downloads its model (~500 MB) on first use; later runs read the cache.
- **GLiREL is opt-in** and is not in any extra (last released 2025-04): `pip install glirel`. Without it `--relations` prints a skip notice instead of running.
- Neo4j is **optional** — extraction runs with no database. Storage needs one (see below) and uses a local sentence-transformers embedder, so no API key is involved either way.

```bash
# Optional: environment for the storage step
cp ../.env.example ../.env     # NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD
```

## Run

```bash
# List the schemas and the demos each one showcases
uv run python examples/domain-schemas/run.py --list

# Extraction only, no database
uv run python examples/domain-schemas/run.py --schema medical

# Any demo, on any schema
uv run python examples/domain-schemas/run.py --schema news --relations
uv run python examples/domain-schemas/run.py --schema legal --batch --streaming

# Extract and store the graph in Neo4j (make neo4j-start provides this one)
NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=test-password \
  uv run python examples/domain-schemas/run.py --schema poleo --relations --store

# Tuning
uv run python examples/domain-schemas/run.py --schema podcast --threshold 0.5 --device mps
```

`--store` is implied when `NEO4J_URI` is set; pass `--no-store` to keep a run purely local.

### The old per-domain scripts

The eight scripts (`podcast_transcripts.py`, `news_articles.py`, …) are now **thin wrappers** that forward to the runner, kept for one release so existing links keep working:

```bash
uv run python examples/domain-schemas/podcast_transcripts.py   # == run.py --schema podcast
```

They print a notice and will be removed in the release after next. Use `run.py --schema <name>`.

## Expected output

```
======================================================================
MEDICAL schema — Healthcare Document Analysis
======================================================================
All sample documents are synthetic: quotes, figures and events are invented
for demonstration. Nothing here is attributable to a real person or company.

  Schema:       medical (3 documents)
  Model:        gliner-community/gliner_medium-v2.5
  Threshold:    0.4
  Device:       cpu
  Entity types: ['disease', 'drug', 'symptom', 'procedure', 'body_part', 'gene', 'organism']
  Demos:        (none)

Document 1: Clinical Case Summary
Source: Internal Medicine Case Report (synthetic)
--------------------------------------------------
  Entities extracted: 27

  OBJECT:
    - atorvastatin [DRUG] (99%)
    - lisinopril [DRUG] (98%)
    - clopidogrel [DRUG] (98%)
    - type 2 diabetes mellitus [DISEASE] (97%)
    - Percutaneous coronary intervention [PROCEDURE] (97%)
    - left anterior descending artery [BODY_PART] (95%)
...
======================================================================
MEDICAL KNOWLEDGE GRAPH SUMMARY
======================================================================

Total unique entities: 71

Entity breakdown:
  OBJECT: 71

Diseases & Conditions:
  - Rheumatoid Arthritis
  - Metastatic Non-Small Cell Lung Cancer
  - type 2 diabetes mellitus
  ...
```

With `--store` the run ends with a graph instead of a list:

```
======================================================================
STORING IN NEO4J
======================================================================

  backend: bolt (bolt://localhost:7687)
  Stored 7 entity nodes from 8 extracted mentions (dedup actions: {'none': 7, 'merged': 1})
  Linked all of them to the :Extractor node 'GLiNEREntityExtractor'

  Read-back:
    entities tagged with GLiNEREntityExtractor: 5 (first 5)
      - Case No. 3:23-cv-01234-ABC (EVENT:CASE)
      - MEGACORP TECHNOLOGIES, INC. (ORGANIZATION)
    search_entities("licensing breach arbitration"): 3 hits
      - Commercial Arbitration Tribunal (ORGANIZATION:COURT) (0.80)
    no RELATED_TO edges yet — run with --relations --store to add some
```

## Techniques demonstrated

### Config-driven extractor creation

`ExtractionConfig` + `create_gliner_extractor` is the same code path `MemorySettings.extraction` takes internally, so it transfers unchanged to an application:

```python
from neo4j_agent_memory import ExtractionConfig
from neo4j_agent_memory.extraction import create_gliner_extractor

config = ExtractionConfig(gliner_schema="podcast", gliner_threshold=0.45, gliner_device="cpu")
extractor = create_gliner_extractor(config)
```

The terse alternative, when you do not already have a config object:

```python
from neo4j_agent_memory.extraction import GLiNEREntityExtractor

extractor = GLiNEREntityExtractor.for_schema("podcast", threshold=0.45)
```

### Letting the library do all of it

This example extracts explicitly so the output is inspectable. In an application you usually want the one-call path instead — extraction, entity creation, `MENTIONS` edges and relation storage happen inside `add_message`:

```python
settings = MemorySettings(
    backend="bolt",
    neo4j=Neo4jConfig(uri=..., password=...),
    llm=None,
    embedding="sentence-transformers/all-MiniLM-L6-v2",
    extraction=ExtractionConfig(extractor_type=ExtractorType.GLINER, gliner_schema="medical"),
)

async with MemoryClient(settings) as client:
    await client.short_term.add_message(
        session_id, "user", clinical_note, extract_entities=True, extract_relations=True
    )
```

### GLiREL relation extraction (no LLM)

```python
from neo4j_agent_memory.extraction import GLiNERWithRelationsExtractor, is_glirel_available

if is_glirel_available():
    extractor = GLiNERWithRelationsExtractor.for_schema("news", entity_threshold=0.4)
    result = await extractor.extract(text)
    for rel in result.relations:
        print(f"{rel.source} -[{rel.relation_type}]-> {rel.target}")
```

With `--store`, the relations found this way are persisted as `(:Entity)-[:RELATED_TO {relation_type}]->(:Entity)` via `long_term.add_relationship`.

### Batch extraction (native GLiNER inference)

```python
results = await extractor.extract_batch(
    texts, batch_size=10, on_progress=lambda done, total: print(f"{done}/{total}")
)
print(f"Total entities: {sum(r.entity_count for r in results)}")
```

`GLiNEREntityExtractor.extract_batch` returns a plain `list[ExtractionResult]`, one per input — the GPU-efficient path. The multi-stage `ExtractionPipeline.extract_batch` returns a `BatchExtractionResult` with success/failure bookkeeping instead; they are not interchangeable.

### Streaming extraction (long documents)

```python
from neo4j_agent_memory.extraction import create_streaming_extractor

streamer = create_streaming_extractor(extractor, chunk_size=2000, overlap=200)
async for chunk_result in streamer.extract_streaming(long_document):
    print(f"Chunk {chunk_result.chunk.index + 1}: {chunk_result.entity_count} entities")

complete = await streamer.extract(long_document, deduplicate=True)
```

### Storing a graph, not a list

The storage step is deliberately local and bolt-pinned:

```python
settings = MemorySettings(
    backend="bolt",                                      # MEMORY_API_KEY must not redirect writes
    neo4j=Neo4jConfig(uri=uri, username=..., password=...),
    llm=None,                                            # no LLM client is constructed
    embedding="sentence-transformers/all-MiniLM-L6-v2",   # local embeddings (no API key)
    extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),  # already extracted
)
```

Then, per run: `register_extractor` once, `add_entity` per entity, `link_entity_to_extractor` for provenance, `add_relationship` for the relations GLiREL found, and a read-back through `get_entities_by_extractor` / `search_entities` / `get_related_entities`.

For OpenAI embeddings instead, set `embedding="openai/text-embedding-3-small"` and export `OPENAI_API_KEY`. To run with no LLM anywhere in the stack, see [`examples/no_llm/`](../no_llm/).

**On the hosted backend (NAMS):** extraction runs server-side, and the hosted long-term API stores neither `subtype` nor `attributes`, nor the provenance edges used here — which is why this example pins `backend="bolt"`. See the [hosted-backend how-to](https://neo4j.com/labs/agent-memory/how-to/use-nams).

## Schema details

`list_schemas()` is the authoritative list; `get_schema(name).entity_types` returns each schema's `{label: description}` mapping. Extracted labels are mapped onto POLE+O types with a subtype (`drug` → `OBJECT:DRUG`, `actor` → `PERSON:ACTOR`); unknown labels become `OBJECT` with the label as the subtype, so custom schemas are queryable without a library change.

| Schema | Entity labels |
|---|---|
| `poleo` | person, organization, location, event, object |
| `podcast` | person, company, product, concept, book, location, event, role, metric, technology |
| `news` | person, organization, location, event, date |
| `scientific` | author, institution, method, dataset, metric, concept, tool |
| `business` | company, person, product, industry, financial_metric, location |
| `entertainment` | actor, director, film, tv_show, character, award, studio, genre |
| `medical` | disease, drug, symptom, procedure, body_part, gene, organism |
| `legal` | case, person, organization, law, court, date, monetary_amount |

## Creating custom schemas

```python
from neo4j_agent_memory.extraction import DomainSchema, GLiNEREntityExtractor

real_estate_schema = DomainSchema(
    name="real_estate",
    entity_types={
        "property": "A real estate property, building, or land parcel",
        "agent": "A real estate agent or broker",
        "buyer": "A property buyer or purchaser",
        "seller": "A property seller or owner",
        "price": "A property price or valuation",
        "location": "A neighborhood, city, or address",
        "feature": "A property feature or amenity",
    },
)

extractor = GLiNEREntityExtractor(schema=real_estate_schema, threshold=0.5)
result = await extractor.extract(property_listing_text)
```

Extending a built-in schema is a dict merge:

```python
from neo4j_agent_memory.extraction import get_schema

base = get_schema("business")
extended = DomainSchema(
    name="business_extended",
    entity_types={**base.entity_types, "loyalty_tier": "A customer loyalty program tier"},
)
```

## Adding a corpus

1. Add `samples/<schema>.py` exporting a `SampleSet` (copy the shape of `samples/medical.py`).
2. Register it in the tuple in `samples/__init__.py`.
3. `--schema <schema>` works immediately, as do `--relations`, `--batch` and `--streaming`.

The schema name must be one of `list_schemas()`, or a custom `DomainSchema` registered in the library.

## Performance tips

1. **Adjust the threshold.** Lower (0.3–0.4) extracts more spans with more noise; higher (0.6–0.7) is more precise and misses more. Each corpus ships a tuned default; override with `--threshold`.
2. **Use a GPU.** `--device cuda` or `--device mps`.
3. **Batch.** `--batch` uses GLiNER's native batch inference, which matters most on GPU.
4. **Stream long documents.** `--streaming` chunks the input; use it past ~100K tokens.
5. **Filter.** The runner always calls `result.filter_invalid_entities()` to drop stopwords, bare numbers and single characters.

## Troubleshooting

### GLiNER not installed

```
  ERROR: GLiNER is not installed.

  To run this example, install GLiNER:
    uv sync --all-extras
    # or: pip install "neo4j-agent-memory[gliner]"
```

Install it and re-run. The first run downloads the model (~500 MB).

### GLiREL skipped

`--relations` prints `GLiREL is not installed, so this demo is skipped.` — install the optional package with `pip install glirel`. It is not part of `--all-extras`.

### Neo4j authentication fails

The repo's Docker Neo4j (`make neo4j-start`) uses `test-password`, which is also the runner's default. Export `NEO4J_PASSWORD` for any other instance.

### Low confidence scores

- Lower `--threshold`.
- Pick the schema that matches your content, or write your own (see above) — descriptions are prompts.

## Going further

- **How-to:** [Domain schemas](https://neo4j.com/labs/agent-memory/how-to/entity-extraction-schemas) and [basic extraction & pipelines](https://neo4j.com/labs/agent-memory/how-to/entity-extraction).
- **Reference:** [Extractor classes](https://neo4j.com/labs/agent-memory/reference/extractors), [schemas](https://neo4j.com/labs/agent-memory/reference/schemas).
- **Measuring quality:** the `benchmarks/` module in this repository computes precision/recall/F1 for an extractor over a labelled suite — the right tool for comparing a domain schema against generic labels on your own corpus.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `neo4j-agent-memory` v0.5.0 with gliner 0.2.x on 2026-09-10: all eight schemas run end to end on CPU, and `--store` was exercised against Neo4j 5.26 (GLiREL is not installed in the repo environment, so `--relations` was verified through its skip path and with a stubbed extractor in `tests/examples/test_domain_schemas.py`)._
