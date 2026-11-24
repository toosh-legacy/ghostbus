# Decisions

One line each: decision — reason — alternative considered.

- Spec file is `TRANSIT_SPEC.md` (no `SPEC.md` existed); treated it as the source of truth and left the name as-is — avoid duplicating an 84 KB file — copy/rename to `SPEC.md`.
- Mode = full (Docker daemon responds), but lite adapters are the default runtime config overnight — fewer moving parts that can fail unattended; full adapters implemented behind the same interfaces and tested with the `full` marker — run everything on Postgres/Redis by default.
- Python 3.12 via `uv python pin 3.12` (system Python is 3.13) — spec convention is 3.12 — use 3.13.
- Single root `pyproject.toml` (hatchling, one wheel with multiple top-level packages `gbcore`, `gbagency`, `gbstream`, `gbapi`, `gbdata`, `gbsim`, `gbml`, `gbcopilot`, `gbevals`, `gbschemas`) instead of one uv workspace member per directory — one lockfile, one venv, much less boilerplate, same directory layout as section 19 — a full uv workspace with 10 member projects.
- Recorder launched via `tasks.py record` as a detached process (DETACHED_PROCESS + CREATE_BREAKAWAY_FROM_JOB) — must outlive the shell/uv job object on Windows — Windows service / scheduled task (needs admin).
- Recorder liveness = heartbeat age < 150 s AND `stopped` flag false AND PID alive — a stale heartbeat from a clean exit fooled the first check — heartbeat age only.
- Postgres published on host port 15433 and Azurite on 10010 (5432, 55432, 10000 are used by other projects' containers on this machine) — avoid conflicting with an existing local DB — stop the other service.
- Push to GitHub (origin/main) after every commit — user instruction mid-run overrides the spec's 'never push' — commit locally only.
- D2 year = 2025 (12 complete months published); loaded the most recent 6 months (Jul–Dec 2025) — spec scope — more months (disk/time allow it; Phase B).
- D2 `scheduled`/`actual` are treated as local wall-clock on a dummy 1900-01-01/02 date (day 02 = after midnight, same service date) — confirmed by profiling (route 1 first trip 04:37) — treat as true UTC.
- D2 `half_trip_id` is an MBTA-internal ID, not GTFS trip_id; a historical missed trip = a D2 half-trip whose rows all have null `actual` — D2 lists scheduled rows even when unobserved, and D2 half-trip counts match GTFS scheduled counts (median ratio 1.00) — match D2 to GTFS trips by time (fragile).
- Completeness per route-day = D2 half-trips with ≥1 observed event / GTFS scheduled trips (feed in effect that date, from the archive) — spec 9.0 D2 definition, join-free — per-trip matching.
- Pilot routes = top 10 by observed events Jul–Dec 2025: 111, SL3 (743), 23, 28, SL1 (741), 1, 39, 109, 66, 77; Silver Line names mapped to GTFS ids in the MBTA adapter — spec rule — hand-picked routes.
- Archived GTFS kept as zips under lake/raw/gtfs/archive and read directly (no unzip) — saves ~1 GB of extracted text — unzip per spec wording.
- Static network (shapes, stop-on-shape) built from the current GTFS feed only; historical expansion uses each date's archived feed for scheduled-trip counts — live/sim only need current geometry — geometry per archive version.
- Exact-duplicate D2 events deduplicated on (date, route, half_trip, timepoint, order, point_type, scheduled) — spec 'duplicate events' — keep all.
- Ordering = per-vehicle reorder buffer released at watermark − 60 s (vehicle watermark or global event clock); events older than the last released event are dropped as late — gives "within lateness ⇒ same as sorted" exactly — apply-immediately with no reordering.
- Map positions update immediately (latest by event time); matching/arrivals/curated outputs use the ordered stream (≤ 60 s behind) — satisfies both "update within 2 s" and ordered processing — single path with 60 s display lag.
- Failed fetch attempts are events too (`FeedFetchEvent(ok=False)`, from the recorder manifest during replay) — event-time clock must keep ticking during outages so ghost state becomes Unknown — wall-clock timer (breaks determinism).
- Ghost scoring on the simulator uses each trip's final state (last evaluation before scheduled end + 10 min); outage gate checks evaluations from outage start + 2 min (the staleness threshold) to outage end — the rule cannot fire earlier by definition — whole window.
- Map matching uses shapely STRtree over segment bounding boxes + vectorized projection; small backward jitter keeps the raw projection, arrival detection uses a running max — unbiased distance error — clamping (biases forward).
- Simulator truth arrival = first time true distance reaches stop − 15 m (same definition as the detector) — measures detector error, not definition mismatch — stop-position arrival time.
- OTP startpoints use the same −60/+360 s window as other points (spec only states the early bound for startpoints) — symmetric, conservative; flagged for developer review — early bound only.
- Metrics computed over the whole route-month-daytype-band group with GROUPING SETS rollups ('all'); EWT computed from pooled headways of the group — simple, matches formula — per-stop EWT then averaged.
- Missed-trip time band = band of the half-trip's first scheduled timepoint — trips span bands — per-event band.
- Copilot views are materialized tables in var/analytics.duckdb (not views over Parquet) — lets the copilot connection run read_only with enable_external_access=false — views over files.
- Hive partition values read with hive_types_autocast=false — numeric route ids were silently cast to integers — explicit casts per query.
- API "now" = processor event clock (falls back to wall clock with no events) — replay and live give identical stop pages — wall clock.
- Stop page "Not yet tracking" + elevated risk come from the ghost rules; when feed is stale the API forces ghost state unknown and drops live delay (schedule + historical range) — spec F2 AC3.
- Request logging middleware logs method + path only (never query strings) and uvicorn access log is disabled — nearby-stop coordinates are never logged (spec 15).
- Model A samples: 12% of trips (stable hash), horizons 1–5 timepoints, train capped at 400k rows per fold — fits the overnight time budget — all pairs (~12 M rows).
- Model A "recent segment delay" = median of (delay_j − delay_k) over the last 3 same-day trips whose segment completed strictly before t_pred (DuckDB ASOF join) — spec feature, leakage-safe by construction — per-stop recent delay.
- D2 pairs whose recorded actual at j is not after the actual at k are dropped from Model A samples — out-of-order records in the source; a target cannot precede the prediction — keep them.
- 20% of training rows have live-only features (recent segment, headway, delay change) blanked — those are often unavailable when serving from GTFS-RT — train only on complete rows.
- Pinned Model A = the last backtest fold's model (trained through 2025-12-10, calibrated on the following week) — its coverage/MAE are measured on held-out weeks — refit on all data without a held-out check.
- Model B chosen by mean Brier across folds (HGB + isotonic); it beats the rolling 28-day baseline but only ties the overall-rate baseline — signal from pre-4 AM features is weak; reported honestly — tune more features.
- Model classes live in gbml/models.py — the first artifacts pickled under `__main__` were unloadable from the API — custom unpickler.
- Web served on port 3100 (3000 is used by another local app); Playwright uses the system Edge channel — no browser download needed — bundled Chromium download.
- Ghost evaluation is 'unknown' for the first 3 minutes of feed after a pipeline (re)start — the 60 s reorder buffer otherwise makes every in-progress trip look unmatched — no warm-up (false High after restarts).
- Node is available so the Next.js app is the primary UI; the FastAPI /demo page (lite fallback) is not built — spec makes /demo the no-Node fallback only — build both.
- ghost-def bumped to v1.1: a would-be High trip is Elevated when a vehicle on the same route/direction runs its claimed trip > 10 min early and departed within 15 min of this trip's schedule (likely wrong trip assignment) — the wrong_trip scenario produced false Highs under v1.0 (precision 0.78) — keep v1.0 and accept the misses.
- Terminal departure needs two consecutive pings beyond stop + 15 m — layover GPS noise fired false early departures — single-ping trigger.
- Recorded-hour determinism check uses a closed window ending 30 s before the newest snapshot — the live recorder appends during the eval — open window.
- CI has no lake data (raw data is never committed), so data-dependent tests/evals skip in CI; the fast eval subset runs fully locally — commit sample data.
- Phase 7 (Azure/Phase C prep: infra README, Bicep stubs, Azure adapter stubs) skipped at the developer's request mid-run — they will do the cloud work themselves.
- Live outputs mirrored to the operational DB every 60 s of event time (vehicle_latest upsert, arrival_observed append, latest ghost_eval per trip, prediction_log + delayed-label fill of observed_ts); full history stays in curated Parquet — `PERSIST_LIVE=0` disables — write every event to the DB (too chatty on SQLite).
- Stop-page source priority: our model (live vehicle with a known delay) > agency TripUpdates prediction (fetched within 2 min) > schedule + history; agency ranges reuse the full historical p10–p90 spread around the agency time — MBTA predictions carry no uncertainty — show agency point estimates.
- Model A predictions are batched per stop-page request (one model call) and `trips_on` is cached per service date — per-trip calls made the stop page ~0.5–4 s under load — per-trip calls.
- SSE route filtering moved into the Broadcaster (publish time) — with filtering after dequeue, ghost updates for all routes filled every bounded client queue and evicted vehicle updates (found by the load test) — larger queues.
- SSE load test runs clients in a separate process — clients sharing the server's event loop measured the harness (only 243/500 connected) — single-process test.
- Alerts recorded every 60 s (VehiclePositions/TripUpdates stay at 20 s) and shown on the stop page — alerts change rarely; politer polling — 20 s for all feeds.
- Copilot LLM provider = OpenAI (developer's choice, overriding the spec's "no API keys" for Phase B); optional and off by default (`COPILOT_PROVIDER=openai` + `OPENAI_API_KEY` in the git-ignored .env); only view schemas and the question are sent, never data rows; the model only writes SQL, which still passes the validator/read-only/limit/timeout path; without a key it falls back to offline — Ollama (llama3.2:3b, kept as an option but produced mostly invalid SQL in a spot check).
- Recorder status checks auto-restart a dead recorder; a scheduled-task watchdog was blocked by permissions and is left for the developer (see BLOCKERS #10).

## OpenAI copilot run and prompt tightening (2026-11-23)
First real run with `gpt-4o-mini`: 82.5% gold / 90% refusals / 0 red-team failures. Failures were missing
`day_type`/`time_band = 'all'` filters, refusing monthly-trend questions, using `missed_trip_rate` on
`v_missed_trip_daily`, and mapping "Red Line" to SL1. Prompt now spells out those rules, marks subway/rail as
unanswerable, and adds two worked examples. Result: 95.0% gold / 100% refusals / 0 red-team failures; DB unchanged.
Cost of a full comparison run is about $0.01. The offline provider stays the default and the eval-gate provider.

## Copilot diagnostics follow-up (2026-11-23)
Repeated runs showed three failure causes: (1) "SL3" alone triggered refusals (prompt over-warned about Silver
Line ambiguity) — prompt now states SL1 = '741', SL3 = '743' are in the data; (2) missing day_type/time_band
filters on trend queries — fixed deterministically by `agent.default_band_filters` (sqlglot adds `= 'all'` for
whichever filter the query never mentions; the validator still checks the result); (3) gpt-4o-mini treats 2025
months as "future" and refuses them, while sometimes answering real forecast questions with past data — prompt
now says every listed month is past, and forecast questions are refused by a regex before any LLM call. Requests
carry `seed: 7` for best-effort repeatability. Result over 4 runs: 97.5–100% gold, 100% refusals, 0 red-team
failures. Known flaky item: "bunching rate of route 39 in the evening in December 2025" is sometimes refused.
