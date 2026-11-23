from __future__ import annotations

import asyncio
import math
from dataclasses import asdict

import numpy as np
import pytest
from gbcore.geo import to_lonlat
from gbcore.settings import get_settings
from gbschemas.events import FeedFetchEvent, VehicleEvent
from gbstream.arrivals import ArrivalDetector, TripProgress, interpolate_crossing
from gbstream.ghost import FeedHealth, ScheduledTrip, evaluate_trip, feed_health
from gbstream.mapmatch import MapMatcher, VehicleMatchState
from gbstream.network import Shape
from hypothesis import given, settings
from hypothesis import strategies as hst

HAVE_STATIC = (get_settings().lake_dir / "curated" / "static" / "trips.parquet").exists()
needs_static = pytest.mark.skipif(not HAVE_STATIC, reason="run `make data` first")

# Base point in Boston, metric
X0, Y0 = 330000.0, 4690000.0


def mk_shape(pts: list[tuple[float, float]], sid: str = "s") -> Shape:
    xy = np.array([(X0 + x, Y0 + y) for x, y in pts], dtype=float)
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    lon, lat = to_lonlat(xy[:, 0], xy[:, 1])
    return Shape(sid, xy, cum, np.asarray(lat), np.asarray(lon))


def ping_at(shape: Shape, d: float, nx: float = 0.0, ny: float = 0.0) -> tuple[float, float]:
    x = np.interp(d, shape.cum, shape.xy[:, 0]) + nx
    y = np.interp(d, shape.cum, shape.xy[:, 1]) + ny
    lon, lat = to_lonlat(x, y)
    return float(lat), float(lon)


# ------------------------------------------------------------------ map matching
def run_track(shape: Shape, dists: list[float], noise: float = 0.0, seed: int = 0, dt: float = 20.0):
    rng = np.random.default_rng(seed)
    mm = MapMatcher({shape.shape_id: shape})
    st = VehicleMatchState()
    out = []
    for i, d in enumerate(dists):
        lat, lon = ping_at(shape, d, *rng.normal(0, noise, 2))
        out.append(mm.match(st, shape.shape_id, lat, lon, i * dt))
    return out


def test_mapmatch_straight_with_noise():
    s = mk_shape([(0, 0), (3000, 0)])
    dists = list(np.arange(0, 3000, 120.0))
    res = run_track(s, dists, noise=15, seed=1)
    err = [abs(r.dist_m - d) for r, d in zip(res, dists)]
    assert np.median(err) <= 25


def test_mapmatch_out_and_back_loop_prefers_forward():
    # out along y=0, back along y=30: the return leg passes 30 m from the outbound leg
    s = mk_shape([(0, 0), (2000, 0), (2000, 30), (0, 30)])
    dists = list(np.arange(0, s.length, 100.0))
    res = run_track(s, dists, noise=5, seed=2)
    err = [abs(r.dist_m - d) for r, d in zip(res, dists)]
    assert max(err) < 40, err


def test_mapmatch_self_crossing_shape():
    # figure-eight style: a square path that crosses its earlier leg
    s = mk_shape([(0, 0), (1000, 0), (1000, 1000), (500, 1000), (500, -500)])
    dists = list(np.arange(0, s.length, 80.0))
    res = run_track(s, dists, noise=5, seed=3)
    err = [abs(r.dist_m - d) for r, d in zip(res, dists)]
    assert np.median(err) < 10 and max(err) < 60


def test_mapmatch_terminal_layover_stationary():
    s = mk_shape([(0, 0), (2000, 0)])
    res = run_track(s, [5.0] * 10 + [100, 200, 300], noise=8, seed=4)
    assert all(r.status == "matched" for r in res)
    assert all(r.dist_m < 40 for r in res[:10])


def test_mapmatch_rejects_off_route():
    s = mk_shape([(0, 0), (2000, 0)])
    mm = MapMatcher({"s": s})
    st = VehicleMatchState()
    lat, lon = ping_at(s, 500, 0, 150)
    assert mm.match(st, "s", lat, lon, 0).status == "off_route"


def test_mapmatch_backjump_noise_then_persist():
    s = mk_shape([(0, 0), (3000, 0)])
    mm = MapMatcher({"s": s})
    st = VehicleMatchState()
    for i, d in enumerate([1000, 1100, 1200]):
        mm.match(st, "s", *ping_at(s, d), i * 20)
    r = mm.match(st, "s", *ping_at(s, 500), 60)
    assert r.status == "backjump_noise" and r.dist_m == pytest.approx(1200, abs=5)
    r = mm.match(st, "s", *ping_at(s, 510), 80)
    assert r.status == "backjump_noise"
    r = mm.match(st, "s", *ping_at(s, 520), 100)
    assert r.status == "matched" and r.dist_m == pytest.approx(520, abs=5)


# ------------------------------------------------------------------ arrivals
STOPS = np.array([0.0, 500.0, 1000.0, 1500.0])
IDS = ["a", "b", "c", "d"]
SEQ = [1, 2, 3, 4]


def detect(track):
    det, prog = ArrivalDetector(), TripProgress("t")
    out = []
    for t, d in track:
        out += det.update(prog, STOPS, IDS, SEQ, t, d, route_id="r", vehicle_id="v", source="sim")
    return out


def test_interpolate_crossing():
    assert interpolate_crossing(0, 0, 100, 1000, 485) == pytest.approx(48.5)


def test_arrival_interpolation_and_departure():
    arr = detect([(0, 0), (10, 10), (40, 100), (100, 700), (200, 1600)])
    kinds = [(a.stop_id, a.kind) for a in arr]
    assert kinds[0] == ("a", "departure")
    b = next(a for a in arr if a.stop_id == "b")
    assert b.arrival_ts == pytest.approx(40 + (485 - 100) / 600 * 60)
    assert {a.stop_id for a in arr} == {"a", "b", "c", "d"}


def test_arrival_passby_and_low_confidence_gap():
    arr = detect([(0, 0), (10, 20), (60, 520)])  # 500 m in 50 s = 10 m/s -> pass-by
    assert next(a for a in arr if a.stop_id == "b").kind == "pass-by"
    arr = detect([(0, 0), (10, 20), (400, 990)])  # 390 s gap -> low confidence
    assert next(a for a in arr if a.stop_id == "b").confidence == "low"


def test_arrival_jitter_backwards_never_retriggers():
    arr = detect([(0, 0), (10, 20), (30, 495), (50, 470), (70, 498), (90, 600)])
    assert [a.stop_id for a in arr].count("b") == 1


# ------------------------------------------------------------------ ghost rules
TRIP = ScheduledTrip("t1", "1", 0, "2026-11-24", first_dep_ts=10_000, last_arr_ts=13_000, first_stop_id="a")
OK = FeedHealth(True, "ok", 9_990, 500)


def test_ghost_high():
    g = evaluate_trip(10_000 + 301, TRIP, OK, None, None)
    assert g.state == "high" and g.def_version == "ghost-def v1.1"


def test_ghost_elevated_not_tracking():
    assert evaluate_trip(10_000 - 500, TRIP, OK, None, None).state == "elevated"
    assert evaluate_trip(10_000 + 200, TRIP, OK, None, None).state == "elevated"


def test_ghost_elevated_tu_without_vehicle():
    g = evaluate_trip(10_000 - 100, TRIP, OK, None, 10_000 - 110)
    assert g.state == "elevated" and "prediction" in g.reason


def test_ghost_low_with_vehicle_and_far_future():
    assert evaluate_trip(10_400, TRIP, OK, 10_390, None).state == "low"
    assert evaluate_trip(10_000 - 1200, TRIP, OK, None, None).state == "low"


def test_ghost_unknown_when_feed_stale_or_low_rate():
    h = feed_health(10_400, 10_400 - 121, 500, None)
    assert not h.healthy
    assert evaluate_trip(10_400, TRIP, h, None, None).state == "unknown"
    h2 = feed_health(10_400, 10_390, 100, 500.0)
    assert not h2.healthy and "30%" in h2.reason


# ------------------------------------------------------------------ dedupe / ordering properties
def _proc():
    from gbstream.network import Network

    class _Net(Network):  # minimal network with no trips: exercises dedupe/ordering only
        def __init__(self):
            self.shapes = {}

        def has_trip(self, trip_id):
            return False

    from gbstream.processor import Processor

    return Processor(_Net(), schedule=[])


def _ev(vid: str, ts: float, fetch: float) -> VehicleEvent:
    return VehicleEvent(vid, None, None, None, 42.3, -71.06, ts, fetch)


@settings(max_examples=60, deadline=None)
@given(hst.lists(hst.tuples(hst.sampled_from(["a", "b", "c"]), hst.integers(0, 2000)), min_size=1, max_size=80))
def test_dedupe_idempotent(items):
    evs = [_ev(v, float(t), float(t)) for v, t in items]
    p1 = _proc()
    for e in evs:
        p1.process(e)
    p1.flush()
    p2 = _proc()
    for e in evs + evs:  # everything delivered twice
        p2.process(e)
    p2.flush()
    key = lambda p: [(x.vehicle_id, x.vehicle_ts) for x in p.pings]
    assert key(p1) == key(p2)
    assert len(p1.pings) + p1.stats.late_dropped == len({(v, t) for v, t in items})


@settings(max_examples=60, deadline=None)
@given(hst.lists(hst.integers(0, 3000), min_size=1, max_size=60, unique=True), hst.randoms())
def test_ordering_within_lateness_equals_sorted(ts_list, rnd):
    ts_sorted = sorted(ts_list)
    # deliver with bounded disorder: each event's delivery time = ts + jitter in [0, 59]
    deliveries = sorted((t + rnd.randint(0, 59), t) for t in ts_sorted)
    p = _proc()
    for d, t in deliveries:
        p.process(_ev("v", float(t), float(d)))
    p.flush()
    assert [x.vehicle_ts for x in p.pings] == [float(t) for t in ts_sorted]
    assert p.stats.late_dropped == 0


def test_events_past_watermark_are_dropped_and_counted():
    p = _proc()
    p.process(_ev("v", 1000.0, 1000.0))
    p.process(_ev("v", 1100.0, 1100.0))  # releases 1000 (watermark 1100 - 60)
    p.process(_ev("v", 900.0, 1101.0))  # older than last released -> late
    p.flush()
    assert p.stats.late_dropped == 1
    assert [x.vehicle_ts for x in p.pings] == [1000.0, 1100.0]


# ------------------------------------------------------------------ simulator gates + replay determinism
@pytest.fixture(scope="module")
def net():
    from gbstream.network import Network

    return Network()


@needs_static
@pytest.mark.parametrize("scenario", ["normal", "feed_outage", "many_ghosts", "wrong_trip"])
def test_simulator_scenario_gates(net, scenario):
    from gbsim.evaluate import gates, run_scenario

    r = run_scenario(scenario, net, hours=1.5, route_ids=["1", "111"])
    g = gates(r)
    assert g["mapmatch_median_le_25m"], r["mapmatch"]
    assert g["arrival_median_le_20s"] and g["arrival_p90_le_60s"], r["arrivals"]
    assert g["ghost_precision_ge_0.9"] and g["ghost_recall_ge_0.9"], r["ghost"]
    if scenario == "feed_outage":
        assert g["outage_100pct_unknown"], r["ghost"]


def _canon(proc) -> dict:
    o = proc.outputs()
    o.pop("stats")
    return o


@needs_static
def test_replay_1x_vs_20x_identical(net):
    from gbsim.evaluate import DEFAULT_DATE
    from gbsim.simulator import simulate
    from gbstream.processor import Processor
    from gbstream.replay import batches_from_events, replay

    sim = simulate(net, "normal", DEFAULT_DATE, hours=1.0, route_ids=["1"])
    slept = {1.0: 0.0, 20.0: 0.0}

    def fake_sleep(speed):
        async def s(x):
            slept[speed] += x

        return s

    outs = {}
    for speed in (1.0, 20.0):
        proc = Processor(net, schedule=sim.schedule)
        r = asyncio.run(replay(batches_from_events(sim.events), proc, speed, fake_sleep(speed)))
        assert r["dropped"] == 0
        outs[speed] = _canon(proc)
    assert outs[1.0] == outs[20.0]
    assert slept[1.0] == pytest.approx(20 * slept[20.0])
    assert len(outs[1.0]["arrivals"]) > 0


@needs_static
def test_replay_real_20x_no_drops(net):
    from gbsim.evaluate import DEFAULT_DATE
    from gbsim.simulator import simulate
    from gbstream.processor import Processor
    from gbstream.replay import batches_from_events, replay

    sim = simulate(net, "normal", DEFAULT_DATE, hours=1.0, route_ids=["1"])
    w0 = sim.window[0]
    evs = [e for e in sim.events if e.fetch_ts < w0 + 80]  # ~4 s of real time at 20x
    p_fast, p_ref = Processor(net, schedule=sim.schedule), Processor(net, schedule=sim.schedule)
    r = asyncio.run(replay(batches_from_events(evs), p_fast, 20.0))
    for e in evs:
        p_ref.process(e)
    p_ref.flush()
    assert r["dropped"] == 0 and _canon(p_fast) == _canon(p_ref)


@settings(max_examples=5, deadline=None)
@given(hst.sampled_from([0.0, 1.0, 5.0, 50.0]))
def test_replay_any_speed_identical_property(speed):
    evs = [_ev("v", float(t), float(t)) for t in range(0, 600, 20)]
    evs += [FeedFetchEvent("VehiclePositions", float(t), float(t), 1) for t in range(0, 600, 20)]
    evs.sort(key=lambda e: e.fetch_ts)

    async def nosleep(x):
        return None

    from gbstream.replay import batches_from_events, replay

    p = _proc()
    asyncio.run(replay(batches_from_events(evs), p, speed, nosleep))
    assert [x.vehicle_ts for x in p.pings] == [float(t) for t in range(0, 600, 20)]


# ------------------------------------------------------------------ decoder round-trip + recorded data
def test_decoder_roundtrip():
    from gbstream.decoder import decode, encode_vehicle_positions

    evs = [_ev("v1", 100.0, 0.0), _ev("v2", 105.0, 0.0)]
    evs[0].trip_id, evs[0].route_id = "T1", "1"
    out = decode("VehiclePositions", encode_vehicle_positions(evs, 110), 111.0, "sim")
    vs = [e for e in out if isinstance(e, VehicleEvent)]
    assert [(v.vehicle_id, v.vehicle_ts, v.trip_id) for v in vs] == [("v1", 100.0, "T1"), ("v2", 105.0, None)]
    assert math.isclose(vs[0].lat, 42.3, abs_tol=1e-5)
    assert isinstance(out[-1], FeedFetchEvent) and out[-1].n_entities == 2


@needs_static
def test_recorded_lake_replay_smoke(net):
    from gbstream.processor import Processor
    from gbstream.replay import iter_lake_events, list_snapshots

    raw = get_settings().lake_dir / "raw"
    snaps = list_snapshots(raw)
    if len(snaps) < 4:
        pytest.skip("no recorded snapshots yet")
    t0 = snaps[0][0]
    p = Processor(net, route_ids={"1", "111", "23", "28", "39", "66", "77", "109", "741", "743"})
    for batch in iter_lake_events(raw, t0, t0 + 300, p.route_ids):
        for e in batch:
            p.process(e)
    p.flush()
    assert p.stats.vehicle_events > 0
    assert p.stats.matched > 0


# ------------------------------------------------------------------ full-mode adapter (Redis Streams)
def _redis_up() -> bool:
    try:
        import redis

        return bool(redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5).ping())
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.full
@pytest.mark.skipif(not _redis_up(), reason="Redis (full mode) not reachable")
def test_redis_streams_bus_roundtrip():
    import uuid

    from gbcore.adapters_full import RedisStreamsBus

    async def go():
        bus = RedisStreamsBus(get_settings().redis_url, stream=f"gb:test:{uuid.uuid4().hex}")
        evs = [_ev("v", float(t), float(t)) for t in range(5)]
        for e in evs:
            await bus.publish(e)
        await bus.close()
        got = [e async for e in bus.subscribe()]
        await bus.r.delete(bus.stream)
        return evs, got

    evs, got = asyncio.run(go())
    assert [asdict(e) for e in got] == [asdict(e) for e in evs]


def test_ghost_v11_possible_reassignment_is_elevated_not_high():
    g = evaluate_trip(10_000 + 900, TRIP, OK, None, None,
                      possible_reassignment="vehicle v reports trip t9 while running 25 min ahead of it")
    assert g.state == "elevated" and "wrong trip assignment" not in g.reason or "t9" in g.reason
    assert g.def_version == "ghost-def v1.1"
    # without the signal, the same trip is high
    assert evaluate_trip(10_000 + 900, TRIP, OK, None, None).state == "high"
