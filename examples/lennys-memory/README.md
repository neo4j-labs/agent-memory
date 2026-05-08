# Lenny's Podcast Memory Explorer

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

A full-stack AI agent application that transforms 299 episodes of Lenny's Podcast into a searchable knowledge graph with conversational AI, interactive graph visualization, geospatial analysis, and Wikipedia-enriched entity cards -- all powered by [neo4j-agent-memory](https://github.com/neo4j-labs/agent-memory).

**[Try the live demo →](https://lennys-memory.vercel.app)**

<!-- TODO: Add screenshot of the main app interface -->
![App Screenshot](docs/images/app-screenshot.png)

<!-- TODO: Add screenshot of the Neo4j data model -->
![Data Model](docs/images/data-model.png)

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not officially
> supported. There are no SLAs or guarantees around backwards compatibility and
> deprecation. For questions and support, please use the
> [Neo4j Community Forum](https://community.neo4j.com).

---

## What This Demo Shows

This is the flagship demo application for the `neo4j-agent-memory` library. It demonstrates how to build a production-grade AI agent that:

- **Remembers conversations** across sessions using short-term memory
- **Builds a knowledge graph** of people, companies, locations, and concepts extracted from unstructured text
- **Learns user preferences** from natural conversation and uses them to personalize responses
- **Records reasoning traces** so the agent can learn from its own past behavior
- **Enriches entities** with Wikipedia descriptions, images, and external links
- **Visualizes memory** as an interactive graph and geospatial map

> Think of it as RAG with a graph-powered memory layer -- your agent doesn't just retrieve documents, it understands the relationships between entities, remembers what you've asked before, and gets smarter over time.

---

## Architecture

<!-- Export the Excalidraw diagram to PNG and replace this placeholder -->
![Lenny's Memory Architecture](img/architecture.png)

> *Diagram source: [img/architecture.excalidraw](img/architecture.excalidraw) -- open in [Excalidraw](https://excalidraw.com) to edit*

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI + PydanticAI + neo4j-agent-memory |
| **Frontend** | Next.js 14 + Chakra UI v3 + TypeScript |
| **Graph Visualization** | Neo4j Visualization Library (NVL) |
| **Map Visualization** | Leaflet + react-leaflet + Turf.js |
| **Database** | Neo4j 5.x (with APOC plugin) |
| **LLM** | OpenAI GPT-4o |
| **Entity Extraction** | spaCy + GLiNER2 + LLM pipeline (via `ExtractionConfig`) |
| **Entity Deduplication** | Embedding similarity + fuzzy matching (via `DeduplicationConfig`) |
| **Entity Enrichment** | Wikipedia/Wikimedia API |
| **Observability** | OpenTelemetry / Opik tracing support |

## neo4j-agent-memory v0.1.0 Features

This example showcases several features from neo4j-agent-memory v0.1.0:

- **`ExtractionConfig`** with `gliner_schema="podcast"` -- uses the podcast-optimized domain schema for entity extraction
- **`DeduplicationConfig`** -- auto-merges entities at 95%+ similarity, flags for review at 85%+, with fuzzy string matching
- **Observability** -- optional `get_tracer()` integration for OpenTelemetry/Opik tracing of extraction pipelines
- **Streaming extraction** -- `StreamingExtractor` for memory-efficient processing of long podcast transcripts
- **Provenance tracking** -- `link_entity_to_message()` and `link_entity_to_extractor()` for tracing entity origins

## v2.0 Features

The latest version includes significant UI/UX improvements:

### Neo4j Labs Branding
- Labs Purple (#6366F1) primary accent with Neo4j Teal (#009999) secondary
- Custom typography: Syne (headings), Public Sans (body), JetBrains Mono (code)
- Beta status badge and Labs disclaimer throughout

### Inline Tool Result Cards
Tool outputs are now displayed as rich, interactive cards directly in the chat:

| Tool Pattern | Card Type | Description |
|-------------|-----------|-------------|
| Location tools | **MapCard** | Inline Leaflet map with markers, expandable to fullscreen |
| Entity context tools | **EntityCard** | Wikipedia-style knowledge panel with image, description, mentions |
| Entity/graph tools | **GraphCard** | Inline NVL graph visualization, expandable to fullscreen |
| Memory graph search | **MemoryGraphCard** | Combined vector search + graph traversal visualization |
| Search/list tools | **DataCard** | Responsive table with auto-detected columns |
| Stats/metrics tools | **StatsCard** | Grid of color-coded metric boxes |
| Other tools | **RawJsonCard** | Collapsible JSON viewer for debugging |

<!-- TODO: Add screenshot showing tool call cards in action -->
![Tool Call Cards](docs/images/tool-call-cards.png)

### Onboarding & Education
- **WelcomeModal**: First-time user introduction explaining memory types
- **Suggested queries**: Clickable prompt cards in the empty state
- **Memory type explanations**: Short-term, long-term, and reasoning memory

### Simplified Conversation Model
- **Quick Start suggestions**: Previous first messages shown as clickable cards in the sidebar
- **Single conversation focus**: Each session treats the app as a fresh conversation
- **Clicking a suggestion**: Creates a new conversation with that message as the first query
- **Always-on memory**: Memory is always enabled (no toggle) for simplified UX

### Agent Configuration Panel
The right sidebar displays static agent configuration info:
- **Available Tools**: All 19 agent tools organized by category
- **Agent Capabilities**: Multi-step reasoning, conversation memory, preference learning, knowledge graph
- **Tool Call Cards**: Documentation of all 7 card types with descriptions and triggering tools

### Mobile-First Responsive Design
- Responsive layout with drawer navigation on mobile
- Touch-optimized controls (44px minimum targets)
- Floating action button to open agent configuration panel on mobile

---

## Quick Start

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- Docker (for Neo4j)
- OpenAI API key

### 1. Start Neo4j

```bash
make neo4j
```

This starts Neo4j at http://localhost:7474 (user: `neo4j`, password: `password`).

### 2. Install Dependencies

```bash
make install
```

### 3. Configure Environment

Backend:
```bash
cd backend
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

Frontend:
```bash
cd frontend
cp .env.example .env
```

### 4. Load Podcast Transcripts

Load a sample (5 transcripts) for quick testing:
```bash
make load-sample
```

Or load the full dataset (299 transcripts):
```bash
make load-full
```

**Additional loading options:**

```bash
# Fast loading without entity extraction (significantly faster)
make load-fast

# Resume an interrupted load (skip already loaded transcripts)
make load-resume

# Preview what would be loaded without actually loading
make load-dry-run
```

**Post-processing options:**

```bash
# Extract entities from already loaded sessions (if you used --no-entities initially)
make extract-entities

# Backfill RELATED_TO relationships between existing entities
make backfill-relationships

# Check relationship extraction status
make backfill-relationships-status

# Geocode Location entities (add lat/lon coordinates for spatial queries)
make geocode-locations

# Enrich entities with Wikipedia data (descriptions, images, links)
make enrich

# Check enrichment progress
make enrich-status
```

The loader shows real-time progress with ETA:
```
Overall  [████████████░░░░░░░░░░░░░░░░░░] 450/1200 (38%) ETA: 2m 15s [3/10] Brian Chesky.txt
```

### 5. Run the Application

Backend (port 8000):
```bash
make run-backend
```

Frontend (port 3000):
```bash
make run-frontend
```

Visit http://localhost:3000 to start exploring.

### 6. (Optional) Connect Claude Desktop via MCP

You can also query the Lenny's Podcast knowledge graph directly from Claude Desktop using the MCP server. This connects to the same Neo4j instance as the web app.

Start the MCP server:
```bash
make mcp-server
```

Then add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lennys-memory": {
      "command": "neo4j-agent-memory",
      "args": ["mcp", "serve",
               "--password", "password",
               "--profile", "extended",
               "--session-strategy", "per_day",
               "--user-id", "lenny-desktop"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

After restarting Claude Desktop, you can ask questions like:
- "Search for episodes about product-market fit"
- "What entities are related to Brian Chesky?"
- "What are my stored preferences?"

The MCP server provides 16 tools (or 6 in core profile) for searching, storing, and querying the knowledge graph. See the [MCP Tools Reference](../../docs/modules/ROOT/pages/reference/mcp-tools.adoc) for details.

---

## How It Works: A Deep Dive

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Next.js Frontend                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────────────┐ │
│  │   Chat    │  │  Memory  │  │  Graph   │  │   Map               │ │
│  │   UI      │  │  Context │  │  View    │  │   View              │ │
│  │  (SSE)    │  │  Panel   │  │  (NVL)   │  │  (Leaflet)          │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────────────────┘ │
└───────┼──────────────┼─────────────┼─────────────┼──────────────────┘
        │              │             │             │
        ▼              ▼             ▼             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                                 │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ PydanticAI  │  │ Memory   │  │  Entity  │  │  Location        │ │
│  │ Agent       │  │ Context  │  │  Routes  │  │  Routes          │ │
│  │ (19 tools)  │  │ Routes   │  │          │  │  (geospatial)    │ │
│  └──────┬──────┘  └────┬─────┘  └────┬─────┘  └────┬─────────────┘ │
└─────────┼───────────────┼─────────────┼─────────────┼───────────────┘
          │               │             │             │
          ▼               ▼             ▼             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    neo4j-agent-memory                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │  Short-Term  │  │  Long-Term   │  │     Reasoning           │   │
│  │  Memory      │  │  Memory      │  │     Memory               │   │
│  │              │  │              │  │                            │   │
│  │ Conversations│  │ Entities     │  │  Reasoning Traces         │   │
│  │ Messages     │  │ Preferences  │  │  Tool Call Records        │   │
│  │ Embeddings   │  │ Facts        │  │  Performance Stats        │   │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘   │
│         │                 │                        │                  │
│         ▼                 ▼                        ▼                  │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │           Neo4j Graph Database                               │    │
│  │   Nodes: Conversation, Message, Entity, Preference,          │    │
│  │          ReasoningTrace, ReasoningStep, ToolCall              │    │
│  │   Vectors: Semantic search on messages, entities              │    │
│  │   Spatial: Point indexes on Location entities                 │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### The Three Memory Types in Action

#### Short-Term Memory: Conversation History

Every user message and assistant response is stored as a `Message` node in Neo4j, linked sequentially within a `Conversation`:

```
(Conversation: "lenny-podcast-brian-chesky")
    -[:FIRST_MESSAGE]-> (Message: "What did Brian say about...")
    -[:NEXT_MESSAGE]->  (Message: "Brian Chesky discussed...")
    -[:NEXT_MESSAGE]->  (Message: "Can you tell me more about...")
```

This enables:
- **Semantic search** across all past conversations using vector indexes
- **Session isolation** -- each thread has its own conversation history
- **Temporal ordering** -- messages are linked in sequence for context reconstruction

#### Long-Term Memory: The Knowledge Graph

Entities are extracted from podcast transcripts using a multi-stage pipeline and stored as typed nodes in Neo4j with the POLE+O model (Person, Object, Location, Event, Organization):

```cypher
// Entities extracted from transcripts
(:Entity:Person {name: "Brian Chesky", enriched_description: "American businessman...", 
                 wikipedia_url: "https://en.wikipedia.org/wiki/Brian_Chesky",
                 image_url: "https://upload.wikimedia.org/..."})

(:Entity:Organization {name: "Airbnb", enriched_description: "American company..."})

(:Entity:Location {name: "San Francisco", location: point({latitude: 37.77, longitude: -122.41})})

// Entities are linked to source messages with provenance
(Message)-[:MENTIONS]->(Entity:Person)
```

User preferences are also stored in long-term memory, automatically extracted from conversation:

```cypher
(:Preference {category: "format", preference: "Prefers detailed summaries with quotes", 
              importance: 0.8})
```

#### Reasoning Memory: Reasoning Traces

Every agent interaction is recorded as a reasoning trace, capturing the full chain of thought:

```cypher
(ReasoningTrace {task: "Compare growth strategies", success: true})
    -[:HAS_STEP]-> (ReasoningStep {thought: "Search Brian Chesky's comments on growth"})
        -[:USES_TOOL]-> (ToolCall {tool: "search_by_speaker", duration_ms: 245, status: "success"})
    -[:HAS_STEP]-> (ReasoningStep {thought: "Now search Andy Johns' perspective"})
        -[:USES_TOOL]-> (ToolCall {tool: "search_by_speaker", duration_ms: 198, status: "success"})
```

This enables the agent to:
- **Find similar past queries** and reuse successful strategies
- **Track tool performance** (success rates, latency)
- **Improve over time** by learning which tool sequences work best

### Agent Tool Suite (19 Tools)

The PydanticAI agent has access to 19 specialized tools organized into categories:

#### Podcast Content Search

| Tool | Description |
|------|-------------|
| `search_podcast_content` | Semantic search across all transcripts with similarity scoring |
| `search_by_speaker` | Find what a specific person said (e.g., "What did Brian Chesky say about growth?") |
| `search_by_episode` | Search within a specific guest's episode |
| `get_episode_list` | List all available episodes with guest names |
| `get_speaker_list` | Get unique speakers with appearance counts |
| `get_memory_stats` | Total counts of conversations, messages, entities |

#### Entity Knowledge Graph

| Tool | Description |
|------|-------------|
| `search_entities` | Find people, companies, and concepts with type filtering |
| `get_entity_context` | Full entity details including Wikipedia enrichment and podcast mentions |
| `find_related_entities` | Discover co-occurring entities through the knowledge graph |
| `get_most_mentioned_entities` | Top entities by mention count, filterable by type |

#### Geospatial Analysis

| Tool | Description |
|------|-------------|
| `search_locations` | Find locations mentioned in podcasts with coordinates |
| `find_locations_near` | Radius-based geospatial query using haversine distance |
| `get_episode_locations` | Geographic profile of a specific episode |
| `find_location_path` | Shortest path between two locations through the knowledge graph |
| `get_location_clusters` | Group locations by country for heatmap visualization |
| `calculate_location_distances` | Pairwise distances between multiple locations |

#### Personalization and Learning

| Tool | Description |
|------|-------------|
| `get_user_preferences` | Retrieve stored user preferences for response tailoring |
| `find_similar_past_queries` | Find successful reasoning traces for similar tasks |

### Dynamic System Prompt with Memory Context

The agent's system prompt is dynamically constructed before each response, injecting relevant memory:

```python
@agent.system_prompt
async def add_memory_context(ctx: RunContext[AgentDeps]) -> str:
    parts = []
    
    # 1. User preferences from long-term memory
    preferences = await memory.long_term.search_preferences(...)
    if preferences:
        parts.append("## User Preferences\n" + format_preferences(preferences))
    
    # 2. Similar past reasoning traces from reasoning memory
    similar_traces = await memory.reasoning.get_similar_traces(current_task)
    if similar_traces:
        parts.append("## Relevant Past Interactions\n" + format_traces(similar_traces))
    
    return "\n\n".join(parts)
```

This means the agent:
- **Adapts its response format** based on learned user preferences (bullet points vs. detailed analysis)
- **Reuses successful strategies** from past interactions with similar queries
- **Personalizes content** based on the user's stated interests

### Automatic Preference Learning

The chat endpoint automatically detects and stores user preferences from natural conversation:

```python
PREFERENCE_INDICATORS = [
    "i prefer", "i like", "i want", "i love", "i enjoy",
    "please always", "please don't", "can you always",
    "i'm interested in", "i care about",
]
```

When a user says "I prefer detailed answers with direct quotes," this is automatically categorized and stored as a preference that influences future responses.

### Entity Extraction Pipeline

The system uses a three-stage extraction pipeline for optimal accuracy and cost:

```
Podcast Transcript Text
         │
         ▼
┌─────────────────┐
│   Stage 1:      │   Fast, free, good for common entities
│   spaCy NER     │   PERSON, ORG, GPE, DATE
│   (statistical) │   ~5ms per segment
└────────┬────────┘
         ▼
┌─────────────────┐
│   Stage 2:      │   Zero-shot, domain-flexible with descriptions
│   GLiNER2       │   Custom entity types + POLE+O categories
│   (transformer) │   ~50ms per segment
└────────┬────────┘
         ▼
┌─────────────────┐
│   Stage 3:      │   Highest accuracy, context-aware
│   LLM Fallback  │   Complex cases, relationship extraction
│   (GPT-4o-mini) │   ~500ms per segment
└────────┬────────┘
         ▼
┌─────────────────┐
│   Merge by      │   Keep highest confidence version of each entity
│   Confidence    │   Filter stopwords (pronouns, articles, numbers)
└────────┬────────┘
         ▼
    Neo4j Storage
    (with POLE+O type labels)
```

The **podcast domain schema** for GLiNER2 is optimized for this content:

| Entity Type | Description | Examples |
|-------------|-------------|----------|
| person | Hosts, guests, people discussed | Brian Chesky, Lenny Rachitsky |
| company | Startups, businesses, organizations | Airbnb, Stripe, Y Combinator |
| product | Products, services, apps, tools | Figma, Notion, Slack |
| concept | Methodologies, frameworks, strategies | Product-market fit, North Star metric |
| book | Books and publications | "The Hard Thing About Hard Things" |
| technology | Platforms and technical tools | React, Kubernetes, GPT-4 |
| role | Job titles and positions | CPO, VP of Growth, PM |
| metric | Business KPIs | DAU, NPS, Retention rate |

### Entity Enrichment with Wikipedia

Entities can be enriched with data from Wikipedia/Wikimedia, adding descriptions, images, and external links. Enrichment is a post-processing step that runs after data loading.

#### Running Enrichment

```bash
# Enrich all unenriched entities
make enrich

# Check current enrichment status
make enrich-status

# Advanced options via the script directly:
cd backend && uv run python ../scripts/enrich_entities.py --help
```

**Script options:**
- `--types PERSON ORGANIZATION` - Enrich only specific entity types
- `--limit 100` - Limit number of entities to process
- `--rate-limit 1.0` - Seconds between API calls (default: 0.5 = 2 req/sec)
- `--dry-run` - Preview what would be enriched without making changes
- `--status` - Show current enrichment progress and exit

**Progress display:**
```
Progress [████████████████░░░░░░░░░░░░░░] 156/400 (39%) ETA: 2m 15s | ✓142 ✗8 !6 | Brian Chesky
```

#### Enrichment Data

Enriched entities receive the following properties:

```cypher
(:Entity:Person {
    name: "Brian Chesky",
    enriched_description: "American businessman and industrial designer...",
    wikipedia_url: "https://en.wikipedia.org/wiki/Brian_Chesky",
    image_url: "https://upload.wikimedia.org/...",
    wikidata_id: "Q4429008",
    enriched_at: datetime(),
    enrichment_provider: "wikimedia"
})
```

Entities not found in Wikipedia are marked with `enrichment_error` to avoid repeated lookups.

#### EntityCard Display

When the agent returns entity data via `get_entity_context` or similar tools, enriched entities are displayed as rich **EntityCard** components in the chat:

**Compact view (inline in chat):**
- Entity image thumbnail (100x100px)
- Name with type badge (Person/Organization/Location/etc.)
- Description preview (3 lines)
- Quick stats: mention count, related entities
- Wikipedia link

**Expanded view (fullscreen dialog):**
- Large image display
- Quick Facts panel (type, subtype, mentions, Wikidata ID)
- Full description
- Related entities badges
- Podcast mentions with speaker and episode info

Enrichment data is also surfaced in:
- **Memory Context panel**: Entity cards show images, descriptions, and Wikipedia links
- **Graph View**: Node property panel displays enrichment section with image and description
- **Map View**: Location popups include enrichment context

### Relationship Extraction with GLiREL

In addition to extracting entities, the system can extract relationships between entities using GLiREL (GLiNER for Relations). This creates `RELATED_TO` relationships between Entity nodes, capturing semantic connections like "works_at", "founded_by", "lives_in", etc.

#### How It Works

When messages are processed with relationship extraction enabled, the system:

1. **Extracts entities** using the multi-stage pipeline (spaCy + GLiNER2 + LLM)
2. **Runs GLiREL** on the same text to identify relationships between entity pairs
3. **Creates RELATED_TO relationships** in Neo4j with:
   - `relation_type`: The semantic type (e.g., "WORKS_AT", "FOUNDED_BY")
   - `confidence`: GLiREL's confidence score (0.0-1.0)
   - `created_at`: Timestamp of extraction

#### Relationship Types

GLiREL extracts relationships based on the POLE+O ontology:

| Relation Type | Description | Example |
|---------------|-------------|---------|
| `WORKS_AT` | Person employed by organization | Brian Chesky → Airbnb |
| `FOUNDED_BY` | Organization founded by person | Airbnb → Brian Chesky |
| `LIVES_IN` | Person resides in location | Brian Chesky → San Francisco |
| `LOCATED_IN` | Entity located in place | Airbnb → San Francisco |
| `MEMBER_OF` | Person belongs to organization | Person → Y Combinator |
| `SUBSIDIARY_OF` | Organization owned by another | Instagram → Meta |
| `PARTICIPATED_IN` | Person involved in event | Founder → IPO |
| `KNOWS` | Person acquainted with person | Brian Chesky → Joe Gebbia |

#### Backfilling Relationships for Existing Data

If you have an existing database with entities but no `RELATED_TO` relationships (e.g., data loaded before relationship extraction was implemented), you can backfill them:

```bash
# Check current status
make backfill-relationships-status

# Run the backfill
make backfill-relationships
```

**Script options:**

```bash
cd backend && uv run python ../scripts/backfill_relationships.py --help

Options:
  --status              Show current status and exit
  --dry-run             Preview without making changes
  --reprocess           Reprocess all messages (not just pending)
  --limit N             Process only N messages
  --batch-size N        Messages per batch (default: 50)
  --threshold FLOAT     Confidence threshold (default: 0.5)
  --device cpu|cuda|mps Device for GLiREL model
```

**Progress display:**
```
  Progress: 450/1200 (38%) | Relations: 2,847 stored | 12.3 msg/s | ETA: 1m 05s
```

#### Querying Relationships

Once relationships are extracted, you can query them in Neo4j:

```cypher
// Find all relationships between entities
MATCH (e1:Entity)-[r:RELATED_TO]->(e2:Entity)
RETURN e1.name, r.relation_type, e2.name, r.confidence
ORDER BY r.confidence DESC
LIMIT 20

// Find who works at a specific company
MATCH (p:Entity:Person)-[r:RELATED_TO {relation_type: "WORKS_AT"}]->(o:Entity:Organization)
WHERE o.name = "Airbnb"
RETURN p.name, r.confidence

// Find all relationships for a person
MATCH (p:Entity {name: "Brian Chesky"})-[r:RELATED_TO]-(other:Entity)
RETURN p.name, r.relation_type, other.name, other.type
```

### SSE Streaming Architecture

The chat endpoint uses Server-Sent Events for real-time streaming:

```
Client                    Server                    Agent
  │                         │                         │
  │ POST /api/chat          │                         │
  │ ──────────────────────> │                         │
  │                         │ Start reasoning trace   │
  │                         │ ───────────────────────>│
  │                         │                         │
  │ SSE: {"type":"token"}   │ Token stream            │
  │ <────────────────────── │ <───────────────────────│
  │ SSE: {"type":"token"}   │                         │
  │ <────────────────────── │                         │
  │                         │                         │
  │ SSE: {"type":"tool_call"}│ Tool invocation         │
  │ <────────────────────── │ <───────────────────────│
  │                         │                         │
  │ SSE: {"type":"tool_result"}│ Tool result           │
  │ <────────────────────── │ ───────────────────────>│
  │                         │                         │
  │ SSE: {"type":"token"}   │ More tokens             │
  │ <────────────────────── │ <───────────────────────│
  │                         │                         │
  │ SSE: {"type":"done"}    │ Complete trace           │
  │ <────────────────────── │ ───────────────────────>│
```

Event types:
- `token` -- Streamed text content as the agent generates its response
- `tool_call` -- Agent invoked a tool (name, arguments)
- `tool_result` -- Tool execution result with timing data
- `done` -- Response complete (includes message ID and trace ID)
- `error` -- Error occurred during processing

### Frontend Visualization Features

#### Interactive Graph View (NVL)

The graph visualization is powered by the Neo4j Visualization Library:

- **Conversation-scoped**: Shows only nodes and relationships relevant to the current thread
- **Episode data integration**: Automatically includes full conversations and entities from podcast episodes referenced in tool call results (extracts `session_id`, `episode`, `episode_guest`, and `guest` fields)
- **Reasoning memory visualization**: Displays ReasoningTrace → ReasoningStep → ToolCall → Tool relationships for the current session
- **Color-coded nodes**: Messages (blue), Entities (green/orange/red by type), Preferences (purple), Traces (gray)
- **Double-click to expand**: Click any node to fetch and display its neighbors from the graph
- **Memory type filtering**: Toggle visibility of short-term, long-term, and reasoning memory nodes
- **Property panel**: Click a node to see all its properties, including Wikipedia enrichment data with images

#### Interactive Map View (Leaflet)

The map visualization supports advanced geospatial exploration:

- **Three view modes**: Individual markers, marker clusters, and heatmap
- **Three basemaps**: OpenStreetMap, ESRI Satellite, OpenTopoMap
- **Color-coded markers**: Locations colored by subtype (city, country, region, landmark)
- **Distance measurement**: Click two locations to calculate great-circle distance
- **Shortest path**: Select two locations to find and visualize the graph path between them
- **Location statistics**: Side panel with counts by type and subtype
- **Conversation-scoped**: Filter to show only locations from the current thread

#### Agent Configuration Panel

A persistent side panel (or bottom sheet on mobile) showing static agent configuration:

- **Available Tools**: All 19 agent tools organized by category (Podcast Search, Entity Queries, Location Analysis, Memory & Preferences)
- **Agent Capabilities**: Descriptions of multi-step reasoning, conversation memory, preference learning, and knowledge graph capabilities
- **Tool Call Cards**: Documentation of all 7 visualization card types (MapCard, DataCard, StatsCard, EntityCard, GraphCard, MemoryGraphCard, RawJsonCard) with descriptions and the tools that trigger each card type

This panel is static (no API calls) and serves as a reference for understanding the agent's capabilities.

---

## Example Questions

Here are questions that showcase different capabilities:

### Semantic Search
- "What did Brian Chesky say about product management?"
- "Find discussions about growth strategies"
- "What advice did guests give about career transitions?"

### Entity Knowledge Graph
- "Who are the most frequently mentioned people across all episodes?"
- "What companies are related to Airbnb in the knowledge graph?"
- "Tell me about Y Combinator -- what do guests say about it?"

### Geospatial Analysis
- "What locations are mentioned in the Brian Chesky episode?"
- "Find all cities mentioned within 100km of San Francisco"
- "Which countries are discussed most frequently?"

### Cross-Reference and Comparison
- "Compare what Brian Chesky and Andy Johns said about growth"
- "What topics do Melissa Perri and Marty Cagan agree on?"
- "Find episodes that mention both startups and mental health"

### Personalization
- "I prefer detailed answers with direct quotes from guests"
- "I'm interested in B2B SaaS topics"
- (The agent remembers these preferences for future responses)

---

## API Reference

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat` | SSE streaming chat with the AI agent |

### Threads

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/threads` | List conversation threads |
| POST | `/api/threads` | Create a new thread |
| GET | `/api/threads/{id}` | Get thread with messages |
| PATCH | `/api/threads/{id}` | Update thread title |
| DELETE | `/api/threads/{id}` | Delete a thread |

### Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memory/context` | Get thread-scoped entities, preferences, recent messages |
| GET | `/api/memory/graph` | Export memory graph with optional `episode_session_ids` param for podcast data |
| GET | `/api/memory/graph/neighbors/{node_id}` | Get neighbors for incremental graph exploration |
| GET | `/api/memory/traces` | List reasoning traces |
| GET | `/api/memory/traces/{id}` | Get trace with steps and tool calls |
| GET | `/api/memory/similar-traces` | Find similar past reasoning traces |
| GET | `/api/memory/tool-stats` | Tool usage statistics |

### Entities

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/entities` | List entities (type/query filtering) |
| GET | `/api/entities/top` | Most mentioned entities by type |
| GET | `/api/entities/{name}/context` | Entity details with enrichment and mentions |
| GET | `/api/entities/related/{name}` | Related entities via co-occurrence |

### Preferences

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/preferences` | List preferences (category filtering) |
| POST | `/api/preferences` | Add a preference |
| DELETE | `/api/preferences/{id}` | Delete a preference |

### Locations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/locations` | Get locations with optional session filtering |
| GET | `/api/locations/nearby` | Find locations within a radius (lat, lon, radius_km) |
| GET | `/api/locations/bounds` | Find locations in a bounding box |
| GET | `/api/locations/clusters` | Location density by country |
| GET | `/api/locations/path` | Shortest graph path between two locations |

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check with memory connection status |

---

## Data Loading Pipeline

### Loading Transcripts

The `scripts/load_transcripts.py` script processes podcast transcripts with:

- **Concurrent loading**: Parallel transcript processing for throughput
- **Resume capability**: Skip already-loaded transcripts for interrupted loads
- **Real-time progress**: Rich progress bars with ETA
- **Entity extraction**: Multi-stage NER pipeline (spaCy + GLiNER2 + LLM)
- **Retry logic**: Exponential backoff for transient failures
- **Detailed statistics**: Files, turns, speakers, and throughput on completion

```bash
# Usage
python scripts/load_transcripts.py --data-dir data

# Options
--sample N              Load only N transcripts (for testing)
--no-entities           Skip entity extraction (faster loading)
--no-embeddings         Skip embedding generation
--resume                Skip already-loaded transcripts
--dry-run               Preview what would be loaded
--batch-size N          Messages per batch (default: 100)
--concurrency N         Concurrent transcript loaders (default: 3)
--extract-entities-only Extract entities from already loaded sessions
--skip-schema-setup     Skip database schema setup
-v, --verbose           Show detailed progress
```

### Geocoding Locations

The `scripts/geocode_locations.py` script adds coordinates to Location entities:

```bash
# Free geocoding via OpenStreetMap (rate limited: 1 req/sec)
python scripts/geocode_locations.py

# Options
--provider nominatim|google  Geocoding provider (default: nominatim)
--api-key KEY               Google Maps API key
--batch-size N              Batch processing size (default: 50)
--skip-existing             Skip locations with existing coordinates
-v, --verbose               Show detailed progress
```

After geocoding, spatial queries like `find_locations_near` become available to the agent.

---

## Neo4j Graph Schema

The loaded data creates this schema in Neo4j:

### Node Types

| Label | Memory Type | Description |
|-------|-------------|-------------|
| `Conversation` | Short-term | One per podcast episode |
| `Message` | Short-term | Each speaker turn with metadata |
| `Entity` | Long-term | Extracted people, companies, locations, etc. |
| `Preference` | Long-term | User preferences learned from conversation |
| `ReasoningTrace` | Reasoning | Complete trace of an agent task |
| `ReasoningStep` | Reasoning | Individual reasoning step |
| `ToolCall` | Reasoning | Tool invocation with timing |

Entity nodes have additional type labels: `:Person`, `:Organization`, `:Location`, `:Event`, `:Object`.

### Key Relationships

```cypher
// Short-term memory (conversation chain)
(Conversation)-[:FIRST_MESSAGE]->(Message)
(Conversation)-[:HAS_MESSAGE]->(Message)
(Message)-[:NEXT_MESSAGE]->(Message)

// Knowledge graph (entities)
(Message)-[:MENTIONS]->(Entity)
(Entity)-[:EXTRACTED_FROM]->(Message)
(Entity)-[:SAME_AS]->(Entity)     // deduplication
(Entity)-[:RELATED_TO]->(Entity)  // semantic relationships (works_at, founded_by, etc.)

// Reasoning memory (reasoning)
(ReasoningTrace)-[:INITIATED_BY]->(Message)
(ReasoningTrace)-[:HAS_STEP]->(ReasoningStep)
(ReasoningStep)-[:USES_TOOL]->(ToolCall)
```

The `RELATED_TO` relationship includes properties:
- `relation_type`: Semantic type (e.g., "WORKS_AT", "FOUNDED_BY", "LIVES_IN")
- `confidence`: Extraction confidence score (0.0-1.0)
- `created_at`: Timestamp of when the relationship was created

### Example Cypher Queries

```cypher
// Find the most mentioned people across all episodes
MATCH (e:Entity:Person)<-[:MENTIONS]-(m:Message)
RETURN e.name, count(m) AS mentions
ORDER BY mentions DESC LIMIT 20

// Find enriched entities with Wikipedia data
MATCH (e:Entity)
WHERE e.enriched_description IS NOT NULL
RETURN e.name, e.type, e.enriched_description, e.wikipedia_url, e.image_url
LIMIT 10

// Find entities mentioned together (co-occurrence)
MATCH (e1:Entity)<-[:MENTIONS]-(m:Message)-[:MENTIONS]->(e2:Entity)
WHERE e1.name < e2.name
RETURN e1.name, e2.name, count(m) AS co_mentions
ORDER BY co_mentions DESC LIMIT 20

// Geospatial: find locations near San Francisco
MATCH (e:Entity:Location)
WHERE e.location IS NOT NULL
  AND point.distance(e.location, point({latitude: 37.77, longitude: -122.42})) < 50000
RETURN e.name, e.location.latitude, e.location.longitude

// Explore relationships between entities
MATCH (e1:Entity)-[r:RELATED_TO]->(e2:Entity)
WHERE r.confidence > 0.7
RETURN e1.name, r.relation_type, e2.name, r.confidence
ORDER BY r.confidence DESC
LIMIT 20

// Get a conversation's full context
MATCH (c:Conversation {session_id: "lenny-podcast-brian-chesky"})
MATCH (c)-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
RETURN m.content, m.speaker, collect(e.name) AS mentioned_entities
ORDER BY m.created_at
```

---

## Key Architectural Decisions

### Why Neo4j for Agent Memory?

1. **Connected data is first-class**: Entity co-occurrence, conversation chains, and reasoning traces are naturally expressed as graph relationships. A relational database would require complex JOINs; a vector store would miss the relationship structure entirely.

2. **Vector + graph in one database**: Neo4j 5.x supports both vector indexes (for semantic search) and graph traversal (for relationship queries) in a single system. No need to sync between a vector store and a graph store.

3. **Spatial indexing built in**: Neo4j's `Point` type and spatial functions enable geospatial queries (radius search, bounding box) without an external service.

4. **Schema flexibility**: Dynamic node labels (`:Entity:Person:Individual`) allow the POLE+O type system to be expressed directly in the graph schema, enabling efficient type-specific queries.

### Why Three Memory Types?

The three-memory architecture mirrors how human memory works:

- **Short-term** (episodic): What happened in this conversation? What did the user just ask?
- **Long-term** (semantic): What do we know about Brian Chesky? What are the user's preferences?
- **Reasoning** (reasoning): How did we successfully answer "compare two guests" last time?

Each type has different storage patterns, query patterns, and lifecycle requirements. Combining them gives the agent both context and wisdom.

### Why PydanticAI?

PydanticAI provides structured, type-safe agent development with:
- Type-checked tool definitions using Python type hints
- Dependency injection for the memory client
- Built-in support for multi-step reasoning
- Clean separation between agent logic and tools

### Why SSE Over WebSockets?

Server-Sent Events are simpler than WebSockets for this use case:
- Unidirectional streaming (server to client) is all we need
- Works through proxies and load balancers without special configuration
- Built-in reconnection in the browser
- Each chat message is a separate HTTP request, making it stateless

---

## Project Structure

```
lennys-memory/
├── data/                          # Podcast transcript files (299 .txt files)
├── scripts/
│   ├── load_transcripts.py        # Data loading with entity extraction
│   └── geocode_locations.py       # Geocoding for Location entities
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   └── src/
│       ├── main.py                # FastAPI entry point with CORS
│       ├── config.py              # Settings (Neo4j, OpenAI, enrichment)
│       ├── agent/
│       │   ├── agent.py           # PydanticAI agent + system prompt
│       │   ├── dependencies.py    # Agent dependency injection
│       │   └── tools.py           # 19 agent tools
│       ├── api/
│       │   ├── schemas.py         # Pydantic request/response models
│       │   └── routes/
│       │       ├── chat.py        # SSE streaming + preference extraction
│       │       ├── threads.py     # Thread CRUD operations
│       │       └── memory.py      # Memory context, graph, traces, entities,
│       │                          # preferences, locations
│       └── memory/
│           └── client.py          # Memory client singleton
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/                   # Next.js app router
│       │   ├── layout.tsx         # Root layout with fonts
│       │   └── page.tsx           # Main page with WelcomeModal
│       ├── theme/
│       │   └── index.ts           # Neo4j Labs custom theme (v2.0)
│       ├── components/
│       │   ├── ui/
│       │   │   └── provider.tsx   # Chakra provider with custom theme
│       │   ├── chat/
│       │   │   ├── ChatContainer.tsx    # Main chat interface
│       │   │   ├── MessageList.tsx      # Message display
│       │   │   ├── Message.tsx          # Individual message
│       │   │   ├── ToolCallDisplay.tsx  # Tool result card routing (v2.0)
│       │   │   ├── PromptInput.tsx      # Input with suggested prompts
│       │   │   └── cards/               # Tool result cards (v2.0)
│       │   │       ├── index.ts         # Barrel exports
│       │   │       ├── types.ts         # Card type definitions
│       │   │       ├── toolCardRegistry.ts  # Tool-to-card mapping
│       │   │       ├── BaseCard.tsx     # Shared card wrapper
│       │   │       ├── ToolResultCard.tsx   # Smart card selector
│       │   │       ├── MapCard.tsx      # Inline Leaflet map
│       │   │       ├── GraphCard.tsx    # Inline NVL graph
│       │   │       ├── DataCard.tsx     # Table display
│       │   │       ├── StatsCard.tsx    # Metrics grid
│       │   │       └── RawJsonCard.tsx  # JSON fallback
│       │   ├── layout/
│       │   │   ├── AppLayout.tsx        # Responsive layout with Labs branding
│       │   │   ├── Sidebar.tsx          # Thread list
│       │   │   └── Footer.tsx           # Labs footer links (v2.0)
│       │   ├── memory/
│       │   │   ├── MemoryContext.tsx     # Entity cards + preferences panel
│       │   │   ├── MemoryGraphView.tsx  # NVL graph visualization
│       │   │   └── MemoryMapView.tsx    # Leaflet map visualization
│       │   ├── branding/
│       │   │   └── LabsDisclaimer.tsx   # Labs disclaimer alert (v2.0)
│       │   └── onboarding/
│       │       └── WelcomeModal.tsx     # First-time user modal (v2.0)
│       ├── hooks/
│       │   ├── useChat.ts              # SSE streaming hook with AbortController
│       │   ├── useQuickStart.ts        # Quick-start suggestions from previous conversations
│       │   └── useThreads.ts           # Thread management hook (legacy)
│       └── lib/
│           ├── api.ts                  # API client functions
│           └── types.ts                # TypeScript type definitions
├── Makefile                       # All build/run/load commands
├── docker-compose.yml             # Neo4j container configuration
└── README.md
```

---

## What Makes This Different

### vs. Standard RAG

Most RAG applications treat documents as flat chunks in a vector store. This demo builds a **knowledge graph** where entities are connected by co-occurrence, enriched with external knowledge, and queryable by type, relationship, and geography. The agent doesn't just find relevant text -- it understands the structure of the knowledge.

### vs. ChatGPT Memory

ChatGPT's memory is a flat list of facts. neo4j-agent-memory provides **structured, typed memory** with three distinct layers, graph relationships between entities, and reasoning memory that lets the agent learn from its own reasoning patterns.

### vs. LangGraph/MemGPT

These frameworks focus on agent orchestration. neo4j-agent-memory is **specifically designed for memory persistence** with a graph-native data model, entity extraction pipeline, deduplication, enrichment, and spatial queries. It complements orchestration frameworks rather than competing with them.

---

## Cloud Deployment

This application can be deployed to Railway (backend) and Vercel (frontend).

### Prerequisites

- [Neo4j Aura](https://neo4j.com/cloud/aura/) account (free tier available)
- [Railway](https://railway.app/) account
- [Vercel](https://vercel.com/) account
- OpenAI API key

### Backend Deployment (Railway)

#### 1. Create a Neo4j Aura Instance

1. Go to [Neo4j Aura](https://console.neo4j.io/)
2. Create a new database (free tier works)
3. Save the connection URI and password

#### 2. Deploy Backend to Railway

1. Fork/clone this repository to your GitHub account
2. Go to [Railway](https://railway.app/) and create a new project
3. Select "Deploy from GitHub repo"
4. Select your forked repository
5. **Important**: Set the **Root Directory** to:
   ```
   neo4j-agent-memory/examples/lennys-memory/backend
   ```

#### 3. Configure Environment Variables in Railway

Add these environment variables in Railway's project settings:

| Variable | Description | Example |
|----------|-------------|---------|
| `NEO4J_URI` | Neo4j Aura connection URI | `neo4j+s://xxxxxxxx.databases.neo4j.io` |
| `NEO4J_USERNAME` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | `your-password` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `CORS_ORIGINS` | Frontend URL(s), comma-separated | `https://your-app.vercel.app` |
| `CORS_ORIGIN_REGEX` | Regex for preview deployments | `https://.*\.vercel\.app` |
| `DEBUG` | Disable debug mode in production | `false` |

#### 4. Verify Deployment

After deployment, test the health endpoint:
```bash
curl https://your-app.up.railway.app/health
```

Expected response:
```json
{"status": "healthy", "memory_connected": true}
```

### Frontend Deployment (Vercel)

#### 1. Deploy to Vercel

1. Go to [Vercel](https://vercel.com/) and create a new project
2. Import your GitHub repository
3. Set the **Root Directory** to:
   ```
   neo4j-agent-memory/examples/lennys-memory/frontend
   ```
4. Framework preset should auto-detect as "Next.js"

#### 2. Configure Environment Variables in Vercel

Add this environment variable in Vercel's project settings:

| Variable | Description | Example |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Railway backend URL **with /api suffix** | `https://your-app.up.railway.app/api` |

**Important**: The URL must include `/api` at the end.

#### 3. Redeploy

After adding environment variables, trigger a new deployment in Vercel.

### Troubleshooting

#### CORS Errors in Browser Console

If you see `Access-Control-Allow-Origin` errors:

1. **Backend not running**: Check Railway deploy logs for startup errors
2. **Missing CORS config**: Ensure `CORS_ORIGINS` or `CORS_ORIGIN_REGEX` is set in Railway
3. **Wrong URL**: Verify `NEXT_PUBLIC_API_URL` includes the `/api` suffix

#### 502 Bad Gateway from Railway

This means the FastAPI app is not starting. Check Railway deploy logs for:

1. **Missing environment variables**: `NEO4J_URI`, `NEO4J_PASSWORD`, `OPENAI_API_KEY`
2. **Neo4j connection failure**: Verify URI format (`neo4j+s://` for Aura)
3. **Package installation errors**: Check build logs

#### Health Check Returns `memory_connected: false`

The backend started but cannot connect to Neo4j:

1. Verify `NEO4J_URI` format (should be `neo4j+s://...` for Aura)
2. Check `NEO4J_PASSWORD` is correct
3. Ensure the Neo4j Aura instance is running

### Loading Data to Cloud Neo4j

To load podcast data into your Aura instance:

```bash
cd backend

# Set environment variables for your Aura instance
export NEO4J_URI="neo4j+s://xxxxxxxx.databases.neo4j.io"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="your-password"
export OPENAI_API_KEY="sk-..."

# Load sample data (5 transcripts)
python ../scripts/load_transcripts.py --data-dir ../data --sample 5

# Or load full dataset
python ../scripts/load_transcripts.py --data-dir ../data
```

---

## License

This example is part of the [neo4j-agent-memory](https://github.com/neo4j-labs/agent-memory) project, licensed under Apache 2.0.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` v0.1.2 / v0.2-dev on 2026-05-03. During this verification pass, two phantom-method calls in `scripts/load_transcripts.py` (`get_messages` → `get_conversation`) and `backend/src/api/routes/threads.py` (`delete_conversation` → `clear_session`) were corrected. Full UI/end-to-end smoke not re-run; library-side wiring is current._
