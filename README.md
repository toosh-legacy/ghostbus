# GhostBus

Real-time bus reliability platform for MBTA buses: **ghost-bus detection**, **calibrated arrival ranges** and
**published reliability scores** from MBTA open data. Python (FastAPI, DuckDB, scikit-learn), Next.js. Built locally
behind interfaces; Azure deployment (Phase C) swaps adapters by configuration.

Spec: [`TRANSIT_SPEC.md`](TRANSIT_SPEC.md) · Overnight results: [`docs/MORNING_REPORT.md`](docs/MORNING_REPORT.md)

## Quick start (Windows, no WSL needed)

```bash
uv sync                       # Python 3.12 env
make record                   # start the polite live recorder (detached; heartbeat in var/)
make recorder-status          # check / restart it
make data                     # D1 GTFS (+archive) + D2 arrival/departure 2025 -> Parquet lake, metrics, analytics DB
make train                    # Model A + Model B, 3-fold backtest -> ml/reports/latest.md, pins models
make eval                     # every gate in spec 17.2 -> evals/reports/latest.md
make test                     # pytest (unit, property-based, integration, API, data)
make demo                     # API :8000 (replay of recorded data) + web :3100
cd apps/web && npm run test:e2e   # Playwright + axe happy path (needs `make demo` running)
```
No `make`? Use `uv run python tasks.py <target>`. Full-mode services: `make up` (PostGIS :15433, Redis :6379, Azurite :10010),
then `MODE=full STREAM_BUS=redis LIVE_STATE=redis DATABASE_URL=postgresql+psycopg://ghostbus:ghostbus@localhost:15433/ghostbus`.

## What's inside

| Path | What |
|---|---|
| `services/stream/gbstream` | recorder, decoder, dedupe + watermark ordering, STRtree map matching, arrival detection, ghost rules (ghost-def v1.1), replay engine, curated writers |
| `sim/gbsim` | simulator with exact ground truth (6 seeded scenarios) and scorer |
| `data/pipeline/gbdata` | GTFS current + archive, calendar expansion, stop-on-shape, D2 pipeline, completeness, reliability metrics, analytics DB, DB seeding |
| `ml/gbml` | Model A (quantile GB + conformal), Model B (calibrated classifier), registry with promotion rule, serving, online coverage + PSI |
| `copilot/gbcopilot` | sqlglot SQL validator, read-only query tool, offline intent-template provider |
| `services/api/gbapi` | FastAPI `/v1` (stops, arrivals, routes live, SSE, reliability, CSV, methodology, copilot, admin), API keys + quotas |
| `apps/web` | Next.js app: stops, stop page, live map, reliability, methodology, ask, admin |
| `evals/` | gold set (generated from data), red team, eval runner and reports |
| `docs/` | decisions, blockers, environment, changelog, methodology, threat model, ADRs |

## Headline numbers (see reports)

- Simulator: map matching median ≈ 6–7 m, arrival median ≈ 4 s / p90 ≈ 15–24 s, ghost precision = recall = 1.0 in all 6 scenarios, 100% Unknown during outages.
- Model A: p50 MAE 95.7 s vs best baseline 139.9 s (31.6% better); 80% interval coverage 79.3% after conformal calibration.
- Copilot: 40/40 gold questions, 10/10 refusals, 0 red-team failures. Privacy: 0 coordinates in logs.

## Data & licenses
MBTA GTFS / GTFS-Realtime (MassDOT Developers License Agreement), MBTA Bus Arrival Departure Times (CC0), map tiles ©
OpenStreetMap contributors. Raw data is never committed; hashes are in `data/manifest.json`. Not an official MBTA product.
