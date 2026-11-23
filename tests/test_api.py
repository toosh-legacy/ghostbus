from __future__ import annotations

import logging
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from gbcore.settings import get_settings

LAKE = get_settings().lake_dir / "curated"
pytestmark = pytest.mark.skipif(not (LAKE / "metrics" / "route_metrics.parquet").exists(),
                                reason="run `make data` first")
ADMIN = {"X-Admin-Token": get_settings().admin_token}


@pytest.fixture(scope="module")
def client():
    from gbapi.app import create_app

    app = create_app(pipeline_mode="off")
    with TestClient(app) as c:
        ctx = app.state.ctx
        from gbsim.evaluate import DEFAULT_DATE
        from gbsim.simulator import simulate
        from gbstream.replay import batches_from_events

        sim = simulate(ctx.net, "many_ghosts", DEFAULT_DATE, start_local_h=7.0, hours=1.5, route_ids=["1"])
        batches = list(batches_from_events(sim.events))
        for b in batches[: int(len(batches) * 0.6)]:  # replay ~55 minutes of simulated feed
            ctx.pipeline.feed(b)
        c.sim = sim
        yield c


def route1_stop(client) -> str:
    ctx = client.app.state.ctx
    p = ctx.net.pattern(client.sim.schedule[len(client.sim.schedule) // 2].trip_id)
    return p.stop_ids[len(p.stop_ids) // 2]


def test_healthz_readyz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["db"]


def test_routes(client):
    r = client.get("/v1/routes").json()
    assert len(r["data"]) == 10 and "data_as_of" in r


def test_nearby_and_privacy(client, caplog):
    caplog.set_level(logging.INFO)
    r = client.get("/v1/stops/nearby", params={"lat": 42.3297, "lon": -71.0839, "radius_m": 800})
    assert r.status_code == 200
    assert len(r.json()["data"]) > 0
    server_logs = [rec.getMessage() for rec in caplog.records if not rec.name.startswith("httpx")]
    assert not any("42.3297" in m or "71.0839" in m for m in server_logs)


def test_search(client):
    r = client.get("/v1/stops/search", params={"q": "Nubian"}).json()
    assert any("Nubian" in s["name"] for s in r["data"])
    assert len(client.get("/v1/stops/search", params={"q": "111"}).json()["data"]) > 5


def test_stop_arrivals_ranges_and_ghost_states_during_replay(client):
    sid = route1_stop(client)
    r = client.get(f"/v1/stops/{sid}/arrivals", params={"horizon_min": 90})
    assert r.status_code == 200
    body = r.json()
    assert "feed_health" in body and body["feed_health"]["healthy"]
    arr = body["data"]["arrivals"]
    assert arr, body
    for a in arr:
        p = a["predicted"]
        assert p["p10_utc"] <= p["p50_utc"] <= p["p90_utc"]
        assert a["ghost"]["state"] in {"low", "elevated", "high", "unknown"}
        assert a["ghost"]["reason"]
        assert a["source"] in {"model", "agency", "schedule"}
        assert "min" in p["range_text"] or p["range_text"] == "due now"
    states = {a["ghost"]["state"] for a in arr}
    assert states & {"low", "elevated", "high"}


def test_stop_arrivals_not_found_is_problem_json(client):
    r = client.get("/v1/stops/nope/arrivals")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 404


def test_route_live(client):
    r = client.get("/v1/routes/1/live").json()
    assert r["data"]["shapes"] and r["data"]["stops"]
    assert r["data"]["vehicles"], "sim vehicles should be live"
    assert all("age_s" in v and "stale" in v for v in r["data"]["vehicles"])


def test_reliability_and_csv(client):
    r = client.get("/v1/reliability/routes").json()
    assert len(r["data"]) == 10
    row = r["data"][0]
    for k in ("on_time_performance", "headway_adherence", "excess_wait_time_s", "bunching_rate", "missed_trip_rate"):
        assert k in row
    pm = client.get("/v1/reliability/routes", params={"timeband": "pm_peak", "day_type": "weekday"}).json()
    assert len(pm["data"]) == 10
    d = client.get("/v1/reliability/routes/111").json()["data"]
    assert d["trend"] and d["worst_timepoints"] and d["daily"]
    csv = client.get("/v1/reliability/export.csv")
    assert csv.status_code == 200 and csv.text.startswith("route_id,")


def test_methodology(client):
    d = client.get("/v1/methodology").json()["data"]
    assert d["ghost_def_version"] == "ghost-def v1.0"
    assert "bunching_rate" in d["metric_definitions"]
    assert d["data_sources"]


def test_admin_requires_token(client):
    assert client.get("/admin/v1/pipeline").status_code == 401
    for path in ("/admin/v1/feed-health", "/admin/v1/pipeline", "/admin/v1/models", "/admin/v1/datasets"):
        r = client.get(path, headers=ADMIN)
        assert r.status_code == 200, path
    st = client.get("/admin/v1/pipeline", headers=ADMIN).json()["data"]["stats"]
    assert st["duplicates_dropped"] > 0 and st["arrivals"] > 0


def test_sse_stream_emits_events(client):
    ctx = client.app.state.ctx
    q = ctx.pipeline.bc.subscribe()
    try:
        ctx.pipeline.bc.publish({"type": "vehicle", "route_id": "1", "vehicle_id": "x"})
        assert q.get_nowait()["vehicle_id"] == "x"
    finally:
        ctx.pipeline.bc.unsubscribe(q)


def test_stop_page_latency_p95(client):
    sid = route1_stop(client)
    ts = []
    for _ in range(20):
        t = time.perf_counter()
        client.get(f"/v1/stops/{sid}/arrivals")
        ts.append(time.perf_counter() - t)
    assert pd.Series(ts).quantile(0.95) < 0.3


def test_metrics_match_hand_computed_fixture():
    import duckdb
    from gbdata.metrics import build

    # hand-computed fixture: 4 events, 1 route-day
    con = duckdb.connect()
    con.execute("""CREATE TABLE arr AS SELECT * FROM (VALUES
      (DATE '2025-12-01','X',0,'t1','s1','tp1',1,'Startpoint','Schedule',28800,28830,30,NULL::INT,NULL::INT,'2025-12'),
      (DATE '2025-12-01','X',0,'t1','s2','tp2',2,'Endpoint','Schedule',29400,29900,500,NULL,NULL,'2025-12'),
      (DATE '2025-12-01','X',0,'t2','s1','tp1',1,'Startpoint','Headway',30000,30060,60,600,300,'2025-12'),
      (DATE '2025-12-01','X',0,'t3','s1','tp1',1,'Startpoint','Headway',30600,NULL,NULL,600,100,'2025-12'),
      (DATE '2025-12-01','X',0,'t4','s1','tp1',1,'Startpoint','Headway',31200,31210,10,600,1000,'2025-12')
    ) t(service_date, route_id, direction_id, half_trip_id, stop_id, time_point_id, time_point_order, point_type,
        standard_type, sched_s, actual_s, delay_s, scheduled_headway, headway, month)""")
    con.execute("CREATE TABLE comp AS SELECT DATE '2025-12-01' service_date, 'X' route_id, 0.75 completeness")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        build(con, Path(d))
        m = pd.read_parquet(Path(d) / "route_metrics.parquet")
        r = m[(m.day_type == "all") & (m.time_band == "all")].iloc[0]
        assert r.on_time_performance == pytest.approx(0.5)  # 30 s ok, 500 s late
        # headway events with headway: t3 (100/600 -> outside, bunched), t4 (1000/600 outside); t2 has 300/600 ok
        assert r.headway_adherence == pytest.approx(1 / 3)
        assert r.bunching_rate == pytest.approx(1 / 3)
        h = [300, 100, 1000]
        ewt = sum(x * x for x in h) / (2 * sum(h)) - (3 * 600 * 600) / (2 * 1800)
        assert r.excess_wait_time_s == pytest.approx(ewt)
        assert r.missed_trips == 1 and r.scheduled_trips == 4  # t3 has no actual events
        assert r.missed_trip_rate == pytest.approx(0.25)


def test_unknown_route_days_never_counted_as_missed():
    import tempfile
    from pathlib import Path

    import duckdb
    from gbdata.metrics import build

    con = duckdb.connect()
    con.execute("""CREATE TABLE arr AS SELECT * FROM (VALUES
      (DATE '2025-12-02','X',0,'t1','s1','tp1',1,'Startpoint','Schedule',28800,NULL::INT,NULL::INT,NULL::INT,NULL::INT,'2025-12'),
      (DATE '2025-12-02','X',0,'t2','s1','tp1',1,'Startpoint','Schedule',29800,29810,10,NULL,NULL,'2025-12')
    ) t(service_date, route_id, direction_id, half_trip_id, stop_id, time_point_id, time_point_order, point_type,
        standard_type, sched_s, actual_s, delay_s, scheduled_headway, headway, month)""")
    con.execute("CREATE TABLE comp AS SELECT DATE '2025-12-02' service_date, 'X' route_id, 0.5 completeness")
    with tempfile.TemporaryDirectory() as d:
        build(con, Path(d))
        lab = pd.read_parquet(Path(d) / "trip_labels.parquet")
        assert set(lab.label) == {"unknown"} and not lab.missed.any()
        daily = pd.read_parquet(Path(d) / "missed_trip_daily.parquet")
        assert daily.unknown.all() and daily.missed_rate.isna().all()
