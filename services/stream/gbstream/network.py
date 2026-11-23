"""In-memory static network for the pilot routes: trips, shapes, stops on shapes, schedules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from gbcore.settings import get_settings
from gbcore.timeutil import gtfs_time_to_seconds


@dataclass
class Shape:
    shape_id: str
    xy: np.ndarray  # (n, 2) metres
    cum: np.ndarray  # (n,) distance along shape at each vertex
    lat: np.ndarray
    lon: np.ndarray

    @property
    def length(self) -> float:
        return float(self.cum[-1])


@dataclass
class TripPattern:
    trip_id: str
    route_id: str
    direction_id: int
    shape_id: str
    service_id: str
    stop_ids: list[str]
    stop_seqs: list[int]
    stop_dists: np.ndarray  # dist along shape per stop
    sched_arr_s: np.ndarray  # GTFS seconds since service-day reference
    sched_dep_s: np.ndarray
    timepoints: np.ndarray  # bool


class Network:
    def __init__(self, static_dir: Path | None = None) -> None:
        d = static_dir or get_settings().lake_dir / "curated" / "static"
        self.dir = d
        self.routes = pd.read_parquet(d / "routes.parquet")
        self.stops = pd.read_parquet(d / "stops.parquet").set_index("stop_id")
        self.trips_df = pd.read_parquet(d / "trips.parquet")
        shapes = pd.read_parquet(d / "shapes.parquet")
        self.shapes: dict[str, Shape] = {}
        for sid, g in shapes.groupby("shape_id"):
            self.shapes[sid] = Shape(sid, g[["x", "y"]].to_numpy(), g["dist_m"].to_numpy(), g["lat"].to_numpy(),
                                     g["lon"].to_numpy())
        self.sos = pd.read_parquet(d / "stop_on_shape.parquet")
        st = pd.read_parquet(d / "stop_times.parquet")
        st["arr_s"] = gtfs_time_to_seconds(st["arrival_time"])
        st["dep_s"] = gtfs_time_to_seconds(st["departure_time"])
        st = st.sort_values(["trip_id", "stop_sequence"])
        self._st = st
        self.calendar = pd.read_parquet(d / "calendar.parquet")
        self.calendar_dates = pd.read_parquet(d / "calendar_dates.parquet")
        self._patterns: dict[str, TripPattern] = {}
        self._trip_meta = self.trips_df.set_index("trip_id")
        self._st_groups = {k: g for k, g in st.groupby("trip_id")}
        sos_key = self.sos.set_index(["shape_id", "stop_id", "stop_sequence"])["dist_m"]
        self._sos_key = sos_key.to_dict()
        self._sos_stop = self.sos.groupby(["shape_id", "stop_id"])["dist_m"].min().to_dict()

    def has_trip(self, trip_id: str | None) -> bool:
        return trip_id is not None and trip_id in self._st_groups

    def pattern(self, trip_id: str) -> TripPattern | None:
        p = self._patterns.get(trip_id)
        if p is not None:
            return p
        if trip_id not in self._st_groups:
            return None
        meta = self._trip_meta.loc[trip_id]
        g = self._st_groups[trip_id]
        shape_id = meta["shape_id"]
        dists = []
        for sid, seq in zip(g["stop_id"], g["stop_sequence"]):
            dd = self._sos_key.get((shape_id, sid, int(seq)))
            if dd is None:
                dd = self._sos_stop.get((shape_id, sid), np.nan)
            dists.append(dd)
        dists = np.array(dists, dtype=float)
        # fill gaps monotonically
        if np.isnan(dists).any():
            s = pd.Series(dists).interpolate(limit_direction="both")
            dists = s.to_numpy()
        dists = np.maximum.accumulate(dists)
        p = TripPattern(trip_id, meta["route_id"], int(meta["direction_id"]), shape_id, meta["service_id"],
                        list(g["stop_id"]), [int(x) for x in g["stop_sequence"]], dists,
                        g["arr_s"].to_numpy(dtype=float), g["dep_s"].to_numpy(dtype=float),
                        (g["timepoint"].fillna("1") == "1").to_numpy())
        self._patterns[trip_id] = p
        return p

    def active_services(self, d: date) -> set[str]:
        from gbdata.gtfs import expand_service

        return expand_service(self.calendar, self.calendar_dates, d, d)[d]

    def trips_on(self, d: date, route_ids: set[str] | None = None) -> list[str]:
        sids = self.active_services(d)
        t = self.trips_df[self.trips_df["service_id"].isin(sids)]
        if route_ids:
            t = t[t["route_id"].isin(route_ids)]
        return list(t["trip_id"])

    def stop_name(self, stop_id: str) -> str:
        try:
            return str(self.stops.loc[stop_id, "stop_name"])
        except KeyError:
            return stop_id


@lru_cache
def get_network() -> Network:
    return Network()
