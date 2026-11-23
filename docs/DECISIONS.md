# Decisions

One line each: decision — reason — alternative considered.

- Spec file is `TRANSIT_SPEC.md` (no `SPEC.md` existed); treated it as the source of truth and left the name as-is — avoid duplicating an 84 KB file — copy/rename to `SPEC.md`.
- Mode = full (Docker daemon responds), but lite adapters are the default runtime config overnight — fewer moving parts that can fail unattended; full adapters implemented behind the same interfaces and tested with the `full` marker — run everything on Postgres/Redis by default.
- Python 3.12 via `uv python pin 3.12` (system Python is 3.13) — spec convention is 3.12 — use 3.13.
- Single root `pyproject.toml` (hatchling, one wheel with multiple top-level packages `gbcore`, `gbagency`, `gbstream`, `gbapi`, `gbdata`, `gbsim`, `gbml`, `gbcopilot`, `gbevals`, `gbschemas`) instead of one uv workspace member per directory — one lockfile, one venv, much less boilerplate, same directory layout as section 19 — a full uv workspace with 10 member projects.
- Recorder launched via `tasks.py record` as a detached process (DETACHED_PROCESS + CREATE_BREAKAWAY_FROM_JOB) — must outlive the shell/uv job object on Windows — Windows service / scheduled task (needs admin).
- Recorder liveness = heartbeat age < 150 s AND `stopped` flag false AND PID alive — a stale heartbeat from a clean exit fooled the first check — heartbeat age only.
- Postgres published on host port 15433 and Azurite on 10010 (5432, 55432, 10000 are used by other projects' containers on this machine) — avoid conflicting with an existing local DB — stop the other service.
