# Full-Stack Chat Agent — Frontend

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The Next.js 16 / React 19 / Chakra UI v3 client for the news research
> agent: a thread sidebar, SSE-streamed messages with expandable tool-call
> cards, a live memory-context panel, and a full-screen NVL memory-graph
> explorer.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory),
> a Neo4j Labs project. It is actively maintained but not officially supported.
> APIs may change. Community support is available via the
> [Neo4j Community Forum](https://community.neo4j.com).

This app makes **no direct memory-library calls** — every memory feature it
shows is served by the FastAPI backend in [`../backend`](../backend). If you
want to talk to agent memory from TypeScript directly, use the TypeScript SDK
in [`typescript/`](../../../typescript) (`@neo4j-labs/agent-memory`).

## Prerequisites

- **Node.js 22 LTS or newer** (`engines.node: ">=22"`; Node 18 and 20 are EOL,
  and Next 16 requires ≥ 20.9)
- The backend running and reachable — see [`../README.md`](../README.md)

## Run it

```bash
cd examples/full-stack-chat-agent/frontend

cp .env.example .env      # only NEXT_PUBLIC_API_URL lives here
npm ci                    # `prepare` runs Chakra typegen for you
npm run dev               # http://localhost:3000
```

`NEXT_PUBLIC_API_URL` points at the backend's `/api` prefix. To use a remote
backend, set it to that origin plus `/api` (the health check derives the
server root by stripping the trailing `/api`):

```bash
NEXT_PUBLIC_API_URL=https://my-backend.example.com/api npm run dev
```

**Expected output.** The dev server prints `Ready in …` and serves
<http://localhost:3000>. With the backend up you get the thread sidebar on the
left, the chat pane in the middle, and the memory-context panel on the right.
With the backend **down**, the app says so — an amber banner naming the URL it
tried — instead of silently rendering an empty conversation. If the backend is
up but `/health` reports `memory_connected: false` (usually a Neo4j password
mismatch), the banner says that instead.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Next dev server (Turbopack) on :3000 |
| `npm run build` | Production build; runs `tsc` as part of the build |
| `npm run start` | Serve the production build |
| `npm run type-check` | `tsc --noEmit` |
| `npm run lint` | `eslint .` — flat config, `exhaustive-deps` as an error |
| `npm run typegen` | Regenerate Chakra token types from `src/theme/index.ts` |

`npm ci && npm run type-check && npm run lint && npm run build` is the full
gate; run it before opening a PR that touches this directory.

## Directory map

```
src/
├── app/
│   ├── layout.tsx           # fonts, metadata, <Provider>
│   ├── page.tsx             # composes the hooks; owns `memoryVersion`
│   └── globals.css          # scrollbars + the spin keyframe, nothing else
├── components/
│   ├── branding/            # Labs Beta badge and footer
│   ├── chat/                # ChatContainer, MessageList, Message,
│   │                        #   PromptInput (send/stop), ToolCallDisplay
│   ├── layout/              # AppLayout (header, sidebar shell), Sidebar
│   ├── memory/
│   │   ├── graph.ts             # labels → colours, memory layer, captions,
│   │   │                        #   Neo4j value formatting (pure functions)
│   │   ├── MemoryGraphDialog.tsx # Chakra Dialog + data loading
│   │   ├── GraphCanvas.tsx       # the NVL adapter
│   │   ├── GraphLegend.tsx       # memory-layer legend
│   │   ├── GraphStatsPanel.tsx   # "what is on screen" counts
│   │   ├── MemoryFilterBar.tsx   # the three memory-layer toggles
│   │   ├── NodePropertyPanel.tsx # node/relationship inspector + expand
│   │   └── MemoryContext.tsx     # the right-hand memory panel
│   └── ui/                  # Chakra provider, colour mode, StatusAlert
├── hooks/
│   ├── useChat.ts           # streaming state machine + AbortController
│   ├── useThreads.ts        # thread CRUD
│   └── useHealth.ts         # /health, for the "no memory" banner
├── lib/
│   ├── api.ts               # typed client; `streamChat` is an async generator
│   └── types.ts             # the client/server contract, incl. `SSEEvent`
└── theme/index.ts           # Neo4j Labs Chakra system (Labs Purple, fonts)
```

## SSE event contract

`POST /api/chat` responds with `text/event-stream`. Each line is
`data: <json>`; `lib/types.ts` models the payloads as a discriminated union
(`SSEEvent`), which is what makes the `switch` in `useChat` exhaustive.

| `type` | Fields | UI effect |
|---|---|---|
| `token` | `content` | Appended to the streaming assistant message |
| `tool_call` | `id`, `name`, `args` | Adds a pending tool-call card |
| `tool_result` | `id`, `name`, `result`, `duration_ms` | Flips that card to success and shows the duration |
| `done` | `message_id`, `trace_id?` | Ends the turn and bumps `memoryVersion`, so the memory panel and graph refetch |
| `error` | `message` | Shown as an alert above the message list |

The stream is read from a `fetch` response body rather than `EventSource`,
because the endpoint is a POST with a JSON body. `streamChat` takes an
`AbortSignal`: the Stop button and the thread-change cleanup in `useChat`
both abort through it, so a cancelled turn stops the backend generating
instead of orphaning the request.

## Which panel calls which endpoint

| UI surface | Endpoint |
|---|---|
| Backend/memory banner | `GET /health` (server root, not under `/api`) |
| Thread sidebar | `GET/POST /api/threads`, `PATCH/DELETE /api/threads/{id}` |
| Message list | `GET /api/threads/{id}` (a 404 means "new thread", anything else is shown as an error) |
| Sending a message | `POST /api/chat` (SSE) |
| Memory context panel | `GET /api/memory/context?thread_id=` |
| Memory graph dialog | `GET /api/memory/graph?session_id=` |
| Double-click / "Expand neighbours" | `GET /api/memory/graph/neighbors/{node_id}` |

## Notes for readers

- **Memory layers.** The graph dialog's three filters map onto the library's
  three memory layers and the node labels it actually writes: short-term
  (`Conversation`, `Message`), long-term (`Preference`, `Entity` plus the
  PascalCase POLE+O labels `Person`, `Organization`, `Location`, `Event`,
  `Object`) and reasoning (`ReasoningTrace`, `ReasoningStep`, `ToolCall`,
  `Tool`). See `src/components/memory/graph.ts`.
- **NVL is client-only.** `@neo4j-nvl/react` renders to a canvas and touches
  `window` on import, so `GraphCanvas` loads it through `next/dynamic` with
  `ssr: false`. Its `Node` / `Relationship` / `MouseEventCallbacks` types are
  imported from the library rather than re-declared, which is how the canvas
  gets NVL's own `onNodeDoubleClick` instead of hand-timed double clicks.
- **Colour tokens.** Use Chakra v3 semantic tokens (`bg`, `bg.panel`,
  `bg.subtle`, `fg`, `fg.muted`, `border.subtle`) so both colour modes work.
  `bg.canvas` and `fg.default` are Chakra **v2** names that do not exist in
  v3 — Chakra passes an unknown token through as a literal CSS value, so the
  mistake is silent. `eslint.config.mjs` rejects them.
- **Literal colours** are only acceptable for the NVL palette, because NVL
  paints to a canvas and cannot read CSS variables.

## Support

Community-supported through the
[Neo4j Community Forum](https://community.neo4j.com). Issues and pull requests
are welcome at
[neo4j-labs/agent-memory](https://github.com/neo4j-labs/agent-memory/issues).

---

_Verified against `neo4j-agent-memory` 0.6.0-dev with Next 16.3.4, React 19.3.0,
Chakra UI 3.37.0, `@neo4j-nvl/*` 1.2.1, TypeScript 5.9 and Node 25 on
2026-09-10 (`npm ci && npm run type-check && npm run lint && npm run build`
all clean; UI verified against a stubbed backend)._
