# MCP Server Quick Start

## Quick Start

```bash
git clone https://github.com/neo4j-labs/agent-memory.git
cd agent-memory
uv sync --extra mcp
make neo4j-start && make neo4j-wait
export OPENAI_API_KEY=sk-...
uv run python -m neo4j_agent_memory.mcp.server \
  --transport http --port 5000 --neo4j-password test-password
```

> **Note:** `OPENAI_API_KEY` is required for embedding-based search (`memory_search`, `memory_store`). You can also pass it inline:
>
> ```bash
> OPENAI_API_KEY=sk-... uv run python -m neo4j_agent_memory.mcp.server \
>   --transport http --port 5000 --neo4j-password test-password
> ```
>
> Or use the `--openai-api-key` flag instead of the environment variable.

Server runs at `http://localhost:5000/mcp`. All CORS origins allowed by default.

## Transports

### HTTP (recommended)

```bash
uv run python -m neo4j_agent_memory.mcp.server \
  --transport http --port 5000 --neo4j-password test-password
```

Endpoint: `http://localhost:5000/mcp` — POST JSON-RPC, get JSON back.

**Browser-based clients** require `--stateless` to disable session ID tracking:

```bash
uv run python -m neo4j_agent_memory.mcp.server \
  --transport http --port 5000 --neo4j-password test-password --stateless
```

### SSE

```bash
uv run python -m neo4j_agent_memory.mcp.server \
  --transport sse --port 5000 --neo4j-password test-password
```

Endpoint: `http://localhost:5000/sse`, messages to `/messages`.

### stdio (Claude Desktop)

```bash
uv run python -m neo4j_agent_memory.mcp.server --neo4j-password test-password
```

Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "neo4j-memory": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/agent-memory",
        "run",
        "python",
        "-m",
        "neo4j_agent_memory.mcp.server",
        "--neo4j-password",
        "test-password"
      ]
    }
  }
}
```

## CORS

All origins allowed by default. To restrict:

```bash
uv run python -m neo4j_agent_memory.mcp.server \
  --transport http --port 5000 --neo4j-password test-password \
  --allow-origin https://app.example.com \
  --allow-origin https://admin.example.com
```

## Tools

| Tool                   | Description                        |
| ---------------------- | ---------------------------------- |
| `memory_search`        | Hybrid vector + graph search       |
| `memory_store`         | Store messages, facts, preferences |
| `entity_lookup`        | Get entity with relationships      |
| `conversation_history` | Get session history                |
| `graph_query`          | Read-only Cypher queries           |

## All flags

| Flag               | Default                 | Description                                                |
| ------------------ | ----------------------- | ---------------------------------------------------------- |
| `--transport`      | `stdio`                 | `stdio`, `sse`, or `http`                                  |
| `--port`           | `8080`                  | Port for HTTP transports                                   |
| `--host`           | `127.0.0.1`             | Bind address (`0.0.0.0` for external)                      |
| `--neo4j-uri`      | `bolt://localhost:7687` | Neo4j URI                                                  |
| `--neo4j-user`     | `neo4j`                 | Neo4j username                                             |
| `--neo4j-password` | _(required)_            | Neo4j password                                             |
| `--neo4j-database` | `neo4j`                 | Neo4j database                                             |
| `--allow-origin`   | `*`                     | CORS origin (repeatable)                                   |
| `--stateless`      | `false`                 | Disable session ID tracking (required for browser clients) |
| `--openai-api-key` | `$OPENAI_API_KEY`       | OpenAI API key                                             |

## Troubleshooting

| Error                       | Fix                                   |
| --------------------------- | ------------------------------------- |
| `No module named 'fastmcp'` | `uv sync --extra mcp`                 |
| Neo4j connection refused    | `make neo4j-start && make neo4j-wait` |
| Browser client: no tools    | Use `--transport http --stateless`    |
