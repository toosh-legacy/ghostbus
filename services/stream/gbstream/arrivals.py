"""Arrival detection from map-matched pings (spec 9.3).

- Arrival at a stop = first moment distance along shape reaches (stop distance - 15 m), linearly
  interpolated between the two pings bracketing the crossing.
- Pass-by (no dwell) is still an arrival, marked kind="pass-by".
- Gaps > 3 minutes between the bracketing pings => confidence "low".
- First (terminal) stop: departure detection instead (leaving the stop's buffer: dist > stop + 15 m), confirmed
  by two consecutive pings beyond the buffer so GPS noise during a layover cannot fire a false departure; the
  time is interpolated between the last ping inside the buffer and the first ping outside it.
- Uses a running maximum of distance so GPS jitter backwards never re-triggers a stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ARRIVAL_BUFFER_M = 15.0
LONG_GAP_S = 180.0
PASSBY_SPEED_MS = 7.0


@dataclass
class ArrivalEvent:
    trip_id: str
    route_id: str
    vehicle_id: str
    stop_id: str
    stop_sequence: int
    stop_index: int
    arrival_ts: float
    kind: str  # stop | pass-by | departure
    confidence: str  # high | low
    gap_s: float
    source: str


@dataclass
class TripProgress:
    trip_id: str
    next_idx: int | None = None
    last_ts: float | None = None
    last_dist: float | None = None  # running max
    emitted: list[int] = field(default_factory=list)
    below: tuple[float, float] | None = None  # last raw ping inside the terminal buffer (t, d)
    first_out: tuple[float, float] | None = None  # first raw ping outside it, awaiting confirmation


def interpolate_crossing(t0: float, d0: float, t1: float, d1: float, threshold: float) -> float:
    if d1 <= d0:
        return t1
    f = (threshold - d0) / (d1 - d0)
    return t0 + min(1.0, max(0.0, f)) * (t1 - t0)


class ArrivalDetector:
    def update(self, prog: TripProgress, stop_dists: np.ndarray, stop_ids: list[str], stop_seqs: list[int],
               ts: float, dist: float, *, route_id: str, vehicle_id: str, source: str) -> list[ArrivalEvent]:
        out: list[ArrivalEvent] = []
        if prog.last_ts is None:
            # first ping: skip stops already passed (we cannot time them)
            dm = dist
            i = 0
            if dm > stop_dists[0] + ARRIVAL_BUFFER_M:
                i = 1
            while i < len(stop_dists) and i > 0 and stop_dists[i] - ARRIVAL_BUFFER_M <= dm:
                i += 1
            prog.next_idx, prog.last_ts, prog.last_dist = i, ts, dm
            return out
        if ts <= prog.last_ts:
            return out
        d0, t0 = prog.last_dist, prog.last_ts
        dm = max(d0, dist)
        gap = ts - t0
        i = prog.next_idx or 0
        if i == 0:
            thr = stop_dists[0] + ARRIVAL_BUFFER_M
            if dist < thr:
                prog.below, prog.first_out = (ts, dist), None
                prog.last_ts, prog.last_dist = ts, dist  # no running max inside the terminal buffer
                return out
            if prog.first_out is None:
                prog.first_out = (ts, dist)
                if prog.below is None:
                    prog.below = (t0, d0)
                return out  # wait for confirmation
            (tb, db), (ta, da) = prog.below, prog.first_out
            t = interpolate_crossing(tb, db, ta, da, thr)
            g0 = ta - tb
            out.append(ArrivalEvent(prog.trip_id, route_id, vehicle_id, stop_ids[0], stop_seqs[0], 0, float(t),
                                    "departure", "low" if g0 > LONG_GAP_S else "high", float(g0), source))
            prog.emitted.append(0)
            i = 1
            d0, t0 = da, ta
            dm = max(da, dist)
            gap = ts - t0
        while i < len(stop_dists):
            if i == 0:
                break
            else:
                thr = stop_dists[i] - ARRIVAL_BUFFER_M
                kind = None
            if dm < thr:
                break
            t = interpolate_crossing(t0, d0, ts, dm, thr)
            if kind is None:
                speed = (dm - d0) / gap if gap > 0 else 0.0
                kind = "pass-by" if speed > PASSBY_SPEED_MS and gap <= 90 else "stop"
            out.append(ArrivalEvent(prog.trip_id, route_id, vehicle_id, stop_ids[i], stop_seqs[i], i, float(t), kind,
                                    "low" if gap > LONG_GAP_S else "high", float(gap), source))
            prog.emitted.append(i)
            i += 1
        prog.next_idx, prog.last_ts, prog.last_dist = i, ts, dm
        return out
