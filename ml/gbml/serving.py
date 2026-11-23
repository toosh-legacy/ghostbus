"""Serving: the API loads the pinned versions; predictions include the model version."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from gbcore.settings import get_settings
from gbcore.timeutil import AGENCY_TZ, is_holiday, time_band

from .data import FEATURES_A
from .registry import ModelRegistry


class ModelAPredictor:
    def __init__(self, model, version: str) -> None:
        self.m = model
        self.version = version
        self.fallback = None
        tp = get_settings().lake_dir / "curated" / "metrics" / "timepoint_delay_summary.parquet"
        self.stop_to_tp: dict[tuple[str, str], str] = {}
        if tp.exists():
            t = pd.read_parquet(tp, columns=["route_id", "time_point_id", "stop_id"]).drop_duplicates()
            self.stop_to_tp = {(r.route_id, str(r.stop_id)): r.time_point_id for r in t.itertuples()}

    def with_fallback(self, fb) -> ModelAPredictor:
        self.fallback = fb
        return self

    def features(self, *, route_id, direction_id, stop_id, sched_local_hour, current_delay_s, pattern, stop_index,
                 last_stop_index, service_date: date) -> pd.DataFrame:
        tp_j = self.stop_to_tp.get((route_id, str(stop_id)))
        tps = pattern.timepoints
        horizon = int(max(1, min(5, tps[last_stop_index + 1: stop_index + 1].sum())))
        hm = self.m.hist_median
        hist = hm["by_hour"].get((route_id, tp_j, sched_local_hour), hm["by_tp"].get((route_id, tp_j), 0.0)) if tp_j else 0.0
        row = {"delay_k": float(current_delay_s), "delay_chg": np.nan,
               "route_code": self.m.cats["route_code"].get(route_id, -1),
               "direction_id": int(direction_id), "tp_j_code": self.m.cats["tp_j_code"].get(tp_j, 250),
               "horizon": horizon,
               "sched_travel_s": float(pattern.sched_arr_s[stop_index] - pattern.sched_arr_s[last_stop_index]),
               "hour": int(sched_local_hour), "dow": (service_date.weekday() + 1) % 7,  # DuckDB dayofweek: Sun=0
               "holiday": int(is_holiday(service_date)), "recent_seg_delta": np.nan, "headway_k": np.nan,
               "hist_med_j": float(hist)}
        return pd.DataFrame([row])[FEATURES_A]

    def predict(self, *, route_id, direction_id, stop_id, sched_ts, sched_local_hour, current_delay_s, live,
                pattern=None, stop_index=None, last_stop_index=None, now=None, service_date=None, **kw):
        from gbapi.predict import Prediction

        if not live or current_delay_s is None or last_stop_index is None or pattern is None:
            return self.fallback.predict(route_id=route_id, direction_id=direction_id, stop_id=stop_id,
                                         sched_ts=sched_ts, sched_local_hour=sched_local_hour,
                                         current_delay_s=current_delay_s, live=live)
        X = self.features(route_id=route_id, direction_id=direction_id, stop_id=stop_id,
                          sched_local_hour=sched_local_hour, current_delay_s=current_delay_s, pattern=pattern,
                          stop_index=stop_index, last_stop_index=last_stop_index, service_date=service_date)
        q = self.m.predict(X).iloc[0]
        lo, mid, hi = sorted([float(q["p10"]), float(q["p50"]), float(q["p90"])])
        floor = (now or sched_ts) - sched_ts
        lo, mid, hi = max(lo, floor), max(mid, floor), max(hi, floor)
        return Prediction(sched_ts + lo, sched_ts + mid, sched_ts + hi, "model", self.version,
                          {**X.iloc[0].to_dict(), "band": time_band(sched_local_hour)})


class ModelBPriors:
    def __init__(self, model, version: str) -> None:
        self.m = model
        self.version = version

    def trip_priors(self, net, service_date: date | None = None) -> dict[str, float]:
        d = service_date or datetime.now(AGENCY_TZ).date()
        rows = []
        tids = net.trips_on(d)
        counts: dict[str, int] = {}
        for t in tids:
            p = net.pattern(t)
            if p is None:
                continue
            counts[p.route_id] = counts.get(p.route_id, 0) + 1
            h = int(p.sched_dep_s[0] // 3600) % 24
            rows.append({"trip_id": t, "route_id": p.route_id, "direction_id": p.direction_id, "hour": h,
                         "time_band": time_band(h)})
        if not rows:
            return {}
        df = pd.DataFrame(rows)
        lr = self.m.latest_rates
        df["dow"] = d.weekday()
        df["holiday"] = int(is_holiday(d))
        df["route_rate_7d"] = df["route_id"].map(lr["route_7d"])
        df["route_rate_28d"] = df["route_id"].map(lr["route_28d"])
        df["hour_rate_28d"] = [lr["band_28d"].get((r, b)) for r, b in zip(df["route_id"], df["time_band"])]
        df["trips_that_day"] = df["route_id"].map(counts)
        p = self.m.predict_proba(df)
        return dict(zip(df["trip_id"], np.round(p, 4)))


def load_pinned() -> tuple[ModelAPredictor | None, ModelBPriors | None]:
    reg = ModelRegistry()
    a, va = reg.load("model_a")
    b, vb = reg.load("model_b")
    return (ModelAPredictor(a, f"model_a-{va}") if a is not None else None,
            ModelBPriors(b, f"model_b-{vb}") if b is not None else None)


def _now_utc() -> datetime:
    return datetime.now(UTC)
