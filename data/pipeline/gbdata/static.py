"""Static network build for the pilot routes from one GTFS feed (current feed by default).

Outputs (lake/curated/static/):
- routes.parquet, stops.parquet, trips.parquet, stop_times.parquet
- shapes.parquet (shape_id, seq, lat, lon, x, y, dist_m) — full resolution, metric projection
- shapes_simplified.parquet (for the map)
- stop_on_shape.parquet (shape_id, stop_id, stop_sequence, dist_m, offset_m, off_shape_flag)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from gbcore.geo import to_metric
from gbcore.settings import get_settings
from shapely.geometry import LineString, Point

from .gtfs import read_table, validate_feed

OFF_SHAPE_FLAG_M = 50.0


def static_dir() -> Path:
    return get_settings().lake_dir / "curated" / "static"


def stop_distances_on_shape(line: LineString, stops_xy: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Distance along shape for an ordered list of stops, enforcing monotonic order.

    Each stop is projected onto the portion of the shape after the previous stop, which handles
    loops and shapes that pass near themselves.
    """
    out = []
    prev = 0.0
    L = line.length
    for x, y in stops_xy:
        p = Point(x, y)
        # search the remaining part of the line from prev; fallback to global projection
        best_d, best_off = None, None
        # sample candidate distances: global projection and constrained projection
        d_global = line.project(p)
        cands = [d_global]
        if prev > 0:
            from shapely.ops import substring

            rest = substring(line, prev, L)
            if rest.length > 0:
                cands.append(prev + rest.project(p))
        for d in cands:
            off = line.interpolate(d).distance(p)
            if d + 1e-6 < prev:  # going backwards is not allowed for ordered stops
                continue
            if best_off is None or off < best_off - 1e-6:
                best_d, best_off = d, off
        if best_d is None:
            best_d = prev
            best_off = line.interpolate(prev).distance(p)
        out.append((float(best_d), float(best_off)))
        prev = best_d
    return out


def build(zpath: Path, route_ids: list[str], out_dir: Path | None = None) -> dict:
    out_dir = out_dir or static_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = validate_feed(zpath)
    if missing:
        raise ValueError(f"GTFS feed missing required files: {missing}")
    rset = set(route_ids)
    routes = read_table(zpath, "routes.txt")
    routes = routes[routes["route_id"].isin(rset)]
    trips = read_table(zpath, "trips.txt")
    trips = trips[trips["route_id"].isin(rset)]
    st = read_table(zpath, "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id",
                                              "stop_sequence", "timepoint"])
    st = st[st["trip_id"].isin(set(trips["trip_id"]))].copy()
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    stops = read_table(zpath, "stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"])
    stops = stops[stops["stop_id"].isin(set(st["stop_id"]))].copy()
    stops["stop_lat"] = stops["stop_lat"].astype(float)
    stops["stop_lon"] = stops["stop_lon"].astype(float)
    sx, sy = to_metric(stops["stop_lon"].to_numpy(), stops["stop_lat"].to_numpy())
    stops["x"], stops["y"] = sx, sy
    shapes = read_table(zpath, "shapes.txt")
    shapes = shapes[shapes["shape_id"].isin(set(trips["shape_id"]))].copy()
    shapes["seq"] = shapes["shape_pt_sequence"].astype(int)
    shapes["lat"] = shapes["shape_pt_lat"].astype(float)
    shapes["lon"] = shapes["shape_pt_lon"].astype(float)
    shapes = shapes.sort_values(["shape_id", "seq"])[["shape_id", "seq", "lat", "lon"]]
    x, y = to_metric(shapes["lon"].to_numpy(), shapes["lat"].to_numpy())
    shapes["x"], shapes["y"] = x, y
    shapes["dist_m"] = shapes.groupby("shape_id", group_keys=False).apply(
        lambda g: pd.Series(np.concatenate([[0], np.cumsum(np.hypot(np.diff(g["x"]), np.diff(g["y"])))]),
                            index=g.index))
    # stop-on-shape using a representative trip per shape (longest stop pattern)
    stop_xy = stops.set_index("stop_id")[["x", "y"]]
    rows = []
    simplified = []
    for shape_id, g in shapes.groupby("shape_id"):
        line = LineString(g[["x", "y"]].to_numpy())
        simp = line.simplify(5.0)
        from gbcore.geo import to_lonlat

        lo, la = to_lonlat(*np.array(simp.coords).T)
        simplified.append(pd.DataFrame({"shape_id": shape_id, "seq": range(len(lo)), "lat": la, "lon": lo}))
        tids = trips.loc[trips["shape_id"] == shape_id, "trip_id"]
        pat = st[st["trip_id"].isin(set(tids))]
        if pat.empty:
            continue
        rep = pat.groupby("trip_id").size().idxmax()
        seq = pat[pat["trip_id"] == rep].sort_values("stop_sequence")
        xy = [tuple(stop_xy.loc[s]) for s in seq["stop_id"]]
        dists = stop_distances_on_shape(line, xy)
        for (sid, sseq), (d, off) in zip(zip(seq["stop_id"], seq["stop_sequence"]), dists):
            rows.append({"shape_id": shape_id, "stop_id": sid, "stop_sequence": int(sseq), "dist_m": d,
                         "offset_m": off, "off_shape_flag": off > OFF_SHAPE_FLAG_M, "shape_length_m": line.length})
    sos = pd.DataFrame(rows)
    routes.to_parquet(out_dir / "routes.parquet", index=False)
    stops.to_parquet(out_dir / "stops.parquet", index=False)
    trips.to_parquet(out_dir / "trips.parquet", index=False)
    st.to_parquet(out_dir / "stop_times.parquet", index=False)
    shapes.to_parquet(out_dir / "shapes.parquet", index=False)
    pd.concat(simplified).to_parquet(out_dir / "shapes_simplified.parquet", index=False)
    sos.to_parquet(out_dir / "stop_on_shape.parquet", index=False)
    cal = read_table(zpath, "calendar.txt")
    cal.to_parquet(out_dir / "calendar.parquet", index=False)
    read_table(zpath, "calendar_dates.txt").to_parquet(out_dir / "calendar_dates.parquet", index=False)
    return {"routes": len(routes), "stops": len(stops), "trips": len(trips), "stop_times": len(st),
            "shapes": shapes["shape_id"].nunique(), "stop_on_shape": len(sos),
            "stops_off_shape_gt_50m": int(sos["off_shape_flag"].sum()) if len(sos) else 0,
            "off_shape_examples": sos[sos["off_shape_flag"]].head(10).to_dict("records") if len(sos) else []}
