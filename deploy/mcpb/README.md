# Neo4j Agent Memory - MCP Desktop Extension

MCP server extension for Claude Desktop that provides persistent graph memory backed by Neo4j.

## Features

- **Three memory types**: Short-term (conversations), Long-term (entities, preferences, facts), Reasoning (traces)
- **POLE+O entity extraction**: Automatic extraction of Person, Object, Location, Event, Organization entities
- **Knowledge graph**: Entities linked by typed relationships in Neo4j
- **Automatic preference detection**: Learns user preferences from natural conversation
- **Observational memory**: Context compression for long conversations
- **Two tool profiles**: Core (6 tools) or Extended (16 tools)

## Requirements

- A dedicated [AuraDB instance and its connection credentials](../../examples/AURA_SETUP.md)
- Python 3.10+ (for `uvx` runtime)
- An OpenAI API key for the default embedding provider

## Quick Start

1. Install from Claude Desktop extension directory
2. Configure `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` and `OPENAI_API_KEY` in the extension environment. Copy the Aura setup's username into `NEO4J_USER`.
3. Request a storage tool call and verify its record in Aura Query. The [maintained Claude Desktop tutorial](../../docs/modules/ROOT/pages/tutorials/mcp-server.adoc) gives the complete storage/readback flow.

## Alternative Installation (Developer Path)

Add to your Claude Desktop configuration (`claude_desktop_config.json`). Replace the literal placeholders with the Aura credentials; Desktop does not inherit terminal exports. Keep the populated configuration private:

```json
{
  "mcpServers": {
    "neo4j-agent-memory": {
      "command": "uvx",
      "args": [
        "neo4j-agent-memory[mcp,openai]",
        "mcp",
        "serve",
        "--backend", "bolt"
      ],
      "env": {
        "NEO4J_URI": "neo4j+s://<instance-id>.databases.neo4j.io",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "replace-with-your-Aura-password",
        "NEO4J_DATABASE": "neo4j",
        "OPENAI_API_KEY": "replace-with-your-OpenAI-key"
      }
    }
  }
}
```

## Tool Profiles

### Core (6 tools)
| Tool | Description |
|------|-------------|
| `memory_search` | Hybrid vector + graph search across all memory types |
| `memory_get_context` | Assembled context for a session |
| `memory_store_message` | Store message with auto entity extraction |
| `memory_add_entity` | Create/update entity with POLE+O typing |
| `memory_add_preference` | Record user preference |
| `memory_add_fact` | Store subject-predicate-object triple |

### Extended (+10 tools)
| Tool | Description |
|------|-------------|
| `memory_get_conversation` | Full conversation history |
| `memory_list_sessions` | Browse stored sessions |
| `memory_get_entity` | Entity details with graph relationships |
| `memory_export_graph` | Subgraph export for visualization |
| `memory_create_relationship` | Link entities together |
| `memory_start_trace` | Begin reasoning trace |
| `memory_record_step` | Record reasoning step |
| `memory_complete_trace` | Complete reasoning trace |
| `memory_get_observations` | Session observations and insights |
| `graph_query` | Read-only Cypher queries |

## Links

- [Documentation](https://neo4j.com/labs/agent-memory)
- [GitHub](https://github.com/neo4j-labs/agent-memory)
- [PyPI](https://pypi.org/project/neo4j-agent-memory/)

---
Neo4j Labs Project - Community Supported
