# Lenny's Memory — frontend

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

Next.js 16 (App Router) + React 19 + Chakra UI v3 client for the
[Lenny's Memory](../README.md) demo: a streaming chat UI with inline
tool-result cards (map, graph, entity knowledge panel, data table, stats) and a
live memory panel backed by the example's FastAPI backend.

> ⚠️ **Neo4j Labs Project**
>
> Part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a
> Neo4j Labs project. Actively maintained but not officially supported; APIs may
> change. Community support is available via the
> [Neo4j Community Forum](https://community.neo4j.com).

## Prerequisites

- **Node.js >= 22** (`engines.node` is enforced; Next 16 requires >= 20.9 and
  Node 20 went end-of-life on 2026-04-30)
- The example's **backend running on `http://localhost:8000`** — see
  [`../README.md`](../README.md) for Neo4j, data loading and backend setup. The
  frontend talks to it over REST + SSE and never connects to Neo4j itself.

## Run it

```bash
cd examples/lennys-memory/frontend
cp .env.example .env             # NEXT_PUBLIC_API_URL, analytics opt-in
npm install
npm run dev                       # http://localhost:3000
```

From the example root, `make run-frontend` does the same thing.

### Expected output

```
▲ Next.js 16.3.4 (Turbopack)
- Local:   http://localhost:3000
✓ Ready in 1.2s
```

Open <http://localhost:3000>: a welcome modal on first visit, a sidebar of
curated quick-start queries, and a **Memory & Agent** panel on the right. Ask
one of the suggested questions — tool calls render as cards inline, and the
panel's "Stored Memory" section fills in with the entities extracted from the
conversation (Wikipedia image, enriched description, link) plus any preferences
long-term memory detected, refetched after every completed turn.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Dev server (Turbopack, the Next 16 default) |
| `npm run build` | Production build — the gate the whole app must pass |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | `eslint .` (flat config; Next 16 removed `next lint`) |
| `npm test` | Vitest unit tests for the tool-card registry |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api` | Backend base URL |
| `NEXT_PUBLIC_ENABLE_ANALYTICS` | unset (off) | Set to `true` to mount `@vercel/analytics`. Off by default so a fork does not send page views to someone else's project; the hosted Neo4j Labs demo sets it to `true`. |

## How the UI is put together

```
src/
├── app/                     layout.tsx (fonts, theme provider, analytics gate)
│                            page.tsx  (chat + memory panel composition)
├── components/
│   ├── chat/                ChatContainer, MessageList, PromptInput, ToolCallDisplay
│   │   └── cards/           tool-result cards
│   │       ├── cardType.ts      which card renders a given tool result
│   │       ├── extractors/      one module per card: result -> card props
│   │       ├── toolCardRegistry.ts  re-export barrel over the two above
│   │       └── *Card.tsx        Map / Graph / MemoryGraph / Entity / Data / Stats / RawJson
│   ├── layout/              AppLayout, Sidebar, Footer
│   ├── memory/              MemoryContext (panel), LiveMemory (stored memory section),
│   │                        MemoryGraphView, MemoryMapView
│   ├── onboarding/          WelcomeModal
│   └── ui/                  Chakra provider, switch, tooltip
├── hooks/                   useChat, useMemoryContext, useQuickStart
├── lib/                     api.ts (the only place that calls fetch), types.ts
└── theme/                   Neo4j Labs Chakra v3 system
```

Two conventions worth copying:

- **All API access goes through `lib/api.ts`.** One timeout-aware `fetch`
  wrapper, one `process.env` read, and every request accepts an `AbortSignal` —
  including the chat stream, so cancelling a turn actually stops the backend
  generator instead of leaving the agent running.
- **Card selection is separate from data extraction.** `cardType.ts` answers
  "which card?", `extractors/*` answer "what does that card need?". Both are
  pure functions with unit tests in `test/`, which is why the registry can grow
  without becoming unreadable.

## Known gaps

- **`MemoryGraphView.tsx` and `MemoryMapView.tsx` are not mounted.** They are
  complete full-page NVL / Leaflet views (~2,600 lines) that nothing imports:
  the app renders chat plus the memory panel only. They are kept, upgraded and
  type-checked here because whether they come back as `/graph` and `/map`
  routes or get deleted is an open maintainer decision. Until that lands, the
  inline `GraphCard` / `MemoryGraphCard` / `MapCard` tool cards are the only way
  the UI shows a graph or a map. `LabsDisclaimer.tsx` and `useThreads.ts` are
  unmounted for the same reason. Do not read them as examples of what the
  shipped app does.
- **`react-leaflet-cluster` is declared but unused** — the map view implements
  its cluster and heatmap modes with plain `CircleMarker`s. It is pinned at the
  React 19-compatible major so the view can adopt it when it is remounted.
- **No end-to-end tests.** `npm test` covers the registry's pure functions only.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

## License

Apache 2.0 — see the main `neo4j-agent-memory` repository.

---

**Verified against:** `neo4j-agent-memory` 0.6.0-dev (backend), Next 16.3.4,
React 19.3.0, Chakra UI 3.37.0, `@neo4j-nvl/*` 1.2.1, react-leaflet 5.0.0,
TypeScript 5.9, Vitest 5.0.0, Node 25 — 2026-09-10.
