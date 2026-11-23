# Environment (detected 2026-11-23 14:57 CST, start of the overnight run)

| Check | Result |
|---|---|
| OS | Windows 11 Pro 10.0.26200 (x86_64), Git Bash (MINGW64) + PowerShell 5.1 |
| Inside WSL | No (repo lives on the Windows file system: `C:\Users\tusha\Documents\Project\ghostbus`) |
| docker | Docker 29.6.1, context `desktop-linux`; **daemon responds** (server 29.6.1, 12.3 GB memory) |
| docker compose | v5.2.0 |
| python | System `python` 3.13.7 (also 3.11, 3.10); **Python 3.12.12 available via uv** and pinned in `.python-version` |
| uv | 0.9.22 |
| node / npm | v22.19.0 / 11.6.2 |
| git | 2.51.0.windows.1 |
| make | GNU Make 4.4.1 |
| curl | 8.15.0 |
| CPU | 16 logical cores |
| Free disk (C:) | 229 GB free of 952 GB (well above the 15 GB needed) |

## Mode chosen: **full** (with lite adapters as the always-available fallback)

- Docker works, so the full-mode stack (`docker-compose.yml`: Postgres 16 + PostGIS, Redis, Azurite) is defined
  and full-mode adapters (Redis Streams bus, Redis live state, Postgres via `DATABASE_URL`) are implemented.
- Lite adapters (in-process asyncio bus, in-memory live state, SQLite `var/app.db`) are the **default runtime
  configuration** for the unattended run, because they have no moving parts that can die overnight
  (see `docs/DECISIONS.md`). Switching is configuration only: `MODE=full STREAM_BUS=redis LIVE_STATE=redis DATABASE_URL=...`.
- Tests marked `full` run when Docker services are reachable and are skipped (never deleted) otherwise.
- Windows without WSL: `pathlib` everywhere, Python task runner `tasks.py` mirrors the Makefile, the recorder runs
  as a detached background Python process that writes `var/recorder_heartbeat.json` every minute.
