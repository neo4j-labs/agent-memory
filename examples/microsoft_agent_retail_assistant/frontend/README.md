# Smart Shopping Assistant — frontend

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Beta](https://img.shields.io/badge/Status-Beta-6366F1)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

> Next.js 16 (App Router) + React 19 + Chakra UI v3 client for the retail
> assistant. The app-level README is one directory up — start there; this file
> covers only the frontend.

> ⚠️ **Neo4j Labs Project**
>
> Part of [`neo4j-agent-memory`](https://github.com/neo4j-labs/agent-memory), a
> Neo4j Labs project: actively maintained, not officially supported. Community
> support via the [Neo4j Community Forum](https://community.neo4j.com).

## Prerequisites

- **Node.js 22 LTS or newer** (Node 18 reached end of life in April 2025). The
  `npm test` script uses Node's built-in test runner and TypeScript type
  stripping, which needs 22.6+.
- The backend from [`../backend`](../backend) running on port 8000.

## Quick start

```bash
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm ci                             # package-lock.json is committed
npm run dev                        # http://localhost:3000
```

The header shows a **Connected** badge once `GET /health` answers; if the
backend is not up you get a **Disconnected** badge and an explicit card saying
so, rather than three empty panels.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Dev server on :3000 |
| `npm run build` | Production build (type-checks as part of the build) |
| `npm start` | Serve the production build |
| `npm run lint` | `eslint .` against `eslint.config.mjs` (flat config — `next lint` was removed in Next 16) |
| `npm run typecheck` | `next typegen && tsc --noEmit` (typegen first: `next-env.d.ts` references generated route types) |
| `npm test` | `node --test` over `src/**/*.test.ts` — the SSE parser and the graph transform |

## Environment

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL of the FastAPI backend. Baked in at build time, like every `NEXT_PUBLIC_*` value. |

## What the three tabs show

- **Chat** — streaming turn over `POST /chat`. The response is Server-Sent
  Events, and because the request is a `POST` it cannot use `EventSource`
  (GET-only); `streamChat()` reads the body and parses frames itself. Tool
  calls appear as cards whose spinner clears when the matching `tool_result`
  frame arrives.
- **Memory Graph** — a Memory Explorer with two panels. *Memory Context* puts
  short-term, long-term and reasoning side by side with live counts, exactly as
  the backend assembles them for the next prompt. *Knowledge Graph* draws the
  session subgraph with `react-force-graph-2d`; clicking a node re-queries
  `GET /memory/graph?center_entity=…` for its two-hop neighbourhood.
- **Preferences** — the learned preference store, grouped by category, plus a
  form that writes one directly through `POST /memory/preferences` (the
  deterministic counterpart to the agent's own `remember_preference` tool).

### Shopper switcher and scoping

The header picker sends `user_id` with every chat turn; the backend upserts a
`(:User {identifier})` node and keeps one session per shopper, so short-term
memory and the graph view are per-shopper. **Preferences are not**: this backend
reads them through an untenanted `search_preferences`, so the panel is labelled
*All learned preferences (global)* — see "Multi-tenancy is not enabled" in the
app README for why.

## API contract

Every response shape lives in [`src/lib/api.ts`](src/lib/api.ts) — one
`API_BASE`, one exported interface per payload, and a discriminated union for
the SSE frames (`token` / `tool_call` / `tool_result` / `done` / `error`) so the
`switch` in `ChatInterface` is checked for exhaustiveness. If the backend's
projection changes, `npm run typecheck` is what tells you.

## Layout

```
src/
├── app/
│   ├── layout.tsx          # Chakra + next-themes provider, globals.css
│   ├── globals.css         # the little a CSS reset cannot express
│   └── page.tsx            # shell: health badge, shopper switcher, three tabs
├── components/
│   ├── ChatInterface.tsx   # streaming chat, tool-call cards
│   ├── MemoryExplorer.tsx  # three-layer context + knowledge graph panels
│   ├── MemoryGraph.tsx     # react-force-graph-2d, dynamically imported
│   ├── PreferencePanel.tsx # grouped preferences + write form
│   └── ui/                 # Chakra provider and colour-mode snippet
└── lib/
    ├── api.ts              # typed backend client (+ api.test.ts)
    ├── graph.ts            # payload → force-graph transform (+ graph.test.ts)
    └── useAsyncData.ts     # fetch-on-mount hook used by both panels
```

## Notes for readers

- **Colour mode** is real: `next-themes` stamps the mode on `<html>`, every
  surface uses a Chakra v3 semantic token (`bg.panel`, `fg.muted`,
  `border.subtle`, `teal.solid`), and the header has a toggle. No hard-coded
  `gray.50`/`white`.
- **Chakra v3 only.** `noOfLines` (v2) is `lineClamp` in v3, and
  `maxW="container.xl"` does not exist in v3's default system — it is
  `maxW="breakpoint-xl"`.
- **No `setState` in an effect body.** `useAsyncData` derives its loading flag
  by comparing request keys, which keeps it clear of React 19's
  `react-hooks/set-state-in-effect` rule instead of suppressing it.
- **Known upstream quirk.** In a *production* build, roughly one cold load in
  five logs React error #418 (hydration mismatch) from the `next-themes`
  script that stamps the colour-mode class on `<html>` before React hydrates.
  React recovers by re-rendering on the client and the page is fully
  functional; `next dev` never shows it, and removing `next-themes` (light
  mode only) removes it. `suppressHydrationWarning` on `<html>` is already the
  documented mitigation.

## Support

- 💬 [Neo4j Community Forum](https://community.neo4j.com)
- 🐛 [GitHub Issues](https://github.com/neo4j-labs/agent-memory/issues)
- 📖 [`neo4j-agent-memory` documentation](https://neo4j.com/labs/agent-memory/)

---

_Verified against `next` 16.3.4, `react` 19.3.0, `@chakra-ui/react` 3.37.0,
`react-force-graph-2d` 1.29.1, `eslint` 9.39.5, `typescript` 5.9.3 on Node
25.0.0, 2026-09-10. `npm install`, `npm run lint`, `npm run typecheck`,
`npm test` (7 tests) and `npm run build` all pass. The production build was
served with `next start` and driven in headless Chromium against a stubbed
backend: all three tabs render, the knowledge graph draws and re-centres on a
node click, the preference form writes and the list refreshes, the colour-mode
toggle flips `<html class>`, and switching shoppers starts a new session. The
console is clean apart from the intermittent `next-themes` hydration notice
described above. The chat turn itself needs a real backend and an
`OPENAI_API_KEY`. `eslint` is held at 9 because `eslint-config-next@16.3.4`
bundles an `eslint-plugin-react` that crashes under ESLint 10._
