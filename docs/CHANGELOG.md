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

## Phase 3 — metrics + API
- Reliability metrics (OTP, headway adherence, EWT, bunching, missed-trip rate) by route/month/day type/time band; historical missed-trip labels with unknown handling; DuckDB analytics DB with allowlisted views.
- Operational DB (SQLAlchemy + Alembic, SQLite lite / Postgres full), seeded with the static network and manifest.
- FastAPI: all section-13 endpoints incl. SSE stream, CSV export, methodology, admin views, problem+json errors; OpenAPI exported to packages/schemas/openapi.json.

## Phase 4 — machine learning
- Model A: quantile HistGradientBoosting (p10/p50/p90) + conformalized quantile regression; 3-fold rolling-origin backtest: p50 MAE 95.7 s vs best baseline (propagation) 139.9 s (31.6% better); 80% coverage 79.3%.
- Model B: missed-trip probability (HGB + isotonic): Brier 0.01757 vs rolling-28d baseline 0.01767 (beats), ROC-AUC 0.60, PR-AUC 0.036 vs base rate ~0.018.
- ModelRegistry with pinned versions and the 1% promotion rule; serving in the stop page (Model A) and ghost priors (Model B); delayed-label join + PSI utilities; leakage tests.

## Phase 5 — frontend
- Next.js 16 app: /, /stops/[id], /routes/[id] (Leaflet + SSE + list alternative), /reliability (+ route detail, CSV), /methodology, /ask, /admin; PWA manifest; typed client generated from OpenAPI.
- Playwright + axe happy path (mobile viewport) passes on 5 pages; `make demo` serves API :8000 + web :3100 with a replay of recorded data.
- Ghost rules get a 3-minute warm-up (unknown) after each pipeline start so restarts never produce false ghosts.

## Phase 6 — copilot + evaluation
- SQL validator (sqlglot: single SELECT, allowlisted views only, no table functions/file access/PRAGMA/ATTACH, comments stripped, LIMIT 500, 5 s timeout, read-only connection with external access disabled), offline intent-template provider, /v1/copilot/ask + /ask page.
- Gold set (40 + 10 unanswerable, generated from data) and red team (25 prompts + 20 SQL payloads); `make eval` writes evals/reports/latest.md — 12/12 gates pass.
- ghost-def v1.1 (wrong-trip reassignment rule) and confirmed terminal departure detection.

## Wrap-up
- Public API keys (hashed) + per-key/anonymous token-bucket quotas with RateLimit headers; drift PSI in /admin/v1/models; models retrained and re-pinned via the promotion rule (Model A 95.6 s MAE, 79.5% coverage; Model B Brier 0.01748).
- docs: methodology.md, threat-model.md, ADR stubs, BLOCKERS, README, MORNING_REPORT. Phase 7 (Azure) skipped at the developer's request.

## Follow-ups (after the overnight run)
- Operational DB sink for live outputs (vehicles, arrivals, ghost evaluations, prediction log with delayed labels).
- Agency TripUpdates predictions as the third stop-page source.
- Batched Model A inference + cached service-day trips (stop page back under 300 ms p95).
- Alerts feed recorded (60 s) + `/v1/alerts` + stop-page alert list; recorder restarted 23:15 UTC to add it (gap logged).
- PWA service worker (app shell only; live data never cached).
- SSE fan-out fix (route filter at publish time) + 500-client load test: all 500 clients received all 154 vehicle updates; 20x replay kept pace (89.6 s vs 90 s).
- Copilot LLM providers: OpenAI (optional, key from .env) and Ollama (optional), sharing one prompt + SQL extractor; model-written SQL is still validated; provider comparison script `python -m gbevals.compare_providers`.

## Copilot OpenAI provider run
- Tighter LLM SQL prompt (filters, trends, subway refusals, few-shot examples); gpt-4o-mini scores 95% gold, 100% refusals, 0 red-team failures (evals/reports/provider_comparison.md).
- Copilot diagnostics: SL alias rule, deterministic day_type/time_band default filters, forecast refusal before LLM call, OpenAI seed; gpt-4o-mini 97.5-100% gold, 100% refusals, 0 red-team failures.

## Copilot on LangChain/LangGraph + MCP
- LangGraph copilot graph (structured outputs, retries, SqliteSaver memory), MCP server + langchain-mcp-adapters, methodology RAG with hybrid retrieval and bge/LLM reranking + ablation, LangGraph daily brief (Send map-reduce), versioned prompts with eval lock, cost guard ($3/session), call log, OpenTelemetry spans, optional LangSmith, background jobs + SSE (sse-starlette), eval-live with LLM judge.
