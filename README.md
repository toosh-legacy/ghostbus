# GhostBus

Real-time bus reliability platform for MBTA buses: **ghost-bus detection**, **calibrated arrival ranges**, **published
reliability scores** and a **LangGraph copilot** that answers reliability questions through an MCP tool server, with
every query validated read-only. Python (FastAPI, DuckDB, LightGBM, LangChain/LangGraph), Next.js (Vercel AI SDK,
MapLibre + deck.gl). Built locally behind interfaces; the Azure deployment swaps adapters by configuration.

Spec: [`TRANSIT_SPEC.md`](TRANSIT_SPEC.md) · Overnight results: [`docs/MORNING_REPORT.md`](docs/MORNING_REPORT.md)

## Quick start (Windows, no WSL needed)

```bash
uv sync                       # Python 3.12 env
make record                   # start the polite live recorder (detached; heartbeat in var/)
make recorder-status          # check / restart it
make data                     # D1 GTFS (+archive) + D2 arrival/departure 2025 -> Parquet lake, metrics, analytics DB
make train                    # LightGBM + Optuna + MAPIE, 3-fold backtest, MLflow registry champion alias
make eval                     # every gate -> evals/reports/latest.md  (make eval-live: real OpenAI models, $3 cap)
make test                     # pytest (unit, property-based, integration, API, data)
make demo                     # API :8000 (replay of recorded data) + web :3100
cd apps/web && npm run test:e2e   # Playwright + axe happy path (needs `make demo` running)
```
No `make`? Use `uv run python tasks.py <target>`. Full-mode services: `make up` (PostGIS :15433, Redis :6379, Azurite :10010),
then `MODE=full STREAM_BUS=redis LIVE_STATE=redis DATABASE_URL=postgresql+psycopg://ghostbus:ghostbus@localhost:15433/ghostbus`.

## What's inside

| Path | What |
|---|---|
| `services/stream/gbstream` | APScheduler recorder, GTFS-RT decoder, dedupe + watermark ordering, STRtree map matching, arrival detection, ghost rules (ghost-def v1.1), replay engine |
| `sim/gbsim` | simulator with exact ground truth (6 seeded scenarios) and scorer |
| `data/pipeline/gbdata` | GTFS current + archive, D2 arrival/departure pipeline (DuckDB over Parquet), completeness, reliability metrics, analytics DB |
| `ml/gbml` | Model A (LightGBM quantile + Optuna + MAPIE CQR), Model B (LightGBM / logistic, isotonic), MLflow tracking + registry, Evidently drift, Polars features |
| `copilot/gbcopilot` | LangGraph copilot graph, GhostBus MCP server + langchain-mcp-adapters client, methodology RAG (BM25 + dense + rerank), daily-brief map-reduce graph, versioned prompts, cost guard, OpenTelemetry |
| `services/api/gbapi` | FastAPI `/v1` (stops, arrivals, live routes, SSE via sse-starlette, reliability, CSV, methodology, copilot/brief jobs, admin), API keys + quotas |
| `apps/web` | Next.js: stop pages, MapLibre + deck.gl live map, Recharts reliability, AI SDK streaming copilot chat, shadcn/ui |
| `evals/` | gold set, red team, RAG ablation, live eval with LLM judge, promptfoo suite, Locust load test, LangSmith experiments |
| `docs/` | decisions, blockers, environment, changelog, methodology, threat model, ADRs |

## Headline numbers (see reports)

- Arrival model: p50 MAE **94.0 s** vs 139.9 s for the best baseline (32.8% better); 80% interval coverage **79.4%** after MAPIE conformal calibration (`ml/reports/latest.md`).
- Simulator: map matching worst-scenario median 7.0 m; arrival detection median 4.7 s / p90 22.6 s; ghost precision = recall = 1.0 in 6 scenarios; 100% Unknown during outages; replay identical at 1x and 20x.
- Copilot: offline graph 40/40 gold, 10/10 refusals, 0 failures across 54 red-team cases (prompts, SQL, MCP tool attacks); gpt-4o-mini/gpt-4o 97.5% gold with 0 red-team failures (`evals/reports/live.md`).
- Methodology RAG: recall@3 0.812 (hybrid) → 0.938 (bge-reranker-base) → 0.969 (gpt-4o-mini rerank).
- Quality: mypy clean, promptfoo 47/47, Playwright + axe e2e, 0 coordinates in logs.

## Data & licenses
MBTA GTFS / GTFS-Realtime (MassDOT Developers License Agreement), MBTA Bus Arrival Departure Times (CC0), map tiles ©
OpenStreetMap contributors. Raw data is never committed; hashes are in `data/manifest.json`. Not an official MBTA product.

## MCP server: connect from any MCP client

GhostBus ships a read-only MCP server (official Python MCP SDK) with four tools: `get_route_metrics`,
`get_missed_trip_rates`, `compare_routes` and `get_definition`. Arguments are checked against fixed vocabularies and
every query goes through the same sqlglot validator, allowlist, read-only DuckDB connection, 500-row limit and 5 s
timeout as the copilot. The LangGraph copilot itself calls these tools through `langchain-mcp-adapters`.

Run it over stdio:

```
uv run python -m gbcopilot.mcp_server
```

**Claude Desktop / Claude Code / Cursor** (`mcpServers` entry; use your absolute repo path):

```json
{
  "mcpServers": {
    "ghostbus": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/ghostbus", "run", "python", "-m", "gbcopilot.mcp_server"]
    }
  }
}
```

Claude Code: `claude mcp add ghostbus -- uv --directory C:/path/to/ghostbus run python -m gbcopilot.mcp_server`.

**MCP Inspector:** `npx @modelcontextprotocol/inspector uv run python -m gbcopilot.mcp_server`.

**From Python (LangChain):**

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"ghostbus": {"command": "uv", "args": ["run", "python", "-m", "gbcopilot.mcp_server"],
                                            "transport": "stdio"}})
tools = await client.get_tools()
```

Every tool is annotated `readOnlyHint: true`, `openWorldHint: false`. Contract tests: `tests/test_copilot_graph.py`
(tool list and schemas, happy paths, rejected arguments, parity with direct SQL).

