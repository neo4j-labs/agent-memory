# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`neo4j-agent-memory` is a Python package that provides a comprehensive memory system for AI agents using Neo4j as the backend. It implements a three-layer memory architecture:

- **Short-Term Memory**: Conversations and messages with temporal context
- **Long-Term Memory**: Entities, preferences, and facts (declarative knowledge)
- **Reasoning Memory**: Reasoning traces and tool usage patterns

### POLE+O Data Model

The long-term memory uses the POLE+O entity model (Person, Object, Location, Event, Organization):

- **PERSON**: Individuals, aliases, personas
- **OBJECT**: Physical/digital items (vehicles, phones, documents, devices)
- **LOCATION**: Geographic areas, addresses, places
- **EVENT**: Incidents, meetings, transactions
- **ORGANIZATION**: Companies, non-profits, government agencies

Each entity type supports subtypes for finer classification (e.g., `OBJECT:VEHICLE`, `LOCATION:ADDRESS`). Types and subtypes are stored as uppercase properties but converted to PascalCase labels in Neo4j (e.g., `:Person`, `:Vehicle`).

## Build & Development Commands

```bash
# Install dependencies (uses uv package manager)
uv sync

# Install with all optional dependencies
uv sync --all-extras

# Run unit tests only
make test-unit

# Run all tests (auto-starts Docker Neo4j)
make test-all

# Run integration tests only
make test-integration

# Start/stop Neo4j Docker container
make neo4j-start
make neo4j-stop
make neo4j-wait    # Wait for Neo4j to be ready

# Type checking
make typecheck

# Linting and formatting
make lint
make format

# Run all checks (lint, format, typecheck)
make check

# Pre-commit checks (format, lint, typecheck, unit tests)
make pre-commit
```

## Architecture

### Package Structure

```
src/neo4j_agent_memory/
├── __init__.py              # MemoryClient main entry point
├── config/settings.py       # Pydantic settings configuration
├── core/memory.py           # Base protocols and models
├── schema/
│   ├── __init__.py          # Schema exports
│   ├── models.py            # POLE+O entity types, schema config
│   └── persistence.py       # Schema persistence (Neo4j storage)
├── memory/
│   ├── short_term.py          # Conversations, messages
│   ├── long_term.py          # Entities, preferences, facts (POLE+O)
│   └── reasoning.py        # Reasoning traces, tool calls
├── extraction/
│   ├── base.py              # EntityExtractor protocol, ExtractedEntity
│   ├── llm_extractor.py     # LLM-based extraction (OpenAI)
│   ├── spacy_extractor.py   # spaCy NER extraction
│   ├── gliner_extractor.py  # GLiNER zero-shot NER, GLiREL relations
│   ├── pipeline.py          # Multi-stage extraction pipeline
│   ├── streaming.py         # Streaming extraction for long documents
│   └── factory.py           # Extractor factory and builder
├── resolution/
│   ├── base.py              # EntityResolver protocol
│   ├── exact.py             # Exact string matching
│   ├── fuzzy.py             # RapidFuzz-based matching
│   ├── long_term.py          # Embedding similarity
│   └── composite.py         # Chained strategy resolver (type-aware)
├── embeddings/
│   ├── base.py              # Embedder protocol
│   ├── openai.py            # OpenAI embeddings
│   ├── vertex_ai.py         # Vertex AI embeddings (Google Cloud)
│   └── bedrock.py           # Amazon Bedrock embeddings (AWS)
├── integration.py             # MemoryIntegration convenience layer
├── mcp/
│   ├── __init__.py          # MCP package exports
│   ├── server.py            # MCP server (stdio/SSE/HTTP transports)
│   ├── _tools.py            # 16 MCP tools (core + extended profiles)
│   ├── _resources.py        # 4 MCP resources (context, entities, preferences, stats)
│   ├── _prompts.py          # 3 MCP prompts (conversation, reasoning, review)
│   ├── _instructions.py     # Server instructions for LLM guidance
│   ├── _common.py           # Shared context helpers (get_client, get_integration, get_observer)
│   ├── _preference_detector.py  # Pattern-based preference detection
│   └── _observer.py         # Observational memory (context compression)
├── services/
│   ├── __init__.py          # Service exports
│   └── geocoder.py          # Geocoding services (Nominatim, Google, cached)
├── enrichment/
│   ├── __init__.py          # Enrichment exports
│   ├── base.py              # EnrichmentProvider protocol, EnrichmentResult
│   ├── wikimedia.py         # Wikipedia/Wikimedia enrichment provider
│   ├── diffbot.py           # Diffbot Knowledge Graph provider
│   ├── factory.py           # Provider factory, caching, composite providers
│   └── background.py        # BackgroundEnrichmentService for async processing
├── graph/
│   ├── client.py            # Async Neo4j client wrapper
│   ├── schema.py            # Index/constraint management
│   ├── queries.py           # All Cypher queries (centralized)
│   └── query_builder.py     # Dynamic query builder with label validation
├── cli/
│   ├── __init__.py          # CLI exports
│   └── main.py              # CLI commands (extract, schemas, stats)
├── observability/
│   ├── __init__.py          # Observability exports
│   ├── base.py              # Abstract Tracer/Span interfaces, NoOp implementations
│   ├── otel.py              # OpenTelemetry provider
│   └── opik.py              # Opik provider (LLM-focused observability)
└── integrations/
    ├── langchain/           # LangChain memory + retriever
    ├── pydantic_ai/         # Pydantic AI dependency + tools
    ├── llamaindex/          # LlamaIndex memory
    ├── crewai/              # CrewAI memory
    ├── google_adk/          # Google ADK MemoryService
    ├── strands/             # AWS Strands Agents tools
    └── agentcore/           # AWS AgentCore HybridMemoryProvider
    ├── crewai/              # CrewAI memory
    ├── openai_agents/       # OpenAI Agents SDK memory + tools
    └── microsoft_agent/     # Microsoft Agent Framework (ContextProvider, GDS)

benchmarks/                   # Extraction quality benchmarks (separate module)
├── __init__.py              # Benchmark exports
├── metrics.py               # Precision/recall/F1 calculations
└── runner.py                # Benchmark runner and test case definitions
```

### Key Classes

- **`MemoryClient`**: Main entry point, manages connections and provides access to all memory types
- **`MemoryIntegration`**: High-level convenience wrapper with session strategies, auto-extraction, and preference detection. Used by both MCP server and create-context-graph.
- **`ShortTermMemory`**: Handles conversations and messages
- **`LongTermMemory`**: Handles entities (POLE+O), preferences, and facts
- **`ReasoningMemory`**: Handles reasoning traces and tool calls
- **`UserMemory`** _(v0.2)_: First-class `:User` identity for multi-tenant deployments
- **`BufferedWriter`** _(v0.2)_: Fire-and-forget Cypher writer with bounded queue and background drain
- **`ConsolidationMemory`** _(v0.2)_: Dry-runnable hygiene jobs (entity dedupe, trace summarization, preference supersedence, conversation archival)
- **`EvalMemory`** _(v0.2)_: Labelled-suite evaluation harness for retrieval, audit, and preference quality
- **`Neo4jClient`**: Async wrapper around neo4j Python driver
- **`ExtractionPipeline`**: Multi-stage entity extraction (spaCy → GLiNER → LLM)
- **`CompositeResolver`**: Type-aware entity resolution
- **`MemoryObserver`**: Observational memory - tracks context per session and generates reflections when token thresholds are exceeded
- **`PreferenceDetector`**: Pattern-based preference detection from user messages

### v0.2 surface (in development on the `adopt-existing-graph` branch)

The v0.2 feature drop adds production-readiness primitives. Each is opt-in and lives behind a `MemoryClient` property accessor; existing v0.1 code keeps working unchanged.

- **`client.schema.adopt_existing_graph(label_to_type=..., name_property_per_label=...)`** — attach the `:Entity` super-label and library properties to nodes from a pre-existing graph. Idempotent. Use with `SchemaModel.CUSTOM` to map non-POLE+O domain types. Headline v0.2 feature. Doc: `docs/.../how-to/adopt-existing-graph.adoc`.
- **`MemorySettings.memory.multi_tenant=True`** plus **`user_identifier=`** kwarg on short/long/reasoning APIs — scopes reads and writes per tenant. **`client.users`** (`UserMemory`) provides `upsert_user()`, `get_user()`, `link_user_to_conversation()`. Doc: `docs/.../how-to/multi-tenancy.adoc`.
- **`MemorySettings.memory.write_mode="buffered"`** + **`client.buffered.submit(query, params)`** + **`client.flush()`** + **`client.write_errors`** — fire-and-forget Cypher with a bounded queue. The user-visible response path is decoupled from Neo4j round-trips. Doc: `docs/.../how-to/buffered-writes.adoc`.
- **`client.consolidation`** — `dedupe_entities()`, `summarize_long_traces()`, `detect_superseded_preferences()`, `archive_expired_conversations()`. All default to `dry_run=True`. Doc: `docs/.../how-to/consolidation.adoc`.
- **`client.eval.run(EvalSuite(audit=[AuditCase(...)], preference=[PreferenceCase(...)], ...))`** — labelled regression tests. Returns an `EvalReport` with per-dimension scores. Doc: `docs/.../how-to/evaluation.adoc`.
- **Reasoning audit edges**: `record_tool_call(touched_entities=[EntityRef(...)])`, `@client.reasoning.on_tool_call_recorded` decorator hook for domain-specific inference, `TraceOutcome` (success/error_kind/summary/related_entities/metrics) on `complete_trace(outcome=...)`. The headline payoff: a one-hop `MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)` audit query. Doc: `docs/.../how-to/audit-reasoning.adoc`.
- **Encryption helper**: `core.encryption` for at-rest encryption of sensitive memory fields. Doc: `docs/.../how-to/privacy-and-audit.adoc`.

**Schema additions in v0.2:**

```
(:User {identifier})                                     # UserMemory
(:User)-[:HAS_CONVERSATION]->(:Conversation)             # multi-tenant scoping
(ReasoningStep)-[:TOUCHED]->(:Entity)                    # audit edges (NEW relationship type)
```

**Smoke-testing the v0.2 surface:** see the four small examples — `examples/{existing-graph,buffered-writes,audit-trail,eval-harness}/` — all run with `llm=None` and a local sentence-transformers embedder, no API keys required. Each pairs with a `tests/examples/test_*_example.py` smoke test wired into `example-tests` CI.

**Phantom-method guard:** `tests/examples/test_no_phantom_methods.py` cross-references every `client.<layer>.<method>(` call in `examples/` against the actual class API. Catches silent breakage when an example calls a renamed/removed method (this guard caught five real cases during the v0.2 examples-review pass — see `CHANGELOG.md`).

### Neo4j Schema

The package creates these node types:
- `Conversation`, `Message` (short-term)
- `Entity` (with `type`, `subtype` for POLE+O), `Preference`, `Fact` (long-term)
  - Entity nodes have dynamic PascalCase labels for type/subtype (e.g., `:Entity:Person:Individual`, `:Entity:Object:Vehicle`)
- `ReasoningTrace`, `ReasoningStep`, `ToolCall`, `Tool` (reasoning)

#### Short-Term Memory Relationships

Messages in conversations are linked sequentially for efficient traversal:

```
(Conversation) -[:FIRST_MESSAGE]-> (Message)     # O(1) access to first message
(Conversation) -[:HAS_MESSAGE]-> (Message)       # Membership (kept for backward compat)
(Message) -[:NEXT_MESSAGE]-> (Message)           # Sequential chain
(Message) -[:MENTIONS]-> (Entity)                # Entity mentions in message
```

#### Long-Term Memory Relationships

Entities can be linked to each other via extracted relationships:

```
(Entity) -[:RELATED_TO {relation_type, confidence}]-> (Entity)  # Extracted relationships
(Entity) -[:SAME_AS]-> (Entity)                                  # Entity deduplication
```

#### Cross-Memory Relationships

Reasoning memory can link to short-term memory messages:

```
(ReasoningTrace) -[:INITIATED_BY]-> (Message)    # Trace triggered by user message
(ToolCall) -[:TRIGGERED_BY]-> (Message)          # Tool call triggered by message
(ReasoningStep) -[:TOUCHED]-> (Entity)           # v0.2: audit edges for "what did this step affect?"
```

Vector indexes are created for embedding-based search on Message, Entity, Preference, and ReasoningTrace nodes.

## Testing

### Test Structure

- `tests/unit/` - Unit tests with mocked dependencies
- `tests/integration/` - Integration tests requiring Neo4j
- `tests/benchmark/` - Performance benchmarks
- `tests/docs/` - Documentation tests (code snippets, build pipeline, links)

### Running Tests

The test suite uses Docker to run Neo4j automatically:

```bash
# Run unit tests (no Docker needed)
make test-unit

# Run all tests (auto-starts Docker Neo4j)
make test-all

# Run integration tests only
make test-integration

# Run tests with coverage
make coverage-all

# Run documentation tests
make test-docs              # All doc tests
make test-docs-syntax       # Syntax validation only (fast)
make test-docs-build        # Build pipeline tests
make test-docs-links        # Link validation
make test-docs-integration  # Doc examples against Neo4j
```

### Environment Variables for Tests

- `RUN_INTEGRATION_TESTS=1` - Force run integration tests
- `SKIP_INTEGRATION_TESTS=1` - Skip integration tests
- `AUTO_START_DOCKER=true` - Auto-start Docker if Neo4j not available (default)
- `AUTO_STOP_DOCKER=1` - Stop Docker after tests finish

### Test Fixtures

Key fixtures in `tests/conftest.py`:
- `memory_settings` - Configuration for test Neo4j instance
- `memory_client` - Connected MemoryClient with mock embedder/extractor/resolver
- `clean_memory_client` - Same as above but cleans database before/after each test
- `mock_embedder`, `mock_extractor`, `mock_resolver` - Mock implementations for testing

## Documentation

### Structure (Diataxis Framework)

The documentation follows the [Diataxis framework](https://diataxis.fr/) with four content types:

```
docs/
├── tutorials/           # Learning-oriented: guided walkthroughs
├── how-to/              # Task-oriented: solving specific problems
├── reference/           # Information-oriented: API descriptions
├── explanation/         # Understanding-oriented: conceptual discussions
└── build.js             # Node.js build script (Asciidoctor)
```

### Building Documentation

```bash
cd docs
npm install
npm run build           # Build to _site/
npm run serve           # Local preview server
npm run lint            # Validate links
```

### Documentation Testing

The `tests/docs/` directory validates documentation quality:

- **Syntax validation**: All 500+ Python code snippets compile without syntax errors
- **Build pipeline**: Tests that `npm run build` produces expected HTML output
- **Link validation**: Internal xref links point to existing files
- **Code extraction**: Utility to extract code blocks from AsciiDoc for testing

```bash
# Run all documentation tests
make test-docs

# Run only syntax validation (fast, no Neo4j required)
make test-docs-syntax

# Run build pipeline tests
make test-docs-build
```

### Diagram Management

Documentation diagrams use Excalidraw JSON format stored in `docs/assets/images/diagrams/excalidraw/`.

```bash
# List all diagram placeholders in documentation
make docs-diagrams-list

# Check status of diagrams (which are implemented, missing, etc.)
make docs-diagrams-status

# Show only missing diagrams that need to be created
make docs-diagrams-missing

# Generate manifest.json for diagram tracking
make docs-diagrams-manifest

# Add image references to .adoc files for existing diagrams
make docs-diagrams-add-refs
```

The diagram management script is at `scripts/manage_diagrams.py`. Excalidraw files can be created using the Claude Code Excalidraw skill at `.claude/skills/docs-excalidraw/`.

## CLI (Command Line Interface)

The package provides a CLI for entity extraction, schema management, and MCP server. Install with CLI extras:

```bash
uv pip install neo4j-agent-memory[cli]
# For MCP server support:
uv pip install neo4j-agent-memory[cli,mcp]
```

### Commands

```bash
# Extract entities from text
neo4j-agent-memory extract "John Smith works at Acme Corp in New York"

# Extract from a file
neo4j-agent-memory extract --file document.txt

# Extract with specific entity types
neo4j-agent-memory extract "..." -e Person -e Organization

# Extract with different output formats
neo4j-agent-memory extract "..." --format json    # JSON output
neo4j-agent-memory extract "..." --format jsonl   # JSON Lines (streaming)
neo4j-agent-memory extract "..." --format table   # Rich table (default)

# Use different extractors
neo4j-agent-memory extract "..." --extractor gliner  # GLiNER (default)
neo4j-agent-memory extract "..." --extractor llm     # LLM-based
neo4j-agent-memory extract "..." --extractor hybrid  # GLiNER + LLM

# Pipe from stdin
echo "John works at Acme" | neo4j-agent-memory extract -

# Schema management (requires Neo4j connection)
neo4j-agent-memory schemas list --password $NEO4J_PASSWORD
neo4j-agent-memory schemas show my_schema --format yaml
neo4j-agent-memory schemas validate schema.yaml

# Statistics
neo4j-agent-memory stats --password $NEO4J_PASSWORD --format json

# MCP server (requires mcp extra)
neo4j-agent-memory mcp serve --password $NEO4J_PASSWORD
neo4j-agent-memory mcp serve --profile core --transport sse --port 8080
neo4j-agent-memory mcp serve --session-strategy per_day --user-id alice
```

### Environment Variables

- `NEO4J_URI` - Neo4j connection URI (default: bolt://localhost:7687)
- `NEO4J_USER` - Neo4j username (default: neo4j)
- `NEO4J_PASSWORD` - Neo4j password (required for schemas/stats/mcp commands)
- `MCP_USER_ID` - User ID for per_day/persistent session strategies (MCP server)

## Observability

The package supports tracing via OpenTelemetry and Opik for monitoring extraction pipelines.

### Installation

```bash
# OpenTelemetry support
uv pip install neo4j-agent-memory[opentelemetry]

# Opik support (LLM-focused observability)
uv pip install neo4j-agent-memory[opik]
```

### Usage

```python
from neo4j_agent_memory.observability import get_tracer, TracingProvider

# Auto-detect available provider (Opik > OpenTelemetry > NoOp)
tracer = get_tracer()

# Or specify explicitly
tracer = get_tracer(provider="opentelemetry", service_name="my-extraction-service")
tracer = get_tracer(provider="opik", project_name="my-project")

# Use decorator for tracing functions
@tracer.trace("extract_entities")
async def extract(text: str):
    ...

# Or use context manager for manual spans
async with tracer.async_span("extraction", {"text_length": len(text)}) as span:
    result = await extractor.extract(text)
    span.set_attribute("entity_count", len(result.entities))
```

### Providers

- **OpenTelemetry**: Standard observability with OTLP export support
- **Opik**: LLM-focused observability with nested traces, feedback scores, and dashboards
- **NoOp**: Disabled tracing (zero overhead)

## Extraction Quality Benchmarks

The `benchmarks/` module provides tools for measuring extraction quality with precision, recall, and F1 metrics.

### Running Benchmarks

```python
from benchmarks import (
    BenchmarkRunner,
    BenchmarkSuite,
    BenchmarkTestCase,
    BenchmarkConfig,
    ExpectedEntity,
    create_sample_benchmark_suite,
)
from neo4j_agent_memory.extraction import GLiNEREntityExtractor

# Create extractor
extractor = GLiNEREntityExtractor.for_schema("poleo")

# Load or create a benchmark suite
suite = BenchmarkSuite.from_json_file("my_benchmark.json")
# Or use sample suite
suite = create_sample_benchmark_suite()

# Run benchmarks
runner = BenchmarkRunner(extractor)
result = await runner.run_suite(suite)

# Access metrics
print(f"Micro F1: {result.metrics.micro_f1:.3f}")
print(f"Macro F1: {result.metrics.macro_f1:.3f}")
print(f"Avg latency: {result.avg_latency_ms:.1f}ms")
print(f"Throughput: {result.throughput_docs_per_sec:.2f} docs/sec")

# Per-entity-type metrics
for entity_type, metrics in result.metrics.entity_metrics.items():
    print(f"{entity_type}: P={metrics.precision:.2f}, R={metrics.recall:.2f}, F1={metrics.f1_score:.2f}")
```

### Creating Benchmark Suites

```python
# Define test cases with expected entities
test_cases = [
    BenchmarkTestCase(
        id="tc-001",
        text="John Smith works at Acme Corporation.",
        expected_entities=[
            ExpectedEntity(name="John Smith", entity_type="PERSON"),
            ExpectedEntity(
                name="Acme Corporation",
                entity_type="ORGANIZATION",
                aliases=["Acme Corp", "Acme"],  # Alternative names that match
            ),
        ],
    ),
]

suite = BenchmarkSuite(
    name="my_benchmark",
    description="Custom benchmark suite",
    test_cases=test_cases,
    config=BenchmarkConfig(
        warmup_runs=1,      # Warmup iterations (not counted)
        num_runs=3,         # Iterations per test case
        timeout_seconds=30, # Timeout per extraction
    ),
)

# Save to JSON for reuse
suite.to_json_file("my_benchmark.json")
```

### Comparing Extractors

```python
from neo4j_agent_memory.extraction import (
    GLiNEREntityExtractor,
    SpacyEntityExtractor,
)

extractors = [
    GLiNEREntityExtractor.for_schema("poleo"),
    SpacyEntityExtractor("en_core_web_sm"),
]

runner = BenchmarkRunner(extractors[0])
results = await runner.compare_extractors(extractors, suite)

for result in results:
    print(f"{result.extractor_name}: F1={result.metrics.micro_f1:.3f}")
```

### Metrics

- **Precision**: TP / (TP + FP) - How many extracted entities are correct
- **Recall**: TP / (TP + FN) - How many expected entities were found
- **F1 Score**: Harmonic mean of precision and recall
- **Micro averages**: Aggregate across all entity types
- **Macro averages**: Average of per-type metrics

## Common Patterns

### Basic Usage

```python
from neo4j_agent_memory import MemoryClient, MemorySettings

settings = MemorySettings(
    neo4j={"uri": "bolt://localhost:7687", "password": "password"}
)

async with MemoryClient(settings) as client:
    # Short-term: Store conversation (messages are auto-linked sequentially)
    message = await client.short_term.add_message(session_id, "user", "Hello")
    
    # Long-term: Store entity with POLE+O type
    await client.long_term.add_entity(
        "John Smith",
        "PERSON",
        subtype="INDIVIDUAL",
        description="A customer"
    )
    
    # Long-term: Store preference
    await client.long_term.add_preference("food", "Loves Italian cuisine")
    
    # Reasoning: Record reasoning linked to triggering message
    trace = await client.reasoning.start_trace(
        session_id,
        "Find restaurant",
        triggered_by_message_id=message.id,  # Links trace to message
    )
    
    # Get combined context for LLM
    context = await client.get_context("restaurant recommendation")
```

### Message Linking

Messages are automatically linked in sequence using `FIRST_MESSAGE` and `NEXT_MESSAGE` relationships:

```python
# Messages added individually or in batch are automatically linked
await client.short_term.add_message(session_id, "user", "First message")
await client.short_term.add_message(session_id, "assistant", "Second message")

# For existing data without links, migrate with:
migrated = await client.short_term.migrate_message_links()
# Returns: {"conversation_id": num_messages_linked, ...}
```

### Linking Reasoning Memory to Messages

```python
# Link a reasoning trace to the message that initiated it
trace = await client.reasoning.start_trace(
    session_id,
    task="Handle user request",
    triggered_by_message_id=message.id,  # Creates INITIATED_BY relationship
)

# Link a tool call to the message that triggered it
await client.reasoning.record_tool_call(
    step_id,
    tool_name="search_api",
    arguments={"query": "restaurants"},
    result=[...],
    message_id=message.id,  # Creates TRIGGERED_BY relationship
)

# Or link an existing trace to a message post-hoc
await client.reasoning.link_trace_to_message(trace.id, message.id)
```

### POLE+O Entity Types

```python
from neo4j_agent_memory.memory.long_term import Entity, POLEO_TYPES

# Add entities with subtypes
await client.long_term.add_entity("Ford F-150", "OBJECT", subtype="VEHICLE")
await client.long_term.add_entity("123 Main St", "LOCATION", subtype="ADDRESS")
await client.long_term.add_entity("Acme Corp", "ORGANIZATION", subtype="COMPANY")

# Type:subtype string format also works
await client.long_term.add_entity("Meeting Q1", "EVENT:MEETING")
```

### Entity Deduplication on Ingest

Long-term memory supports automatic entity deduplication when adding entities. This uses embedding similarity and optional fuzzy string matching to identify potential duplicates:

```python
from neo4j_agent_memory.memory import (
    LongTermMemory,
    DeduplicationConfig,
    DeduplicationResult,
)

# Configure deduplication thresholds
dedup_config = DeduplicationConfig(
    enabled=True,                    # Enable deduplication (default)
    auto_merge_threshold=0.95,       # Auto-merge if similarity >= 0.95
    flag_threshold=0.85,             # Flag for review if >= 0.85 but < 0.95
    use_fuzzy_matching=True,         # Also use fuzzy string matching
    fuzzy_threshold=0.9,             # Fuzzy match threshold
    max_candidates=10,               # Max candidates to check
    match_same_type_only=True,       # Only match entities of same type
)

# Pass config when creating LongTermMemory
long_term = LongTermMemory(
    client=neo4j_client,
    embedder=embedder,
    deduplication=dedup_config,
)

# add_entity now returns (entity, dedup_result) tuple
entity, dedup_result = await long_term.add_entity(
    name="Jon Smith",
    entity_type="PERSON",
)

# Check what happened
if dedup_result.action == "merged":
    print(f"Auto-merged with {dedup_result.matched_entity_name}")
    # entity is the existing entity, new name added as alias
elif dedup_result.action == "flagged":
    print(f"Flagged as potential duplicate of {dedup_result.matched_entity_name}")
    # entity is created, SAME_AS relationship added for review
else:
    print("No duplicates found, entity created normally")

# Disable deduplication for specific entity
entity, _ = await long_term.add_entity(
    name="Unique Entity",
    entity_type="OBJECT",
    deduplicate=False,  # Skip deduplication check
)
```

**Managing Duplicates:**

```python
# Find potential duplicates pending review
duplicates = await long_term.find_potential_duplicates(limit=100)
for entity1, entity2, confidence in duplicates:
    print(f"{entity1.name} might be same as {entity2.name} ({confidence:.1%})")

# Review a duplicate pair
await long_term.review_duplicate(
    source_id=entity1.id,
    target_id=entity2.id,
    confirm=True,  # True to merge, False to reject
)

# Get all entities in a SAME_AS cluster
cluster = await long_term.get_same_as_cluster(entity_id)

# Get deduplication statistics
stats = await long_term.get_deduplication_stats()
print(f"Total: {stats.total_entities}, Merged: {stats.merged_entities}")
print(f"Pending reviews: {stats.pending_reviews}")
```

**SAME_AS Relationships:**

When entities are flagged as potential duplicates, a `SAME_AS` relationship is created:

```cypher
(Entity)-[:SAME_AS {
    confidence: 0.88,
    match_type: "embedding",  # or "fuzzy" or "both"
    status: "pending",        # or "confirmed" or "rejected"
    created_at: datetime()
}]->(Entity)
```

### Provenance Tracking

Track where entities were extracted from and which extractor produced them:

```python
# Register an extractor (auto-created on first link, but can be explicit)
await client.long_term.register_extractor(
    "GLiNEREntityExtractor",
    version="1.0.0",
    config={"threshold": 0.5, "schema": "podcast"},
)

# Link entity to source message
entity, _ = await client.long_term.add_entity("John Smith", "PERSON")
await client.long_term.link_entity_to_message(
    entity,
    message_id,
    confidence=0.95,
    start_pos=10,
    end_pos=20,
    context="... mentioned John Smith in the meeting ...",
)

# Link entity to extractor
await client.long_term.link_entity_to_extractor(
    entity,
    "GLiNEREntityExtractor",
    confidence=0.95,
    extraction_time_ms=150.5,
)

# Get provenance for an entity
provenance = await client.long_term.get_entity_provenance(entity)
for source in provenance["sources"]:
    print(f"From message {source['message_id']} at position {source['start_pos']}")
for extractor in provenance["extractors"]:
    print(f"Extracted by {extractor['name']} v{extractor['version']}")

# Get all entities extracted from a message
entities = await client.long_term.get_entities_from_message(message_id)
for entity, info in entities:
    print(f"{entity.name} at {info['start_pos']}-{info['end_pos']}")

# Get entities by extractor
entities = await client.long_term.get_entities_by_extractor("GLiNEREntityExtractor")

# List all extractors with stats
extractors = await client.long_term.list_extractors()
for ex in extractors:
    print(f"{ex['name']}: {ex['entity_count']} entities")

# Get extraction statistics
stats = await client.long_term.get_extraction_stats()
print(f"Total entities: {stats['total_entities']}")
print(f"From {stats['source_messages']} messages")

# Delete provenance for an entity
deleted = await client.long_term.delete_entity_provenance(entity)
```

**Provenance Schema:**

```cypher
// Extractor node
(:Extractor {
    id: "uuid",
    name: "GLiNEREntityExtractor",
    version: "1.0.0",
    config: "{...}",
    created_at: datetime()
})

// EXTRACTED_FROM relationship (Entity -> Message)
(Entity)-[:EXTRACTED_FROM {
    confidence: 0.95,
    start_pos: 10,
    end_pos: 20,
    context: "...",
    created_at: datetime()
}]->(Message)

// EXTRACTED_BY relationship (Entity -> Extractor)
(Entity)-[:EXTRACTED_BY {
    confidence: 0.95,
    extraction_time_ms: 150.5,
    created_at: datetime()
}]->(Extractor)
```

### Geocoding Location Entities

Location entities can be geocoded to add latitude/longitude coordinates as a Neo4j `Point` property, enabling geospatial queries:

```python
from neo4j_agent_memory.services.geocoder import create_geocoder

# Create geocoder (Nominatim is free, Google requires API key)
geocoder = create_geocoder(provider="nominatim", cache_results=True)

# Pass geocoder to LongTermMemory or set on existing instance
client.long_term._geocoder = geocoder

# Add location with automatic geocoding
location = await client.long_term.add_entity(
    "Empire State Building, New York",
    "LOCATION",
    subtype="LANDMARK",
    geocode=True,  # Auto-geocode if geocoder is configured
)

# Or provide coordinates directly
location = await client.long_term.add_entity(
    "Central Park",
    "LOCATION",
    coordinates=(40.7829, -73.9654),  # (latitude, longitude)
)

# Batch geocode existing locations without coordinates
stats = await client.long_term.geocode_locations(skip_existing=True)
# Returns: {"processed": 100, "geocoded": 85, "skipped": 10, "failed": 5}

# Spatial search - find locations within radius
nearby = await client.long_term.search_locations_near(
    latitude=40.75,
    longitude=-73.98,
    radius_km=5.0,
    limit=10,
)

# Bounding box search
locations = await client.long_term.search_locations_in_bounding_box(
    min_lat=40.7, min_lon=-74.0,
    max_lat=40.8, max_lon=-73.9,
)

# Get coordinates for a specific location entity
coords = await client.long_term.get_location_coordinates(entity_id)
# Returns: (40.748817, -73.985428) or None
```

### Background Entity Enrichment

Entities can be automatically enriched with additional data from external services like Wikipedia and Diffbot. Enrichment is non-blocking - entities are stored immediately, and enrichment data is fetched asynchronously in the background.

```python
from neo4j_agent_memory import MemorySettings, MemoryClient
from neo4j_agent_memory.config.settings import EnrichmentConfig, EnrichmentProvider

# Configure enrichment in settings
settings = MemorySettings(
    neo4j={"uri": "bolt://localhost:7687", "password": "password"},
    enrichment=EnrichmentConfig(
        enabled=True,
        providers=[EnrichmentProvider.WIKIMEDIA],  # Free, no API key required
        background_enabled=True,  # Async processing
        cache_results=True,  # Cache to avoid repeated API calls
        entity_types=["PERSON", "ORGANIZATION", "LOCATION"],  # Types to enrich
        min_confidence=0.7,  # Minimum confidence to trigger enrichment
    ),
)

async with MemoryClient(settings) as client:
    # Add entity - enrichment happens automatically in background
    entity, dedup_result = await client.long_term.add_entity(
        "Albert Einstein",
        "PERSON",
        confidence=0.95,
    )
    
    # Entity is stored immediately with basic data
    # Background service fetches Wikipedia data and updates entity

    # After enrichment completes, entity will have additional fields:
    # - enriched_description: Wikipedia summary
    # - wikipedia_url: Link to Wikipedia page
    # - wikidata_id: Wikidata Q identifier
    # - enriched_at: Timestamp of enrichment

# Using Diffbot for richer data (requires API key)
settings = MemorySettings(
    enrichment=EnrichmentConfig(
        enabled=True,
        providers=[EnrichmentProvider.DIFFBOT, EnrichmentProvider.WIKIMEDIA],
        diffbot_api_key="your-diffbot-api-key",  # Or set DIFFBOT_API_KEY env var
    ),
)
```

**Direct Provider Usage (without background service):**

```python
from neo4j_agent_memory.enrichment import WikimediaProvider, DiffbotProvider

# Wikimedia (free, rate-limited to 2 requests/second)
provider = WikimediaProvider(rate_limit=0.5)  # 0.5s between requests
result = await provider.enrich("Albert Einstein", "PERSON")

if result.status == EnrichmentStatus.SUCCESS:
    print(f"Description: {result.description}")
    print(f"Wikipedia: {result.wikipedia_url}")
    print(f"Wikidata ID: {result.wikidata_id}")
    print(f"Image: {result.image_url}")

# Diffbot (requires API key, richer data)
provider = DiffbotProvider(api_key="your-key")
result = await provider.enrich("Apple Inc", "ORGANIZATION")
print(f"Related entities: {result.related_entities}")
```

**Environment Variables:**

```bash
NAM_ENRICHMENT__ENABLED=true
NAM_ENRICHMENT__PROVIDERS=["wikimedia", "diffbot"]
NAM_ENRICHMENT__DIFFBOT_API_KEY=your-api-key
NAM_ENRICHMENT__CACHE_RESULTS=true
NAM_ENRICHMENT__BACKGROUND_ENABLED=true
NAM_ENRICHMENT__ENTITY_TYPES=["PERSON", "ORGANIZATION", "LOCATION", "EVENT"]
```

### Extraction Pipeline

```python
from neo4j_agent_memory.extraction import (
    ExtractionPipeline,
    create_extractor,
    ExtractorBuilder,
)
from neo4j_agent_memory.config.settings import ExtractionConfig

# Use factory (respects config)
config = ExtractionConfig(
    enable_spacy=True,
    enable_gliner=True,
    enable_llm_fallback=True,
)
extractor = create_extractor(config)

# Or use builder for custom setup
extractor = (
    ExtractorBuilder()
    .with_spacy("en_core_web_sm")
    .with_gliner(threshold=0.5)
    .with_llm_fallback("gpt-4o-mini")
    .merge_by_confidence()
    .build()
)

result = await extractor.extract("John works at Acme Corp in NYC")
for entity in result.entities:
    print(f"{entity.name}: {entity.full_type}")  # e.g., "John: PERSON"
```

### Batch Extraction

For processing multiple texts efficiently, use `extract_batch()`:

```python
from neo4j_agent_memory.extraction import (
    ExtractionPipeline,
    BatchExtractionResult,
)

# Create pipeline
pipeline = ExtractionPipeline(stages=[extractor1, extractor2])

# Process multiple texts in parallel
texts = ["John works at Acme.", "Sarah lives in NYC.", "Bob met Jane at the conference."]

def on_progress(completed: int, total: int) -> None:
    print(f"Progress: {completed}/{total}")

result: BatchExtractionResult = await pipeline.extract_batch(
    texts,
    batch_size=10,           # Texts per batch (memory management)
    max_concurrency=5,       # Parallel extractions
    on_progress=on_progress, # Progress callback
    fail_fast=False,         # Continue on errors (default)
)

# Access results
print(f"Processed: {result.total_items}, Success: {result.successful_items}")
print(f"Total entities: {result.total_entities}")
print(f"Success rate: {result.success_rate:.1%}")

# Get all entities across texts
all_entities = result.get_all_entities()

# Get errors for failed items
for index, error_msg in result.get_errors():
    print(f"Item {index} failed: {error_msg}")

# Individual results maintain input order
for item in result.results:
    print(f"Text {item.index}: {item.result.entity_count} entities, {item.duration_ms:.1f}ms")
```

**GLiNER Batch Extraction (GPU-optimized):**

```python
from neo4j_agent_memory.extraction import GLiNEREntityExtractor

# GLiNER supports native batch inference for better GPU utilization
extractor = GLiNEREntityExtractor.for_schema("podcast", device="cuda")

# Batch extraction uses native GLiNER batch_predict_entities
results = await extractor.extract_batch(
    texts,
    batch_size=32,  # Larger batches for GPU efficiency
    on_progress=on_progress,
)
```

### Streaming Extraction for Long Documents

For very long documents (>100K tokens), use streaming extraction to process chunks efficiently:

```python
from neo4j_agent_memory.extraction import (
    StreamingExtractor,
    create_streaming_extractor,
    GLiNEREntityExtractor,
)

# Create base extractor
extractor = GLiNEREntityExtractor.for_schema("podcast")

# Wrap with streaming extractor
streamer = StreamingExtractor(
    extractor,
    chunk_size=4000,      # Characters per chunk
    overlap=200,          # Overlap between chunks
    split_on_sentences=True,  # Try to split on sentence boundaries
)

# Or use factory with defaults
streamer = create_streaming_extractor(extractor)

# Stream results chunk by chunk (memory efficient)
async for chunk_result in streamer.extract_streaming(long_document):
    print(f"Chunk {chunk_result.chunk.index}: {chunk_result.entity_count} entities")
    if not chunk_result.success:
        print(f"  Error: {chunk_result.error}")

# Or get complete result with automatic deduplication
result = await streamer.extract(
    long_document,
    deduplicate=True,  # Remove duplicate entities across chunks
    on_progress=lambda done, total: print(f"Progress: {done}/{total}"),
)

print(f"Stats: {result.stats.total_chunks} chunks, "
      f"{result.stats.deduplicated_entities} entities "
      f"(from {result.stats.total_entities} raw)")

# Convert to standard ExtractionResult
extraction_result = result.to_extraction_result(source_text=long_document)
```

**Token-based Chunking:**

```python
# Chunk by approximate token count instead of characters
streamer = StreamingExtractor(
    extractor,
    chunk_size=1000,     # Tokens per chunk
    overlap=50,          # Token overlap
    chunk_by_tokens=True,
)
```

**Chunk Utilities:**

```python
from neo4j_agent_memory.extraction import chunk_text_by_chars, chunk_text_by_tokens

# Manual chunking for custom processing
chunks = chunk_text_by_chars(text, chunk_size=4000, overlap=200)
for chunk in chunks:
    print(f"Chunk {chunk.index}: chars {chunk.start_char}-{chunk.end_char}")
    print(f"  First: {chunk.is_first}, Last: {chunk.is_last}")
    print(f"  Approx tokens: {chunk.approx_token_count}")
```

### GLiREL Relationship Extraction (without LLM)

GLiREL extracts relationships between entities without requiring LLM calls:

```python
from neo4j_agent_memory.extraction import (
    is_glirel_available,
    GLiRELExtractor,
    GLiNERWithRelationsExtractor,
    DEFAULT_RELATION_TYPES,
)

# Check if GLiREL is available
if is_glirel_available():
    # Option 1: Separate entity and relationship extraction
    from neo4j_agent_memory.extraction import GLiNEREntityExtractor

    entity_extractor = GLiNEREntityExtractor.for_schema("poleo")
    entity_result = await entity_extractor.extract(text)

    relation_extractor = GLiRELExtractor()
    relations = await relation_extractor.extract_relations(
        text,
        entities=entity_result.entities,
    )

    # Option 2: Combined extraction (recommended)
    extractor = GLiNERWithRelationsExtractor.for_poleo()
    result = await extractor.extract("John works at Acme Corp in NYC.")
    print(f"Entities: {result.entities}")   # John, Acme Corp, NYC
    print(f"Relations: {result.relations}")  # John -[WORKS_AT]-> Acme Corp

# Default relation types for POLE+O model
print(DEFAULT_RELATION_TYPES.keys())
# works_at, lives_in, member_of, knows, located_in, founded_by, owns, etc.
```

### Automatic Relationship Storage

When adding messages with entity extraction enabled, extracted relationships are automatically stored as `RELATED_TO` relationships in Neo4j:

```python
# Relationships are stored automatically when adding messages
await memory.short_term.add_message(
    "session-1",
    "user",
    "Brian Chesky founded Airbnb in San Francisco.",
    extract_entities=True,
    extract_relations=True,  # Default: True
)

# This creates:
# - Entity nodes: Brian Chesky (PERSON), Airbnb (ORGANIZATION), San Francisco (LOCATION)
# - MENTIONS relationships: Message -> Entity
# - RELATED_TO relationships: (Brian Chesky)-[:RELATED_TO {relation_type: "FOUNDED"}]->(Airbnb)

# Batch operations also support relationship extraction
await memory.short_term.add_messages_batch(
    "session-1",
    messages,
    extract_entities=True,
    extract_relations=True,  # Default: True (only applies when extract_entities=True)
)

# Or extract from existing session
result = await memory.short_term.extract_entities_from_session(
    "session-1",
    extract_relations=True,  # Default: True
)
print(f"Extracted {result['relations_extracted']} relationships")
```

### Schema Persistence

Schemas can be stored in and loaded from Neo4j, enabling schema management without code changes:

```python
from neo4j_agent_memory.schema import (
    EntitySchemaConfig,
    EntityTypeConfig,
    RelationTypeConfig,
    SchemaManager,
    StoredSchema,
)

# Create schema manager with connected client
manager = SchemaManager(client._client)  # or pass Neo4jClient directly

# Create a custom schema
medical_schema = EntitySchemaConfig(
    name="medical",
    version="1.0",
    description="Medical records schema",
    entity_types=[
        EntityTypeConfig(
            name="PATIENT",
            description="A patient",
            subtypes=["ADULT", "PEDIATRIC"],
            attributes=["name", "dob", "mrn"],
        ),
        EntityTypeConfig(
            name="CONDITION",
            description="Medical condition or diagnosis",
            subtypes=["CHRONIC", "ACUTE"],
        ),
    ],
    relation_types=[
        RelationTypeConfig(
            name="DIAGNOSED_WITH",
            source_types=["PATIENT"],
            target_types=["CONDITION"],
        ),
    ],
)

# Save schema to Neo4j
stored = await manager.save_schema(medical_schema, created_by="admin")
print(f"Saved schema {stored.name} v{stored.version} (id: {stored.id})")

# Load schema by name (gets latest active version)
loaded_schema = await manager.load_schema("medical")

# Load specific version
v1_schema = await manager.load_schema_version("medical", "1.0")

# List all schemas
schemas = await manager.list_schemas()
for s in schemas:
    print(f"{s.name}: v{s.latest_version} ({s.version_count} versions)")

# List all versions of a schema
versions = await manager.list_schema_versions("medical")

# Set a specific version as active
await manager.set_active_version("medical", "1.0")

# Check if schema exists
if await manager.schema_exists("medical"):
    print("Medical schema is available")

# Delete schema
await manager.delete_schema(stored.id)  # Single version
await manager.delete_all_versions("medical")  # All versions
```

**Schema Versioning:**

When saving a schema with the same name, a new version is created. By default, the new version becomes active:

```python
# Create v1.0
await manager.save_schema(schema_v1)

# Update schema
schema_v2 = EntitySchemaConfig(name="medical", version="2.0", ...)
await manager.save_schema(schema_v2)  # Now v2.0 is active

# Save without activating
schema_v3 = EntitySchemaConfig(name="medical", version="3.0-beta", ...)
await manager.save_schema(schema_v3, set_active=False)

# Activate v3.0-beta later
await manager.set_active_version("medical", "3.0-beta")
```

**Neo4j Schema Node:**

Schemas are stored as `(:Schema)` nodes:

```cypher
(:Schema {
    id: "uuid",
    name: "medical",
    version: "1.0",
    description: "Medical records schema",
    config: "{...}",  // JSON-serialized EntitySchemaConfig
    is_active: true,
    created_at: datetime(),
    created_by: "admin"
})
```

### GLiNER2 Domain Schemas

GLiNER2 supports domain-specific schemas that improve extraction accuracy:

```python
from neo4j_agent_memory.extraction import (
    GLiNEREntityExtractor,
    get_schema,
    list_schemas,
)

# List available schemas
print(list_schemas())
# ['poleo', 'podcast', 'news', 'scientific', 'business', 'entertainment', 'medical', 'legal']

# Create extractor with domain schema
extractor = GLiNEREntityExtractor.for_schema("podcast")

# Or use with ExtractorBuilder
extractor = (
    ExtractorBuilder()
    .with_spacy()
    .with_gliner_schema("podcast", threshold=0.5)  # Use schema with descriptions
    .with_llm_fallback()
    .build()
)

# Or via config
config = ExtractionConfig(
    gliner_schema="podcast",  # Use podcast domain schema
    gliner_model="gliner-community/gliner_medium-v2.5",
)
```

Available schemas:
- `poleo` - POLE+O model for investigations/intelligence
- `podcast` - Podcast transcripts (person, company, product, concept, book, technology)
- `news` - News articles (person, organization, location, event, date)
- `scientific` - Research papers (author, institution, method, dataset, metric, tool)
- `business` - Business documents (company, person, product, industry, financial_metric)
- `entertainment` - Movies/TV (actor, director, film, tv_show, character, award)
- `medical` - Healthcare (disease, drug, symptom, procedure, body_part, gene)
- `legal` - Legal documents (case, person, organization, law, court, monetary_amount)

**Checking GLiNER Availability:**

```python
from neo4j_agent_memory.extraction import is_gliner_available

if not is_gliner_available():
    print("GLiNER not installed. Install with: uv sync --all-extras")
else:
    extractor = GLiNEREntityExtractor.for_schema("podcast")
```

**Creating Custom Schemas:**

```python
from neo4j_agent_memory.extraction.domain_schemas import DomainSchema

real_estate_schema = DomainSchema(
    name="real_estate",
    entity_types={
        "property": "A real estate property, building, or land parcel",
        "agent": "A real estate agent or broker",
        "buyer": "A property buyer or purchaser",
        "seller": "A property seller or owner",
        "price": "A property price, valuation, or asking price",
        "location": "A neighborhood, city, or street address",
    },
)

extractor = GLiNEREntityExtractor(schema=real_estate_schema, threshold=0.5)
```

See `docs/entity-extraction.md` for detailed documentation and `examples/domain-schemas/` for example applications.

### Framework Integrations

```python
# LangChain
from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory
memory = Neo4jAgentMemory(memory_client=client, session_id="user-123")

# Pydantic AI
from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency
deps = MemoryDependency(client=client, session_id="user-123")

# Google ADK
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
memory_service = Neo4jMemoryService(client, user_id="user-123")

# Strands Agents (AWS)
from neo4j_agent_memory.integrations.strands import context_graph_tools
tools = context_graph_tools(neo4j_uri="bolt://localhost:7687", neo4j_password="password", embedding_provider="bedrock")

# AgentCore Hybrid Memory (AWS)
from neo4j_agent_memory.integrations.agentcore import HybridMemoryProvider
provider = HybridMemoryProvider(memory_client=client, routing_strategy="auto")

# Microsoft Agent Framework (Preview)
from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory, create_memory_tools
memory = Neo4jMicrosoftMemory(memory_client=client, session_id="user-123")
tools = create_memory_tools(memory)
```

### Google Cloud Integration (v0.0.3)

The library provides comprehensive Google Cloud support including Vertex AI embeddings, Google ADK integration, and an MCP server for Cloud API Registry.

#### Vertex AI Embeddings

```python
from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

# Create embedder with Vertex AI
embedder = VertexAIEmbedder(
    model="text-embedding-004",     # or gecko@003, gecko-multilingual
    project_id="your-gcp-project",  # or from GOOGLE_CLOUD_PROJECT env
    location="us-central1",
    task_type="RETRIEVAL_DOCUMENT", # or RETRIEVAL_QUERY, SEMANTIC_SIMILARITY
)

# Single embedding
embedding = await embedder.embed("Hello world")

# Batch embedding (up to 250 texts per batch)
embeddings = await embedder.embed_batch(["Text 1", "Text 2", "Text 3"])

# Use with MemoryClient via config
from neo4j_agent_memory import MemorySettings
from neo4j_agent_memory.config.settings import EmbeddingConfig, EmbeddingProvider

settings = MemorySettings(
    embedding=EmbeddingConfig(
        provider=EmbeddingProvider.VERTEX_AI,
        model="text-embedding-004",
        project_id="your-project",
        location="us-central1",
    ),
)
```

**Supported Models:**
- `text-embedding-004` - Recommended, 768 dimensions
- `textembedding-gecko@003` - Legacy, 768 dimensions
- `textembedding-gecko-multilingual@001` - Multilingual, 768 dimensions

#### Google ADK MemoryService

```python
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

async with MemoryClient(settings) as client:
    # Create memory service for ADK agents
    memory_service = Neo4jMemoryService(
        memory_client=client,
        user_id="user-123",
        include_entities=True,      # Extract entities from sessions
        include_preferences=True,   # Learn preferences from conversations
    )
    
    # Store a conversation session
    session = {
        "id": "session-1",
        "messages": [
            {"role": "user", "content": "I prefer dark mode"},
            {"role": "assistant", "content": "Noted!"},
        ]
    }
    await memory_service.add_session_to_memory(session)
    
    # Search across all memory types
    results = await memory_service.search_memories("user preferences", limit=10)
    
    # Get session history
    history = await memory_service.get_memories_for_session("session-1")
    
    # Add individual memory
    await memory_service.add_memory(
        content="Prefers Python over JavaScript",
        memory_type="preference",
        category="programming",
    )
```

#### MCP Server

The MCP server exposes tools organized into two profiles:

**Core Profile (6 tools):**

| Tool | Description |
|------|-------------|
| `memory_search` | Hybrid vector + graph search across all memory types |
| `memory_get_context` | Assembled context for a session (conversation + entities + preferences) |
| `memory_store_message` | Store message with auto entity extraction and preference detection |
| `memory_add_entity` | Create/update entity with POLE+O typing and resolution |
| `memory_add_preference` | Record user preference with category |
| `memory_add_fact` | Store subject-predicate-object triple |

**Extended Profile (16 tools, default) adds:**

| Tool | Description |
|------|-------------|
| `memory_get_conversation` | Full conversation history for a session |
| `memory_list_sessions` | List sessions with message counts and previews |
| `memory_get_entity` | Entity details with graph relationship traversal |
| `memory_export_graph` | Subgraph export as JSON for visualization |
| `memory_create_relationship` | Typed relationship between entities |
| `memory_start_trace` | Begin reasoning trace recording |
| `memory_record_step` | Record reasoning step with optional tool call |
| `memory_complete_trace` | Complete trace with outcome |
| `memory_get_observations` | Session observations from observational memory |
| `graph_query` | Read-only Cypher queries |

**Server Instructions:** Sent during MCP initialization to guide the LLM on memory tool usage (defined in `_instructions.py`).

**Resources:** `memory://context/{session_id}` (core), `memory://entities`, `memory://preferences`, `memory://graph/stats` (extended).

**Prompts:** `memory-conversation` (core), `memory-reasoning`, `memory-review` (extended).

**Starting the Server:**

```bash
# stdio transport (for Claude Desktop)
neo4j-agent-memory mcp serve --password secret

# SSE transport (for Cloud Run/HTTP deployment)
neo4j-agent-memory mcp serve --transport sse --port 8080 --password secret

# Core profile (fewer tools, less context overhead)
neo4j-agent-memory mcp serve --profile core --password secret

# With session strategy and auto-preference detection
neo4j-agent-memory mcp serve --session-strategy per_day --user-id alice --password secret

# Disable automatic preference detection
neo4j-agent-memory mcp serve --no-auto-preferences --password secret

# Custom observation threshold (default: 30000 tokens)
neo4j-agent-memory mcp serve --observation-threshold 50000 --password secret
```

**Claude Desktop Configuration:**

```json
{
  "mcpServers": {
    "neo4j-agent-memory": {
      "command": "neo4j-agent-memory",
      "args": ["mcp", "serve", "--password", "your-password"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

**Programmatic Usage:**

```python
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.mcp.server import create_mcp_server, Neo4jMemoryMCPServer

# Recommended: factory function with settings
settings = MemorySettings(...)
server = create_mcp_server(settings, profile="extended")
await server.run_async(transport="stdio")

# Backward-compatible: pre-connected client
async with MemoryClient(settings) as client:
    server = Neo4jMemoryMCPServer(client, profile="extended")
    await server.run()
```

**MemoryIntegration Layer:**

The `MemoryIntegration` class provides a high-level interface shared by MCP tools and create-context-graph:

```python
from neo4j_agent_memory import MemoryIntegration, SessionStrategy

async with MemoryIntegration(
    neo4j_uri="bolt://localhost:7687",
    neo4j_password="password",
    session_strategy=SessionStrategy.PER_DAY,
    user_id="alice",
    auto_extract=True,       # entity extraction on store
    auto_preferences=True,   # preference detection on store
) as memory:
    await memory.store_message("user", "I love Italian food")
    context = await memory.get_context()
    results = await memory.search("food preferences")
    await memory.add_entity("Bella Italia", "ORGANIZATION", subtype="RESTAURANT")
```

Session strategies:
- `PER_CONVERSATION` (default): New UUID per MemoryIntegration instance
- `PER_DAY`: `"{user_id}-YYYY-MM-DD"` for daily continuity
- `PERSISTENT`: Fixed `user_id` for maximum continuity

**Desktop Extension:** The `deploy/mcpb/` directory contains a `.mcpb` manifest for Claude Desktop's extension directory.

#### Cloud Run Deployment

Production deployment templates are in `deploy/cloudrun/`:

```bash
# Build and deploy
gcloud run deploy neo4j-memory-mcp \
  --source deploy/cloudrun \
  --region us-central1 \
  --set-secrets NEO4J_URI=neo4j-uri:latest,NEO4J_PASSWORD=neo4j-password:latest
```

See `deploy/cloudrun/README.md` for full deployment instructions.

### Microsoft Agent Framework (Preview)

```python
from neo4j_agent_memory.integrations.microsoft_agent import (
    Neo4jMicrosoftMemory,
    GDSConfig,
    GDSAlgorithm,
    create_memory_tools,
)

gds_config = GDSConfig(
    enabled=True,
    expose_as_tools=[GDSAlgorithm.SHORTEST_PATH, GDSAlgorithm.NODE_SIMILARITY],
    fallback_to_basic=True,  # Use Cypher if GDS not installed
)

memory = Neo4jMicrosoftMemory(
    memory_client=client,
    session_id="user-123",
    gds_config=gds_config,
)

# Create callable tools bound to the memory instance
tools = create_memory_tools(memory)

# Use context provider with ChatAgent
from agent_framework import ChatAgent
agent = ChatAgent(
    context_providers=[memory.context_provider],
    tools=tools,
)
```

## Important Implementation Details

1. **POLE+O Entity Types**: Entities now use string `type` and optional `subtype` fields instead of the legacy `EntityType` enum. The enum is kept for backward compatibility but string types are preferred.

2. **Entity Subtypes**: Use `entity.full_type` to get the complete type string (e.g., `"OBJECT:VEHICLE"`). The `subtype` field is optional.

3. **Neo4j DateTime Conversion**: Neo4j returns `neo4j.time.DateTime` objects that must be converted to Python `datetime` using `.to_native()`. Helper function `_to_python_datetime()` handles this.

4. **Metadata Serialization**: Neo4j doesn't support Map types as node properties. Dict metadata must be serialized to JSON strings using `_serialize_metadata()` and deserialized with `_deserialize_metadata()`.

5. **Relationship Objects**: When querying relationships in Neo4j, the returned relationship objects have a different structure than nodes. Use `rel._properties` or handle via fallback patterns.

6. **Async Context Manager**: `MemoryClient` is designed to be used as an async context manager (`async with`) for proper connection handling.

7. **Optional Dependencies**: Framework integrations and extractors (LangChain, spaCy, GLiNER, etc.) are optional. They're wrapped in try/except ImportError blocks.

8. **Type-Aware Resolution**: The `CompositeResolver` now supports type-aware resolution - entities of different types (e.g., PERSON vs LOCATION) are never merged even if they have similar names.

9. **Entity Type Labels**: Entity `type` and `subtype` are added as PascalCase Neo4j node labels (e.g., `:Entity:Person:Individual`) for efficient querying. The `query_builder.py` module sanitizes types to ensure they are valid Neo4j label identifiers and converts them to PascalCase. Both POLE+O types and custom types become labels. For POLE+O types, subtypes are validated against known subtypes; for custom types, any valid identifier works as a subtype.

10. **Entity Stopword Filtering**: Extracted entities are filtered to exclude common stopwords (pronouns like "they", "them", articles, common verbs), purely numeric values, and single-character names. The `ENTITY_STOPWORDS` frozenset in `extraction/base.py` contains ~200 filtered words. Use `is_valid_entity_name()` to check if a name is valid, or `ExtractionResult.filter_invalid_entities()` to filter a result.

11. **Geocoding for Locations**: Location entities can have a `location` property containing Neo4j Point coordinates. Use `GeocodingConfig` to configure providers (Nominatim free, Google requires API key). The `geocoder.py` module provides `NominatimGeocoder`, `GoogleGeocoder`, and `CachedGeocoder` classes. A Point index is created on `Entity.location` for efficient spatial queries.

12. **GLiNER Availability Check**: GLiNER is an optional dependency. Use `is_gliner_available()` from `neo4j_agent_memory.extraction` to check if GLiNER is installed before creating extractors. The GLiNER model is lazy-loaded on first `extract()` call, so ImportError may occur during extraction rather than at extractor creation time.

13. **Entity Deduplication**: `add_entity()` now returns a tuple `(Entity, DeduplicationResult)` instead of just `Entity`. Deduplication is enabled by default with `DeduplicationConfig()`. Use `deduplicate=False` parameter to skip deduplication for specific entities. Duplicates above `auto_merge_threshold` (default 0.95) are automatically merged; those between `flag_threshold` (0.85) and auto_merge are flagged with `SAME_AS` relationships for human review.

14. **Schema Persistence**: Custom schemas can be stored in Neo4j using `SchemaManager`. Schemas are stored as `(:Schema)` nodes with JSON-serialized config. Multiple versions of the same schema can exist, with one marked as active. Use `save_schema()` to store, `load_schema()` to retrieve by name, and `load_schema_version()` for specific versions. Indexes are created on `Schema.name` and `Schema.id` for efficient lookups.

15. **Streaming Extraction**: For very long documents (>100K tokens), use `StreamingExtractor` to process chunks efficiently. It yields results as each chunk is processed (async generator), handles entity position adjustment to document-level coordinates, and automatically deduplicates entities across chunks. Configure `chunk_size` (chars or tokens), `overlap`, and `chunk_by_tokens` for different chunking strategies.

16. **Provenance Tracking**: Entities can be linked to their source messages via `EXTRACTED_FROM` relationships and to extractors via `EXTRACTED_BY` relationships. Use `link_entity_to_message()` and `link_entity_to_extractor()` to create provenance links. Query with `get_entity_provenance()`, `get_entities_from_message()`, or `get_entities_by_extractor()`. Extractor nodes (`(:Extractor)`) are auto-created when linking.

17. **Background Enrichment**: Entities can be enriched with additional data from external services (Wikipedia, Diffbot) in a non-blocking background process. Use `EnrichmentConfig` to configure providers. The `enrichment/` module provides `WikimediaProvider`, `DiffbotProvider`, `CachedEnrichmentProvider`, `CompositeEnrichmentProvider`, and `BackgroundEnrichmentService`. Enrichment happens asynchronously after entity creation - the entity is stored immediately, then enriched data is fetched and merged in the background. Enrichment is disabled by default; enable with `enrichment.enabled=True` in settings.

18. **Centralized Cypher Queries**: All Cypher queries are centralized in `graph/queries.py`. This module contains:
    - **Query constants**: All static queries as uppercase constants (e.g., `CREATE_CONVERSATION`, `GET_ENTITY`, `SEARCH_MESSAGES_BY_EMBEDDING`)
    - **Query builder functions**: Functions that generate dynamic DDL queries where identifiers can't be parameterized (e.g., `create_constraint_query()`, `create_vector_index_query()`)
    - **Metadata search helper**: `build_metadata_search_query()` for dynamic WHERE clause construction
    
    When adding new database operations:
    - Add queries as constants in `queries.py` (uppercase, descriptive names)
    - Import and use via `from neo4j_agent_memory.graph import queries` then `queries.CREATE_MESSAGE`
    - For DDL operations with dynamic names (indexes, constraints), use the query builder functions
    - The `query_builder.py` module handles entity creation with dynamic labels (type/subtype)

19. **Retrieving Session Messages**: Use `short_term.get_conversation(session_id)` to retrieve messages for a session. This returns a `Conversation` object with a `.messages` attribute containing the list of `Message` objects. There is no `get_session_messages()` method.

    ```python
    # Correct usage
    conversation = await client.short_term.get_conversation(session_id)
    messages = conversation.messages  # List[Message]
    
    # Access message properties
    for msg in messages:
        print(f"{msg.role.value}: {msg.content}")  # role is MessageRole enum
    ```

20. **Entity Search Parameters**: When searching entities by type, use `entity_types` (plural, as a list), not `entity_type` (singular string):

    ```python
    # Correct usage
    results = await client.long_term.search_entities(
        query="Apple",
        entity_types=["ORGANIZATION", "PERSON"],  # List of types
        limit=10,
    )
    
    # Wrong - this parameter doesn't exist
    # results = await client.long_term.search_entities(query="Apple", entity_type="ORGANIZATION")
    ```

21. **Entity Model Attributes**: The `Entity` model uses `.type` for the entity type, not `.entity_type`:

    ```python
    entity, _ = await client.long_term.add_entity("Apple Inc", "ORGANIZATION")
    print(entity.type)       # "ORGANIZATION" - correct
    print(entity.subtype)    # Optional subtype
    print(entity.full_type)  # "ORGANIZATION" or "ORGANIZATION:COMPANY" with subtype
    # entity.entity_type     # Wrong - this attribute doesn't exist
    ```

22. **Entity Metadata Access**: Entity enrichment data (from Wikipedia, Diffbot) is stored in the `metadata` dict, not as direct attributes. Use `getattr()` with fallback to `metadata.get()`:

    ```python
    # Enrichment fields may be in metadata dict
    metadata = entity.metadata or {}
    enriched_description = getattr(entity, "enriched_description", None) or metadata.get("enriched_description")
    wikipedia_url = getattr(entity, "wikipedia_url", None) or metadata.get("wikipedia_url")
    image_url = getattr(entity, "image_url", None) or metadata.get("image_url")
    ```

23. **Neo4j Property Key Warnings**: When querying optional properties in Cypher, avoid referencing properties that may not exist in the schema. Use `'property' IN keys(node)` to check existence before accessing:

    ```cypher
    // Wrong - warns if 'aliases' property doesn't exist on any node
    WHERE e.name = $name OR $name IN e.aliases
    
    // Correct - check property exists first
    WHERE e.name = $name OR ('aliases' IN keys(e) AND $name IN e.aliases)
    ```

24. **Graph Visualization with Episode Session IDs**: The `/api/memory/graph` endpoint accepts an `episode_session_ids` parameter (comma-separated) to include full conversations and entities from podcast episodes in addition to the current thread. This is used when tool call results contain references to specific episodes.

    ```python
    # Backend endpoint signature
    @router.get("/memory/graph")
    async def get_memory_graph(
        session_id: str | None = None,
        episode_session_ids: str | None = None,  # e.g., "lenny-podcast-brian-chesky,lenny-podcast-andy-johns"
        limit: int = 1000,
    ) -> MemoryGraph:
        ...
    ```

25. **Sidebar Entity Filtering by Thread**: The `/api/memory/context` endpoint filters entities to show only those mentioned in the current conversation (via `MENTIONS` relationships to messages), rather than a global search. It prioritizes enriched entities (those with Wikipedia data) and filters out short names (≤2 chars).

    ```cypher
    // Query used when thread_id is provided
    MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
    WITH e, count(m) AS mention_count
    WHERE size(e.name) > 2
    RETURN e, mention_count,
           CASE WHEN e.enriched_description IS NOT NULL THEN 1 ELSE 0 END AS is_enriched
    ORDER BY is_enriched DESC, mention_count DESC
    LIMIT 15
    ```

26. **Guest Name to Session ID Conversion**: The lennys-memory frontend converts guest names to session IDs using a slug pattern. This is used to map episode references in tool results to their corresponding session IDs.

    ```typescript
    // Frontend pattern (MemoryGraphView.tsx)
    const guestToSessionId = (guestName: string): string => {
      const normalized = guestName
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")  // Remove diacritics
        .toLowerCase()
        .replace(/\s+/g, "-")
        .replace(/[^a-z0-9-]/g, "")
        .replace(/-+/g, "-")
        .replace(/^-|-$/g, "");
      return `lenny-podcast-${normalized}`;
    };
    ```

27. **Neo4j Relationship Property Access**: When querying relationships in Neo4j, the returned relationship objects use `._properties` to access properties, not direct dict conversion. Always use fallback patterns:

    ```python
    # Correct way to access relationship properties
    if hasattr(rel, "_properties"):
        props = {k: serialize_neo4j_value(v) for k, v in rel._properties.items()}
    elif hasattr(rel, "items"):
        props = {k: serialize_neo4j_value(v) for k, v in rel.items()}
    else:
        props = {}
    ```

28. **MCP Tool Profiles**: The MCP server supports two profiles: `core` (6 tools) and `extended` (16 tools, default). Tools are registered via `register_tools(mcp, profile="extended")` which calls `_register_core_tools()` and optionally `_register_extended_tools()`. Resources and prompts follow the same pattern. The profile is set at server startup via `--profile` CLI flag or `profile` parameter on `create_mcp_server()`.

29. **MCP Server Instructions**: Server instructions are sent during MCP initialization via FastMCP's `instructions` parameter. They guide the LLM to call `memory_get_context` at conversation start, `memory_store_message` for important messages, and `memory_search` when asked about past interactions. Instructions vary by profile (core vs extended).

30. **MemoryIntegration Session Strategies**: The `MemoryIntegration` class resolves session IDs via three strategies: `per_conversation` (new UUID per instance), `per_day` (`"{user_id}-YYYY-MM-DD"`), `persistent` (fixed user_id). The strategy is configured at construction time and applied transparently in all operations. The `resolve_session_id(hint)` method always returns the hint if provided, falling back to the strategy.

31. **Automatic Preference Detection**: When `auto_preferences=True` on `MemoryIntegration`, `store_message()` fires a background `asyncio.create_task()` that runs `PreferenceDetector.detect()` on user messages. Detected preferences are stored via `client.long_term.add_preference()`. The detector uses regex patterns (not LLM calls) for zero-latency, zero-cost detection. It favors precision over recall.

32. **Observational Memory**: The `MemoryObserver` tracks accumulated context per session (character count, message count) and extracts inline observations (decisions, facts) from user messages. When the token count exceeds `threshold_tokens` (default 30000), it generates keyword-based reflections from older messages. The observer is created in the MCP server lifespan and wired to `MemoryIntegration` via the `observer` property. The `memory_get_observations` tool returns the three-tier hierarchy: reflections, observations, and session stats.

33. **MCP Tool Annotations**: All MCP tools include FastMCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`). Read tools are marked `readOnlyHint=True, idempotentHint=True`. Write tools are marked `readOnlyHint=False, idempotentHint=False`. No tools are marked destructive.

## Environment Variables

- `NEO4J_URI` - Neo4j connection URI (default: `bolt://localhost:7687`)
- `NEO4J_USERNAME` - Neo4j username (default: `neo4j`)
- `NEO4J_PASSWORD` - Neo4j password (default for tests: `test-password`)
- `OPENAI_API_KEY` - Required for OpenAI embeddings and LLM extraction
- `GOOGLE_CLOUD_PROJECT` - GCP project ID for Vertex AI embeddings
- `VERTEX_AI_LOCATION` - GCP region for Vertex AI (default: `us-central1`)
- `NAM_EMBEDDING__AWS_REGION` - AWS region for Bedrock embeddings (e.g., `us-east-1`)
- `NAM_EMBEDDING__AWS_PROFILE` - AWS credentials profile name (optional)
- `GOOGLE_GEOCODING_API_KEY` - API key for Google Geocoding (optional, for geocoding Location entities)
- `DIFFBOT_API_KEY` - API key for Diffbot Knowledge Graph enrichment (optional)
- `NAM_ENRICHMENT__ENABLED` - Enable background entity enrichment (default: `false`)
- `NAM_ENRICHMENT__PROVIDERS` - JSON array of enrichment providers (default: `["wikimedia"]`)
- `MCP_USER_ID` - User ID for per_day/persistent MCP session strategies (optional)
- `RUN_INTEGRATION_TESTS` - Set to `1` to enable integration tests
- `AUTO_START_DOCKER` - Set to `true` to auto-start Neo4j Docker (default)

## Makefile Targets

```bash
make help              # Show all available targets
make install           # Install core dependencies
make install-all       # Install all optional dependencies
make test-unit         # Run unit tests
make test-integration  # Run integration tests (starts Docker)
make test-all          # Run all tests (starts Docker)
make neo4j-start       # Start Neo4j Docker container
make neo4j-stop        # Stop Neo4j Docker container
make neo4j-wait        # Wait for Neo4j to be ready
make neo4j-shell       # Open cypher-shell to Neo4j
make check             # Run lint, format check, typecheck
make pre-commit        # Run all checks + unit tests
make ci                # Full CI simulation

# Simple Examples
make example-basic     # Run basic usage example
make example-resolution # Run entity resolution example
make example-langchain # Run LangChain integration example
make example-pydantic  # Run Pydantic AI integration example
make examples          # Run all examples

# Full-Stack Chat Agent Example
make chat-agent-install  # Install backend + frontend dependencies
make chat-agent-backend  # Run FastAPI backend server
make chat-agent-frontend # Run Next.js frontend dev server
make chat-agent          # Show instructions for running both
```

## Running Examples

Examples are located in `examples/` and can be run via Makefile targets or directly:

```bash
# Via Makefile (auto-starts Docker Neo4j if NEO4J_URI not set)
make example-basic

# Or run directly
uv run python examples/basic_usage.py
```

### Environment Configuration

Examples load environment variables from `examples/.env`. Copy the template:

```bash
cp examples/.env.example examples/.env
```

Key variables:
- `NEO4J_URI` - If set, uses this Neo4j instance; if not set, auto-starts Docker
- `NEO4J_PASSWORD` - Neo4j password (use `test-password` for Docker)
- `OPENAI_API_KEY` - Required for OpenAI embeddings and LLM extraction

If `NEO4J_URI` is not set, the Makefile targets will automatically start the Docker Neo4j container with `test-password`.

## Full-Stack Chat Agent Example

Located in `examples/full-stack-chat-agent/`, this is a complete demonstration of the neo4j-agent-memory package with:

- **Backend**: FastAPI + PydanticAI agent with all three memory types
- **Frontend**: Next.js 14 + Chakra UI with SSE streaming
- **News Graph Tools**: Search, filter, and analyze news articles
- **Memory Graph Visualization**: Interactive graph view using Neo4j Visualization Library (NVL)
  - Conversation-scoped filtering: Shows only nodes relevant to the current thread
  - Double-click to expand: Click a node twice to fetch and display its neighbors
  - "Expand Neighbors" button in the property panel for alternative expansion
  - Memory type filtering (short-term, user-profile, reasoning)
- **Automatic Preference Extraction**: Detects and stores user preferences from conversation
- **Memory Context Panel**: Real-time display of short-term, long-term, and reasoning memory

### Running the Chat Agent

```bash
# Install dependencies
make chat-agent-install

# Configure environment
cp examples/full-stack-chat-agent/backend/.env.example examples/full-stack-chat-agent/backend/.env
# Edit .env and add OPENAI_API_KEY

# Terminal 1: Start backend
make chat-agent-backend

# Terminal 2: Start frontend
make chat-agent-frontend

# Open http://localhost:3000
```

### Key Files

**Backend:**
- `backend/src/agent/agent.py` - PydanticAI agent with memory-enhanced system prompt
- `backend/src/agent/tools.py` - News graph query tools
- `backend/src/api/routes/chat.py` - SSE streaming chat endpoint with automatic preference extraction
- `backend/src/api/routes/memory.py` - Memory context and preferences API endpoints
- `backend/src/api/routes/threads.py` - Thread/conversation CRUD operations

**Frontend:**
- `frontend/src/hooks/useChat.ts` - React hook with SSE handling
- `frontend/src/hooks/useThreads.ts` - Thread management hook
- `frontend/src/components/chat/` - Chat UI components (MessageList, PromptInput, ToolCallDisplay)
- `frontend/src/components/memory/MemoryContext.tsx` - Memory context panel showing preferences, entities, recent messages
- `frontend/src/components/memory/MemoryGraphView.tsx` - Interactive NVL graph visualization of memory nodes

## Lenny's Memory Example (Flagship Demo)

Located in `examples/lennys-memory/`, this is the flagship demo for the library launch. It loads 299 Lenny's Podcast episodes into a knowledge graph with a full-stack AI chat agent.

### Tech Stack
- **Backend**: FastAPI + PydanticAI + neo4j-agent-memory
- **Frontend**: Next.js 14 + Chakra UI v3 + TypeScript
- **Graph Viz**: Neo4j Visualization Library (NVL)
- **Map Viz**: Leaflet + react-leaflet + Turf.js
- **Database**: Neo4j 5.x with APOC
- **LLM**: OpenAI GPT-4o

### Key Features

- **19 agent tools**: Podcast search, entity queries, geospatial analysis, preferences, reasoning memory
- **Three memory types**: Short-term (conversations), long-term (entities, preferences), reasoning (reasoning traces)
- **Wikipedia enrichment**: Entities auto-enriched with descriptions, images, Wikipedia URLs
- **SSE streaming**: Real-time token delivery with tool call visualization
- **Automatic preference learning**: Detects user preferences from natural conversation

### v2.0 UI/UX Features

The latest version includes significant frontend improvements:

- **Neo4j Labs Branding**: Labs Purple (#6366F1) primary accent, custom typography (Syne headings, Public Sans body, JetBrains Mono code), Beta badge, Labs disclaimer
- **Inline Tool Result Cards**: Tool outputs display as rich cards in the chat:
  - `MapCard`: Inline Leaflet maps for location tools, expandable to fullscreen
  - `EntityCard`: Wikipedia-style knowledge panel for enriched entities (image, description, mentions)
  - `GraphCard`: Inline NVL graphs for entity/relationship tools, expandable to fullscreen
  - `DataCard`: Responsive tables for search/list results
  - `StatsCard`: Grid of color-coded metrics
  - `RawJsonCard`: Collapsible JSON fallback
- **Onboarding**: WelcomeModal for first-time users, suggested query chips
- **Mobile-First Design**: Responsive layout, drawer navigation, FAB for new conversations

### Agent Tools (19 total)

**Podcast Content Search (6):** `search_podcast_content`, `search_by_speaker`, `search_by_episode`, `get_episode_list`, `get_speaker_list`, `get_memory_stats`

**Entity Knowledge Graph (4):** `search_entities`, `get_entity_context`, `find_related_entities`, `get_most_mentioned_entities`

**Geospatial Analysis (6):** `search_locations`, `find_locations_near`, `get_episode_locations`, `find_location_path`, `get_location_clusters`, `calculate_location_distances`

**Personalization (2):** `get_user_preferences`, `find_similar_past_queries`

### Graph Visualization Features

- Conversation-scoped filtering via `threadId` prop
- Double-click to expand node neighbors
- Memory type filtering (short-term, long-term, reasoning)
- Wikipedia enrichment section in node property panel with images
- **Episode session ID extraction**: Automatically extracts `session_id`, `episode`, `episode_guest`, and `guest` fields from tool call results to include related podcast conversations in the graph
- **Reasoning memory visualization**: Shows ReasoningTrace → HAS_STEP → ReasoningStep → USES_TOOL → ToolCall → INSTANCE_OF → Tool relationships

### Map Visualization Features

The map view (`MemoryMapView.tsx`) supports:
- **Conversation-scoped filtering**: Pass `threadId` to show only locations mentioned in the current conversation
- **Multiple view modes**: Markers, Clusters, and Heatmap visualizations
- **Multiple basemaps**: OpenStreetMap, Satellite (ESRI), and Terrain views
- **Distance measurement**: Click locations to measure distances using Turf.js great-circle calculations
- **Shortest path visualization**: Select two locations to find and display the graph path between them
- **Color-coded markers**: Locations colored by subtype (city, country, landmark, etc.)

### Memory Context Panel

- Entity cards with images, descriptions, Wikipedia links
- **Thread-scoped entities**: Shows only entities mentioned in the current conversation (not global search)
- Prioritizes enriched entities with Wikipedia data
- User preferences displayed by category
- Agent tools accordion
- Responsive: side panel on desktop, bottom sheet on mobile

### Key Frontend Files (v2.0)

**Theme & Branding:**
- `frontend/src/theme/index.ts` - Neo4j Labs theme with brand colors, fonts, semantic tokens
- `frontend/src/components/ui/provider.tsx` - Chakra provider using custom theme
- `frontend/src/components/layout/Footer.tsx` - Labs footer with repo/community links
- `frontend/src/components/branding/LabsDisclaimer.tsx` - Labs project disclaimer

**Tool Result Cards:**
- `frontend/src/components/chat/cards/types.ts` - TypeScript interfaces (CardType, LocationData, GraphNodeData, EntityData, etc.)
- `frontend/src/components/chat/cards/toolCardRegistry.ts` - Tool-to-card mapping logic (includes `hasEntityData()`, `extractEntityData()`)
- `frontend/src/components/chat/cards/BaseCard.tsx` - Shared card wrapper with expand button
- `frontend/src/components/chat/cards/ToolResultCard.tsx` - Smart card selector component
- `frontend/src/components/chat/cards/MapCard.tsx` - Inline Leaflet map, dynamic import
- `frontend/src/components/chat/cards/EntityCard.tsx` - Wikipedia-style knowledge panel for enriched entities
- `frontend/src/components/chat/cards/GraphCard.tsx` - Inline NVL graph, dynamic import
- `frontend/src/components/chat/cards/DataCard.tsx` - Table with auto-detected columns
- `frontend/src/components/chat/cards/StatsCard.tsx` - Metrics grid display
- `frontend/src/components/chat/cards/RawJsonCard.tsx` - JSON fallback

**Scripts:**
- `scripts/enrich_entities.py` - Wikipedia enrichment script with progress bars, rate limiting, status checking

**Onboarding:**
- `frontend/src/components/onboarding/WelcomeModal.tsx` - First-time user modal with memory type explanations

### API Endpoints

**Chat:** `POST /api/chat` (SSE streaming)

**Threads:** `GET/POST /api/threads`, `GET/PATCH/DELETE /api/threads/{id}`

**Memory:** `GET /api/memory/context`, `GET /api/memory/graph`, `GET /api/memory/graph/neighbors/{node_id}`, `GET /api/memory/traces`, `GET /api/memory/traces/{id}`, `GET /api/memory/similar-traces`, `GET /api/memory/tool-stats`

**Entities:** `GET /api/entities`, `GET /api/entities/top`, `GET /api/entities/{name}/context`, `GET /api/entities/related/{name}`

**Preferences:** `GET/POST /api/preferences`, `DELETE /api/preferences/{id}`

**Locations:** `GET /api/locations`, `GET /api/locations/nearby`, `GET /api/locations/bounds`, `GET /api/locations/clusters`, `GET /api/locations/path`

### Running

```bash
cd examples/lennys-memory
make neo4j          # Start Neo4j
make install        # Install dependencies
make load-sample    # Load 5 episodes (quick test)
make run-backend    # FastAPI on :8000
make run-frontend   # Next.js on :3000
```

See `examples/lennys-memory/README.md` for a full deep dive.

## Google Cloud Financial Advisor Example

Located in `examples/financial-services-advisor/google-cloud-financial-advisor/`, this demonstrates the Google Cloud ecosystem integration with a multi-agent compliance investigation app.

### Tech Stack
- **Backend**: FastAPI + Google ADK (Agent Development Kit) + neo4j-agent-memory
- **Frontend**: React + Vite + Chakra UI v3 + Framer Motion + TypeScript
- **Database**: Neo4j 5.x
- **LLM**: Gemini 2.5 Flash (via Google AI Studio)
- **Embeddings**: Vertex AI text-embedding-004

### Multi-Agent Architecture

A supervisor agent orchestrates 4 specialist agents:

| Agent | Tools | Responsibility |
|-------|-------|---------------|
| **Supervisor** | — | Routes queries, synthesizes findings |
| **KYC Agent** | `verify_identity`, `check_documents`, `assess_risk` | Identity verification, document checking |
| **AML Agent** | `scan_transactions`, `detect_patterns`, `analyze_velocity` | Transaction monitoring, pattern detection |
| **Relationship Agent** | `map_network`, `trace_ownership`, `analyze_connections` | Network analysis, beneficial ownership |
| **Compliance Agent** | `screen_sanctions`, `check_pep`, `generate_sar_report` | Sanctions/PEP screening, SAR generation |

### Real-Time SSE Streaming & Agent Visualization

The chat system supports two modes:
- **`POST /api/chat`** — Original synchronous endpoint, returns final response JSON
- **`POST /api/chat/stream`** — SSE streaming endpoint, emits real-time agent events

**SSE Event Types:**
- `agent_start` / `agent_complete` — Agent lifecycle events
- `agent_delegate` — Supervisor delegating to a sub-agent (from/to)
- `tool_call` / `tool_result` — Tool invocations with args and truncated results
- `memory_access` — Neo4j memory search/store operations detected by tool name patterns
- `thinking` — Intermediate agent reasoning text (non-final responses)
- `response` — Final consolidated response text
- `trace_saved` — Reasoning trace persisted to Neo4j (trace_id, step_count, tool_call_count)
- `done` — Stream complete with summary (agents_consulted, total_duration_ms)
- `error` — Error during stream processing

**Internal ADK function filtering**: `transfer_to_agent` and `transfer` are ADK-internal functions for agent delegation — these are excluded from `tool_call`/`tool_result` events to avoid noise. Delegation is instead surfaced via `agent_delegate` events detected from `event.actions.transfer_to_agent` and author transitions.

**`_truncate_result()` helper**: Always returns a string — uses `json.dumps()` for dicts/lists to prevent `[object Object]` display on the frontend.

### Reasoning Trace Persistence

After each streaming chat completes, reasoning traces are saved to Neo4j via the `MemoryClient.reasoning` layer:
1. `start_trace(session_id, task)` — Creates trace node
2. `add_step(trace_id, thought, action)` — One step per agent activation
3. `record_tool_call(step_id, tool_name, arguments, result, status)` — Tool calls nested under steps
4. `complete_trace(trace_id, outcome, success)` — Finalizes with outcome text

**Trace retrieval API:**
- `GET /api/traces/{session_id}` — Lists traces for a session (with steps and tool calls)
- `GET /api/traces/detail/{trace_id}` — Single trace by ID

Entity extraction is triggered via `memory_service.add_session()` which calls `adk_memory_service.add_session_to_memory(session)` with `extract_on_store=True`. Optional extractors (spaCy, GLiNER, LLM) warn but don't fail if not installed.

### Neo4j Domain Data Integration

All domain data (customers, transactions, organizations, alerts, sanctions, PEPs) is stored in Neo4j and queried via `Neo4jDomainService`. Agent memory (conversations, findings) uses the neo4j-agent-memory APIs.

**Architecture: Hybrid data access**
- **Domain data**: `Neo4jDomainService` queries Neo4j directly via `MemoryClient.graph` (the `Neo4jClient`)
- **Agent memory**: `FinancialMemoryService` wraps `Neo4jMemoryService` for ADK integration
- **Single connection**: Both share the same Neo4j driver via `MemoryClient.graph`

**`MemoryClient.graph` property** (added to the package):
```python
# Exposes the underlying Neo4jClient for custom Cypher queries
async with MemoryClient(settings) as client:
    results = await client.graph.execute_read(
        "MATCH (c:Customer) RETURN c.name LIMIT 10"
    )
```

**`Neo4jDomainService`** (`backend/src/services/neo4j_service.py`):
- Initialized in `main.py` lifespan: `Neo4jDomainService(memory_service.client.graph)`
- Stored on `app.state.neo4j_service`, accessed in routes via `request.app.state`
- Provides async methods: `list_customers`, `get_customer`, `get_transactions`, `detect_structuring`, `detect_rapid_movement`, `detect_layering`, `find_connections`, `detect_shell_companies`, `trace_ownership`, `get_network_risk`, `list_alerts`, `create_alert`, `check_sanctions`, `check_pep`, etc.
- Alert creation uses `MERGE...ON CREATE SET` (not `CREATE`) to avoid uniqueness constraint violations
- Alert fallback IDs use UUID: `ALERT-{uuid.uuid4().hex[:8].upper()}`

**Tool function binding** (`_bind_tool` pattern):
- Tool functions accept `neo4j_service` as a keyword-only arg
- ADK `FunctionTool` inspects signatures to determine LLM-visible params
- `_bind_tool()` uses `inspect.signature` + `@wraps` to create wrappers that hide `neo4j_service`
- `functools.partial` does NOT work — it sets defaults but doesn't remove params from the signature

**Neo4j DateTime conversion**:
- Neo4j returns `neo4j.time.DateTime` objects, not Python `datetime`
- Use `.to_native()` to convert: `_to_python_datetime()` helper in `alerts.py`

**Cypher gotchas** (Neo4j 5+):
- Implicit grouping: mixing aggregation with non-aggregated expressions requires `WITH` clause
- `CASE WHEN ... AND x NOT IN [...]` — extract into variables first: `WITH a.status AS stat ... NOT stat IN [...]`
- Dynamic WHERE: When adding optional filters after a MATCH pattern, use `WHERE` (not `AND`) — `AND` is only valid inside an existing `WHERE` clause

### Key Implementation Details

- **ADK Runner**: `Runner.run_async()` returns `AsyncGenerator` — use `async for`, not `await`
- **ADK SessionService**: `get_session()` and `create_session()` are async — need `await`
- **GOOGLE_API_KEY**: ADK reads from `os.environ`, not `.env` — `main.py` calls `load_dotenv()` early
- **Nested BaseSettings**: Each nested Pydantic settings class needs its own `env_file=("../.env", ".env")` config
- **Event null guards**: ADK events can have `content` set but `content.parts` as `None` — always guard with `and event.content.parts`
- **Local dev deps**: Uses `[tool.uv.sources]` with editable local path: `neo4j-agent-memory = { path = "../../..", editable = true }`

### Frontend Architecture

**Real-time agent visualization** with Framer Motion + Chakra UI v3:
- `useAgentStream` hook manages SSE connection, parses events, tracks per-agent state
- `AgentOrchestrationView` — Animated panel showing live agent activity during processing (slide-in cards, pulsing active dots, staggered tool call animations)
- `AgentActivityTimeline` — Post-completion Chakra UI `Timeline` showing reasoning trace per message
- `ToolCallCard` — Animated tool call display with spinning loader → checkmark transition
- `MemoryAccessIndicator` — Neo4j memory read/write flash animation

**UI/UX patterns (Chakra UI v3)**:
- Semantic tokens throughout: `bg.panel`, `bg.subtle`, `fg`, `fg.muted`, `border.subtle`
- `colorPalette` for agent color coding (blue=supervisor, teal=KYC, orange=AML, purple=relationship, red=compliance)
- Chakra `Stat`, `Skeleton`, `EmptyState`, `Collapsible`, `Timeline` compound components
- `framer-motion` `AnimatePresence` + `motion.div` for orchestrated animations

**react-icons/lu v5.5.0 naming**: Uses newer names — `LuTriangleAlert` (not `LuAlertTriangle`), `LuCircleAlert` (not `LuAlertCircle`), `LuCircleCheck` (not `LuCheckCircle`)

### Running

```bash
cd examples/google-cloud-financial-advisor

# Configure environment
cp .env.example .env
# Edit .env: set GOOGLE_API_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# Install dependencies
make install

# Load sample data (reads credentials from .env)
make load-data

# Start backend + frontend
make dev
# Backend: http://localhost:8000  Frontend: http://localhost:5173
```

### Key Files

**Backend:**
- `backend/src/main.py` - FastAPI app, initializes `Neo4jDomainService` in lifespan, registers all routers
- `backend/src/config.py` - Pydantic settings with nested VertexAI/Neo4j/GCS configs
- `backend/src/services/neo4j_service.py` - `Neo4jDomainService` — all domain Cypher queries
- `backend/src/services/memory_service.py` - MemoryClient wrapper (uses `connect()`, not `initialize()`)
- `backend/src/agents/supervisor.py` - Google ADK supervisor with sub-agents, passes `neo4j_service`
- `backend/src/agents/kyc_agent.py` - KYC specialist agent with `_bind_tool()` pattern
- `backend/src/agents/aml_agent.py` - AML specialist agent
- `backend/src/agents/relationship_agent.py` - Relationship/network analyst
- `backend/src/agents/compliance_agent.py` - Compliance/regulatory agent
- `backend/src/tools/kyc_tools.py` - KYC tools querying Neo4j via `neo4j_service` kwarg
- `backend/src/tools/aml_tools.py` - AML tools (structuring, velocity, pattern detection)
- `backend/src/tools/relationship_tools.py` - Network analysis tools
- `backend/src/tools/compliance_tools.py` - Sanctions/PEP screening, SAR generation
- `backend/src/api/routes/chat.py` - `POST /api/chat` (sync) and `POST /api/chat/stream` (SSE) with reasoning trace recording
- `backend/src/api/routes/traces.py` - `GET /api/traces/{session_id}` and `GET /api/traces/detail/{trace_id}`
- `backend/src/api/routes/customers.py` - Customer endpoints via `Neo4jDomainService`
- `backend/src/api/routes/alerts.py` - Alert endpoints via `Neo4jDomainService`
- `backend/src/api/routes/investigations.py` - Investigation endpoint

**Frontend:**
- `frontend/src/hooks/useAgentStream.ts` - SSE connection hook, per-agent state management
- `frontend/src/lib/api.ts` - API client with `streamChatMessage()`, `getSessionTraces()`, SSE parsing
- `frontend/src/components/Chat/ChatInterface.tsx` - Chat UI with streaming + orchestration panel
- `frontend/src/components/Chat/AgentOrchestrationView.tsx` - Real-time animated multi-agent visualization
- `frontend/src/components/Chat/AgentActivityTimeline.tsx` - Post-completion reasoning trace timeline
- `frontend/src/components/Chat/ToolCallCard.tsx` - Animated tool call display with `formatValue()` helper
- `frontend/src/components/Chat/MemoryAccessIndicator.tsx` - Neo4j memory operation indicator
- `frontend/src/components/Dashboard/Sidebar.tsx` - Grouped nav, active route indicator, alert badge count
- `frontend/src/components/Dashboard/CustomerDashboard.tsx` - Stat components, Skeleton loading, semantic tokens
- `frontend/src/components/Dashboard/AlertsPanel.tsx` - EmptyState, Skeleton loading, semantic tokens
- `frontend/src/components/Investigation/InvestigationPanel.tsx` - Timeline audit trail, Collapsible sections
- `frontend/src/components/Investigation/AgentWorkflow.tsx` - Agent workflow visualization
- `frontend/src/components/Graph/NetworkViewer.tsx` - vis-network graph visualization

**Data:**
- `data/customers.json` - 3 sample customers with full KYC properties (dob, nationality, docs)
- `data/organizations.json` - Shell companies and related entities with shell indicators
- `data/transactions.json` - 16 transactions including structuring patterns
- `data/sanctions.json` - Sanctioned entities with aliases
- `data/pep.json` - Politically Exposed Persons with relatives
- `data/alerts.json` - Pre-built compliance alerts
- `data/load_sample_data.py` - Loads all JSON data into Neo4j (customers, orgs, txns, sanctions, PEPs, alerts)

**Tests:**
- `tests/examples/test_google_cloud_financial_advisor.py` - Structure validation tests (directory structure, file existence, dependencies)
- `tests/examples/test_financial_advisor_neo4j_integration.py` - Unit tests: Neo4jDomainService, tool functions, route helpers, _bind_tool, SSE helpers, traces route, structure validation

See `examples/financial-services-advisor/google-cloud-financial-advisor/README.md` for the full getting started tutorial.

## Documentation

The documentation is located in `docs/` and follows the [Diataxis framework](https://diataxis.fr/) for organizing technical documentation into four distinct types.

### Documentation Structure

```
docs/
├── index.adoc                    # Landing page
├── tutorials/                    # Learning-oriented guides
│   ├── first-agent-memory.adoc   # Build your first memory-enabled agent
│   ├── conversation-memory.adoc  # Add memory to a chatbot
│   └── knowledge-graph.adoc      # Build a knowledge graph from documents
├── how-to/                       # Task-oriented guides
│   ├── messages.adoc             # Store and search messages
│   ├── entities.adoc             # Work with entities
│   ├── entity-extraction.adoc    # Configure extraction pipeline
│   └── integrations/             # Framework-specific guides
├── reference/                    # Information-oriented content
│   ├── configuration.adoc        # All configuration options
│   ├── cli.adoc                  # CLI command reference
│   └── api/                      # API documentation
└── explanation/                  # Understanding-oriented content
    ├── memory-types.adoc         # The three memory types explained
    ├── poleo-model.adoc          # POLE+O data model concepts
    └── extraction-pipeline.adoc  # How entity extraction works
```

### Diataxis Quadrants

| Quadrant | Purpose | Style | Example |
|----------|---------|-------|---------|
| **Tutorials** | Teach through doing | Step-by-step, beginner-friendly | "Build your first agent with memory" |
| **How-To Guides** | Solve specific problems | Concise, goal-focused | "How to configure entity extraction" |
| **Reference** | Provide facts | Comprehensive, scannable | "Configuration options reference" |
| **Explanation** | Build understanding | Conceptual, discursive | "Why we use the POLE+O model" |

### Building Documentation

```bash
# Install dependencies
cd docs && npm install

# Build documentation
npm run build

# Serve with live reload
npm run serve

# Validate links
npm run lint
```

### Writing Documentation

When adding or modifying documentation:

1. **Identify the quadrant**: Is this teaching (tutorial), helping accomplish a task (how-to), providing facts (reference), or explaining concepts (explanation)?

2. **Use the right tone**:
   - Tutorials: "We will..." (inclusive, guiding)
   - How-To: "Do X to achieve Y" (direct, practical)
   - Reference: Neutral, factual descriptions
   - Explanation: "This works because..." (conceptual)

3. **Cross-reference appropriately**: Use `xref:` links to connect related content across quadrants

4. **Add diagram/screenshot placeholders**: Use NOTE blocks for images to be added later:
   ```asciidoc
   [NOTE]
   ====
   **[SCREENSHOT PLACEHOLDER]**
   _Description: What the screenshot should show_
   Image path: `images/screenshots/filename.png`
   ====
   ```

### Documentation Build System

- **Format**: AsciiDoc (`.adoc` files)
- **Builder**: Custom Node.js script using `@asciidoctor/core`
- **Features**: Auto-navigation generation, search (Pagefind), dark/light theme
- **Output**: Static HTML in `docs/_site/`
- **Deployment**: Vercel (auto-deploys on push to main)
