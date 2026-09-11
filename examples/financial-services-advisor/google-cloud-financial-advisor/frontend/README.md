# Financial Services Advisor — frontend (Google Cloud)

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Vite 8 + React 19 + Chakra UI v3 SPA for the multi-agent compliance demo. The
> app-level README is one directory up — start there; this file covers only the
> frontend.

> ⚠️ **Neo4j Labs Project**
>
> Part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a
> Neo4j Labs project: actively maintained, not officially supported. Community
> support via the [Neo4j Community Forum](https://community.neo4j.com).

## Prerequisites

- **Node.js 22 LTS or newer** (Node 18 reached end of life in April 2025; the
  Docker build image is `node:22-alpine`).
- The FastAPI backend from [`../backend`](../backend) running on port 8000
  (`make run-backend` from the example root), with Neo4j loaded
  (`make load-data`).

## Quick start

```bash
cp .env.example .env   # optional — the defaults work for local development
npm ci                 # package-lock.json is committed
npm run dev            # http://localhost:5173
```

`npm run dev` proxies `/api` to `http://localhost:8000` (see `vite.config.ts`),
so no CORS configuration is needed. Without the backend running, the dashboard
panels show their error states and the chat refuses to stream — the SPA itself
still loads.

**Expected first run:** the sidebar lists Dashboard, Chat, Customers,
Investigations, Alerts and Context Graph. Open **Chat**, pick a suggested
prompt such as *Detect Structuring Pattern*, and the agent orchestration panel
appears live: the Supervisor card, then delegation to the AML agent, tool-call
cards filling in as results stream back, and Neo4j memory-access flashes. When
the turn finishes the panel collapses into an **Agent Activity** timeline
showing `loaded from Neo4j` once the persisted reasoning trace is read back.
Reload the page and the conversation and its audit trail replay from the graph.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Dev server on :5173 with the `/api` proxy |
| `npm run build` | `tsc --noEmit && vite build` → `dist/` |
| `npm run preview` | Serve the production build |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | `eslint .` against `eslint.config.js` (flat config; `--ext` is a no-op there) |
| `npm test` | `vitest run` — the SSE parser, the graph transform, and an App mount smoke test |

## Environment

`VITE_*` values are inlined at **build** time (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | Absolute URL of the backend API when the SPA is deployed on its own (e.g. two Cloud Run services). Leave unset to use the relative path served by the Vite proxy or nginx. |
| `VITE_DEV_PROXY_TARGET` | `http://localhost:8000` | Dev server only: where `/api` is proxied. |

In the container image, `/api` is proxied by nginx instead: `nginx.conf.template`
is rendered at start-up by `nginx:alpine`'s envsubst entrypoint, so these are
**runtime** configuration rather than baked into the image:

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `80` | Cloud Run sets this to 8080. |
| `BACKEND_URL` | `http://backend:8080` | Upstream for `/api`. `docker-compose.yml` already passes the compose value, and `infrastructure/cloudbuild.yaml` passes the deployed backend's URL. |
| `BACKEND_HOST` | `backend` | Host header and TLS SNI sent upstream — set it to the backend's hostname when `BACKEND_URL` is `https://…`, because Cloud Run routes on the Host header. |

For a split Cloud Run deployment, the simpler option is to build the image with
`--build-arg VITE_API_BASE_URL=https://<backend>.run.app/api` so the browser
calls the backend directly and nginx only serves static files.

## Where things live

```
src/
├── App.tsx                      # routes + the app-level chat session context
├── main.tsx                     # React root, Chakra provider, TanStack Query
├── theme/index.ts               # Neo4j Labs theme (Labs Purple, Neo4j Teal)
├── hooks/
│   └── useAgentStream.ts        # per-agent state machine over the SSE stream
├── lib/
│   ├── api.ts                   # typed fetch client + AgentEvent union
│   ├── sse.ts                   # SSE frame parser (chunk-boundary safe)
│   ├── graph.ts                 # memory-layer classification for the graph view
│   ├── agents.ts               # agent labels / colours, shared by three views
│   └── session.ts               # chat session context + localStorage
└── components/
    ├── Chat/                    # ChatInterface, AgentOrchestrationView,
    │                            # AgentActivityTimeline, ToolCallCard,
    │                            # MemoryAccessIndicator, MarkdownMessage
    ├── Dashboard/               # Sidebar, CustomerDashboard, AlertsPanel
    ├── Graph/MemoryGraphView.tsx # NVL Context Graph (lazy-loaded route)
    └── Investigation/InvestigationPanel.tsx
```

### Streaming

`POST /api/chat/stream` returns Server-Sent Events, and because the request is a
`POST` it cannot use `EventSource` (GET-only). `streamChatMessage()` in
`lib/api.ts` reads the response body and hands chunks to `lib/sse.ts`, which
carries partial lines **and** partial events across chunk boundaries, joins
multi-line `data:` fields, tolerates `\r\n` and ignores `: keep-alive` comments.
`src/lib/sse.test.ts` feeds it a stream split mid-event and one split
byte-by-byte.

### Memory in the UI

| What you see | Where it comes from |
|---|---|
| Transcript after a reload | `GET /api/chat/history/{session_id}` — short-term memory |
| `loaded from Neo4j` timeline | `GET /api/traces/detail/{trace_id}` — reasoning memory |
| Context Graph layers | `GET /api/graph/memory` — `Conversation`/`Message` (short-term), `Entity`/`Preference`/`Fact` (long-term), `ReasoningTrace`/`ReasoningStep`/`ToolCall` (reasoning), plus this app's domain nodes |

The Context Graph's **Domain / Short-term / Long-term / Reasoning** toggles
filter by node label (`src/lib/graph.ts`), and **This conversation only** passes
the current chat `session_id` so you see the memory the last turn produced.
Counts next to each toggle tell you how much of each layer the payload holds; if
the memory layers read `(0)`, run a chat turn and refresh.

Clicking a long-term memory node (an `:Entity`, `:Preference` or `:Fact`) opens
its **audit trail** — `GET /api/graph/audit-trail/{entity_name}`, backed by the
`(:ReasoningStep)-[:TOUCHED]->(:Entity)` edges the backend writes when it records
a tool call with `touched_entities=`. That is the question a compliance reviewer
actually asks: *which agent step touched this entity, and through which tool?*
Double-clicking any node expands its neighbours instead.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)

---

_Verified against `neo4j-agent-memory` 0.6.0-dev (editable path install in the
backend), Vite 8.3, React 19.2, react-router 7.18, Chakra UI 3.37, motion 13.2,
`@neo4j-nvl/*` 1.2.1, TanStack Query 5.102, TypeScript 5.9, ESLint 10 +
typescript-eslint 8, Node 22 — 2026-09-10._
