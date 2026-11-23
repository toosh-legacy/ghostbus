"""Arrival-range predictors behind one interface.

- HistoricalPredictor (fallback, Phase 3): schedule + historical delay quantiles for the stop/route/direction/
  time band; when a live vehicle has a known current delay, the range is centred on that delay instead.
- Model A (Phase 4) plugs in via gbml.serving.ModelAPredictor with the same `predict` signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from gbcore.settings import get_settings
from gbcore.timeutil import time_band


@dataclass
class Prediction:
    p10_ts: float
    p50_ts: float
    p90_ts: float
    source: str  # model | agency | schedule
    model_version: str
    features: dict


class HistoricalPredictor:
    version = "historical-quantiles-v1"

    def __init__(self, path: Path | None = None) -> None:
        path = path or get_settings().lake_dir / "curated" / "metrics" / "delay_quantiles.parquet"
        self.ok = path.exists()
        self.by_stop: dict = {}
        self.by_route: dict = {}
        if self.ok:
            q = pd.read_parquet(path)
            for r in q.itertuples():
                t = (float(r.p10), float(r.p50), float(r.p90))
                if r.stop_id is None or (isinstance(r.stop_id, float)):
                    self.by_route[(r.route_id, int(r.direction_id), r.time_band)] = t
                else:
                    self.by_stop[(r.route_id, int(r.direction_id), str(r.stop_id), r.time_band)] = t

    def quantiles(self, route_id: str, direction_id: int, stop_id: str, band: str) -> tuple[float, float, float]:
        return (self.by_stop.get((route_id, direction_id, stop_id, band))
                or self.by_route.get((route_id, direction_id, band))
                or (-60.0, 60.0, 360.0))

    def predict(self, *, route_id: str, direction_id: int, stop_id: str, sched_ts: float, sched_local_hour: int,
                current_delay_s: float | None, live: bool, **_kw) -> Prediction:
        band = time_band(sched_local_hour)
        q10, q50, q90 = self.quantiles(route_id, direction_id, stop_id, band)
        if live and current_delay_s is not None:
            centre = current_delay_s
            lo, hi = centre + (q10 - q50), centre + (q90 - q50)
        else:
            centre, lo, hi = q50, q10, q90
        return Prediction(sched_ts + lo, sched_ts + centre, sched_ts + hi, "schedule", self.version,
                          {"band": band, "current_delay_s": current_delay_s, "hist": [q10, q50, q90]})


def range_words(now: float, p10: float, p90: float) -> str:
    lo = max(0, round((p10 - now) / 60))
    hi = max(lo, round((p90 - now) / 60))
    if hi == 0:
        return "due now"
    if lo == hi:
        return f"usually about {lo} min"
    return f"usually {lo}–{hi} min"
