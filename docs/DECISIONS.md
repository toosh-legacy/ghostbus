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
