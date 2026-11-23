"""GhostBus API (spec 13): REST + JSON under /v1, RFC 9457 problem details, SSE stream, admin views."""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from gbcore import definitions
from gbcore.geo import to_metric
from gbcore.settings import REPO_ROOT, get_settings
from gbcore.timeutil import AGENCY_TZ, service_date_for, service_day_reference
from gbstream.network import Network
from shapely import STRtree
from shapely.geometry import Point
from starlette.exceptions import HTTPException as StarletteHTTPException

from .pipeline import LivePipeline
from .predict import HistoricalPredictor, range_words

log = logging.getLogger("gbapi")
FEED_STALE_S = 120.0


class Problem(Exception):
    def __init__(self, status: int, title: str, detail: str = "", type_: str = "about:blank") -> None:
        self.status, self.title, self.detail, self.type = status, title, detail, type_


def problem_response(status: int, title: str, detail: str = "", type_: str = "about:blank") -> JSONResponse:
    return JSONResponse({"type": type_, "title": title, "status": status, "detail": detail}, status_code=status,
                        media_type="application/problem+json")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts and ts != float("-inf") else None


class AppContext:
    def __init__(self, pipeline_mode: str, speed: float, scenario: str) -> None:
        s = get_settings()
        self.settings = s
        self.net = Network()
        from gbdata.cli import load_pilot_routes

        self.routes = load_pilot_routes()
        self.route_set = set(self.routes)
        self.predictor = HistoricalPredictor()
        self.model_a = None
        self.model_b = None
        try:  # Phase 4 models (optional)
            from gbml.serving import load_pinned

            self.model_a, self.model_b = load_pinned()
            if self.model_a is not None:
                self.predictor = self.model_a.with_fallback(self.predictor)
        except Exception as e:  # noqa: BLE001
            log.info("models not loaded: %s", e)
        priors = self.model_b.trip_priors(self.net) if self.model_b is not None else {}
        self.pipeline = LivePipeline(self.net, self.route_set, pipeline_mode, speed, scenario, priors,
                                     persist=os.environ.get("PERSIST_LIVE", "1") == "1" and pipeline_mode != "off")
        stops = self.net.stops.reset_index()
        self.stops = stops
        self._stop_pts = [Point(x, y) for x, y in zip(stops["x"], stops["y"])]
        self._stop_tree = STRtree(self._stop_pts)
        st = self.net._st[["trip_id", "stop_id", "stop_sequence"]]
        self.stop_trips = {k: g for k, g in st.groupby("stop_id")}
        rts = self.net.trips_df[["trip_id", "route_id"]].merge(st, on="trip_id")
        self.stop_routes = rts.groupby("stop_id")["route_id"].agg(lambda x: sorted(set(x))).to_dict()
        self.metrics_dir = s.lake_dir / "curated" / "metrics"
        self.lake = duckdb.connect()
        self.prediction_log: list[dict] = []
        self.pipeline.prediction_log = self.prediction_log

    # ---- clock & health (event time)
    def now(self) -> float:
        c = self.pipeline.proc.clock
        return c if c != float("-inf") else time.time()

    def feed_health(self) -> dict:
        h = self.pipeline.proc.health()
        last = h["last_fetch_ts"]
        stale = last is None or (self.now() - last) > FEED_STALE_S
        if self.pipeline.status.mode == "tail" and last is not None:
            # in live tailing mode staleness is also judged against the wall clock
            stale = stale or (time.time() - last) > FEED_STALE_S
        return {"healthy": bool(h["healthy"]) and not stale, "stale": stale, "reason": h["reason"],
                "last_fetch_utc": _iso(last), "mode": self.pipeline.status.mode}


def create_app(pipeline_mode: str | None = None, speed: float | None = None, scenario: str | None = None) -> FastAPI:
    mode = pipeline_mode or os.environ.get("PIPELINE", "tail")
    spd = float(speed if speed is not None else os.environ.get("REPLAY_SPEED", "20"))
    scen = scenario or os.environ.get("SIM_SCENARIO", "normal")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx = AppContext(mode, spd, scen)
        app.state.ctx = ctx
        ctx.pipeline.start()
        yield
        await ctx.pipeline.stop()

    app = FastAPI(title="GhostBus API", version="0.1.0", lifespan=lifespan,
                  description="Real-time bus reliability: ghost-bus detection, calibrated arrival ranges and "
                              "reliability scores from MBTA open data.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    from .keys import ANON_PER_MIN, KeyStore

    keys = KeyStore()

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if not request.url.path.startswith("/v1/") or request.url.path == "/v1/stream":
            return await call_next(request)
        raw = request.headers.get("x-api-key")
        if raw:
            hit = keys.lookup(raw)
            if hit is None:
                return problem_response(401, "Invalid API key", "The X-Api-Key header is not recognised.")
            bucket, quota = hit
        else:
            bucket, quota = f"anon:{request.client.host if request.client else 'x'}", ANON_PER_MIN
        ok, remaining = keys.check(bucket, quota)
        if not ok:
            r = problem_response(429, "Rate limit exceeded", f"Quota is {quota} requests per minute.")
            r.headers["Retry-After"] = "1"
        else:
            r = await call_next(request)
        r.headers["RateLimit-Limit"] = str(quota)
        r.headers["RateLimit-Remaining"] = str(max(0, remaining))
        return r

    @app.middleware("http")
    async def privacy_logging(request: Request, call_next):
        # Never log query strings (nearby-stop coordinates must not be logged).
        t0 = time.perf_counter()
        resp = await call_next(request)
        log.info("%s %s %s %.1fms", request.method, request.url.path, resp.status_code,
                 (time.perf_counter() - t0) * 1000)
        return resp

    @app.exception_handler(Problem)
    async def _problem(_req, exc: Problem):
        return problem_response(exc.status, exc.title, exc.detail, exc.type)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req, exc: StarletteHTTPException):
        return problem_response(exc.status_code, str(exc.detail), "")

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _val(_req, exc: RequestValidationError):
        return problem_response(422, "Validation error", json.dumps(exc.errors(), default=str)[:1000])

    def ctx(request: Request) -> AppContext:
        return request.app.state.ctx

    def envelope(c: AppContext, data, live: bool = False, **extra) -> dict:
        out = {"data": data, "data_as_of": _iso(c.now()) if live else _data_as_of(c), **extra}
        if live:
            out["feed_health"] = c.feed_health()
        return out

    def _data_as_of(c: AppContext) -> str | None:
        p = c.metrics_dir / "route_metrics.parquet"
        return datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat() if p.exists() else None

    def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
        if not x_admin_token or not hmac.compare_digest(x_admin_token, get_settings().admin_token):
            raise Problem(401, "Admin token required", "Send X-Admin-Token.")

    # ------------------------------------------------------------------ probes
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(c: AppContext = Depends(ctx)):
        from gbcore.db import engine
        from sqlalchemy import text

        checks = {}
        try:
            with engine().connect() as con:
                con.execute(text("SELECT 1"))
            checks["db"] = True
        except Exception:  # noqa: BLE001
            checks["db"] = False
        checks["metrics"] = (c.metrics_dir / "route_metrics.parquet").exists()
        checks["models"] = c.model_a is not None
        checks["feed"] = c.feed_health()["healthy"]
        ok = checks["db"] and checks["metrics"]
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    # ------------------------------------------------------------------ stops
    @app.get("/v1/stops/nearby")
    def stops_nearby(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                     radius_m: float = Query(500, gt=0, le=3000), c: AppContext = Depends(ctx)):
        x, y = to_metric(lon, lat)
        p = Point(float(x), float(y))
        idx = c._stop_tree.query(p.buffer(radius_m))
        rows = []
        for i in idx:
            d = c._stop_pts[i].distance(p)
            if d <= radius_m:
                r = c.stops.iloc[int(i)]
                rows.append({"stop_id": r["stop_id"], "name": r["stop_name"], "lat": r["stop_lat"],
                             "lon": r["stop_lon"], "distance_m": round(d), "routes": c.stop_routes.get(r["stop_id"], [])})
        rows.sort(key=lambda r: r["distance_m"])
        return envelope(c, rows[:30])

    @app.get("/v1/stops/search")
    def stops_search(q: str = Query(..., min_length=1, max_length=80), c: AppContext = Depends(ctx)):
        ql = q.strip().lower()
        st = c.stops
        if ql in c.route_set or ql.upper() in {"SL1", "SL3"}:
            rid = {"SL1": "741", "SL3": "743"}.get(ql.upper(), ql)
            ids = {s for s, rs in c.stop_routes.items() if rid in rs}
            hits = st[st["stop_id"].isin(ids)]
        else:
            hits = st[st["stop_name"].str.lower().str.contains(ql, regex=False) | (st["stop_id"] == q.strip())]
        rows = [{"stop_id": r.stop_id, "name": r.stop_name, "lat": r.stop_lat, "lon": r.stop_lon,
                 "routes": c.stop_routes.get(r.stop_id, [])} for r in hits.head(50).itertuples()]
        return envelope(c, rows)

    @app.get("/v1/stops/{stop_id}/arrivals")
    def stop_arrivals(stop_id: str, horizon_min: int = Query(60, ge=5, le=180), c: AppContext = Depends(ctx)):
        if stop_id not in c.stop_trips:
            raise Problem(404, "Stop not found", f"{stop_id} is not served by a pilot route")
        return envelope(c, {"stop": _stop_info(c, stop_id), "arrivals": upcoming(c, stop_id, horizon_min)}, live=True)

    def _stop_info(c: AppContext, stop_id: str) -> dict:
        r = c.net.stops.loc[stop_id]
        return {"stop_id": stop_id, "name": r["stop_name"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"]),
                "routes": c.stop_routes.get(stop_id, [])}

    def upcoming(c: AppContext, stop_id: str, horizon_min: int) -> list[dict]:
        now = c.now()
        health = c.feed_health()
        out = []
        sd = service_date_for(datetime.fromtimestamp(now, UTC))
        g = c.stop_trips[stop_id]
        cands: list[tuple] = []
        for d in sorted({sd, sd.fromordinal(sd.toordinal() - 1)}):
            active = set(c.net.trips_on(d, c.route_set))
            ref = service_day_reference(d).astimezone(UTC).timestamp()
            for tid, seq in zip(g["trip_id"], g["stop_sequence"]):
                if tid not in active:
                    continue
                p = c.net.pattern(tid)
                if p is None:
                    continue
                try:
                    i = p.stop_seqs.index(int(seq))
                except ValueError:
                    continue
                sched = ref + float(p.sched_arr_s[i])
                if not (now - 600 <= sched <= now + horizon_min * 60):
                    continue
                live = c.pipeline.proc.trip_live(tid)
                if live["last_stop_index"] is not None and live["last_stop_index"] >= i:
                    continue  # already served
                cur_delay = None
                if live["last_arrival_ts"] is not None:
                    k = live["last_stop_index"]
                    cur_delay = live["last_arrival_ts"] - (ref + float(p.sched_arr_s[k]))
                use_live = health["healthy"] and live["last_seen"] is not None
                local_hour = datetime.fromtimestamp(sched, AGENCY_TZ).hour
                cands.append((tid, p, d, sched, live, use_live, dict(
                    route_id=p.route_id, direction_id=p.direction_id, stop_id=stop_id, sched_ts=sched,
                    sched_local_hour=local_hour, current_delay_s=cur_delay if use_live else None, live=use_live,
                    pattern=p, stop_index=i, last_stop_index=live["last_stop_index"], ref=ref, now=now,
                    service_date=d)))
        if hasattr(c.predictor, "predict_many"):
            preds = c.predictor.predict_many([k for *_, k in cands])  # one batched model call per request
        else:
            preds = [c.predictor.predict(**k) for *_, k in cands]
        from .predict import Prediction

        for (tid, p, d, sched, live, use_live, _k), pred in zip(cands, preds):
            agency_ts = (live.get("agency_preds") or {}).get(stop_id) if health["healthy"] else None
            if pred.source != "model" and agency_ts is not None:
                # agency prediction: centre on it, keep the historical spread as the honest range
                q10, q50, q90 = pred.features.get("hist") or [-60.0, 60.0, 360.0]
                pred = Prediction(max(now, agency_ts + (q10 - q50)), max(now, agency_ts),
                                  max(now, agency_ts + (q90 - q50)), "agency", "mbta-tripupdates",
                                  {**pred.features, "agency_ts": agency_ts})
            if pred.p90_ts < now - 120:
                continue
            gkey = f"{tid}@{d.isoformat()}"
            ghost = c.pipeline.state.get_ghost(gkey)
            if not health["healthy"]:
                gstate = {"state": "unknown", "reason": "feed is stale or unhealthy; showing schedule plus "
                          "historical ranges", "def_version": definitions.GHOST_DEF_VERSION}
            elif ghost:
                gstate = {"state": ghost["state"], "reason": ghost["reason"], "def_version": ghost["def_version"]}
            else:
                gstate = {"state": "unknown", "reason": "not evaluated yet", "def_version": definitions.GHOST_DEF_VERSION}
            prior = c.pipeline.priors.get(tid)
            tracking = live["last_seen"] is not None and now - live["last_seen"] <= 600
            out.append({
                "trip_id": tid, "route_id": p.route_id, "direction_id": p.direction_id,
                "headsign": str(c.net._trip_meta.loc[tid].get("trip_headsign", "")),
                "scheduled_utc": _iso(sched),
                "predicted": {"p10_utc": _iso(pred.p10_ts), "p50_utc": _iso(pred.p50_ts), "p90_utc": _iso(pred.p90_ts),
                              "range_text": range_words(now, pred.p10_ts, pred.p90_ts), "confidence": 0.8},
                "source": pred.source,
                "model_version": pred.model_version,
                "ghost": {**gstate, "prior_prob": prior},
                "tracking": tracking,
                "status_text": "Tracking" if tracking else "Not yet tracking",
            })
            c.prediction_log.append({"trip_id": tid, "stop_id": stop_id, "made_at": now, "p10": pred.p10_ts,
                                     "p50": pred.p50_ts, "p90": pred.p90_ts, "model_version": pred.model_version,
                                     "features": pred.features})
        if len(c.prediction_log) > 50_000:
            del c.prediction_log[:10_000]
        out.sort(key=lambda r: r["predicted"]["p50_utc"])
        return out

    # ------------------------------------------------------------------ routes
    @app.get("/v1/routes")
    def routes(c: AppContext = Depends(ctx)):
        r = c.net.routes
        rows = [{"route_id": x.route_id, "short_name": x.route_short_name, "long_name": x.route_long_name,
                 "color": getattr(x, "route_color", None)} for x in r.itertuples()]
        order = {rid: i for i, rid in enumerate(c.routes)}
        rows.sort(key=lambda z: order.get(z["route_id"], 99))
        return envelope(c, rows)

    @app.get("/v1/routes/{route_id}/live")
    def route_live(route_id: str, c: AppContext = Depends(ctx)):
        if route_id not in c.route_set:
            raise Problem(404, "Route not found", f"{route_id} is not a pilot route")
        shp = pd.read_parquet(c.net.dir / "shapes_simplified.parquet")
        tr = c.net.trips_df[c.net.trips_df["route_id"] == route_id]
        main_shapes = tr["shape_id"].value_counts().head(2).index.tolist()
        shapes = [{"shape_id": s, "coords": shp[shp["shape_id"] == s][["lat", "lon"]].round(6).to_numpy().tolist()}
                  for s in main_shapes]
        stop_ids = c.net.sos[c.net.sos["shape_id"].isin(main_shapes)]["stop_id"].unique()
        stops = [_stop_info(c, s) for s in stop_ids]
        now = c.now()
        vehicles = []
        for v in c.pipeline.state.vehicles(route_id):
            age = now - v["vehicle_ts"]
            if age > 1800:
                continue
            vehicles.append({**v, "age_s": round(age), "stale": age > 120})
        return envelope(c, {"route_id": route_id, "shapes": shapes, "stops": stops, "vehicles": vehicles}, live=True)

    @app.get("/v1/alerts")
    def alerts(route: str | None = None, c: AppContext = Depends(ctx)):
        """Active service alerts for the pilot routes, from the latest recorded Alerts snapshot."""
        import gzip

        from gbstream.decoder import decode_alerts
        from gbstream.replay import snapshot_ts

        base = get_settings().lake_dir / "raw" / "Alerts"
        files = sorted(base.rglob("*.pb.gz")) if base.exists() else []
        if not files:
            return envelope(c, [], note="no Alerts snapshot recorded yet")
        latest = files[-1]
        now = c.now()
        rows = decode_alerts(gzip.decompress(latest.read_bytes()), {route} if route else c.route_set)
        rows = [r for r in rows if not r["active"] or any((s0 or 0) <= now <= (e0 or 1e12) for s0, e0 in r["active"])]
        return envelope(c, rows, snapshot_utc=_iso(snapshot_ts(latest)))

    @app.get("/v1/stream")
    async def stream(request: Request, route: str | None = None, c: AppContext = Depends(ctx)):
        try:
            q = c.pipeline.bc.subscribe(route)
        except RuntimeError:
            raise Problem(503, "Too many stream subscribers") from None

        async def gen():
            try:
                yield f"event: hello\ndata: {json.dumps({'route': route, 'feed_health': c.feed_health()})}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: {msg['type']}\ndata: {json.dumps(msg, default=str)}\n\n"
            finally:
                c.pipeline.bc.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ reliability
    def _rm(c: AppContext) -> pd.DataFrame:
        p = c.metrics_dir / "route_metrics.parquet"
        if not p.exists():
            raise Problem(503, "Metrics not built", "Run `make data`.")
        return pd.read_parquet(p)

    def _clean(df: pd.DataFrame) -> list[dict]:
        return json.loads(df.replace({np.nan: None}).to_json(orient="records"))

    @app.get("/v1/reliability/routes")
    def reliability_routes(month: str | None = None, timeband: str = "all", day_type: str = "all",
                           c: AppContext = Depends(ctx)):
        m = _rm(c)
        month = month or m["month"].max()
        sub = m[(m["month"] == month) & (m["time_band"] == timeband) & (m["day_type"] == day_type)]
        if sub.empty and month not in set(m["month"]):
            raise Problem(404, "No data for month", f"available: {sorted(set(m['month']))}")
        return envelope(c, _clean(sub), month=month, timeband=timeband, day_type=day_type,
                        months_available=sorted(set(m["month"])),
                        definitions={k: f"/v1/methodology#{k}" for k in definitions.METRICS})

    @app.get("/v1/reliability/routes/{route_id}")
    def reliability_route(route_id: str, c: AppContext = Depends(ctx)):
        m = _rm(c)
        r = m[m["route_id"] == route_id]
        if r.empty:
            raise Problem(404, "Route not found", route_id)
        trend = r[(r["day_type"] == "all") & (r["time_band"] == "all")].sort_values("month")
        bands = r[(r["day_type"] == "all") & (r["time_band"] != "all")]
        tp = pd.read_parquet(c.metrics_dir / "timepoint_delay_summary.parquet")
        last = tp["month"].max()
        worst = (tp[(tp["route_id"] == route_id) & (tp["month"] == last)].groupby(["direction_id", "time_point_id"])
                 .agg(median_delay_s=("median_delay_s", "median"), p90_delay_s=("p90_delay_s", "max"), n=("n", "sum"))
                 .reset_index().sort_values("p90_delay_s", ascending=False).head(10))
        daily = pd.read_parquet(c.metrics_dir / "missed_trip_daily.parquet")
        daily = daily[daily["route_id"] == route_id][["service_date", "scheduled_trips", "missed_trips", "missed_rate",
                                                      "unknown", "completeness"]]
        daily["service_date"] = daily["service_date"].astype(str)
        return envelope(c, {"route_id": route_id, "trend": _clean(trend), "time_bands": _clean(bands),
                            "worst_timepoints": _clean(worst), "worst_timepoints_month": last, "daily": _clean(daily)})

    @app.get("/v1/reliability/export.csv")
    def reliability_csv(month: str | None = None, c: AppContext = Depends(ctx)):
        m = _rm(c)
        if month:
            m = m[m["month"] == month]
        buf = io.StringIO()
        m.to_csv(buf, index=False)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=ghostbus_reliability.csv"})

    @app.get("/v1/methodology")
    def methodology(c: AppContext = Depends(ctx)):
        from gbdata.metrics import THRESHOLDS

        bt = REPO_ROOT / "ml" / "reports" / "latest.json"
        ev = REPO_ROOT / "evals" / "reports" / "latest.json"
        return envelope(c, {
            "metric_definitions": definitions.METRICS, "metric_def_version": definitions.METRIC_DEF_VERSION,
            "ghost_definitions": definitions.GHOST, "ghost_def_version": definitions.GHOST_DEF_VERSION,
            "thresholds": THRESHOLDS, "data_sources": definitions.DATA_SOURCES, "known_gaps": definitions.KNOWN_GAPS,
            "ground_truth_source": get_settings().ground_truth_source,
            "backtests": json.loads(bt.read_text()) if bt.exists() else None,
            "evals": json.loads(ev.read_text()) if ev.exists() else None,
        })

    # ------------------------------------------------------------------ copilot
    @app.post("/v1/copilot/ask")
    async def copilot_ask(request: Request, c: AppContext = Depends(ctx)):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise Problem(400, "Invalid JSON body") from None
        q = str(body.get("question", ""))[:1000]
        if not q.strip():
            raise Problem(422, "question is required")
        from gbcopilot.agent import answer

        return envelope(c, await asyncio.to_thread(answer, q))

    # ------------------------------------------------------------------ admin
    @app.get("/admin/v1/feed-health", dependencies=[Depends(require_admin)])
    def admin_feed(c: AppContext = Depends(ctx)):
        hb_p = get_settings().var_dir / "recorder_heartbeat.json"
        hb = json.loads(hb_p.read_text()) if hb_p.exists() else None
        if hb:
            hb["heartbeat_age_s"] = round((datetime.now(UTC) - datetime.fromisoformat(hb["now_utc"])).total_seconds())
        st = c.pipeline.proc.stats
        return envelope(c, {"recorder": hb, "pipeline_feed_health": c.feed_health(),
                            "processing_lag_max_s": st.max_lag_s, "fetches_processed": st.fetches}, live=True)

    @app.get("/admin/v1/pipeline", dependencies=[Depends(require_admin)])
    def admin_pipeline(c: AppContext = Depends(ctx)):
        from dataclasses import asdict

        s = c.pipeline.status
        return envelope(c, {"status": asdict(s), "stats": asdict(c.pipeline.proc.stats),
                            "subscribers": c.pipeline.bc.n_subscribers, "broadcast_sent": c.pipeline.bc.sent,
                            "curated_files_written": len(c.pipeline.writer.files)}, live=True)

    @app.get("/admin/v1/models", dependencies=[Depends(require_admin)])
    def admin_models(c: AppContext = Depends(ctx)):
        bt = REPO_ROOT / "ml" / "reports" / "latest.json"
        live_cov = None
        try:
            from gbml.online import live_coverage

            live_cov = live_coverage(c.prediction_log, c.pipeline.proc.arrivals)
        except Exception as e:  # noqa: BLE001
            live_cov = {"error": str(e)}
        drift = None
        try:
            from gbml.online import PSI_WARN, psi

            live = [f["features"].get("delay_k") for f in c.prediction_log if f["features"].get("delay_k") is not None]
            ref = getattr(getattr(c.model_a, "m", None), "ref_delay_sample", None)
            if live and ref is not None:
                v = psi(np.asarray(ref), np.asarray(live))
                drift = {"feature": "current delay", "psi": v, "warn": bool(v > PSI_WARN), "n_live": len(live)}
            else:
                drift = {"note": "not enough live model predictions yet", "n_live": len(live)}
        except Exception as e:  # noqa: BLE001
            drift = {"error": str(e)}
        return envelope(c, {"drift": drift, "model_a": getattr(c.model_a, "version", None), "model_b": getattr(c.model_b, "version", None),
                            "backtest": json.loads(bt.read_text()) if bt.exists() else None,
                            "live": live_cov, "predictions_logged": len(c.prediction_log)})

    @app.get("/admin/v1/datasets", dependencies=[Depends(require_admin)])
    def admin_datasets(c: AppContext = Depends(ctx)):
        m = REPO_ROOT / "data" / "manifest.json"
        return envelope(c, json.loads(m.read_text()) if m.exists() else {})

    # ------------------------------------------------------------------ lite demo page
    @app.get("/demo", response_class=HTMLResponse)
    def demo():
        p = Path(__file__).parent / "static" / "demo.html"
        return HTMLResponse(p.read_text(encoding="utf-8") if p.exists() else "<p>demo page not built</p>")

    return app


app = create_app()
