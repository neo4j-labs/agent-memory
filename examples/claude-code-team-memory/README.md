# Team Memory in Your Editor

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Shared, queryable memory for Claude Code, Claude Desktop and Cursor — with **no agent code at all**. The only Python here provisions keys, seeds the workspace and diagnoses the wiring.

Four people, three editors, one memory graph. Alice's session recalls what Bob's
session recorded, because both editors are MCP clients of the same workspace.
Every other example in this repository shares memory across *sessions of one
process*; this one shares it across *people*.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a Neo4j Labs project. It is actively maintained but not officially supported. APIs may change. Community support is available via the [Neo4j Community Forum](https://community.neo4j.com).

## Two servers, two tool surfaces — said once, here

There are two different MCP servers in this example and they are **not** the same
surface. Getting this wrong is the single most common confusion:

| | Hosted NAMS MCP server | Self-hosted `neo4j-agent-memory mcp serve` |
|---|---|---|
| Who runs it | Neo4j, at `https://mcp.memory.neo4jlabs.com/mcp` | You, as a local process |
| Backend | Your NAMS workspace | Your own Neo4j (or NAMS, with `--backend nams`) |
| Auth | OAuth 2.0 (+ PKCE, dynamic client registration) or a `nams_…` Bearer key | `NEO4J_PASSWORD`, or `MEMORY_API_KEY` for `--backend nams` |
| Tools | **47**, scope-gated — `tools/list` returns only what your key's scopes permit, so a workspace key does not see the `workspace_*` admin tools | **6** (`--profile core`) or **16** (`--profile extended`) |
| Setup cost | Paste a URL | Install the `[mcp]` extra, run a process |

Both are wired below, side by side, in every config file. Delete the entry you
don't want. `doctor.py` prints both surfaces from the real registrar so the
numbers in this table cannot quietly drift.

## What's here

| File | Purpose |
|---|---|
| `.mcp.json.example` | Claude Code, project scope — hosted (OAuth) + self-hosted side by side |
| `claude_desktop_config.json.example` | Claude Desktop — hosted via `mcp-remote`, plus the self-hosted stdio server |
| `cursor_mcp.json.example` | Cursor, `.cursor/mcp.json` — same two entries |
| `bundle/build.sh` | Packages `deploy/mcpb/manifest.json` as a double-clickable `.mcpb` Desktop extension |
| `provision_keys.py` | One scoped, rotatable NAMS key per developer (`client.auth.*`) |
| `seed_workspace.py` | 15 synthetic architecture decisions → `bulk_add_messages` → await extraction → read back |
| `doctor.py` | Five checks, in failure-frequency order, before you open any editor |
| [`WALKTHROUGH.md`](WALKTHROUGH.md) | The quit-and-reopen persistence demo, and the `core` vs `extended` contrast |

## Prerequisites

- Python 3.10+
- A NAMS API key from <https://memory.neo4jlabs.com> (starts with `nams_`)
- One of: Claude Code, Claude Desktop, Cursor
- Only for the self-hosted half: a Neo4j 5.x instance you already run, plus `uvx`
  (ships with [uv](https://docs.astral.sh/uv/))

No `OPENAI_API_KEY`: extraction and embeddings run server-side on NAMS.

## Setup

```bash
uv pip install -r requirements.txt
cp .env.example .env
# edit .env: set MEMORY_API_KEY
```

The scripts load this directory's `.env` themselves, so exporting is optional.
`MEMORY_WORKSPACE_ID` is **required on header-scoped deployments** (the
development/staging service) — without it they answer `403` with no further hint.
Leave it unset on a production key, which is already bound to one workspace.

## Run

```bash
# 1. Check everything that does not need the network (key shape, config JSON,
#    the real tool counts for both profiles).
uv run python doctor.py --configs-only

# 2. Mint one key per teammate. Dry-run by default; --confirm actually calls NAMS.
#    Key management needs an ADMIN key — a workspace data key cannot mint keys.
MEMORY_API_KEY="$NAMS_ADMIN_KEY" uv run python provision_keys.py list
MEMORY_API_KEY="$NAMS_ADMIN_KEY" uv run python provision_keys.py create --label alice-laptop
MEMORY_API_KEY="$NAMS_ADMIN_KEY" uv run python provision_keys.py create --label alice-laptop --confirm

# 3. Seed the shared workspace with the decisions the editors should recall.
#    MEMORY_API_KEY comes from .env, or pass it inline if you keep several keys:
MEMORY_API_KEY=nams_xxxxxxxxxxxxxxxx uv run python seed_workspace.py

# 4. Full diagnosis, including the live NAMS checks.
export TEAM_MEMORY_CONVERSATION_ID=<printed by step 3>
uv run python doctor.py

# Optional: also parse the configs your editors already have installed. They are
# parsed, never printed, and a literal key there is a warning (Claude Desktop
# cannot expand ${VAR}) rather than a failure.
uv run python doctor.py --check-installed
```

Then wire an editor:

```bash
# Claude Code (project scope — commit it, it holds no secret)
cp .mcp.json.example ../../.mcp.json

# Cursor
mkdir -p ../../.cursor && cp cursor_mcp.json.example ../../.cursor/mcp.json

# Claude Desktop (macOS path; see the file itself for Windows/Linux)
cp claude_desktop_config.json.example \
  ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Restart the editor, then ask it: **"Why did we choose Neo4j 5.26 LTS, and who
decided?"** It answers from the seeded workspace without being told where to
look. [`WALKTHROUGH.md`](WALKTHROUGH.md) takes it from there.

### Secrets and the config files

No config file in this directory contains a key, and the offline test enforces
that: it greps the whole directory for anything matching a `nams_<opaque>` shape
and fails on a pasted credential.

- **Hosted, interactive** (Claude Code, Desktop via `mcp-remote`): OAuth — there
  is nothing to paste.
- **Claude Code, headless**: `.mcp.json` expands `${MEMORY_API_KEY}` and
  `${VAR:-default}` from your environment, which is why the self-hosted entry
  there reads `"NEO4J_PASSWORD": "${NEO4J_PASSWORD}"`.
- **Claude Desktop and Cursor**: treat `REPLACE_WITH_…` as literal. Desktop does
  not inherit your shell environment, so values go in the file — keep that file
  out of git, or prefer the OAuth entry. If your Cursor build does expand
  `${VAR}`, use that instead of pasting.

## Expected output

`doctor.py --configs-only`, on a clean checkout with no key set:

```
neo4j-agent-memory — team memory doctor

1. Environment
  [FAIL] MEMORY_API_KEY: not set — copy .env.example to .env and set it, or export it
  [PASS] MEMORY_ENDPOINT: https://memory.neo4jlabs.com/v1 (default)
  [WARN] MEMORY_WORKSPACE_ID: unset. Production keys are workspace-bound so this is fine; …

2. MCP config files
  [PASS] .mcp.json.example: valid JSON, 2 server(s), no pasted key
         'team-memory' → remote https://mcp.memory.neo4jlabs.com/mcp
         'team-memory-self-hosted' → uvx --from neo4j-agent-memory[mcp] neo4j-agent-memory mcp serve --transport…
  [PASS] claude_desktop_config.json.example: valid JSON, 2 server(s), no pasted key
  [PASS] cursor_mcp.json.example: valid JSON, 2 server(s), no pasted key

3. Tool surface
  [PASS] self-hosted --profile core: 6 tool(s) (documented: 6)
         memory_add_entity, memory_add_fact, memory_add_preference, memory_get_context, memory_search, memory_store_message
  [PASS] self-hosted --profile extended: 16 tool(s) (documented: 16)
         graph_query, memory_add_entity, …, memory_start_trace, memory_store_message
  [PASS] hosted NAMS MCP server: 47 scope-gated tools at https://mcp.memory.neo4jlabs.com/mcp — …

(--configs-only: skipped the live NAMS checks)

All checks passed. Open your editor and ask it what the team decided.
```

`--configs-only` forgives the missing key (that is the mode); a full run treats
it as a failure and exits `1`.

`seed_workspace.py` against a live workspace:

```
Loaded environment from /path/to/examples/claude-code-team-memory/.env
Conversation 'team-decisions' → 3f6b2c51-6d0e-4f0a-9a3a-2c1b8e0f7a11
Stored 15 decision messages in one request.
Entity: Atlas (ORGANIZATION)
Entity: Helios (ORGANIZATION)
Facts are bolt-only, as expected: NAMS does not expose a facts endpoint.
Extraction right after the write: 15 message(s) pending
Extraction settled: True
Searchable entities: 7
   Alice Nakamura (PERSON)
   Atlas (ORGANIZATION)
   Neo4j 5.26 LTS (OBJECT)
   …
Cypher round-trip: [{'entities': 7}]

Seeded. Point your editor at the workspace and ask it:
   "Why did we choose Neo4j 5.26 LTS, and who decided?"

Export the conversation id so doctor.py checks the same one:
   export TEAM_MEMORY_CONVERSATION_ID=3f6b2c51-6d0e-4f0a-9a3a-2c1b8e0f7a11
```

Entity counts and types depend on what the server's extraction pipeline pulls
out of the transcript and on your active ontology. The offline smoke test
(`tests/examples/test_claude_code_team_memory_example.py`) drives all three
scripts with fixed payloads if you want to watch them run without a key.

## Things worth knowing

- **Extraction is asynchronous.** A read straight after a write can legitimately
  come back empty. `seed_workspace.py` calls
  `short_term.get_extraction_status()` and then
  `long_term.wait_for_extraction(session_id=…, expected_names=[…])` rather than
  sleeping. Prefer `expected_names` over `min_results`: NAMS entity search is
  nearest-neighbour and returns top-k whether or not anything matched.
- **`add_preference` and `add_fact` are bolt-only.** On NAMS they raise
  `NotSupportedError`; `seed_workspace.py` demonstrates the failure instead of
  describing it. Carry that information as entity descriptions, or point the
  self-hosted server at your own Neo4j, where `memory_add_fact` works.
- **`create_api_key()` mints an _admin_ key.** It does not send a `category`,
  and NAMS defaults an un-categorised create to account-wide admin. The
  per-developer editor credential you actually want is a *workspace* key
  (data-plane scopes, bound to one workspace, cannot mint more keys) — create it
  from the dashboard or with an explicit `category: "workspace"` POST.
  `provision_keys.py` prints the exact `curl` for that.
- **Keys are owner-private and expire.** Only the user who created a key can
  list, reveal, rotate or revoke it — even a workspace owner cannot see someone
  else's. Static keys expire roughly 90 days after creation; rotate before then.
- **`--session-strategy per_day`** (used in every config file) gives the
  self-hosted server one session id per user per day, so a day's work is one
  recallable thread instead of a new one per editor restart.

## Going further

- **How-to:** [Team memory in your editor](https://neo4j.com/labs/agent-memory/how-to/team-memory-in-your-editor) — this example, explained.
- **Hosted MCP reference:** [The NAMS MCP server](https://neo4j.com/labs/agent-memory/reference/nams-mcp) — the 47-tool surface, OAuth endpoints, two hostnames.
- **Self-hosted reference:** [MCP Tools](https://neo4j.com/labs/agent-memory/reference/mcp-tools) and the tutorial [Connect Claude Desktop to your knowledge graph](https://neo4j.com/labs/agent-memory/tutorials/mcp-server).
- **Keys:** [Authentication & API keys](https://neo4j.com/labs/agent-memory/reference/authentication) — the two key categories in full.
- **Next examples:** [`nams-quickstart/`](../nams-quickstart/) (the same memory graph from code), [`nams-fastapi/`](../nams-fastapi/) (NAMS inside a service).

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://github.com/neo4j-labs/agent-memory#readme)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (branch `examples-updates`), Python 3.12, FastMCP 4.0.3 (MCP Python SDK 2), and the hosted NAMS MCP server at `mcp.memory.neo4jlabs.com`, with the NAMS transport mocked (`tests/examples/test_claude_code_team_memory_example.py`) — 2026-09-10. `client.auth`, `NamsSettings`/`connect()`, `short_term.get_extraction_status` and `long_term.wait_for_extraction` ship in the 0.6 line; until it is released, install the library from this repository (`uv pip install -e ../..`) rather than from PyPI. Editor configs were validated as JSON against the documented schemas, not by launching each host._
