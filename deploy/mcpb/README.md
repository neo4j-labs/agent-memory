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
- [uv](https://docs.astral.sh/uv/) on the `PATH` that Claude Desktop sees. The extension starts the server with `uvx`, which installs `neo4j-agent-memory[mcp,openai]==0.7.0` from PyPI (Python 3.10+).
- An OpenAI API key for the default embedding provider
- Node.js, to pack the bundle with the [MCPB CLI](https://github.com/anthropics/mcpb)

## Quick start

1. From the repository root, pack this directory into a bundle:

   ```bash
   npx -y @anthropic-ai/mcpb@2.1.2 pack deploy/mcpb dist/neo4j-agent-memory.mcpb
   ```

   `pack` validates `manifest.json` before it writes the file. It creates `dist/` if needed, and git ignores that directory. The [team memory example](../../examples/claude-code-team-memory/) wraps the same manifest in `bundle/build.sh`.
2. In Claude Desktop, open **Settings → Extensions**, choose **Install from file** and select `dist/neo4j-agent-memory.mcpb`.
3. Fill in the fields Desktop shows for the extension: Neo4j URI, username, password, database and OpenAI API key. Copy the Aura setup's `NEO4J_USERNAME` value into the username field. Desktop stores the password and API key as sensitive values and passes all five to the server as `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` and `OPENAI_API_KEY`.
4. Request a storage tool call and verify its record in Aura Query. The [maintained Claude Desktop tutorial](../../docs/modules/ROOT/pages/tutorials/mcp-server.adoc) gives the complete storage/readback flow.

## Alternative installation (developer path)

Add to your Claude Desktop configuration (`claude_desktop_config.json`). Replace the literal placeholders with the Aura credentials; Desktop does not inherit terminal exports. Keep the populated configuration private:

```json
{
  "mcpServers": {
    "neo4j-agent-memory": {
      "command": "uvx",
      "args": [
        "neo4j-agent-memory[mcp,openai]==0.7.0",
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

## Tool profiles

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
