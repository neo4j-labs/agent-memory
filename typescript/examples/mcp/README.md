# Self-hosted MCP server

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs, backward-compatibility guarantees,
> or scheduled deprecation commitments. APIs may change without notice. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

An MCP server you run yourself, backed by the
[Neo4j Agent Memory Service (NAMS)](https://memory.neo4jlabs.com). It exposes
the SDK's 12 memory tools with Zod-validated inputs and read/write annotations,
plus a 13th tool of its own — a read-only Cypher console — behind an allow-list
and a per-call audit log. Speaks **stdio** (Claude Desktop, IDE plugins) or
**Streamable HTTP**.

## Do you need this? Probably not

The hosted **NAMS MCP server** at `https://mcp.memory.neo4jlabs.com/mcp`
exposes a much larger, scope-dependent surface spanning memory, entity review,
ontology, workspace administration and Skills — an authenticated `tools/list`
returns only the tools your key or token's scopes permit — and it supports
OAuth 2.0 with Dynamic Client Registration, so an interactive client can
connect with no long-lived key in a config file. Adding it as a remote MCP server in your client is **zero code**:
no process to run, no key to store on disk, nothing to keep up to date.

See [the hosted NAMS MCP reference](https://neo4j.com/labs/agent-memory/reference/nams-mcp)
for the tool groups, scopes and OAuth endpoints.

Self-host — this example — when you need something the hosted host cannot give
you:

- **Audit every call.** One stderr line per tool call: name, argument *keys*,
  duration, outcome.
- **Expose a subset.** `MCP_TOOLS=memory_get_context,memory_search_entities`
  and nothing else is even advertised in `tools/list`.
- **Add your own tools.** `memory_cypher` here runs read-only Cypher against the
  memory graph via `client.query.cypher` — graph access the standard surface
  deliberately leaves out.
- **Reach it from a closed network.** Run the process inside a boundary that
  cannot talk to `memory.neo4jlabs.com`, and let it hold the only outbound path.

## Prerequisites

- Node.js 22 or later
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com) —
  use a **workspace-scoped data-plane key**, not an admin key
- An MCP client: Claude Desktop, an MCP-aware IDE, or your own

## Build the shared SDK first

This is a source-checkout example. Its `file:../..` dependency and shared
`../tsconfig.base.json` require the repository layout. From the repository root:

```bash
cd typescript
npm ci
npm run build
cd examples/mcp
```

Run the commands below from `typescript/examples/mcp/`. Build **before**
installing this example; package exports point at `typescript/dist/` and npm does
not build the local SDK on installation. For standalone copies, follow the
[copy checklist](../README.md#copying-an-example) and verify the selected npm
artifact supplies every API used here.

## Run it

```bash
if [ ! -e .env ]; then
  (umask 077; set -C; cat .env.example > .env)
fi
chmod 600 .env
npm ci
```

Edit the private `.env` with `MEMORY_API_KEY` before running `npm start`.
The commands preserve an existing file.

```bash
npm start                  # waits on stdio for an MCP client
```

For the separate step-by-step
[Claude Desktop tutorial](../../../docs/modules/ROOT/pages/tutorials/mcp-server-typescript.adoc),
follow its standalone `my-memory-mcp/` setup and `.env.tutorial.example`
template. Its selected tools and resource-accounting helpers differ from this
general server example.

Expected output on **stderr** (stdout is the protocol channel and stays clean):

```
[mcp] neo4j-agent-memory-mcp ready on stdio with 13 tools: memory_create_conversation,
memory_add_messages, memory_get_context, memory_search_messages,
memory_search_entities, memory_get_entity, memory_add_entity,
memory_get_entity_history, memory_record_step, memory_record_tool_call,
memory_get_trace, memory_explain_decision, memory_cypher
```

Then one line per call:

```
[mcp] memory_search_entities keys=[query,limit] 182ms ok
```

With no key set, the process exits 1 with a message on stderr and writes
nothing to stdout.

### Streamable HTTP instead of stdio

```bash
MCP_TRANSPORT=http MCP_PORT=3000 npm start
# → [mcp] POST/GET/DELETE http://localhost:3000/mcp
```

The transport does **not** authenticate callers. Put your own auth in front of
the route before exposing it beyond localhost. Ctrl+C closes the HTTP listener,
the MCP session and the memory client.

### Environment

| Variable | Default | What it does |
|---|---|---|
| `MEMORY_API_KEY` | *(required)* | NAMS data-plane key |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_PORT` | `3000` | HTTP port when `MCP_TRANSPORT=http` |
| `MCP_TOOLS` | *(all)* | Comma-separated allow-list |
| `MCP_ENABLE_CYPHER` | on | `0` drops `memory_cypher` |

## Wire it to Claude Desktop

Compile first, so no loader is involved at launch:

```bash
npm run build   # → dist/index.js
```

Then add to `claude_desktop_config.json` — macOS
`~/Library/Application Support/Claude/claude_desktop_config.json`, Windows
`%APPDATA%\Claude\claude_desktop_config.json`, Linux
`~/.config/Claude/claude_desktop_config.json`:

```jsonc
{
  "mcpServers": {
    "agent-memory": {
      "command": "node",
      "args": ["/absolute/path/to/typescript/examples/mcp/dist/index.js"],
      "cwd": "/absolute/path/to/typescript/examples/mcp",
      "env": { "MEMORY_API_KEY": "nams_..." }
    }
  }
}
```

Use absolute paths — Claude Desktop launches the process from its own working
directory. Restart Desktop, then check the tools indicator near the message
input: you should see 13 tools under `agent-memory`.

If it does not appear, read the MCP log: **Settings → Developer → Open MCP
Logs** (on macOS, `~/Library/Logs/Claude/mcp-server-agent-memory.log`). The
usual causes are a wrong absolute path, a missing `MEMORY_API_KEY`, or `node`
not being on the PATH Desktop sees — in which case use the absolute path to the
Node binary as `command`.

Prefer not to compile? `"command": "npx"`, `"args": ["-y", "tsx",
"/absolute/path/to/.../src/index.ts"]` with the same `cwd` works too, but adds a
loader and a network-capable `npx` to the launch path.

### A note on the key in that file

`claude_desktop_config.json` is **plaintext**, and its `env` block is the only
mechanism Desktop offers a self-hosted stdio server. So:

- use a workspace-scoped data-plane key, never an admin key;
- if the file leaks, have the key owner rotate or revoke that exact key through
  their authenticated management interface, replace Desktop's private value,
  and verify the old key is rejected; API management requires the `keyId` and
  an owner-user or administrator credential, which must stay out of Desktop
  ([key lifecycle](../../../docs/modules/ROOT/pages/reference/authentication.adoc#_key_lifecycle));
- if you want OAuth instead of a long-lived key, use the hosted MCP host — it
  supports it and this pattern cannot.

## How it works

| File | What it owns |
|---|---|
| `src/server.ts` | The `McpServer`: which tools to expose, what to log, the `memory_cypher` tool. No transport, no `process.exit`. |
| `src/index.ts` | Process concerns: env, transport selection, graceful shutdown. |
| `test/server.test.ts` | The server linked to a real MCP `Client` in-process, with a fake memory transport. |

The SDK does the memory half. `registerMemoryTools` from
`@neo4j-labs/agent-memory/mcp/register` carries the Zod input schemas, the
read/write annotations, the dispatch to `MemoryClient`, and the `isError: true`
convention on failure — so this example is ~100 lines of policy rather than a
reimplementation. Swap `registerMemoryTools` for `createMemoryTools()` +
`handleMemoryToolCall()` from `@neo4j-labs/agent-memory/mcp` if you are on the
low-level `Server` API and want JSON Schema instead.

## Test it

```bash
npm test        # 10 tests, no API key and no network
npm run typecheck
```

The tests link a real MCP `Client` to the server over
`InMemoryTransport.createLinkedPair()` and back `MemoryClient` with a fake
`Transport`, asserting the 13 tool names, the annotation split, a successful
round trip, `isError: true` on backend failure, Zod rejection of malformed
arguments before dispatch, the allow-list, and that audit records carry
argument keys but never values.

`test/docs-assembly.test.ts` assembles the
[tutorial's](https://neo4j.com/labs/agent-memory/tutorials/mcp-server-typescript)
server from the page in a temporary project, compiles it and drives it over
stdio against an offline REST fixture. By default the SDK is packed and
unpacked as a registry install lays it out. To run the page's own `npm install`
of the pinned published release instead (this needs network access):

```bash
DOCS_READER_INSTALL=1 npx vitest run test/docs-assembly.test.ts
```

## See also

- [How-to: use the MCP tools](https://neo4j.com/labs/agent-memory/how-to/typescript/mcp)
- [Tutorial: connect Claude Desktop to your memory](https://neo4j.com/labs/agent-memory/tutorials/mcp-server-typescript)
- [Reference: the hosted NAMS MCP server](https://neo4j.com/labs/agent-memory/reference/nams-mcp)
- [Model Context Protocol spec](https://modelcontextprotocol.io)

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [Documentation](https://neo4j.com/labs/agent-memory)

## License

Apache 2.0 — see the repository root.

---

_Compatibility scope: this example targets the current source checkout and its committed package/lock files. Offline tests validate the exercised contracts; they do not establish published-package availability, a live model result, or deployed NAMS behavior. Use the runtime floor above; record the actual package/runtime versions when verifying a release or deployment._
