"""Online evaluation: delayed-label join of logged predictions to detected arrivals (drift lives in drift.py)."""

from __future__ import annotations

import pandas as pd


def join_predictions_to_arrivals(prediction_log: list[dict], arrivals: list) -> pd.DataFrame:
    """Each logged prediction (trip, stop, made_at) joined to the arrival later detected at that stop."""
    if not prediction_log or not arrivals:
        return pd.DataFrame()
    pl = pd.DataFrame(prediction_log)
    ar = pd.DataFrame([{"trip_id": a.trip_id, "stop_id": a.stop_id, "arrival_ts": a.arrival_ts} for a in arrivals
                       if a.kind != "departure"])
    if ar.empty:
        return pd.DataFrame()
    ar = ar.groupby(["trip_id", "stop_id"], as_index=False)["arrival_ts"].min()
    j = pl.merge(ar, on=["trip_id", "stop_id"])
    return j[j["arrival_ts"] > j["made_at"]]  # only outcomes that happened after the prediction


def live_coverage(prediction_log: list[dict], arrivals: list) -> dict:
    j = join_predictions_to_arrivals(prediction_log, arrivals)
    if j.empty:
        return {"n": 0, "coverage": None, "p50_mae_s": None}
    inside = (j["arrival_ts"] >= j["p10"]) & (j["arrival_ts"] <= j["p90"])
    out = {"n": int(len(j)), "coverage": float(inside.mean()), "p50_mae_s": float((j["arrival_ts"] - j["p50"]).abs().mean())}
    out["by_model_version"] = {k: float(((g["arrival_ts"] >= g["p10"]) & (g["arrival_ts"] <= g["p90"])).mean())
                               for k, g in j.groupby("model_version")}
    return out
