# CLAUDE.md — GhostBus

Source of truth: `TRANSIT_SPEC.md` (the build spec). If code and spec disagree, follow the spec; if the spec is
ambiguous, choose the simplest option meeting the acceptance criteria and log it in `docs/DECISIONS.md`.

## Working rules (spec 0.2)
- Unattended runs never stop to ask: log blockers in `docs/BLOCKERS.md`, use the documented fallback, continue.
- Local only in Phases A/B: no `az`/`azd`, no cloud resources, no paid APIs, no API keys.
- Network: package registries, the dataset/feed URLs in spec section 9, light map tiles only.
- MBTA feeds: poll ≥ 15 s apart (recorder uses 20 s), descriptive User-Agent, exponential backoff.
- Every phase ends with passing tests, a Conventional Commit (local only, never push), a `docs/CHANGELOG.md` entry.
- Check the recorder at every phase start: `uv run python tasks.py recorder-status` (restarts it if down, logs gap).

## Environment (spec 0.5)
- Windows 11, no WSL; Docker works (full-mode stack in `docker-compose.yml`, Postgres on 15433, Redis on 6379).
- Default runtime = lite adapters (memory bus, memory live state, SQLite). Full adapters selected by env vars.
- Python 3.12 via uv. Run anything with `uv run ...`. `make <target>` or `uv run python tasks.py <target>`.

## Layout (spec 19)
`packages/core/gbcore` settings, interfaces, time/service-day, geometry · `packages/agency/gbagency` MBTA adapter ·
`services/stream/gbstream` recorder, decoder, processor, replay · `services/api/gbapi` FastAPI + /demo ·
`data/pipeline/gbdata` GTFS + arrdep pipeline · `sim/gbsim` simulator · `ml/gbml` models · `copilot/gbcopilot` ·
`evals/gbevals` · `apps/web` Next.js · `infra/` Phase C stubs · `lake/` data (git-ignored) · `var/` runtime state.

## Hard rules (spec 22)
1. Local only. 2. Poll politely. 3. Never store rider location/identity; nearby coordinates never logged.
4. Event time, never wall clock, in processing; live == replay. 5. Time-based ML splits; no future leakage.
6. Missing data is "unknown", never "missed". 7. Definitions versioned (`ghost-def v1.0`); results keep version.
8. Copilot read-only + allowlisted views; no write/fetch/file tools. 9. Attribute sources; never commit raw data.
10. Every external dependency behind an interface with a local adapter. 11. Behaviour changes ship with tests.
12. Don't stop to ask.
