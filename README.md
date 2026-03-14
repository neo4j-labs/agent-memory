# Neo4j Agent Memory

A graph-native memory system for AI agents. Store conversations, build knowledge graphs, and let your agents learn from their own reasoning -- all backed by Neo4j.

[![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)](https://neo4j.com/labs/)
[![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)](https://neo4j.com/labs/)
[![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)](https://community.neo4j.com)
[![CI](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/neo4j-labs/agent-memory/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/neo4j-agent-memory.svg)](https://badge.fury.io/py/neo4j-agent-memory)
[![Python versions](https://img.shields.io/pypi/pyversions/neo4j-agent-memory.svg)](https://pypi.org/project/neo4j-agent-memory/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

> ⚠️ **This is a Neo4j Labs project.** It is actively maintained but not officially supported. There are no SLAs or guarantees around backwards compatibility and deprecation. For questions and support, please use the [Neo4j Community Forum](https://community.neo4j.com).

> **See it in action**: The [Lenny's Podcast Memory Explorer](examples/lennys-memory/) demo loads 299 podcast episodes into a searchable knowledge graph with an AI chat agent, interactive graph visualization, geospatial map view, and Wikipedia-enriched entity cards.

## Features

- **Three Memory Types**: Short-Term (conversations), Long-Term (facts/preferences), and Reasoning (reasoning traces)
- **POLE+O Data Model**: Configurable entity schema based on Person, Object, Location, Event, Organization types with subtypes
- **Multi-Stage Entity Extraction**: Pipeline combining spaCy, GLiNER2, and LLM extractors with configurable merge strategies
- **Batch & Streaming Extraction**: Process multiple texts in parallel or stream results for long documents
- **Entity Resolution**: Multi-strategy deduplication (exact, fuzzy, semantic matching) with type-aware resolution
- **Entity Deduplication on Ingest**: Automatic duplicate detection with configurable auto-merge and flagging
- **Provenance Tracking**: Track where entities were extracted from and which extractor produced them
- **Background Entity Enrichment**: Automatically enrich entities with Wikipedia and Diffbot data
- **Relationship Extraction & Storage**: Extract relationships using GLiREL (no LLM) and automatically store as graph relationships
- **Vector + Graph Search**: Semantic similarity search and graph traversal in a single database
- **Geospatial Queries**: Spatial indexes on Location entities for radius and bounding box search
- **Temporal Relationships**: Track when facts become valid or invalid
- **CLI Tool**: Command-line interface for entity extraction and schema management
- **Observability**: OpenTelemetry and Opik tracing for monitoring extraction pipelines
- **Agent Framework Integrations**: LangChain, Pydantic AI, LlamaIndex, CrewAI, OpenAI Agents, Strands Agents (AWS)
- **Amazon Bedrock Embeddings**: Use Titan or Cohere embedding models via AWS Bedrock
- **AWS Hybrid Memory**: HybridMemoryProvider with intelligent routing between short-term and long-term memory

## Installation

```bash
# Basic installation
pip install neo4j-agent-memory

# With OpenAI embeddings
pip install neo4j-agent-memory[openai]

# With Google Cloud (Vertex AI embeddings)
pip install neo4j-agent-memory[vertex-ai]

# With Amazon Bedrock embeddings
pip install neo4j-agent-memory[bedrock]

# With AWS Strands Agents
pip install neo4j-agent-memory[strands]

# With all AWS integrations (Bedrock + Strands + AgentCore)
pip install neo4j-agent-memory[aws]

# With Google ADK integration
pip install neo4j-agent-memory[google-adk]

# With MCP server
pip install neo4j-agent-memory[mcp]

# With spaCy for fast entity extraction
pip install neo4j-agent-memory[spacy]
python -m spacy download en_core_web_sm

# With LangChain integration
pip install neo4j-agent-memory[langchain]

# With Microsoft Agent Framework integration
pip install neo4j-agent-memory[microsoft-agent]

# With CLI tools
pip install neo4j-agent-memory[cli]

# With observability (OpenTelemetry)
pip install neo4j-agent-memory[opentelemetry]

# With all optional dependencies
pip install neo4j-agent-memory[all]
```

Using uv:

```bash
uv add neo4j-agent-memory
uv add neo4j-agent-memory --extra openai
uv add neo4j-agent-memory --extra spacy
```

## Quick Start

```python
import asyncio
from pydantic import SecretStr
from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig

async def main():
    # Configure settings
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri="bolt://localhost:7687",
            username="neo4j",
            password=SecretStr("your-password"),
        )
    )

    # Use the memory client
    async with MemoryClient(settings) as memory:
        # Store a conversation message
        await memory.short_term.add_message(
            session_id="user-123",
            role="user",
            content="Hi, I'm John and I love Italian food!"
        )

        # Add a preference
        await memory.long_term.add_preference(
            category="food",
            preference="Loves Italian cuisine",
            context="Dining preferences"
        )

        # Search for relevant memories
        preferences = await memory.long_term.search_preferences("restaurant recommendation")
        for pref in preferences:
            print(f"[{pref.category}] {pref.preference}")

        # Get combined context for an LLM prompt
        context = await memory.get_context(
            "What restaurant should I recommend?",
            session_id="user-123"
        )
        print(context)

asyncio.run(main())
```

## Memory Types

### Short-Term Memory

Stores conversation history and experiences:

```python
# Add messages to a conversation
await memory.short_term.add_message(
    session_id="user-123",
    role="user",
    content="I'm looking for a restaurant"
)

# Get conversation history
conversation = await memory.short_term.get_conversation("user-123")
for msg in conversation.messages:
    print(f"{msg.role}: {msg.content}")

# Search past messages
results = await memory.short_term.search_messages("Italian food")
```

### Long-Term Memory

Stores facts, preferences, and entities:

```python
# Add entities with POLE+O types and subtypes
entity = await memory.long_term.add_entity(
    name="John Smith",
    entity_type="PERSON",  # POLE+O type
    subtype="INDIVIDUAL",  # Optional subtype
    description="A customer who loves Italian food"
)

# Add preferences
pref = await memory.long_term.add_preference(
    category="food",
    preference="Prefers vegetarian options",
    context="When dining out"
)

# Add facts with temporal validity
from datetime import datetime
fact = await memory.long_term.add_fact(
    subject="John",
    predicate="works_at",
    obj="Acme Corp",
    valid_from=datetime(2023, 1, 1)
)

# Search for relevant entities
entities = await memory.long_term.search_entities("Italian restaurants")
```

### Reasoning Memory

Stores reasoning traces and tool usage patterns:

```python
# Start a reasoning trace (optionally linked to a triggering message)
trace = await memory.reasoning.start_trace(
    session_id="user-123",
    task="Find a restaurant recommendation",
    triggered_by_message_id=user_message.id,  # Optional: link to message
)

# Add reasoning steps
step = await memory.reasoning.add_step(
    trace.id,
    thought="I should search for nearby restaurants",
    action="search_restaurants"
)

# Record tool calls (optionally linked to a message)
await memory.reasoning.record_tool_call(
    step.id,
    tool_name="search_api",
    arguments={"query": "Italian restaurants"},
    result=["La Trattoria", "Pasta Palace"],
    status=ToolCallStatus.SUCCESS,
    duration_ms=150,
    message_id=user_message.id,  # Optional: link tool call to message
)

# Complete the trace
await memory.reasoning.complete_trace(
    trace.id,
    outcome="Recommended La Trattoria",
    success=True
)

# Find similar past tasks
similar = await memory.reasoning.get_similar_traces("restaurant recommendation")

# Link an existing trace to a message (post-hoc)
await memory.reasoning.link_trace_to_message(trace.id, message.id)
```

## Advanced Features

### Session Management

List and manage conversation sessions:

```python
# List all sessions with metadata
sessions = await memory.short_term.list_sessions(
    prefix="user-",  # Optional: filter by prefix
    limit=50,
    offset=0,
    order_by="updated_at",  # "created_at", "updated_at", or "message_count"
    order_dir="desc",
)

for session in sessions:
    print(f"{session.session_id}: {session.message_count} messages")
    print(f"  First: {session.first_message_preview}")
    print(f"  Last: {session.last_message_preview}")
```

### Metadata-Based Search

Search messages with MongoDB-style metadata filters:

```python
# Search with metadata filters
results = await memory.short_term.search_messages(
    "restaurant",
    session_id="user-123",
    metadata_filters={
        "speaker": "Lenny",                    # Exact match
        "turn_index": {"$gt": 5},              # Greater than
        "source": {"$in": ["web", "mobile"]},  # In list
        "archived": {"$exists": False},        # Field doesn't exist
    },
    limit=10,
)
```

### Conversation Summaries

Generate summaries of conversations:

```python
# Basic summary (no LLM required)
summary = await memory.short_term.get_conversation_summary("user-123")
print(summary.summary)
print(f"Messages: {summary.message_count}")
print(f"Key entities: {summary.key_entities}")

# With custom LLM summarizer
async def my_summarizer(transcript: str) -> str:
    # Your LLM call here
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Summarize this conversation concisely."},
            {"role": "user", "content": transcript}
        ]
    )
    return response.choices[0].message.content

summary = await memory.short_term.get_conversation_summary(
    "user-123",
    summarizer=my_summarizer,
    include_entities=True,
)
```

### Streaming Trace Recording

Record reasoning traces during streaming responses:

```python
from neo4j_agent_memory import StreamingTraceRecorder

async with StreamingTraceRecorder(
    memory.reasoning,
    session_id="user-123",
    task="Process customer inquiry"
) as recorder:
    # Start a step
    step = await recorder.start_step(
        thought="Analyzing the request",
        action="analyze",
    )
    
    # Record tool calls
    await recorder.record_tool_call(
        "search_api",
        {"query": "customer history"},
        {"found": 5, "results": [...]},
    )
    
    # Add observations
    await recorder.add_observation("Found 5 relevant records")
    
    # Start another step
    await recorder.start_step(thought="Formulating response")

# Trace is automatically completed with timing when context exits
```

### List and Filter Traces

Query reasoning traces with filtering and pagination:

```python
# List traces with filters
traces = await memory.reasoning.list_traces(
    session_id="user-123",           # Optional session filter
    success_only=True,               # Only successful traces
    since=datetime(2024, 1, 1),      # After this date
    until=datetime(2024, 12, 31),    # Before this date
    limit=50,
    offset=0,
    order_by="started_at",           # "started_at" or "completed_at"
    order_dir="desc",
)

for trace in traces:
    print(f"{trace.task}: {'Success' if trace.success else 'Failed'}")
```

### Tool Statistics (Optimized)

Get pre-aggregated tool usage statistics:

```python
# Get stats for all tools (uses pre-aggregated data for speed)
stats = await memory.reasoning.get_tool_stats()

for tool in stats:
    print(f"{tool.name}:")
    print(f"  Total calls: {tool.total_calls}")
    print(f"  Success rate: {tool.success_rate:.1%}")
    print(f"  Avg duration: {tool.avg_duration_ms}ms")

# Migrate existing data to use pre-aggregation
migrated = await memory.reasoning.migrate_tool_stats()
print(f"Migrated stats for {len(migrated)} tools")
```

### Graph Export for Visualization

Export memory graph data for visualization with flexible filtering:

```python
# Export the full memory graph
graph = await memory.get_graph(
    memory_types=["short_term", "long_term", "reasoning"],  # Optional filter
    session_id="user-123",  # Optional: scope to a specific conversation
    include_embeddings=False,  # Don't include large embedding vectors
    limit=1000,
)

print(f"Nodes: {len(graph.nodes)}")
print(f"Relationships: {len(graph.relationships)}")

# Access graph data
for node in graph.nodes:
    print(f"{node.labels}: {node.properties.get('name', node.id)}")

for rel in graph.relationships:
    print(f"{rel.from_node} -[{rel.type}]-> {rel.to_node}")
```

**Conversation-Scoped Graphs**: Use `session_id` to export only the memory associated with a specific conversation:

```python
# Get graph for a specific conversation (thread)
conversation_graph = await memory.get_graph(
    session_id="thread-abc123",  # Only nodes related to this session
    include_embeddings=False,
)

# This returns:
# - Messages in that conversation
# - Entities mentioned in those messages
# - Reasoning traces from that session
# - Relationships connecting them
```

This is particularly useful for visualization UIs that want to show contextually relevant data rather than the entire knowledge graph.

### Location Queries

Query location entities with optional conversation filtering:

```python
# Get all locations with coordinates
locations = await memory.get_locations(has_coordinates=True)

# Get locations mentioned in a specific conversation
locations = await memory.get_locations(
    session_id="thread-abc123",  # Only locations from this conversation
    has_coordinates=True,
    limit=100,
)

# Each location includes:
# - id, name, subtype (city, country, landmark, etc.)
# - latitude, longitude coordinates
# - conversations referencing this location
```

**Geospatial Queries**: Search for locations by proximity or bounding box:

```python
# Find locations within 50km of a point
nearby = await memory.long_term.search_locations_near(
    latitude=40.7128,
    longitude=-74.0060,
    radius_km=50,
    session_id="thread-123",  # Optional: filter by conversation
)

# Find locations in a bounding box (useful for map viewports)
in_view = await memory.long_term.search_locations_in_bounding_box(
    min_lat=40.0,
    max_lat=42.0,
    min_lon=-75.0,
    max_lon=-73.0,
    session_id="thread-123",  # Optional: filter by conversation
)
```

### PydanticAI Trace Recording

Automatically record PydanticAI agent runs as reasoning traces:

```python
from pydantic_ai import Agent
from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace

agent = Agent('openai:gpt-4o')

# Run the agent
result = await agent.run("Find me a good restaurant")

# Record the trace automatically
trace = await record_agent_trace(
    memory.reasoning,
    session_id="user-123",
    result=result,
    task="Restaurant recommendation",
    include_tool_calls=True,
)

print(f"Recorded trace with {len(trace.steps)} steps")
```

## POLE+O Data Model

The package uses the POLE+O data model for entity classification, an extension of the POLE (Person, Object, Location, Event) model commonly used in law enforcement and intelligence analysis:

| Type | Description | Example Subtypes |
|------|-------------|------------------|
| **PERSON** | Individuals, aliases, personas | INDIVIDUAL, ALIAS, PERSONA |
| **OBJECT** | Physical/digital items | VEHICLE, PHONE, EMAIL, DOCUMENT, DEVICE |
| **LOCATION** | Geographic areas, places | ADDRESS, CITY, REGION, COUNTRY, LANDMARK |
| **EVENT** | Incidents, occurrences | INCIDENT, MEETING, TRANSACTION, COMMUNICATION |
| **ORGANIZATION** | Companies, groups | COMPANY, NONPROFIT, GOVERNMENT, EDUCATIONAL |

### Using Entity Types and Subtypes

```python
from neo4j_agent_memory.memory.long_term import Entity, POLEO_TYPES

# Create an entity with type and subtype
entity = Entity(
    name="Toyota Camry",
    type="OBJECT",
    subtype="VEHICLE",
    description="Silver 2023 Toyota Camry"
)

# Access the full type (e.g., "OBJECT:VEHICLE")
print(entity.full_type)

# Available POLE+O types
print(POLEO_TYPES)  # ['PERSON', 'OBJECT', 'LOCATION', 'EVENT', 'ORGANIZATION']
```

### Entity Type Labels in Neo4j

Entity `type` and `subtype` are automatically added as Neo4j node labels in addition to being stored as properties. This enables efficient querying by type:

```python
# When you create this entity:
await client.long_term.add_entity(
    name="Toyota Camry",
    entity_type="OBJECT",
    subtype="VEHICLE",
    description="Silver sedan"
)

# Neo4j creates a node with multiple PascalCase labels:
# (:Entity:Object:Vehicle {name: "Toyota Camry", type: "OBJECT", subtype: "VEHICLE", ...})
```

This allows efficient Cypher queries by type (using PascalCase labels):

```cypher
-- Find all vehicles
MATCH (v:Vehicle) RETURN v

-- Find all people
MATCH (p:Person) RETURN p

-- Find all organizations
MATCH (o:Organization) RETURN o

-- Combine with other criteria
MATCH (v:Vehicle {name: "Toyota Camry"}) RETURN v
```

**Custom Entity Types:** If you define custom entity types outside the POLE+O model, they are also added as PascalCase labels as long as they are valid Neo4j label identifiers (start with a letter, contain only letters, numbers, and underscores):

```python
# Custom types also become PascalCase labels
await client.long_term.add_entity(
    name="Widget Pro",
    entity_type="PRODUCT",      # Custom type -> becomes :Product label
    subtype="ELECTRONICS",      # Custom subtype -> becomes :Electronics label
)

# Neo4j node: (:Entity:Product:Electronics {name: "Widget Pro", ...})

# Query custom types
MATCH (p:Product:Electronics) RETURN p
```

For POLE+O types, subtypes are validated against the known subtypes for that type. For custom types, any valid identifier can be used as a subtype.

## Entity Extraction Pipeline

The package provides a multi-stage extraction pipeline that combines different extractors for optimal accuracy and cost efficiency:

### Pipeline Architecture

```
Text → [spaCy NER] → [GLiNER] → [LLM Fallback] → Merged Results
           ↓              ↓            ↓
       Fast/Free    Zero-shot     High accuracy
```

### Using the Default Pipeline

```python
from neo4j_agent_memory.extraction import create_extractor
from neo4j_agent_memory.config import ExtractionConfig

# Create the default pipeline (spaCy → GLiNER → LLM)
config = ExtractionConfig(
    extractor_type="PIPELINE",
    enable_spacy=True,
    enable_gliner=True,
    enable_llm_fallback=True,
    merge_strategy="confidence",  # Keep highest confidence per entity
)

extractor = create_extractor(config)
result = await extractor.extract("John Smith works at Acme Corp in New York.")
```

### Building a Custom Pipeline

```python
from neo4j_agent_memory.extraction import ExtractorBuilder

# Use the fluent builder API
extractor = (
    ExtractorBuilder()
    .with_spacy(model="en_core_web_sm")
    .with_gliner(model="urchade/gliner_medium-v2.1", threshold=0.5)
    .with_llm_fallback(model="gpt-4o-mini")
    .with_merge_strategy("confidence")
    .build()
)

result = await extractor.extract("Meeting with Jane Doe at Central Park on Friday.")
for entity in result.entities:
    print(f"{entity.name}: {entity.type} (confidence: {entity.confidence:.2f})")
```

### Merge Strategies

When combining results from multiple extractors:

| Strategy | Description |
|----------|-------------|
| `union` | Keep all unique entities from all stages |
| `intersection` | Only keep entities found by multiple extractors |
| `confidence` | Keep highest confidence result per entity |
| `cascade` | Use first extractor's results, fill gaps with others |
| `first_success` | Stop at first stage that returns results |

### Individual Extractors

```python
from neo4j_agent_memory.extraction import (
    SpacyEntityExtractor,
    GLiNEREntityExtractor,
    LLMEntityExtractor,
)

# spaCy - Fast, free, good for common entity types
spacy_extractor = SpacyEntityExtractor(model="en_core_web_sm")

# GLiNER - Zero-shot NER with custom entity types
gliner_extractor = GLiNEREntityExtractor(
    model="gliner-community/gliner_medium-v2.5",
    entity_types=["person", "organization", "location", "vehicle", "weapon"],
    threshold=0.5,
)

# LLM - Most accurate but higher cost
llm_extractor = LLMEntityExtractor(
    model="gpt-4o-mini",
    entity_types=["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"],
)
```

### GLiNER2 Domain Schemas

GLiNER2 supports domain-specific schemas that improve extraction accuracy by providing entity type descriptions:

```python
from neo4j_agent_memory.extraction import (
    GLiNEREntityExtractor,
    get_schema,
    list_schemas,
)

# List available pre-defined schemas
print(list_schemas())
# ['poleo', 'podcast', 'news', 'scientific', 'business', 'entertainment', 'medical', 'legal']

# Create extractor with domain schema
extractor = GLiNEREntityExtractor.for_schema("podcast", threshold=0.45)

# Or use with the ExtractorBuilder
from neo4j_agent_memory.extraction import ExtractorBuilder

extractor = (
    ExtractorBuilder()
    .with_spacy()
    .with_gliner_schema("scientific", threshold=0.5)
    .with_llm_fallback()
    .build()
)

# Extract entities from domain-specific content
result = await extractor.extract(podcast_transcript)
for entity in result.filter_invalid_entities().entities:
    print(f"{entity.name}: {entity.type} ({entity.confidence:.0%})")
```

**Available schemas:**

| Schema | Use Case | Key Entity Types |
|--------|----------|------------------|
| `poleo` | Investigations/Intelligence | person, organization, location, event, object |
| `podcast` | Podcast transcripts | person, company, product, concept, book, technology |
| `news` | News articles | person, organization, location, event, date |
| `scientific` | Research papers | author, institution, method, dataset, metric, tool |
| `business` | Business documents | company, person, product, industry, financial_metric |
| `entertainment` | Movies/TV content | actor, director, film, tv_show, character, award |
| `medical` | Healthcare content | disease, drug, symptom, procedure, body_part, gene |
| `legal` | Legal documents | case, person, organization, law, court, monetary_amount |

See `examples/domain-schemas/` for complete example applications for each schema.

### Batch Extraction

Process multiple texts in parallel for efficient bulk extraction:

```python
from neo4j_agent_memory.extraction import ExtractionPipeline

pipeline = ExtractionPipeline(stages=[extractor])

result = await pipeline.extract_batch(
    texts=["Text 1...", "Text 2...", "Text 3..."],
    batch_size=10,
    max_concurrency=5,
    on_progress=lambda done, total: print(f"{done}/{total}"),
)

print(f"Success rate: {result.success_rate:.1%}")
print(f"Total entities: {result.total_entities}")
```

### Streaming Extraction for Long Documents

Process very long documents (>100K tokens) efficiently:

```python
from neo4j_agent_memory.extraction import StreamingExtractor, create_streaming_extractor

# Create streaming extractor
streamer = create_streaming_extractor(extractor, chunk_size=4000, overlap=200)

# Stream results chunk by chunk
async for chunk_result in streamer.extract_streaming(long_document):
    print(f"Chunk {chunk_result.chunk.index}: {chunk_result.entity_count} entities")

# Or get complete result with automatic deduplication
result = await streamer.extract(long_document, deduplicate=True)
```

### GLiREL Relationship Extraction

Extract relationships between entities without LLM calls:

```python
from neo4j_agent_memory.extraction import GLiNERWithRelationsExtractor, is_glirel_available

if is_glirel_available():
    extractor = GLiNERWithRelationsExtractor.for_poleo()
    result = await extractor.extract("John works at Acme Corp in NYC.")
    print(result.entities)   # John, Acme Corp, NYC
    print(result.relations)  # John -[WORKS_AT]-> Acme Corp
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

## Entity Deduplication

Automatic duplicate detection when adding entities:

```python
from neo4j_agent_memory.memory import LongTermMemory, DeduplicationConfig

config = DeduplicationConfig(
    auto_merge_threshold=0.95,  # Auto-merge above 95% similarity
    flag_threshold=0.85,        # Flag for review above 85%
    use_fuzzy_matching=True,
)

memory = LongTermMemory(client, embedder, deduplication=config)

# add_entity returns (entity, dedup_result) tuple
entity, result = await memory.add_entity("Jon Smith", "PERSON")
if result.action == "merged":
    print(f"Auto-merged with {result.matched_entity_name}")
elif result.action == "flagged":
    print(f"Flagged for review")
```

## Provenance Tracking

Track where entities were extracted from:

```python
# Link entity to source message
await memory.long_term.link_entity_to_message(
    entity, message_id,
    confidence=0.95, start_pos=10, end_pos=20,
)

# Link to extractor
await memory.long_term.link_entity_to_extractor(
    entity, "GLiNEREntityExtractor", confidence=0.95,
)

# Get provenance
provenance = await memory.long_term.get_entity_provenance(entity)
```

## Background Entity Enrichment

Automatically enrich entities with additional data from Wikipedia and Diffbot:

```python
from neo4j_agent_memory import MemorySettings, MemoryClient
from neo4j_agent_memory.config.settings import EnrichmentConfig, EnrichmentProvider

settings = MemorySettings(
    enrichment=EnrichmentConfig(
        enabled=True,
        providers=[EnrichmentProvider.WIKIMEDIA],  # Free, no API key needed
        background_enabled=True,  # Async processing
        entity_types=["PERSON", "ORGANIZATION", "LOCATION"],
    ),
)

async with MemoryClient(settings) as client:
    # Entities are automatically enriched in the background
    entity, _ = await client.long_term.add_entity(
        "Albert Einstein", "PERSON", confidence=0.9,
    )
    # After enrichment: entity gains enriched_description, wikipedia_url, wikidata_id

# Direct provider usage
from neo4j_agent_memory.enrichment import WikimediaProvider

provider = WikimediaProvider()
result = await provider.enrich("Albert Einstein", "PERSON")
print(result.description)  # "German-born theoretical physicist..."
print(result.wikipedia_url)  # "https://en.wikipedia.org/wiki/Albert_Einstein"
```

Environment variables:
```bash
NAM_ENRICHMENT__ENABLED=true
NAM_ENRICHMENT__PROVIDERS=["wikimedia", "diffbot"]
NAM_ENRICHMENT__DIFFBOT_API_KEY=your-api-key  # For Diffbot
```

## CLI Tool

Command-line interface for entity extraction and schema management:

```bash
# Install CLI extras
pip install neo4j-agent-memory[cli]

# Extract entities from text
neo4j-memory extract "John Smith works at Acme Corp in New York"

# Extract from a file with JSON output
neo4j-memory extract --file document.txt --format json

# Use different extractors
neo4j-memory extract "..." --extractor gliner
neo4j-memory extract "..." --extractor llm

# Schema management
neo4j-memory schemas list --password $NEO4J_PASSWORD
neo4j-memory schemas show my_schema --format yaml

# Statistics
neo4j-memory stats --password $NEO4J_PASSWORD
```

## Observability

Monitor extraction pipelines with OpenTelemetry or Opik:

```python
from neo4j_agent_memory.observability import get_tracer

# Auto-detect available provider
tracer = get_tracer()

# Or specify explicitly
tracer = get_tracer(provider="opentelemetry", service_name="my-service")

# Decorator-based tracing
@tracer.trace("extract_entities")
async def extract(text: str):
    return await extractor.extract(text)

# Context manager for manual spans
async with tracer.async_span("extraction") as span:
    span.set_attribute("text_length", len(text))
    result = await extract(text)
```

## Agent Framework Integrations

### LangChain

```python
from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory, Neo4jMemoryRetriever

# As memory for an agent
memory = Neo4jAgentMemory(
    memory_client=client,
    session_id="user-123"
)

# As a retriever
retriever = Neo4jMemoryRetriever(
    memory_client=client,
    k=10
)
docs = retriever.invoke("Italian restaurants")
```

### Pydantic AI

```python
from pydantic_ai import Agent
from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency, create_memory_tools

# As a dependency
agent = Agent('openai:gpt-4o', deps_type=MemoryDependency)

@agent.system_prompt
async def system_prompt(ctx):
    context = await ctx.deps.get_context(ctx.messages[-1].content)
    return f"You are helpful.\n\nContext:\n{context}"

# Or create tools for the agent
tools = create_memory_tools(client)
```

### LlamaIndex

```python
from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory

memory = Neo4jLlamaIndexMemory(
    memory_client=client,
    session_id="user-123"
)
nodes = memory.get("Italian food")
```

### CrewAI

```python
from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

memory = Neo4jCrewMemory(
    memory_client=client,
    crew_id="my-crew"
)
memories = memory.recall("restaurant recommendation")
```

### Google ADK

```python
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

# Create memory service for Google ADK
memory_service = Neo4jMemoryService(
    memory_client=client,
    user_id="user-123",
)

# Store a session
session = {"id": "session-1", "messages": [...]}
await memory_service.add_session_to_memory(session)

# Search memories
results = await memory_service.search_memories("project deadline")
```

### Strands Agents (AWS)

```python
from strands import Agent
from neo4j_agent_memory.integrations.strands import context_graph_tools

# Create pre-built memory tools
tools = context_graph_tools(
    neo4j_uri="bolt://localhost:7687",
    neo4j_password="password",
    embedding_provider="bedrock",
)

# Tools: search_context, get_entity_graph, add_memory, get_user_preferences
agent = Agent(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    tools=tools,
)
```

### Microsoft Agent Framework (Preview)

```python
from neo4j_agent_memory.integrations.microsoft_agent import (
    Neo4jMicrosoftMemory,
    create_memory_tools,
)

# Unified memory interface (context provider + chat history)
memory = Neo4jMicrosoftMemory(
    memory_client=client,
    session_id="user-123",
)

# Create callable tools for the agent
tools = create_memory_tools(memory)

# Use with ChatAgent — tools are auto-invoked by the framework
from agent_framework import ChatAgent
agent = ChatAgent(
    context_providers=[memory.context_provider],
    tools=tools,
)
```

### MCP Server

Expose memory capabilities via Model Context Protocol for AI platforms. See the **[MCP Server Quick Start](QUICKSTART.md)** to get up and running in under a minute.

```bash
# Run the MCP server
python -m neo4j_agent_memory.mcp.server \
    --neo4j-uri bolt://localhost:7687 \
    --neo4j-user neo4j \
    --neo4j-password password

# Or with SSE transport for Cloud Run
python -m neo4j_agent_memory.mcp.server --transport sse --port 8080
```

Available MCP tools:
- `memory_search` - Hybrid vector + graph search
- `memory_store` - Store messages, facts, preferences
- `entity_lookup` - Get entity with relationships
- `conversation_history` - Get session history
- `graph_query` - Execute read-only Cypher queries
- `add_reasoning_trace` - Record agent reasoning traces

See `deploy/cloudrun/` for Cloud Run deployment templates.

## Configuration

### Environment Variables

```bash
# Neo4j connection
NAM_NEO4J__URI=bolt://localhost:7687
NAM_NEO4J__USERNAME=neo4j
NAM_NEO4J__PASSWORD=your-password

# Embedding provider
NAM_EMBEDDING__PROVIDER=openai  # or vertex_ai, bedrock
NAM_EMBEDDING__MODEL=text-embedding-3-small

# OpenAI API key (if using OpenAI embeddings/extraction)
OPENAI_API_KEY=your-api-key

# Google Cloud (for Vertex AI embeddings)
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
VERTEX_AI_LOCATION=us-central1

# AWS (for Bedrock embeddings)
NAM_EMBEDDING__AWS_REGION=us-east-1
NAM_EMBEDDING__AWS_PROFILE=default  # optional
```

### Programmatic Configuration

```python
from neo4j_agent_memory import (
    MemorySettings,
    Neo4jConfig,
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
    ResolutionConfig,
    ResolverStrategy,
)

settings = MemorySettings(
    neo4j=Neo4jConfig(
        uri="bolt://localhost:7687",
        password=SecretStr("password"),
    ),
    embedding=EmbeddingConfig(
        provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,  # or OPENAI, VERTEX_AI, BEDROCK
        model="all-MiniLM-L6-v2",
        dimensions=384,
        # For Vertex AI:
        # provider=EmbeddingProvider.VERTEX_AI,
        # model="text-embedding-004",
        # project_id="your-gcp-project",
        # location="us-central1",
        # For Amazon Bedrock:
        # provider=EmbeddingProvider.BEDROCK,
        # model="amazon.titan-embed-text-v2:0",
        # aws_region="us-east-1",
    ),
    extraction=ExtractionConfig(
        # Use the multi-stage pipeline (default)
        extractor_type=ExtractorType.PIPELINE,
        
        # Pipeline stages
        enable_spacy=True,
        enable_gliner=True,
        enable_llm_fallback=True,
        
        # spaCy settings
        spacy_model="en_core_web_sm",
        
        # GLiNER settings
        gliner_model="urchade/gliner_medium-v2.1",
        gliner_threshold=0.5,
        
        # LLM settings
        llm_model="gpt-4o-mini",
        
        # POLE+O entity types
        entity_types=["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"],
        
        # Merge strategy for combining results
        merge_strategy="confidence",
    ),
    resolution=ResolutionConfig(
        strategy=ResolverStrategy.COMPOSITE,
        fuzzy_threshold=0.85,
        semantic_threshold=0.8,
    ),
)
```

## Entity Resolution

The package includes multiple strategies for resolving duplicate entities:

```python
from neo4j_agent_memory.resolution import (
    ExactMatchResolver,
    FuzzyMatchResolver,
    SemanticMatchResolver,
    CompositeResolver,
)

# Exact matching (case-insensitive)
resolver = ExactMatchResolver()

# Fuzzy matching using RapidFuzz
resolver = FuzzyMatchResolver(threshold=0.85)

# Semantic matching using embeddings
resolver = SemanticMatchResolver(embedder, threshold=0.8)

# Composite: tries exact -> fuzzy -> semantic
resolver = CompositeResolver(
    embedder=embedder,
    fuzzy_threshold=0.85,
    semantic_threshold=0.8,
)
```

## Neo4j Schema

The package automatically creates the following schema:

### Node Labels
- `Conversation`, `Message` - Short-term memory
- `Entity`, `Preference`, `Fact` - Long-term memory
  - Entity nodes also have type/subtype labels (e.g., `:Entity:Person:Individual`, `:Entity:Object:Vehicle`)
- `ReasoningTrace`, `ReasoningStep`, `Tool`, `ToolCall` - Reasoning memory

### Relationships

**Short-term memory:**
- `(Conversation)-[:HAS_MESSAGE]->(Message)` - Membership
- `(Conversation)-[:FIRST_MESSAGE]->(Message)` - First message in conversation
- `(Message)-[:NEXT_MESSAGE]->(Message)` - Sequential message chain
- `(Message)-[:MENTIONS]->(Entity)` - Entity mentions in message

**Long-term memory:**
- `(Entity)-[:RELATED_TO {relation_type, confidence}]->(Entity)` - Extracted relationships
- `(Entity)-[:SAME_AS]->(Entity)` - Entity deduplication

**Cross-memory linking:**
- `(ReasoningTrace)-[:INITIATED_BY]->(Message)` - Trace triggered by message
- `(ToolCall)-[:TRIGGERED_BY]->(Message)` - Tool call triggered by message

### Indexes
- Unique constraints on all ID fields
- Vector indexes for semantic search (requires Neo4j 5.11+)
- Regular indexes on frequently queried properties

## Demo: Lenny's Podcast Memory Explorer

The flagship demo in [`examples/lennys-memory/`](examples/lennys-memory/) showcases every major feature of neo4j-agent-memory by loading 299 episodes of Lenny's Podcast into a knowledge graph with a full-stack AI chat agent.

**[Try the live demo →](https://lennys-memory.vercel.app)**

**What it demonstrates:**

- **19 specialized agent tools** for semantic search, entity queries, geospatial analysis, and personalization
- **Three memory types working together**: conversations inform entity extraction, entities build a knowledge graph, reasoning traces help the agent improve
- **Wikipedia enrichment**: Entities are automatically enriched with descriptions, images, and external links
- **Interactive graph visualization** using Neo4j Visualization Library (NVL) with double-click-to-expand exploration
- **Geospatial map view** with Leaflet -- marker clusters, heatmaps, distance measurement, and shortest-path visualization
- **SSE streaming** for real-time token delivery with tool call visualization
- **Automatic preference learning** from natural conversation
- **Responsive design** -- fully usable on mobile and desktop

```bash
cd examples/lennys-memory
make neo4j          # Start Neo4j
make install        # Install dependencies
make load-sample    # Load 5 episodes for testing
make run-backend    # Start FastAPI (port 8000)
make run-frontend   # Start Next.js (port 3000)
```

See the [Lenny's Memory README](examples/lennys-memory/README.md) for a full architecture deep dive, API reference, and example Cypher queries.

## Requirements

- Python 3.10+
- Neo4j 5.x (5.11+ recommended for vector indexes)

## Development

```bash
# Clone the repository
git clone https://github.com/neo4j-labs/agent-memory.git
cd agent-memory

# Install with uv
uv sync --group dev

# Or use the Makefile
make install
```

### Using the Makefile

The project includes a comprehensive Makefile for common development tasks:

```bash
# Run all tests (unit + integration with auto-Docker)
make test

# Run unit tests only
make test-unit

# Run integration tests (auto-starts Neo4j via Docker)
make test-integration

# Code quality
make lint         # Run ruff linter
make format       # Format code with ruff
make typecheck    # Run mypy type checking
make check        # Run all checks (lint + typecheck + test)

# Docker management for Neo4j
make neo4j-start  # Start Neo4j container
make neo4j-stop   # Stop Neo4j container
make neo4j-logs   # View Neo4j logs
make neo4j-clean  # Stop and remove volumes

# Run examples
make example-basic      # Basic usage example
make example-resolution # Entity resolution example
make example-langchain  # LangChain integration example
make example-pydantic   # Pydantic AI integration example
make examples           # Run all examples

# Full-stack chat agent
make chat-agent-install  # Install backend + frontend dependencies
make chat-agent-backend  # Run FastAPI backend (port 8000)
make chat-agent-frontend # Run Next.js frontend (port 3000)
make chat-agent          # Show setup instructions
```

### Running Examples

Examples are located in `examples/` and demonstrate various features:

| Example | Description | Requirements |
|---------|-------------|--------------|
| [`lennys-memory/`](examples/lennys-memory/) | **Flagship demo**: Podcast knowledge graph with AI chat, graph visualization, map view, entity enrichment | Neo4j, OpenAI, Node.js |
| [`financial-services-advisor/`](examples/financial-services-advisor/) | **AWS Strands demo**: Multi-agent KYC/AML compliance with 5 specialized agents, CDK deployment | Neo4j Aura, AWS Bedrock, Node.js |
| `full-stack-chat-agent/` | Full-stack web app with FastAPI backend and Next.js frontend | Neo4j, OpenAI, Node.js |
| `basic_usage.py` | Core memory operations (short-term, long-term, reasoning) | Neo4j, OpenAI API key |
| `entity_resolution.py` | Entity matching strategies | None |
| `langchain_agent.py` | LangChain integration | Neo4j, OpenAI, langchain extra |
| `pydantic_ai_agent.py` | Pydantic AI integration | Neo4j, OpenAI, pydantic-ai extra |
| `domain-schemas/` | GLiNER2 domain schema examples (8 domains) | GLiNER extra, optional Neo4j |

#### Environment Setup

Examples load environment variables from `examples/.env`. Copy the template:

```bash
cp examples/.env.example examples/.env
# Edit examples/.env with your settings
```

Key variables:
- `NEO4J_URI` - If set, uses this Neo4j; if not set, auto-starts Docker
- `NEO4J_PASSWORD` - Neo4j password (`test-password` for Docker)
- `OPENAI_API_KEY` - Required for OpenAI embeddings and LLM extraction

```bash
# Run with your own Neo4j (uses NEO4J_URI from .env)
make example-basic

# Or without .env (auto-starts Docker Neo4j)
rm examples/.env  # Ensure no .env file
make example-basic  # Will start Docker with test-password
```

### Environment Variables for Testing

```bash
# Control integration test behavior
RUN_INTEGRATION_TESTS=1      # Enable integration tests
SKIP_INTEGRATION_TESTS=1     # Skip integration tests
AUTO_START_DOCKER=1          # Auto-start Neo4j via Docker (default: true)
AUTO_STOP_DOCKER=1           # Auto-stop Neo4j after tests (default: false)
```

The integration test script supports several options:

```bash
# Keep Neo4j running after tests (useful for debugging)
./scripts/run-integration-tests.sh --keep

# Run with verbose output
./scripts/run-integration-tests.sh --verbose

# Run specific test pattern
./scripts/run-integration-tests.sh --pattern "test_short_term"
```

## Publishing to PyPI

1. Update version in `pyproject.toml`
2. Create and push a tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. GitHub Actions will automatically build and publish to PyPI

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com) - Ask questions and get help
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) - Report bugs or request features
- 📖 [Documentation](https://neo4j-agent-memory.vercel.app/) - Full documentation site

## License

Apache License 2.0

## Contributing

Contributions are welcome! Please read the guidelines below before submitting a pull request.

### CI/CD Pipeline

This project uses GitHub Actions for continuous integration and deployment. The pipeline automatically runs on every push to `main` and on all pull requests.

#### Workflow Overview

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| **CI** (`ci.yml`) | Push to `main`, PRs | Linting, type checking, tests, build validation |
| **Release** (`release.yml`) | Git tags (`v*`) | Build and publish to PyPI, create GitHub releases |

#### CI Jobs

The CI workflow runs the following jobs:

1. **Lint** - Code quality checks using Ruff
   - `ruff check` for linting errors
   - `ruff format --check` for formatting consistency

2. **Type Check** - Static type analysis using mypy
   - Validates type annotations in `src/`

3. **Unit Tests** - Fast tests without external dependencies
   - Runs on Python 3.10, 3.11, 3.12, and 3.13
   - Generates code coverage reports (uploaded to Codecov)
   - Command: `pytest tests/unit -v --cov`

4. **Integration Tests** - Tests with Neo4j database
   - Uses GitHub Actions services to spin up Neo4j 5.26
   - Runs on Python 3.12 first, then matrix across all versions
   - Command: `pytest tests/integration -v`

5. **Example Tests** - Validates example code works
   - Quick validation (no Neo4j): import checks, basic functionality
   - Full validation (with Neo4j): smoke tests for examples

6. **Build** - Package build validation
   - Builds wheel and sdist
   - Validates package can be installed and imported
   - Uploads build artifacts

#### Running CI Locally

Before submitting a PR, run the same checks locally:

```bash
# Run all checks (recommended before PR)
make ci

# Or run individual checks:
make lint        # Ruff linting
make format      # Auto-format code
make typecheck   # Mypy type checking
make test        # Unit tests only
make test-all    # Unit + integration tests
```

#### Pull Request Requirements

All PRs must pass these checks before merging:
- ✅ Lint (ruff check)
- ✅ Format (ruff format)
- ✅ Unit tests (all Python versions)
- ✅ Integration tests
- ✅ Build validation

#### Release Process

Releases are automated via GitHub Actions:

1. Update version in `pyproject.toml`
2. Create and push a git tag: `git tag v0.2.0 && git push --tags`
3. GitHub Actions automatically:
   - Builds the package
   - Publishes to PyPI (using trusted publishing)
   - Creates a GitHub release with auto-generated notes

#### Test Categories

```bash
# Unit tests (fast, no external dependencies)
pytest tests/unit -v

# Integration tests (requires Neo4j)
pytest tests/integration -v

# Example validation tests
pytest tests/examples -v

# All tests with coverage
pytest --cov=neo4j_agent_memory --cov-report=html
```

### Code Style

- **Formatter**: Ruff (line length: 88)
- **Linter**: Ruff
- **Type Checker**: mypy (strict mode)
- **Docstrings**: Google style

### Development Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run `make ci` to validate
5. Commit with descriptive messages
6. Push and open a PR against `main`

### Documentation Guidelines (Diataxis Framework)

The documentation follows the [Diataxis framework](https://diataxis.fr/), which organizes content into four distinct types based on user needs. When contributing, place your documentation in the appropriate category:

#### Documentation Types

| Type | Purpose | User Need | Location |
|------|---------|-----------|----------|
| **Tutorials** | Learning-oriented | "I want to learn" | `docs/tutorials/` |
| **How-To Guides** | Task-oriented | "I want to accomplish X" | `docs/how-to/` |
| **Reference** | Information-oriented | "I need to look up Y" | `docs/reference/` |
| **Explanation** | Understanding-oriented | "I want to understand why" | `docs/explanation/` |

#### When to Include Each Documentation Type in a PR

**Tutorials** (`docs/tutorials/`)
- Include when: Adding a major new feature that requires guided learning
- Example: A new memory type, a new integration, or a complex workflow
- Characteristics: Step-by-step, learning-focused, complete working examples
- Not needed for: Bug fixes, minor enhancements, internal refactors

**How-To Guides** (`docs/how-to/`)
- Include when: Adding functionality users will want to accomplish as a task
- Example: "How to configure custom entity types", "How to use batch extraction"
- Characteristics: Goal-oriented, assumes basic knowledge, focused on one task
- Required for: Any new public API method or configuration option

**Reference** (`docs/reference/`)
- Include when: Adding or changing public API (classes, methods, parameters)
- Example: New method signatures, configuration options, CLI commands
- Characteristics: Complete, accurate, structured, no explanation of concepts
- Required for: All public API changes

**Explanation** (`docs/explanation/`)
- Include when: Adding features that involve architectural decisions or trade-offs
- Example: "Why we use POLE+O model", "How entity resolution works"
- Characteristics: Conceptual, discusses alternatives, provides background
- Not needed for: Implementation details users don't need to understand

#### Documentation PR Checklist

For feature PRs, ensure you've updated the appropriate documentation:

- [ ] **New public API?** → Update `docs/reference/` with method signatures
- [ ] **New user-facing feature?** → Add how-to guide in `docs/how-to/`
- [ ] **Major new capability?** → Consider adding a tutorial in `docs/tutorials/`
- [ ] **Architectural change?** → Add explanation in `docs/explanation/`
- [ ] **Code examples compile?** → Run `make test-docs-syntax`

#### Building and Testing Documentation

```bash
# Build documentation locally
cd docs && npm install && npm run build

# Preview documentation
cd docs && npm run serve

# Run documentation tests
make test-docs           # All doc tests
make test-docs-syntax    # Validate Python code snippets compile
make test-docs-build     # Test build pipeline
make test-docs-links     # Validate internal links
```

#### Quick Reference: Diataxis Decision Tree

```
Is this about learning a concept from scratch?
  → Yes: Tutorial (docs/tutorials/)
  → No: ↓

Is this about accomplishing a specific task?
  → Yes: How-To Guide (docs/how-to/)
  → No: ↓

Is this describing what something is or how to use it?
  → Yes: Reference (docs/reference/)
  → No: ↓

Is this explaining why something works the way it does?
  → Yes: Explanation (docs/explanation/)
```
