# Blockers

Format: blocker — what was tried — fallback used / status.

1. **`SPEC.md` did not exist** — found `TRANSIT_SPEC.md` — used it as the spec (status: resolved, see DECISIONS).
2. **First detached recorder instance died silently** when its launching `uv run` process exited (Windows job object) — relaunched; added `CREATE_BREAKAWAY_FROM_JOB` + PID-liveness check in `tasks.py recorder-status` — recorder (PID 40620) has run continuously since 15:01 CST (status: resolved; gap of ~45 s logged in `var/recorder_gaps.log`).
3. **Host ports 5432, 55432, 10000, 3000 already used by other local projects** — did not touch them — Postgres on 15433, Azurite on 10010, web on 3100 (status: resolved).
4. **Models pickled under `__main__` were unloadable from the API** — moved model classes to `ml/gbml/models.py` and retrained (status: resolved).
5. **Next.js 16 build failed: `useParams()` outside `<Suspense>`** — wrapped page content in `<Suspense>` in the root layout (status: resolved).
6. **Playwright bundled browser version not installed** — avoided a download; tests run on the system Edge channel (`PW_CHANNEL` to override) (status: resolved).
7. **Model B signal is weak** — pre-4 AM features beat the baselines only narrowly on Brier (0.01748 vs overall-rate 0.01757 and rolling-28d 0.01767; PR-AUC 0.034 vs 0.018 base rate) — reported honestly; more features in Phase B (status: open, not blocking).
8. **CI has no lake data** (raw data is never committed) — data-dependent tests/evals skip in CI; full suite + `make eval` run locally (status: open, by design).
9. **Phase 7 (Azure prep) skipped** at the developer's request mid-run (status: deferred to the developer).
10. **Recorder killed without an error at 17:48 CST (23:48 UTC) on 2026-11-23**, most likely torn down with this session's background processes under memory pressure (the system was at 1.4–2.7 GB free). Gap 23:48–01:02 UTC (~74 min), logged in `var/recorder_gaps.log`; auto-restarted by `tasks.py recorder-status`. A user-level scheduled-task watchdog was proposed but not created (needs your approval) — run it yourself: `schtasks /Create /TN GhostBusRecorderWatchdog /SC MINUTE /MO 5 /TR "\"C:\Users\tusha\Documents\Project\ghostbus\.venv\Scripts\pythonw.exe\" \"C:\Users\tusha\Documents\Project\ghostbus\tasks.py\" recorder-status" /F` (remove with `schtasks /Delete /TN GhostBusRecorderWatchdog /F`) (status: open).
