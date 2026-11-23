"""Score processor outputs against simulator ground truth (map matching, arrivals, ghosts)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from gbstream.ghost import FEED_STALE_S
from gbstream.network import Network
from gbstream.processor import Processor

from .simulator import SCENARIOS, SimResult, simulate

DEFAULT_DATE = date(2026, 11, 24)  # a Tuesday covered by the current GTFS feed


def run_processor(net: Network, sim: SimResult) -> Processor:
    proc = Processor(net, schedule=sim.schedule)
    for e in sim.events:
        proc.process(e)
    proc.flush()
    return proc


def score(sim: SimResult, proc: Processor) -> dict:
    out = proc.outputs()
    pings = pd.DataFrame(out["pings"])
    wrong_vehicles = set(sim.truth_pings.loc[sim.truth_pings["trip_id"] != sim.truth_pings["reported_trip_id"],
                                             "vehicle_id"])
    res: dict = {"scenario": sim.meta["scenario"], "trips": sim.meta["trips"], "ghosts_true": sim.meta["ghosts"],
                 "stats": out["stats"]}
    # --- map matching
    m = pings[pings["match_status"] == "matched"].merge(sim.truth_pings, on=["vehicle_id", "vehicle_ts"])
    m = m[~m["vehicle_id"].isin(wrong_vehicles)]
    err = (m["dist_m"] - m["true_dist"]).abs()
    res["mapmatch"] = {"n": int(len(err)), "median_m": float(err.median()), "p90_m": float(err.quantile(0.9)),
                       "match_rate": float((pings["match_status"] == "matched").mean())}
    # --- arrivals
    arr = pd.DataFrame(out["arrivals"])
    truth = sim.truth_arrivals
    if len(arr):
        arr = arr[~arr["vehicle_id"].isin(wrong_vehicles)]
        j = arr.merge(truth, left_on=["trip_id", "stop_index"], right_on=["trip_id", "stop_index"])
        ae = (j["arrival_ts"] - j["true_ts"]).abs()
        hi = j["confidence"] == "high"
        res["arrivals"] = {"n_detected": int(len(arr)), "n_matched_truth": int(len(j)),
                           "recall": float(len(j) / max(1, len(truth))),
                           "median_s": float(ae.median()), "p90_s": float(ae.quantile(0.9)),
                           "median_s_high_conf": float(ae[hi].median()) if hi.any() else None}
    else:
        res["arrivals"] = {"n_detected": 0, "median_s": float("nan"), "p90_s": float("nan")}
    # --- ghosts (final state per trip = last evaluation within the trip's window)
    ge = pd.DataFrame(out["ghost_evals"])
    sched = pd.DataFrame([s.__dict__ for s in sim.schedule])
    ge = ge.merge(sched[["trip_id", "last_arr_ts"]], on="trip_id")
    final = ge[ge["evaluated_at"] <= ge["last_arr_ts"] + 600].sort_values("evaluated_at").groupby("trip_id").last()
    tg = sim.truth_ghosts.set_index("trip_id")
    f = final.join(tg, how="inner")
    known = f[f["state"] != "unknown"]
    pred = known["state"] == "high"
    true = known["is_ghost"].astype(bool)
    tp = int((pred & true).sum())
    fp = int((pred & ~true).sum())
    fn = int((~pred & true).sum())
    res["ghost"] = {"trips_scored": int(len(known)), "unknown_final": int((f["state"] == "unknown").sum()),
                    "tp": tp, "fp": fp, "fn": fn,
                    "precision": tp / (tp + fp) if tp + fp else 1.0,
                    "recall": tp / (tp + fn) if tp + fn else 1.0}
    if sim.outage:
        o0, o1 = sim.outage
        w = ge[(ge["evaluated_at"] >= o0 + FEED_STALE_S) & (ge["evaluated_at"] < o1)]
        res["ghost"]["outage_unknown_share"] = float((w["state"] == "unknown").mean()) if len(w) else None
        res["ghost"]["outage_evals"] = int(len(w))
    return res


def run_scenario(name: str, net: Network | None = None, service_date: date = DEFAULT_DATE, hours: float = 2.0,
                 route_ids: list[str] | None = None) -> dict:
    net = net or Network()
    sim = simulate(net, name, service_date, hours=hours, route_ids=route_ids)
    proc = run_processor(net, sim)
    return score(sim, proc)


def gates(r: dict) -> dict:
    g = {"mapmatch_median_le_25m": r["mapmatch"]["median_m"] <= 25,
         "arrival_median_le_20s": r["arrivals"]["median_s"] <= 20,
         "arrival_p90_le_60s": r["arrivals"]["p90_s"] <= 60,
         "ghost_precision_ge_0.9": r["ghost"]["precision"] >= 0.9,
         "ghost_recall_ge_0.9": r["ghost"]["recall"] >= 0.9}
    if "outage_unknown_share" in r["ghost"]:
        g["outage_100pct_unknown"] = r["ghost"]["outage_unknown_share"] == 1.0
    return g


def run_all(net: Network | None = None, **kw) -> list[dict]:
    net = net or Network()
    out = []
    for name in SCENARIOS:
        r = run_scenario(name, net, **kw)
        r["gates"] = gates(r)
        out.append(r)
    return out


if __name__ == "__main__":
    import json
    import time

    t = time.time()
    net = Network()
    for name in SCENARIOS:
        r = run_scenario(name, net)
        r["gates"] = gates(r)
        r.pop("stats")
        print(json.dumps(r, default=lambda x: float(x) if isinstance(x, np.floating) else str(x)))
    print("elapsed", time.time() - t)
