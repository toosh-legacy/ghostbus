# Changelog

## Phase 0 — scaffold + recorder
- Repo layout (spec 19), uv project on Python 3.12, Makefile + tasks.py, .env.example, CLAUDE.md, CI workflow.
- Live GTFS-RT recorder (D3) running detached: 20 s polling, User-Agent, If-Modified-Since, backoff, heartbeat.
- docker-compose full-mode stack (PostGIS on 15433, Redis 6379, Azurite 10010).

## Phase 1 — static and historical data
- D1 current feed + 59 archived feeds (Jul–Dec 2025) with hashes in data/manifest.json; calendar expansion; stop-on-shape (0 stops > 50 m off shape).
- D2 2025 (real, CC0): 3.85 M pilot-route events, Parquet partitioned by route/month; completeness report data/reports/arrdep_profile.md; pilot routes in config/pilot_routes.yaml.

## Phase 2 — simulator + stream processor
- Simulator (6 seeded scenarios, all imperfections, exact ground truth), decoder, dedupe + watermark ordering, STRtree map matching, arrival detection, ghost-def v1.0, live state + broadcaster, replay engine (any speed, event time), curated writers, Redis Streams/live-state full-mode adapters.
- All six scenarios pass map-matching (median ≈ 6 m), arrival (median ≈ 4 s, p90 ≈ 16–24 s) and ghost gates (P = R = 1.0).
