"""Stream processor (spec 7.4 steps 3-9). Deterministic and event-time only.

The processor is a pure state machine: `process(event)` never reads the wall clock, so live, replay
(at any speed) and simulator input produce identical outputs for identical event sequences.

Ordering: per-vehicle reorder buffer with allowed lateness (60 s). An event is released once the
vehicle's watermark (max vehicle_ts seen) or the global event clock (max fetch_ts) has passed
event_ts + lateness. Events older than the last released event for that vehicle are "late" (past the
watermark): counted and dropped. Within the lateness window, output order == sorted order.
Live positions for the map are updated immediately (latest by event time); matching, arrivals and
the curated outputs use the ordered stream.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from gbcore.interfaces import Broadcaster, LiveState, MemoryLiveState
from gbcore.timeutil import service_date_for, service_day_reference
from gbschemas.events import Event, FeedFetchEvent, TripUpdateEvent, VehicleEvent

from .arrivals import ArrivalDetector, ArrivalEvent, TripProgress
from .ghost import (
    REASSIGN_EARLY_S,
    REASSIGN_WINDOW_S,
    FeedHealth,
    GhostEval,
    ScheduledTrip,
    evaluate_trip,
    feed_health,
)
from .mapmatch import MapMatcher, VehicleMatchState
from .network import Network

LATENESS_S = 60.0
GHOST_EVAL_EVERY_S = 30.0
WARMUP_S = 180.0
DEDUPE_HORIZON_S = 3600.0


@dataclass
class Stats:
    events_in: int = 0
    vehicle_events: int = 0
    duplicates_dropped: int = 0
    late_dropped: int = 0
    reordered: int = 0
    matched: int = 0
    off_route: int = 0
    backjump_noise: int = 0
    unknown_trip: int = 0
    arrivals: int = 0
    ghost_evals: int = 0
    fetches: int = 0
    max_lag_s: float = 0.0


@dataclass
class PingOut:
    vehicle_id: str
    trip_id: str | None
    route_id: str | None
    vehicle_ts: float
    fetch_ts: float
    lat: float
    lon: float
    dist_m: float | None
    offset_m: float | None
    confidence: float
    match_status: str
    source: str


@dataclass
class _Buf:
    heap: list = field(default_factory=list)
    watermark: float = float("-inf")
    last_released: float = float("-inf")
    seq: int = 0


def schedule_for_date(net: Network, d: date, route_ids: set[str] | None = None) -> list[ScheduledTrip]:
    ref = service_day_reference(d).astimezone(UTC).timestamp()
    out = []
    for tid in net.trips_on(d, route_ids):
        p = net.pattern(tid)
        if p is None or len(p.stop_ids) < 2:
            continue
        out.append(ScheduledTrip(tid, p.route_id, p.direction_id, d.isoformat(), ref + float(p.sched_dep_s[0]),
                                 ref + float(p.sched_arr_s[-1]), p.stop_ids[0]))
    return out


class Processor:
    def __init__(self, net: Network, *, live_state: LiveState | None = None, broadcaster: Broadcaster | None = None,
                 schedule: list[ScheduledTrip] | None = None, route_ids: set[str] | None = None,
                 typical_rates: dict[int, float] | None = None, priors: dict[str, float] | None = None,
                 auto_schedule: bool = True) -> None:
        self.net = net
        self.matcher = MapMatcher(net.shapes)
        self.detector = ArrivalDetector()
        self.state = live_state or MemoryLiveState()
        self.bc = broadcaster
        self.route_ids = route_ids
        self.typical_rates = typical_rates or {}
        self.priors = priors or {}
        self.stats = Stats()
        self.clock: float = float("-inf")  # event-time clock (max fetch_ts seen)
        self._seen: dict[tuple[str, float], float] = {}
        self._seen_heap: list[tuple[float, tuple[str, float]]] = []
        self._bufs: dict[str, _Buf] = defaultdict(_Buf)
        self._match: dict[tuple[str, str], VehicleMatchState] = {}
        self._prog: dict[tuple[str, str], TripProgress] = {}
        self._trip_last_seen: dict[str, float] = {}
        self._trip_vehicle: dict[str, str] = {}
        self._trip_departed: dict[tuple[str, str], float] = {}  # (vehicle, trip) -> departure ts
        self._all_by_trip: dict[str, ScheduledTrip] | None = None
        self._trip_last_arrival: dict[str, tuple[int, float, str]] = {}  # trip -> (stop_index, ts, stop_id)
        self._tu_last: dict[str, float] = {}
        self._last_vp_fetch: float | None = None
        self._first_fetch: float | None = None
        self._last_vp_rate: int | None = None
        self._next_ghost = float("-inf")
        self._schedule: dict[str, ScheduledTrip] = {t.trip_id: t for t in (schedule or [])}
        self._auto_schedule = auto_schedule and schedule is None
        self._loaded_dates: set[date] = set()
        self._last_ghost_state: dict[str, dict] = {}
        self.pings: list[PingOut] = []
        self.arrivals: list[ArrivalEvent] = []
        self.ghost_evals: list[GhostEval] = []

    # ------------------------------------------------------------------ public
    def process(self, e: Event) -> None:
        self.stats.events_in += 1
        if isinstance(e, VehicleEvent):
            self._on_vehicle(e)
        elif isinstance(e, TripUpdateEvent):
            self._tu_last[e.trip_id] = e.fetch_ts if e.has_prediction else self._tu_last.get(e.trip_id, 0)
            self._advance_clock(e.fetch_ts)
        elif isinstance(e, FeedFetchEvent):
            self.stats.fetches += 1
            if e.feed == "VehiclePositions" and e.ok:
                if self._first_fetch is None:
                    self._first_fetch = e.fetch_ts
                self._last_vp_fetch = e.fetch_ts
                self._last_vp_rate = e.n_entities
            self._advance_clock(e.fetch_ts)

    def flush(self) -> None:
        """Release everything buffered (end of stream)."""
        for vid, b in self._bufs.items():
            while b.heap:
                self._release(heapq.heappop(b.heap)[2])
        self._maybe_ghost(self.clock, force=True)

    def outputs(self) -> dict:
        return {"pings": [asdict(p) for p in self.pings], "arrivals": [asdict(a) for a in self.arrivals],
                "ghost_evals": [asdict(g) for g in self.ghost_evals], "stats": asdict(self.stats)}

    def trip_live(self, trip_id: str) -> dict:
        la = self._trip_last_arrival.get(trip_id)
        return {"vehicle_id": self._trip_vehicle.get(trip_id), "last_seen": self._trip_last_seen.get(trip_id),
                "last_stop_index": la[0] if la else None, "last_arrival_ts": la[1] if la else None,
                "has_tu": trip_id in self._tu_last}

    def health(self) -> dict:
        h = feed_health(self.clock, self._last_vp_fetch, self._last_vp_rate, self._typical(self.clock))
        return {"healthy": h.healthy, "reason": h.reason, "last_fetch_ts": h.last_fetch_ts,
                "last_rate": h.last_rate, "clock": self.clock}

    # ----------------------------------------------------------------- internals
    def _typical(self, ts: float) -> float | None:
        if not self.typical_rates or ts == float("-inf"):
            return None
        return self.typical_rates.get(datetime.fromtimestamp(ts, UTC).hour)

    def _on_vehicle(self, e: VehicleEvent) -> None:
        self.stats.vehicle_events += 1
        k = e.key()
        if k in self._seen:
            self.stats.duplicates_dropped += 1
            return
        self._seen[k] = e.vehicle_ts
        heapq.heappush(self._seen_heap, (e.vehicle_ts, k))
        b = self._bufs[e.vehicle_id]
        if e.vehicle_ts <= b.last_released:
            self.stats.late_dropped += 1
            return
        if e.vehicle_ts < b.watermark:
            self.stats.reordered += 1
        b.watermark = max(b.watermark, e.vehicle_ts)
        b.seq += 1
        heapq.heappush(b.heap, (e.vehicle_ts, b.seq, e))
        # immediate live position (latest by event time) for the map
        cur = self.state.get_meta(f"vlatest:{e.vehicle_id}")
        if cur is None or e.vehicle_ts >= cur:
            self.state.set_meta(f"vlatest:{e.vehicle_id}", e.vehicle_ts)
            data = {"vehicle_id": e.vehicle_id, "trip_id": e.trip_id, "route_id": e.route_id, "lat": e.lat,
                    "lon": e.lon, "bearing": e.bearing, "vehicle_ts": e.vehicle_ts, "source": e.source}
            prev = self.state.get_vehicle(e.vehicle_id) or {}
            data["dist_m"] = prev.get("dist_m")
            data["match_status"] = prev.get("match_status")
            self.state.set_vehicle(e.vehicle_id, data)
            if self.bc:
                self.bc.publish({"type": "vehicle", **data})
        self._drain(b, max(b.watermark, self.clock) - LATENESS_S)
        self._advance_clock(e.fetch_ts)

    def _drain(self, b: _Buf, upto: float) -> None:
        while b.heap and b.heap[0][0] <= upto:
            ev = heapq.heappop(b.heap)[2]
            b.last_released = ev.vehicle_ts
            self._release(ev)

    def _advance_clock(self, ts: float) -> None:
        if ts <= self.clock:
            return
        self.clock = ts
        # global watermark releases quiet vehicles' buffers
        for b in self._bufs.values():
            if b.heap and b.heap[0][0] <= ts - LATENESS_S:
                self._drain(b, ts - LATENESS_S)
        while self._seen_heap and self._seen_heap[0][0] < ts - DEDUPE_HORIZON_S:
            _, k = heapq.heappop(self._seen_heap)
            self._seen.pop(k, None)
        self._maybe_ghost(ts)

    def _release(self, e: VehicleEvent) -> None:
        self.stats.max_lag_s = max(self.stats.max_lag_s, max(0.0, self.clock - e.vehicle_ts))
        tid = e.trip_id
        if not self.net.has_trip(tid):
            self.stats.unknown_trip += 1
            self.pings.append(PingOut(e.vehicle_id, tid, e.route_id, e.vehicle_ts, e.fetch_ts, e.lat, e.lon, None,
                                      None, 0.0, "unknown_trip", e.source))
            return
        p = self.net.pattern(tid)
        assert p is not None
        key = (e.vehicle_id, tid)
        ms = self._match.setdefault(key, VehicleMatchState())
        r = self.matcher.match(ms, p.shape_id, e.lat, e.lon, e.vehicle_ts)
        self.pings.append(PingOut(e.vehicle_id, tid, p.route_id, e.vehicle_ts, e.fetch_ts, e.lat, e.lon, r.dist_m,
                                  r.offset_m, r.confidence, r.status, e.source))
        if r.status == "off_route":
            self.stats.off_route += 1
            return
        if r.status == "backjump_noise":
            self.stats.backjump_noise += 1
            return
        self.stats.matched += 1
        self._trip_last_seen[tid] = max(self._trip_last_seen.get(tid, 0.0), e.vehicle_ts)
        self._trip_vehicle[tid] = e.vehicle_id
        v = self.state.get_vehicle(e.vehicle_id)
        if v is not None and v.get("trip_id") == tid:
            v = dict(v, dist_m=r.dist_m, match_status=r.status)
            self.state.set_vehicle(e.vehicle_id, v)
        prog = self._prog.setdefault(key, TripProgress(tid))
        arrs = self.detector.update(prog, p.stop_dists, p.stop_ids, p.stop_seqs, e.vehicle_ts, r.dist_m,
                                    route_id=p.route_id, vehicle_id=e.vehicle_id, source=e.source)
        for a in arrs:
            if a.kind == "departure":
                self._trip_departed[(e.vehicle_id, tid)] = a.arrival_ts
            self._trip_last_arrival[tid] = (a.stop_index, a.arrival_ts, a.stop_id)
            self.stats.arrivals += 1
            self.arrivals.append(a)
            if self.bc:
                self.bc.publish({"type": "arrival", **asdict(a)})

    def _possible_reassignments(self, active: list[tuple[str, ScheduledTrip]], ts: float) -> dict[str, str]:
        """ghost-def v1.1: vehicles running their claimed trip > 10 min early likely serve an earlier unmatched trip."""
        by_trip = {t.trip_id: t for _, t in active}
        if self._all_by_trip is None or len(self._all_by_trip) != len(self._schedule):
            self._all_by_trip = {t.trip_id: t for t in self._schedule.values()}
        out: dict[str, str] = {}
        for (vid, claimed), dep_ts in self._trip_departed.items():
            ct = self._all_by_trip.get(claimed)
            if ct is None or ct.first_dep_ts - dep_ts <= REASSIGN_EARLY_S:
                continue
            cands = [t for t in self._all_by_trip.values() if t.route_id == ct.route_id
                     and t.direction_id == ct.direction_id and t.trip_id != claimed
                     and t.trip_id not in self._trip_last_seen and abs(t.first_dep_ts - dep_ts) <= REASSIGN_WINDOW_S]
            if cands:
                best = min(cands, key=lambda t: (abs(t.first_dep_ts - dep_ts), t.trip_id))
                if best.trip_id not in by_trip:
                    continue
                out.setdefault(best.trip_id, f"vehicle {vid} reports trip {claimed} while running "
                                             f"{(ct.first_dep_ts - dep_ts) / 60:.0f} min ahead of it (likely wrong trip assignment)")
        return out

    def _ensure_schedule(self, ts: float) -> None:
        if not self._auto_schedule:
            return
        now = datetime.fromtimestamp(ts, UTC)
        sd = service_date_for(now)
        for d in (sd - timedelta(days=1), sd):
            if d not in self._loaded_dates:
                self._loaded_dates.add(d)
                for t in schedule_for_date(self.net, d, self.route_ids):
                    self._schedule[t.trip_id + "@" + t.service_date] = t

    def _maybe_ghost(self, ts: float, force: bool = False) -> None:
        if ts == float("-inf") or (ts < self._next_ghost and not force):
            return
        self._next_ghost = (ts // GHOST_EVAL_EVERY_S + 1) * GHOST_EVAL_EVERY_S
        self._ensure_schedule(ts)
        h = feed_health(ts, self._last_vp_fetch, self._last_vp_rate, self._typical(ts))
        if self._first_fetch is None or ts - self._first_fetch < WARMUP_S:
            # right after (re)start the reorder buffer has not released pings yet: never call ghosts then
            h = FeedHealth(False, "warming up: fewer than 3 minutes of feed observed since start", h.last_fetch_ts,
                           h.last_rate)
        self.state.set_meta("feed_health", {"healthy": h.healthy, "reason": h.reason,
                                            "last_fetch_ts": h.last_fetch_ts, "clock": ts})
        active = [(k, t) for k, t in self._schedule.items() if t.first_dep_ts - 1800 <= ts <= t.last_arr_ts + 600]
        reassign = self._possible_reassignments(active, ts)
        for key, t in active:
            g = evaluate_trip(ts, t, h, self._trip_last_seen.get(t.trip_id), self._tu_last.get(t.trip_id),
                              self.priors.get(t.trip_id), reassign.get(t.trip_id))
            self.stats.ghost_evals += 1
            self.ghost_evals.append(g)
            prev = self._last_ghost_state.get(key)
            d = asdict(g)
            d["first_dep_ts"] = t.first_dep_ts
            self.state.set_ghost(key, d)
            self._last_ghost_state[key] = d
            if self.bc and (prev is None or prev["state"] != g.state):
                self.bc.publish({"type": "ghost", **d})
