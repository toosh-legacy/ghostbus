"""Geometry utilities: metric projection (UTM 19N for Boston) and polyline helpers."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point

METRIC_CRS = "EPSG:32619"  # UTM zone 19N


@lru_cache
def _fwd() -> Transformer:
    return Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)


@lru_cache
def _inv() -> Transformer:
    return Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)


def to_metric(lon, lat):
    """lon/lat (deg) -> x/y (m). Accepts scalars or arrays."""
    return _fwd().transform(lon, lat)


def to_lonlat(x, y):
    return _inv().transform(x, y)


def line_from_lonlat(lons, lats) -> LineString:
    x, y = to_metric(np.asarray(lons, dtype=float), np.asarray(lats, dtype=float))
    return LineString(np.column_stack([x, y]))


def project_point(line: LineString, x: float, y: float) -> tuple[float, float]:
    """(distance along line, perpendicular offset) in metres."""
    p = Point(x, y)
    d = line.project(p)
    return float(d), float(line.interpolate(d).distance(p))


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))
