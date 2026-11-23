"""D4: bus simulator with exact ground truth.

Generates the feed exactly as a consumer would see it: a VehiclePositions fetch every 20 s carrying each
active vehicle's latest ping, plus TripUpdates. Built-in imperfections: dropped pings, duplicate pings
(re-delivery across fetches + injected duplicates), out-of-order delivery (within the lateness window),
a feed outage window, ghost trips (optionally present in trip updates) and wrong trip assignment.

Ground truth: true distance along shape for every ping, true arrival/departure time at every stop
(defined exactly like the detector: first time distance reaches stop - 15 m; departure = stop0 + 15 m),
and true ghost labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from gbcore.geo import to_lonlat
from gbcore.timeutil import service_day_reference
from gbschemas.events import Event, FeedFetchEvent, TripUpdateEvent, VehicleEvent
from gbstream.arrivals import ARRIVAL_BUFFER_M
from gbstream.ghost import ScheduledTrip
from gbstream.network import Network

FETCH_EVERY_S = 20


@dataclass
class Scenario:
    name: str
    seed: int
    start_delay_mean_s: float = 60.0
    start_delay_sd_s: float = 90.0
    speed_factor_mean: float = 1.0  # >1 = slower than schedule
    speed_factor_sd: float = 0.08
    ghost_rate: float = 0.05
    ghost_in_tu_rate: float = 0.5
    drop_rate: float = 0.04
    dup_rate: float = 0.03
    ooo_rate: float = 0.03
    outage: tuple[float, float] | None = None  # minutes from window start
    wrong_trip_n: int = 0
    bunching: bool = False


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("normal", 101),
    "heavy_delays": Scenario("heavy_delays", 102, start_delay_mean_s=240, start_delay_sd_s=180,
                             speed_factor_mean=1.3, speed_factor_sd=0.15),
    "bunching": Scenario("bunching", 103, bunching=True),
    "feed_outage": Scenario("feed_outage", 104, outage=(50.0, 60.0)),
    "many_ghosts": Scenario("many_ghosts", 105, ghost_rate=0.25),
    "wrong_trip": Scenario("wrong_trip", 106, wrong_trip_n=3),
}


@dataclass
class SimResult:
    events: list[Event]
    schedule: list[ScheduledTrip]
    truth_pings: pd.DataFrame
    truth_arrivals: pd.DataFrame
    truth_ghosts: pd.DataFrame
    outage: tuple[float, float] | None
    window: tuple[float, float]
    meta: dict = field(default_factory=dict)


def _traj(p, dep_ts: float, rng: np.random.Generator, sc: Scenario, extra_delay: float) -> tuple[np.ndarray, np.ndarray]:
    """Piecewise-linear (t, d) trajectory knots through the stops, with dwell."""
    start_delay = max(-60.0, rng.normal(sc.start_delay_mean_s, sc.start_delay_sd_s)) + extra_delay
    factor = max(0.7, rng.normal(sc.speed_factor_mean, sc.speed_factor_sd))
    sched_dep = p.sched_dep_s
    sched_arr = p.sched_arr_s
    t = dep_ts + start_delay
    ts, ds = [t - 400.0, t], [p.stop_dists[0], p.stop_dists[0]]  # layover at the terminal
    for i in range(1, len(p.stop_ids)):
        seg_sched = max(20.0, sched_arr[i] - sched_dep[i - 1])
        seg_len = max(1.0, p.stop_dists[i] - p.stop_dists[i - 1])
        seg_t = max(seg_len / 15.0, seg_sched * factor * rng.lognormal(0, 0.12))
        t = t + seg_t
        ts.append(t)
        ds.append(p.stop_dists[i])
        if i < len(p.stop_ids) - 1 and rng.random() < 0.7:
            t = t + rng.uniform(8, 40)
            ts.append(t)
            ds.append(p.stop_dists[i])
    ts.append(t + 300.0)
    ds.append(p.stop_dists[-1])  # sits at the last stop
    return np.array(ts), np.array(ds)


def _first_time_reaching(ts: np.ndarray, ds: np.ndarray, target: float) -> float | None:
    idx = np.nonzero(ds >= target)[0]
    if len(idx) == 0:
        return None
    j = idx[0]
    if j == 0:
        return float(ts[0])
    d0, d1, t0, t1 = ds[j - 1], ds[j], ts[j - 1], ts[j]
    return float(t0 + (target - d0) / (d1 - d0) * (t1 - t0)) if d1 > d0 else float(t1)


def _point_at(shape, d: float) -> tuple[float, float]:
    x = np.interp(d, shape.cum, shape.xy[:, 0])
    y = np.interp(d, shape.cum, shape.xy[:, 1])
    return float(x), float(y)


def simulate(net: Network, scenario: Scenario | str, service_date: date, start_local_h: float = 7.0,
             hours: float = 2.0, route_ids: list[str] | None = None, source: str = "sim") -> SimResult:
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    rng = np.random.default_rng(sc.seed)
    ref = service_day_reference(service_date).astimezone(UTC).timestamp()
    w0 = ref + start_local_h * 3600
    w1 = w0 + hours * 3600
    routes = set(route_ids) if route_ids else None
    trips = []
    for tid in sorted(net.trips_on(service_date, routes)):
        p = net.pattern(tid)
        if p is None or len(p.stop_ids) < 3:
            continue
        dep = ref + float(p.sched_dep_s[0])
        if w0 + 600 <= dep <= w1 - 1800:  # trips that start and mostly run inside the window
            trips.append((dep, tid, p))
    trips.sort(key=lambda x: (x[0], x[1]))
    schedule = [ScheduledTrip(tid, p.route_id, p.direction_id, service_date.isoformat(), dep,
                              ref + float(p.sched_arr_s[-1]), p.stop_ids[0]) for dep, tid, p in trips]
    is_ghost = {tid: bool(rng.random() < sc.ghost_rate) for _, tid, _ in trips}
    ghost_in_tu = {tid: bool(rng.random() < sc.ghost_in_tu_rate) for tid in is_ghost}
    # wrong trip assignment: a real vehicle reports another (later, same route/direction) trip's id
    wrong: dict[str, str] = {}
    if sc.wrong_trip_n:
        cands = [(dep, tid, p) for dep, tid, p in trips if not is_ghost[tid]]
        for k in range(min(sc.wrong_trip_n, len(cands) // 4)):
            dep, tid, p = cands[(k + 1) * len(cands) // (sc.wrong_trip_n + 1)]
            later = [t for d2, t, p2 in cands if p2.route_id == p.route_id and p2.direction_id == p.direction_id
                     and d2 > dep + 1200 and t not in wrong.values() and t not in wrong]
            if later:
                wrong[tid] = later[0]
    truth_pings, truth_arr = [], []
    pending: list[tuple[float, VehicleEvent]] = []  # (deliver_after_ts, event)
    veh_n = 0
    trip_traj = {}
    bunch_toggle = False
    for dep, tid, p in trips:
        if is_ghost[tid]:
            continue
        extra = 0.0
        if sc.bunching:
            bunch_toggle = not bunch_toggle
            extra = 360.0 if bunch_toggle else 0.0
        ts, ds = _traj(p, dep, rng, sc, extra)
        trip_traj[tid] = (ts, ds)
        for i in range(len(p.stop_ids)):
            if i == 0:
                tt = _first_time_reaching(ts, ds, p.stop_dists[0] + ARRIVAL_BUFFER_M)
                kind = "departure"
            else:
                tt = _first_time_reaching(ts, ds, p.stop_dists[i] - ARRIVAL_BUFFER_M)
                kind = "arrival"
            if tt is not None:
                truth_arr.append({"trip_id": tid, "stop_index": i, "stop_id": p.stop_ids[i], "true_ts": tt,
                                  "kind": kind})
        veh_n += 1
        vid = f"sim{veh_n:04d}"
        reported_trip = wrong.get(tid, tid)
        interval = float(rng.choice([15, 20, 30, 45, 60], p=[0.2, 0.3, 0.25, 0.15, 0.1]))
        sigma = rng.uniform(5, 30) / np.sqrt(2)
        shape = net.shapes[p.shape_id]
        t = ts[0] + rng.uniform(0, interval)
        while t <= min(ts[-1], w1):
            ti = float(int(t))
            d = float(np.interp(ti, ts, ds))
            x, y = _point_at(shape, d)
            nx, ny = rng.normal(0, sigma, 2)
            lon, lat = to_lonlat(x + nx, y + ny)
            ev = VehicleEvent(vid, reported_trip, p.route_id, p.direction_id, float(lat), float(lon), ti, 0.0,
                              source=source)
            truth_pings.append({"vehicle_id": vid, "vehicle_ts": ti, "trip_id": tid, "reported_trip_id": reported_trip,
                                "true_dist": d})
            t += interval
            if ti < w0:
                continue
            r = rng.random()
            if r < sc.drop_rate:
                continue
            delay = 0.0
            if r < sc.drop_rate + sc.ooo_rate:
                delay = float(rng.uniform(25, 50))  # delivered late, still inside the lateness window
            pending.append((ti + delay, ev))
            if rng.random() < sc.dup_rate:
                pending.append((ti + float(rng.uniform(20, 40)), ev))
    pending.sort(key=lambda x: (x[0], x[1].vehicle_id, x[1].vehicle_ts))
    # Build fetches every 20 s. A vehicle's latest delivered ping is repeated in later fetches until replaced
    # (exactly how the real feed repeats unchanged positions).
    events: list[Event] = []
    outage = None
    if sc.outage:
        outage = (w0 + sc.outage[0] * 60, w0 + sc.outage[1] * 60)
    latest: dict[str, VehicleEvent] = {}
    k = 0
    f = w0
    while f <= w1:
        newly = []
        while k < len(pending) and pending[k][0] <= f:
            newly.append(pending[k][1])
            k += 1
        if outage and outage[0] <= f < outage[1]:
            # fetch attempts fail: nothing delivered, pings during the outage are lost
            events.append(FeedFetchEvent("VehiclePositions", float(f), float(f), 0, source, ok=False))
            f += FETCH_EVERY_S
            continue
        for ev in newly:
            cur = latest.get(ev.vehicle_id)
            if cur is None or ev.vehicle_ts >= cur.vehicle_ts:
                latest[ev.vehicle_id] = ev
        snap = []
        for vid, ev in sorted(latest.items()):
            if f - ev.vehicle_ts > 300:
                continue  # vehicle went quiet / finished
            snap.append(VehicleEvent(ev.vehicle_id, ev.trip_id, ev.route_id, ev.direction_id, ev.lat, ev.lon,
                                     ev.vehicle_ts, float(f), source=source))
        # out-of-order pings that were delayed are delivered alongside the vehicle's newer one
        for ev in newly:
            if latest.get(ev.vehicle_id) is not ev:
                snap.append(VehicleEvent(ev.vehicle_id, ev.trip_id, ev.route_id, ev.direction_id, ev.lat, ev.lon,
                                         ev.vehicle_ts, float(f), source=source))
        events.extend(snap)
        events.append(FeedFetchEvent("VehiclePositions", float(f), float(f), len(snap), source))
        # trip updates: every not-finished trip departing within 30 min (operating or ghost-in-TU)
        for st in schedule:
            if st.first_dep_ts - 1800 <= f <= st.last_arr_ts:
                if is_ghost[st.trip_id] and not ghost_in_tu[st.trip_id]:
                    continue
                if st.trip_id in wrong:
                    continue
                events.append(TripUpdateEvent(st.trip_id, st.route_id, float(f), None, True, st.first_dep_ts,
                                              source))
        events.append(FeedFetchEvent("TripUpdates", float(f), float(f), 0, source))
        f += FETCH_EVERY_S
    tg = pd.DataFrame([{"trip_id": tid, "is_ghost": g, "in_tu": ghost_in_tu[tid] if g else True,
                        "wrong_reported_as": wrong.get(tid)} for tid, g in is_ghost.items()])
    return SimResult(events, schedule, pd.DataFrame(truth_pings), pd.DataFrame(truth_arr), tg, outage, (w0, w1),
                     {"scenario": sc.name, "seed": sc.seed, "service_date": service_date.isoformat(),
                      "trips": len(trips), "ghosts": int(sum(is_ghost.values())), "wrong": wrong,
                      "window_utc": [datetime.fromtimestamp(w0, UTC).isoformat(),
                                     datetime.fromtimestamp(w1, UTC).isoformat()]})
