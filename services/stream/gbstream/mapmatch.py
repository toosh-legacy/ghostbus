"""Map matching: GPS ping -> distance along the trip's shape (spec 9.2).

1. Project the ping (UTM 19N) onto the shape polyline; an STRtree over segments finds candidates.
2. Monotonic constraint: among candidate projections within 50 m, prefer the one closest to
   previous distance + expected travel (speed x dt). Handles loops / self-crossing shapes.
3. Reject pings > 100 m from the shape (off-route). Backward jumps > 150 m are noise unless they
   persist for 3 consecutive pings.
4. Output distance, offset, confidence and status.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from gbcore.geo import to_metric
from shapely import STRtree, box

CANDIDATE_M = 50.0
OFF_ROUTE_M = 100.0
BACKJUMP_M = 150.0
BACKJUMP_PERSIST = 3
DEFAULT_SPEED = 6.0  # m/s (~13 mph) prior for buses
MAX_SPEED = 25.0


@dataclass
class MatchResult:
    dist_m: float | None
    offset_m: float | None
    confidence: float
    status: str  # matched | off_route | backjump_noise | first


class ShapeIndex:
    """Segment index for one shape."""

    def __init__(self, xy: np.ndarray, cum: np.ndarray) -> None:
        self.a = xy[:-1]
        self.b = xy[1:]
        self.cum = cum
        self.seg_len = np.maximum(np.hypot(*(self.b - self.a).T), 1e-9)
        boxes = [box(min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
                 for p, q in zip(self.a, self.b)]
        self.tree = STRtree(boxes)

    def candidates(self, x: float, y: float, radius: float) -> list[tuple[float, float]]:
        """(dist_along, offset) of the best projection on each nearby segment, local minima only."""
        idx = self.tree.query(box(x - radius, y - radius, x + radius, y + radius))
        if len(idx) == 0:
            return []
        idx = np.sort(idx)
        a, b = self.a[idx], self.b[idx]
        ab = b - a
        t = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1]) / (self.seg_len[idx] ** 2), 0, 1)
        px, py = a[:, 0] + t * ab[:, 0], a[:, 1] + t * ab[:, 1]
        off = np.hypot(px - x, py - y)
        along = self.cum[idx] + t * self.seg_len[idx]
        # collapse runs of adjacent segments into one candidate (the best of each run)
        out: list[tuple[float, float]] = []
        run_start = 0
        for i in range(1, len(idx) + 1):
            if i == len(idx) or idx[i] != idx[i - 1] + 1:
                j = run_start + int(np.argmin(off[run_start:i]))
                out.append((float(along[j]), float(off[j])))
                run_start = i
        return out


@dataclass
class VehicleMatchState:
    shape_id: str | None = None
    dist: float | None = None
    ts: float | None = None
    speed: float = DEFAULT_SPEED
    pending_back: list[tuple[float, float]] = field(default_factory=list)


class MapMatcher:
    def __init__(self, shapes: dict) -> None:
        self.shapes = shapes
        self._idx: dict[str, ShapeIndex] = {}

    def index(self, shape_id: str) -> ShapeIndex:
        si = self._idx.get(shape_id)
        if si is None:
            s = self.shapes[shape_id]
            si = ShapeIndex(s.xy, s.cum)
            self._idx[shape_id] = si
        return si

    def match(self, st: VehicleMatchState, shape_id: str, lat: float, lon: float, ts: float) -> MatchResult:
        if st.shape_id != shape_id:
            st.shape_id, st.dist, st.ts, st.speed, st.pending_back = shape_id, None, None, DEFAULT_SPEED, []
        x, y = to_metric(lon, lat)
        si = self.index(shape_id)
        cands = si.candidates(float(x), float(y), OFF_ROUTE_M)
        if not cands:
            return MatchResult(None, None, 0.0, "off_route")
        best_off = min(o for _, o in cands)
        if best_off > OFF_ROUTE_M:
            return MatchResult(None, best_off, 0.0, "off_route")
        if st.dist is None or st.ts is None:
            # first ping on this shape: nearest candidate, prefer earliest along-distance on ties (start of trip)
            d, o = min(cands, key=lambda c: (round(c[1] / 10), c[0]))
            st.dist, st.ts = d, ts
            return MatchResult(d, o, self._conf(o), "matched")
        dt = max(0.0, ts - st.ts)
        expected = st.dist + st.speed * dt
        near = [c for c in cands if c[1] <= CANDIDATE_M] or [min(cands, key=lambda c: c[1])]
        d, o = min(near, key=lambda c: abs(c[0] - expected) + 0.5 * c[1])
        if d < st.dist - BACKJUMP_M:
            st.pending_back.append((d, ts))
            if len(st.pending_back) < BACKJUMP_PERSIST:
                return MatchResult(st.dist, o, 0.2, "backjump_noise")
            st.pending_back = []  # persisted: accept (e.g. trip restarted / layover loop)
        else:
            st.pending_back = []  # small backwards jitter is GPS noise: keep the raw projection
        if dt > 0:
            inst = (d - st.dist) / dt
            if 0 <= inst <= MAX_SPEED:
                st.speed = 0.7 * st.speed + 0.3 * max(inst, 1.0)
        st.dist, st.ts = d, ts
        return MatchResult(d, o, self._conf(o), "matched")

    @staticmethod
    def _conf(offset: float) -> float:
        return float(max(0.0, min(1.0, 1.0 - offset / OFF_ROUTE_M)))
