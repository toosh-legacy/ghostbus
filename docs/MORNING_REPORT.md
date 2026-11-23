# Morning report — GhostBus Phase A (run 2026-11-23, 14:56–~16:20 CST)

## 1. Mode used and what works

- **Mode: full (Docker available)** with lite adapters as the default runtime (memory bus, memory live state, SQLite).
  Full-mode adapters (Redis Streams bus, Redis live state, Postgres via `DATABASE_URL`) are implemented; Redis Streams
  round-trip test runs against the compose Redis. PostGIS (:15433) and Redis (:6379) containers are up.
- Everything ran far ahead of the timeboxes (Phases 0–6 done in ~70 min), so all P0 and almost all P1 tasks are done.
- **Phase 7 (Azure prep) was skipped at your request** — the cloud part is yours.
- Commands: `make data` · `make train` · `make eval` · `make test` · `make demo` · `cd apps/web && npm run test:e2e`.
- **Demo URLs:** API http://127.0.0.1:8000 (OpenAPI at `/docs`), web app **http://127.0.0.1:3100** (port 3000 is used
  by another app on this machine). `make demo` replays the recorded feed at 10× (simulator if nothing is recorded).
- **Recorder: still running** (PID 40620, started 15:01 CST, polls every 20 s with If-Modified-Since). By 16:10 it had
  stored 171 VehiclePositions + 171 TripUpdates snapshots (~72 MB), 0 fetch errors. Check: `make recorder-status`.
  It was recording during the Boston afternoon/evening peak, so the replay has real service. One ~45 s gap at startup
  (logged in `var/recorder_gaps.log`).
- Commits were pushed to `origin/main` after each phase (you asked for pushes mid-run).

## 2. Phase checklist

| Phase | P0 | P1 |
|---|---|---|
| 0 Scaffold + recorder | done (env detection, layout, uv, Makefile + tasks.py, .env.example, CLAUDE.md, CI, docs, recorder + heartbeat) | — |
| 1 Static + historical data | done (current GTFS + 59 archived feeds, calendar expansion, stop-on-shape, D2 2025 real data, completeness, pilot routes, manifest, data tests) | profile charts: skipped · Alerts feed: skipped |
| 2 Simulator + stream processor | done (simulator with all imperfections, bus interface + memory/Redis adapters, decoder, dedupe/watermark, map matching, arrivals, live state, SSE broadcaster, ghost rules, replay, curated writers, tests) | — |
| 3 Metrics + API | done (metrics, missed-trip labels w/ unknown, DuckDB views, SQLAlchemy + Alembic, all endpoints, fallback ranges, OpenAPI export) | API keys + quotas: done · CSV export: done |
| 4 ML | done (Model A + leakage test + 3 baselines + quantile GB + CQR + 3-fold backtest; Model B + cutoff features + baselines + isotonic; model cards; registry + pinning; serving in stop page + ghost priors; prediction log + delayed-label join) | drift PSI: done (admin models view; reference sample from training) |
| 5 Frontend | done (Next.js `/`, `/stops/[id]`, `/routes/[id]` with SSE map + list, `/reliability`, `/methodology`, generated client, Playwright + axe happy path) | `/reliability/[routeId]`: done · `/admin`: done · PWA manifest: done (no service worker) |
| 6 Copilot + evals | done (validator, offline provider, `/v1/copilot/ask` + `/ask`, gold set from data, red team, `make eval` for every suite, CI fast subset) | — |
| 7 Phase C prep | **skipped (your request)** | — |
| 8 Morning report | done | — |

## 3. Key metrics

**Evaluation gates (`evals/reports/latest.md`): 12 / 12 pass**

| Suite | Value | Gate |
|---|---|---|
| Map matching (worst scenario median) | 7.0 m | ≤ 25 m |
| Arrival detection (worst median / p90) | 4.7 s / 22.6 s | ≤ 20 s / ≤ 60 s |
| Ghost rules (min precision / recall of High, 6 scenarios) | 1.000 / 1.000 | ≥ 0.9 |
| Unknown during outage window | 100% | 100% |
| Replay determinism 1× vs 20× (sim hour + recorded hour) | identical, 0 dropped | identical |
| Model A p50 MAE vs best baseline (propagation) | 95.6 s vs 139.9 s (31.6% better) | beats |
| Model A 80% interval coverage (CQR) | 79.5% (raw 76.6%) | 78–82% |
| Model B Brier vs rolling 28-day baseline | 0.01748 vs 0.01767 (PR-AUC 0.034 vs 0.023) | beats |
| Copilot accuracy (40 gold) | 100% | ≥ 90% |
| Copilot refusals (10) | 100% | 100% |
| Copilot red team (25 prompts + 20 SQL payloads) | 0 failures, DB unchanged, cross join cancelled at 5 s | 0 |
| Privacy (coordinates in logs) | 0 | 0 |

Other: stop-page API p95 < 300 ms (tested); 94 pytest tests incl. Hypothesis properties, all passing; e2e + axe
passes on 5 pages (mobile viewport).

**ML (`ml/reports/latest.md`)** — rolling-origin backtest, test windows 2025-11-20 → 2025-12-31:

| fold | test window | p50 MAE | schedule | propagation | hist. median | coverage raw | coverage (CQR) | width |
|---|---|---|---|---|---|---|---|---|
| 1 | 2025-11-20–2025-12-03 | 95.1 | 266.2 | 136.2 | 249.3 | 76.4% | 78.6% | 288 |
| 2 | 2025-12-04–2025-12-17 | 95.2 | 286.6 | 134.4 | 245.1 | 77.5% | 82.2% | 308 |
| 3 | 2025-12-18–2025-12-31 | 96.6 | 263.1 | 149.1 | 269.5 | 75.8% | 77.7% | 295 |

Model B: HGB + isotonic, Brier 0.01748 vs overall-rate 0.01757 and rolling-28d 0.01767; ROC-AUC 0.58, PR-AUC 0.034
(base rate ≈ 0.018). It beats both baselines, but only narrowly — the signal from pre-4 AM features is weak (Blockers #7).

## 4. Data status

| Dataset | Real / synthetic | Rows | Months | Hash (sha256, first 16) |
|---|---|---|---|---|
| D1 MBTA GTFS current | real | 10 pilot routes: 15,886 trips, 522 stops, 52 shapes | feed of 2026-11-19 | 04cd2571fed0494c |
| D1 GTFS archive | real | 59 feeds → 396,625 scheduled pilot trips | Jul–Dec 2025 | per feed in `data/manifest.json` |
| D2 Bus Arrival Departure Times 2025 | **real** (CC0) | 3,850,520 pilot-route events | 2025-07 … 2025-12 | eae5344638e61319 |
| D3 GTFS-RT recorded | real | 171 + 171 snapshots (growing) | 2026-11-23 → now | per snapshot in `lake/raw/manifest.jsonl` |
| D4 simulator | synthetic (by design) | 6 seeded scenarios, 184 trips each | — | seeds 101–106 |
| D5 gold sets | generated from data | 40 + 10 copilot, 25 + 20 red team | — | `evals/gold/` |

Completeness: 1,840 route-days, **0 below 70%** (mean 97%); D2 half-trip counts match GTFS scheduled counts (median
ratio 1.00). `GROUND_TRUTH_SOURCE=real` — no synthetic fallback was needed.
Pilot routes: 111, SL3 (743), 23, 28, SL1 (741), 1, 39, 109, 66, 77 (`config/pilot_routes.yaml`).

## 5. Blockers (all have fallbacks; details in `docs/BLOCKERS.md`)

- Ports 5432/55432/10000/3000 used by other projects → remapped (15433/10010/3100).
- First recorder instance killed by the uv job object → breakaway flag + liveness check; running since 15:01.
- Model pickles under `__main__` → classes moved to `gbml/models.py`, retrained.
- Next 16 `useParams` prerender error → `<Suspense>` in layout. Playwright browser → system Edge channel.
- Model B gains over baselines are small (open). CI has no lake data, so data-dependent tests skip in CI (by design).

## 6. Decisions made

All in `docs/DECISIONS.md` (~35 one-liners). The most important:
- D2 `half_trip_id` is not a GTFS trip id → missed trip = half-trip with no observed events; completeness = observed
  D2 trips / GTFS scheduled trips for the date (archive feed in effect).
- D2 times are local wall-clock on a dummy 1900-01-01/02 date (day 02 = after midnight, same service date).
- OTP startpoints use the same −1/+6 min window as other points.
- **ghost-def v1.1**: added a wrong-trip-assignment rule (High → Elevated) after v1.0 scored precision 0.78 on the
  wrong-trip scenario; stored results carry their version.
- 3-minute warm-up (Unknown) after each pipeline start; terminal departure needs 2 consecutive pings.
- Failed fetches are events, so the event-time clock ticks during outages (Unknown without a wall clock).

## 7. Top 10 next tasks

1. **Phase C (yours):** Bicep + azd, Azure adapters (Event Hubs, ADLS, ADX, Managed Redis, Web PubSub, Azure ML,
   Azure OpenAI, APIM), `infra/README.md` — Phase 7 prep was intentionally skipped.
2. Run the whole suite in full mode by default (Postgres/PostGIS + Redis Streams processor as a separate service);
   add PostGIS geometry columns + GiST nearby query.
3. When the MBTA publishes Nov 2026 arrival/departure data, compare detected arrivals from the recording to D2 actuals.
4. Improve Model B (weather/holiday/operator-shortage proxies, per-garage rates) — it beats the baselines only narrowly.
5. Persist arrivals / ghost evaluations / prediction log to the operational DB (today: curated Parquet + memory).
6. Agency trip-update predictions as a third `source` on the stop page.
7. Service worker for the PWA and a mobile polish pass; 500-client SSE load test.
8. Optional Ollama provider for the copilot; compare against the offline provider on the gold + red-team sets.
9. More pilot routes / months; rerun backtests (`make data` + `make train`).
10. Rewrite the ADRs (`docs/adr/`) and methodology reasoning in your own words.

## 8. Review personally

- **Metric thresholds:** OTP −60 s / +360 s (also at startpoints), headway ±50%, bunching < 25%, completeness 70%
  (`gbdata/metrics.py` THRESHOLDS).
- **Ghost parameters (v1.1):** High after 5 min, Elevated within 10 min / TU without vehicle 3 min, feed stale 2 min,
  rate < 30%, vehicle fresh 10 min, warm-up 3 min, reassignment 10 min early / 15 min window (`gbstream/ghost.py`).
- **Missed-trip definition** using half-trips with all-null actuals (verify against MBTA's own missed-trip reporting).
- **Synthetic data:** none used for ground truth; the simulator is used only for algorithm gates and the `sim` demo mode.
