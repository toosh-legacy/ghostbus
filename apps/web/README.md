# GhostBus web (Next.js App Router, TypeScript strict, Tailwind, TanStack Query, Leaflet)

| Route | Purpose |
|---|---|
| `/` | Nearby stops (geolocation, used once and never stored) + search + pilot routes |
| `/stops/[id]` | Upcoming trips with 80% ranges in words, source, ghost badge + reason, stale-feed banner |
| `/routes/[id]` | Live map (Leaflet + OSM tiles, SSE updates) with a list alternative; stale buses fade |
| `/reliability`, `/reliability/[routeId]` | Metrics table with filters, trends, worst timepoints, CSV |
| `/ask` | Copilot with the exact SQL shown |
| `/methodology` | Definitions (versioned), sources, licenses, backtests |
| `/admin` | Feed health, pipeline, models, datasets (admin token) |

- API base: `NEXT_PUBLIC_API` (default `http://127.0.0.1:8000`). Typed client: `npm run gen:api` (from `packages/schemas/openapi.json`).
- `npm run build && npm run start -- -p 3100` (port 3000 is often taken). `make demo` starts API + web together.
- E2E + accessibility: `npm run test:e2e` (Playwright, Pixel 7 viewport, axe WCAG 2.2 AA tags, system Edge channel).
