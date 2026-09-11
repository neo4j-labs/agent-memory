# Walkthrough: proving it actually remembers

Two scripted demos. The first proves memory survives quitting the editor; the
second makes the `core`-vs-`extended` context trade-off visible. Both assume
[`README.md`](README.md) setup is done and `doctor.py` is green.

Every prompt below is typed into the editor. Nothing in this file is code you
run — that is the point of the example.

---

## Demo 1 — persistence across a quit and reopen

### Turn 1 (Alice, Claude Code)

> We just agreed that the Atlas retention window is 90 days, not 30. Store that
> decision in team memory and tell me which tool you used.

Expect the editor to call `memory_add_message` (hosted) or `memory_store_message`
(self-hosted) and to say so. With the hosted server the write also queues
server-side entity extraction.

> What do we use for embeddings, and who decided?

It calls `memory_search` / `memory_get_context` and answers from ADR-004:
text-embedding-3-small, Diego Ferreira — seeded by `seed_workspace.py`, never
mentioned in this conversation.

### Now quit the editor completely

Not a new tab, not `/clear`. Quit the application, so no in-process history can
account for what happens next.

### Turn 2 (Alice, reopened)

> What did I tell you about the Atlas retention window?

It answers *90 days* — from the graph, not from context. This is the single claim
the example exists to make: the memory is in a workspace, not in a session.

### Turn 3 (Bob, a different machine, his own key)

> What's the Atlas retention window, and who changed it?

Bob's editor has never seen Alice's conversation. It answers anyway, because both
editors are MCP clients of the same workspace. Swap in Bob's key
(`provision_keys.py create --label bob-laptop --confirm`) to see it end to end —
the point of per-developer keys is that revoking Bob's does not disturb Alice's.

### Turn 4 — the question nobody was asked

> Which of our architecture decisions mention Neo4j, and which people are
> attached to them?

Neither Alice nor Bob told any agent to build an entity index over the decision
log; NAMS extracted it. On the self-hosted `extended` profile the same question
can be answered with `graph_query` and one Cypher statement:

```cypher
MATCH (e:Entity)<-[:MENTIONS]-(m:Message)
WHERE toLower(e.name) CONTAINS 'neo4j'
MATCH (m)-[:MENTIONS]->(p:Entity {type: 'PERSON'})
RETURN e.name AS decision_topic, collect(DISTINCT p.name) AS people
```

That is the graph-shaped payoff: a read the writer never planned for.

---

## Demo 2 — `core` (6 tools) vs `extended` (16 tools)

Tool definitions are tokens in every request. The profile flag is the dial.

### Step 1 — see the two surfaces without starting anything

```bash
uv run python doctor.py --configs-only
```

Section 3 prints both profiles from the library's own registrar
(`register_tools(mcp, profile=…)`), so the lists are the truth rather than a
transcription of this file.

### Step 2 — run `core`

Edit the `team-memory-self-hosted` entry to `--profile core` (the shipped
`.mcp.json.example` already does) and restart the editor. Then:

> List the memory tools you have.

Six: `memory_search`, `memory_get_context`, `memory_store_message`,
`memory_add_entity`, `memory_add_preference`, `memory_add_fact`. Enough for the
full read/write cycle and nothing else. Then ask:

> Run a Cypher query against the memory graph.

It cannot — `graph_query` is an extended tool. That refusal is the trade-off made
concrete.

### Step 3 — run `extended`

Switch to `--profile extended`, restart, and ask again. Ten more tools appear,
including `graph_query`, the reasoning-trace trio (`memory_start_trace`,
`memory_record_step`, `memory_complete_trace`) and `memory_export_graph`. The
Cypher question from Demo 1 Turn 4 now works.

### How to choose

- **`core`** — day-to-day coding. You want recall, not a graph console.
- **`extended`** — investigating memory itself: ad-hoc Cypher, trace review,
  exporting a subgraph to visualise.
- **Hosted NAMS** — a third, larger surface (47 scope-gated tools: ontology
  editing, entity review, Skills, workspace administration). Scopes, not a
  profile flag, decide what a key sees. See [`README.md`](README.md#two-servers-two-tool-surfaces--said-once-here).

---

## Demo 3 — the Desktop bundle

```bash
./bundle/build.sh
# → bundle/dist/neo4j-agent-memory.mcpb
```

The script stages `deploy/mcpb/manifest.json` — the repository's single
definition of the server command — and zips it. Install it in Claude Desktop via
Settings → Extensions → Install from file. Desktop prompts for the manifest's
`env_required` values (`NEO4J_PASSWORD`), which is why the bundle path needs no
hand-edited JSON and no key in a file.

---

## When it doesn't work

| Symptom | Check |
|---|---|
| Editor lists no memory tools | `doctor.py --configs-only` — usually invalid JSON or a server entry with neither `command` nor `url` |
| `403` from the hosted server | `MEMORY_WORKSPACE_ID` unset on a header-scoped (dev/staging) deployment |
| `401` after a while | Static keys expire around 90 days: `provision_keys.py rotate --key-id … --confirm` |
| "I don't have that in memory" right after storing it | Extraction is asynchronous. `doctor.py` reports `extraction status`; `seed_workspace.py` shows how to await it |
| Key management refused | It needs an **admin** key; a workspace data key cannot mint or manage keys |
| `uvx` not found | Install [uv](https://docs.astral.sh/uv/), or point `command` at an absolute path — Claude Desktop does not use your shell `PATH` |
