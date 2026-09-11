# AWS Financial Services Advisor — Frontend

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> The Vite 8 / React 19 / Chakra UI v3 analyst console for the AWS compliance
> advisor: a customer dashboard, alerts and investigations panels, an NVL view of
> the compliance graph, and a chat interface that renders the backend's SSE
> agent stream next to an **agent-memory panel** reading short-term, long-term
> and reasoning memory back out of Neo4j.

> ⚠️ **Neo4j Labs Project**
>
> This example is part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory),
> a Neo4j Labs project. It is actively maintained but not officially supported.
> APIs may change. Community support is available via the
> [Neo4j Community Forum](https://community.neo4j.com).

This app makes **no direct memory-library calls** — every memory feature it
shows is served by the FastAPI backend in [`../backend`](../backend). To talk to
agent memory from TypeScript directly, use the TypeScript SDK in
[`typescript/`](../../../../typescript) (`@neo4j-labs/agent-memory`).

## Prerequisites

- **Node.js 22 LTS or newer** (`engines.node: ">=22"`; Node 18 and 20 are EOL)
- The backend running and reachable — see [`../GETTING_STARTED.md`](../GETTING_STARTED.md)
- Sample data loaded into Neo4j (`make load-data` from the example root), or the
  dashboard, alerts and graph views will be empty

## Run it

```bash
cd examples/financial-services-advisor/aws-financial-services-advisor/frontend

cp .env.example .env.local   # optional; only needed for a remote backend
npm ci
npm run dev                  # http://localhost:5173
```

`npm run dev` proxies `/api` to `http://localhost:8000` (override with
`BACKEND_URL=...`). A **deployed** build has no proxy, so set `VITE_API_URL` to
the backend's absolute `/api` URL at build time:

```bash
VITE_API_URL=https://compliance-api.example.com/api npm run build
npm run preview              # serve dist/ locally
```

### Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server on :5173 with the `/api` proxy |
| `npm run build` | `tsc -b` (type-check) then `vite build` → `dist/` |
| `npm run type-check` | `tsc -b` only |
| `npm run lint` | ESLint 10 flat config (`eslint.config.js`) |
| `npm test` | Vitest unit tests (`src/**/*.test.ts`) |
| `npm run preview` | Serve the production build |

## Expected output

`npm run dev`:

```
  VITE v8.3.0  ready in 81 ms
  ➜  Local:   http://localhost:5173/
```

Then, with the backend up and sample data loaded:

- **Dashboard** — customer count, high-risk count, alert counts, and a customer
  table with risk level, risk score and KYC status.
- **Alerts** — three summary cards plus the alert table; **Acknowledge** on a
  `NEW` alert issues `PATCH /api/alerts/{id}` and the row flips to
  `acknowledged`.
- **Investigations** — summary cards, the investigation table (**Start** on a
  `PENDING` row runs the multi-agent workflow), and the workflow explainer.
- **Context Graph** — the NVL canvas with a per-label colour legend, a
  node/relationship count, "fit to screen", and **double-click a node to expand
  its neighbours** (the header line reports what was added).
- **AI Advisor** — ask e.g. *"Investigate customer CUST-003 for potential money
  laundering"*. While the turn runs you get a live **Agent Activity** panel (one
  card per agent, with tool calls, memory accesses and thoughts) and a **Stop**
  button that really aborts the request. When it finishes you get the reply, an
  **Investigation Summary** (agents, tools, duration, trace id), and the
  **Agent Memory** panel on the right refreshes:

```
Agent Memory            session 997fa833…
Short-term   2          user      Investigate customer CUST-003 …
                        assistant CUST-003 shows a structuring pattern …
Long-term               (see "Known gaps" below)
Reasoning    1          complete  trace-ab…
                        1. scan_transactions   [detect_structuring]
```

Everything in that panel is **re-read from the backend**, so a page reload shows
the same history, entities and reasoning trace.

## How it is wired

| UI surface | Endpoint | Memory layer |
|---|---|---|
| Chat stream | `POST /api/chat/stream` (SSE) | writes short-term + reasoning |
| Memory panel → Short-term | `GET /api/chat/history/{session_id}` | `client.short_term` |
| Memory panel → Long-term | `GET /api/memory/context` *(not implemented yet)* | `client.long_term` |
| Memory panel → Reasoning | `GET /api/traces/{session_id}` | `client.reasoning` |
| Dashboard / customers | `GET /api/customers` | domain graph |
| Alerts | `GET /api/alerts`, `/api/alerts/summary`, `PATCH /api/alerts/{id}` | domain graph |
| Investigations | `GET /api/investigations`, `POST /api/investigations/{id}/start` | domain graph |
| Context graph | `GET /api/graph/memory`, `GET /api/graph/neighbors/{id}` | domain graph |

### SSE event protocol

`src/lib/api.ts` declares the full protocol and `src/hooks/useAgentStream.ts`
handles all of it. The backend drives `supervisor.stream_async(...)`, so tool
calls and reasoning text arrive while the turn runs.

| Emitted today | Handled, not emitted yet |
|---|---|
| `agent_start`, `thinking`, `tool_call`, `tool_result`, `agent_complete`, `response`, `trace_saved`, `done`, `error` | `agent_delegate`, `memory_access` |

Payloads are normalised on the way in, because the backend cannot always fill
every field: `thinking` arrives as token deltas carrying only `content` (they
are concatenated into one buffer per agent), and `tool_call` / `tool_result`
may omit `agent` (they fall back to the agent that is currently running) or
`args` (Strands streams the tool input incrementally).

Frames are parsed by `splitSseFrames()`, which buffers on the blank-line frame
delimiter — an `event:` line and its `data:` line are always decoded together,
so an event split across a network chunk is never dropped. `npm test` covers
that case over a chunked `ReadableStream`.

## Project layout

```
src/
├── App.tsx                      routes (the NVL view is lazy-loaded)
├── main.tsx                     ChakraProvider + TanStack Query
├── theme/index.ts               Neo4j Labs theme, NVL node colours
├── lib/api.ts                   axios clients, SSE parser, response types
├── lib/api.test.ts              Vitest tests for the SSE parser
├── hooks/useAgentStream.ts      SSE consumer; returns a StreamOutcome
├── hooks/useColorMode.ts        light/dark toggle (Chakra's `.dark` class)
└── components/
    ├── Chat/                    chat, orchestration view, memory panel
    ├── Dashboard/               sidebar, customers, alerts
    ├── Graph/MemoryGraphView    NVL wrapper + neighbour expansion
    ├── Investigation/           investigations table + workflow
    ├── branding/                Labs disclaimer
    └── ui/color-mode.tsx        colour-mode button
```

### Conventions

- **Semantic tokens only.** Use `bg`, `bg.subtle`, `bg.panel`, `bg.muted`, `fg`,
  `fg.muted`, `fg.subtle`, `border`, `colorPalette.*` and the theme's `brand` /
  `teal` / `amber` palettes. No literal palette steps (`gray.50`, `teal.500`)
  in `src/` — that is what makes dark mode work. Literal hex values survive only
  in `theme/index.ts`, where NVL needs canvas colours.
- **NVL gotchas.** Mouse callbacks go on `mouseEventCallbacks`, *not*
  `nvlCallbacks` (which takes lifecycle callbacks and silently ignores unknown
  keys). The force-directed layout is `'forceDirected'`, not `'force-directed'`.
- **No state mirroring through effects.** `startStream()` resolves with a
  `StreamOutcome`; the caller updates its own state from that. ESLint's
  `react-hooks/set-state-in-effect` enforces this.

## Known gaps

- **`GET /api/memory/context` does not exist yet.** Entities *are* extracted on
  every turn (`chat_stream` calls `add_session(..., extract_entities=True)`),
  they are simply not exposed over HTTP. The Long-term section says so and
  starts rendering entities and preferences as soon as the endpoint appears —
  `memoryApi.getContext()` treats a 404 as "not wired yet" rather than an error.
- **`GET /api/graph/neighbors/{id}` returns `type`, not `labels`**, and for
  neighbours that `type` is a *domain property* (`individual`, `corporate`)
  rather than a Neo4j label. `normalizeNeighborNode()` bridges the two shapes
  and `getNodeColor()` matches case-insensitively, so expansion works; expanded
  nodes whose domain `type` is not a label still fall back to grey. The real fix
  is for the backend to return `labels(connected) AS labels`.
- **The backend does not stream yet.** See the SSE table above.
- **Investigations have no `findings_count`** and customers have no
  `alerts_count`; those columns were removed rather than rendering `undefined`.

## Support

- [Neo4j Community Forum](https://community.neo4j.com) — questions and help
- [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues) — bugs and
  feature requests
- [Neo4j Labs](https://neo4j.com/labs/) — what a Labs project is

---

**Verified against** `neo4j-agent-memory` 0.6.0-dev (the example's backend uses
the repo checkout), Node 25.0.0 / npm 11.6.2, Vite 8.3.0,
`@vitejs/plugin-react` 6.1.1, React 19.3.0, react-router 7.18.3,
`@chakra-ui/react` 3.37.0, `@neo4j-nvl/base`+`react` 1.2.1,
`@tanstack/react-query` 5.102.8, motion 13.2.0, TypeScript 5.9.3, ESLint 10.10.0
with `typescript-eslint` 8.70.0, Vitest 5.0.0 — on **2026-09-10**.
`npm ci && npm run type-check && npm run lint && npm test && npm run build` all
pass; the UI was exercised in headless Chromium against a stub of the backend's
HTTP contract.
