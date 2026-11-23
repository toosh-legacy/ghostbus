"""Training data for Model A (downstream delay) and Model B (missed-trip probability).

Leakage rules (spec 10, hard rule 5):
- Model A: every feature for a sample predicted at time t_pred (= actual time at timepoint k) uses only data
  timestamped strictly before t_pred. The "recent segment delay" feature is an ASOF join on segments completed
  (actual at j) strictly before t_pred. Historical-median features are fit on the training slice only.
- Model B: features use labels from service dates strictly before the predicted service date (4:00 AM cutoff).
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
from gbcore.settings import get_settings
from gbcore.timeutil import is_holiday

MAX_HORIZON = 5
FEATURES_A = ["delay_k", "delay_chg", "route_code", "direction_id", "tp_j_code", "horizon", "sched_travel_s",
              "hour", "dow", "holiday", "recent_seg_delta", "headway_k", "hist_med_j"]
CATEGORICAL_A = ["route_code", "tp_j_code"]


def _con() -> duckdb.DuckDBPyConnection:
    s = get_settings()
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("SET enable_progress_bar=false")
    p = (s.lake_dir / "curated" / "arrdep").as_posix()
    con.execute(f"CREATE VIEW arr AS SELECT * FROM read_parquet('{p}/**/*.parquet', hive_partitioning=true, hive_types_autocast=false)")
    return con


def build_model_a_samples(sample_pct: int = 12, seed: int = 7, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """Pairs (k -> j) within a trip, j up to MAX_HORIZON timepoints ahead. Trips sampled by a stable hash."""
    con = con or _con()
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ev AS
        SELECT service_date, route_id, direction_id, half_trip_id, time_point_id, time_point_order, sched_s, actual_s,
               delay_s, headway, epoch(actual_utc) AS t_act,
               row_number() OVER (PARTITION BY service_date, route_id, half_trip_id ORDER BY time_point_order, sched_s) AS idx
        FROM arr WHERE actual_s IS NOT NULL AND abs(delay_s) <= 7200
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ev2 AS
        SELECT *, delay_s - lag(delay_s) OVER (PARTITION BY service_date, route_id, half_trip_id ORDER BY idx) AS delay_chg
        FROM ev
    """)
    # all segments (for the recent-segment feature), every trip
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE seg AS
        SELECT k.service_date, k.route_id, k.direction_id, k.time_point_id AS tp_k, j.time_point_id AS tp_j,
               j.t_act AS t_done, (j.delay_s - k.delay_s) AS delta
        FROM ev2 k JOIN ev2 j ON k.service_date = j.service_date AND k.route_id = j.route_id
             AND k.half_trip_id = j.half_trip_id AND j.idx BETWEEN k.idx + 1 AND k.idx + {MAX_HORIZON}
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE seg_rm AS
        SELECT *, median(delta) OVER (PARTITION BY service_date, route_id, direction_id, tp_k, tp_j ORDER BY t_done
                                      ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rm3
        FROM seg
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE samp AS
        SELECT k.service_date, k.route_id, k.direction_id, k.half_trip_id, k.time_point_id AS tp_k, j.time_point_id AS tp_j,
               k.t_act AS t_pred, j.t_act AS t_target, k.delay_s AS delay_k, k.delay_chg, j.idx - k.idx AS horizon,
               j.sched_s - k.sched_s AS sched_travel_s, (j.sched_s // 3600) % 24 AS hour_j,
               (k.sched_s // 3600) % 24 AS hour, dayofweek(k.service_date) AS dow, k.headway AS headway_k,
               j.delay_s AS y
        FROM ev2 k JOIN ev2 j ON k.service_date = j.service_date AND k.route_id = j.route_id
             AND k.half_trip_id = j.half_trip_id AND j.idx BETWEEN k.idx + 1 AND k.idx + {MAX_HORIZON}
        WHERE hash(k.half_trip_id || CAST(k.service_date AS VARCHAR) || '{seed}') % 100 < {sample_pct}
          AND j.t_act > k.t_act  -- drop pairs whose recorded times are out of order (data quirk)
    """)
    df = con.execute("""
        SELECT s.*, r.rm3 AS recent_seg_delta, r.t_done AS recent_seg_t
        FROM samp s ASOF LEFT JOIN seg_rm r
          ON s.service_date = r.service_date AND s.route_id = r.route_id AND s.direction_id = r.direction_id
         AND s.tp_k = r.tp_k AND s.tp_j = r.tp_j AND s.t_pred > r.t_done
    """).df()
    df["service_date"] = pd.to_datetime(df["service_date"])
    df["holiday"] = df["service_date"].dt.date.map(is_holiday).astype(int)
    return df


def add_hist_median(train: pd.DataFrame, *others: pd.DataFrame) -> tuple[pd.DataFrame, ...]:
    """Baseline 3 / feature: historical median delay at j by route, timepoint and hour — fit on train only."""
    med = train.groupby(["route_id", "tp_j", "hour_j"])["y"].median().rename("hist_med_j")
    med2 = train.groupby(["route_id", "tp_j"])["y"].median().rename("hist_med_j2")
    out = []
    for d in (train, *others):
        d = d.join(med, on=["route_id", "tp_j", "hour_j"]).join(med2, on=["route_id", "tp_j"])
        d["hist_med_j"] = d["hist_med_j"].fillna(d["hist_med_j2"]).fillna(0.0)
        out.append(d.drop(columns=["hist_med_j2"]))
    return tuple(out)


def encode_categories(df: pd.DataFrame, cats: dict[str, dict] | None = None) -> tuple[pd.DataFrame, dict]:
    cats = cats or {
        "route_code": {r: i for i, r in enumerate(sorted(df["route_id"].unique()))},
        "tp_j_code": {t: i for i, t in enumerate(
            df["tp_j"].value_counts().head(250).index)},
    }
    df = df.copy()
    df["route_code"] = df["route_id"].map(cats["route_code"]).fillna(-1).astype(int)
    df["tp_j_code"] = df["tp_j"].map(cats["tp_j_code"]).fillna(250).astype(int)
    return df, cats


# ---------------------------------------------------------------------------------------------- Model B
FEATURES_B = ["route_code", "direction_id", "hour", "dow", "holiday", "route_rate_7d", "route_rate_28d",
              "hour_rate_28d", "trips_that_day"]


def build_model_b_frame() -> pd.DataFrame:
    s = get_settings()
    lab = pd.read_parquet(s.lake_dir / "curated" / "metrics" / "trip_labels.parquet")
    lab = lab[lab["label"] != "unknown"].copy()  # unknown route-days excluded from training and evaluation
    lab["service_date"] = pd.to_datetime(lab["service_date"])
    lab["hour"] = (lab["first_sched_s"] // 3600) % 24
    lab["dow"] = lab["service_date"].dt.dayofweek
    lab["holiday"] = lab["service_date"].dt.date.map(is_holiday).astype(int)
    lab["y"] = lab["missed"].astype(int)
    daily = lab.groupby(["route_id", "service_date"]).agg(m=("y", "sum"), n=("y", "size")).reset_index()
    band = lab.groupby(["route_id", "time_band", "service_date"]).agg(m=("y", "sum"), n=("y", "size")).reset_index()

    def rolling_rate(df: pd.DataFrame, keys: list[str], days: int, name: str) -> pd.DataFrame:
        # rate over the `days` service dates strictly before each date (cutoff: 4:00 AM on the service date)
        rows = []
        for k, g in df.groupby(keys):
            g = g.set_index("service_date").sort_index()
            full = g[["m", "n"]].asfreq("D", fill_value=0)
            r = full.rolling(f"{days}D", closed="left").sum()
            rate = (r["m"] / r["n"]).where(r["n"] > 0)
            kk = k if isinstance(k, tuple) else (k,)
            rows.append(pd.DataFrame({**{c: v for c, v in zip(keys, kk)}, "service_date": rate.index, name: rate.values}))
        return pd.concat(rows, ignore_index=True)

    r7 = rolling_rate(daily, ["route_id"], 7, "route_rate_7d")
    r28 = rolling_rate(daily, ["route_id"], 28, "route_rate_28d")
    h28 = rolling_rate(band, ["route_id", "time_band"], 28, "hour_rate_28d")
    lab = lab.merge(r7, on=["route_id", "service_date"], how="left").merge(r28, on=["route_id", "service_date"], how="left")
    lab = lab.merge(h28, on=["route_id", "time_band", "service_date"], how="left")
    lab = lab.merge(daily.rename(columns={"n": "trips_that_day"})[["route_id", "service_date", "trips_that_day"]],
                    on=["route_id", "service_date"], how="left")
    return lab


def time_folds(dates: pd.Series, n_folds: int = 3, test_days: int = 14, calib_days: int = 7) -> list[dict]:
    """Rolling-origin folds: train on everything earlier, calibrate on the next week, test on the next 2 weeks."""
    last = dates.max().date()
    folds = []
    for f in range(n_folds):
        test_end = last - timedelta(days=(n_folds - 1 - f) * test_days)
        test_start = test_end - timedelta(days=test_days - 1)
        calib_start = test_start - timedelta(days=calib_days)
        folds.append({"fold": f + 1, "train_end": calib_start - timedelta(days=1), "calib": (calib_start, test_start - timedelta(days=1)),
                      "test": (test_start, test_end)})
    return folds


def split(df: pd.DataFrame, fold: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    d = df["service_date"].dt.date
    tr = df[d <= fold["train_end"]]
    ca = df[(d >= fold["calib"][0]) & (d <= fold["calib"][1])]
    te = df[(d >= fold["test"][0]) & (d <= fold["test"][1])]
    return tr, ca, te


def date_hash(d: date) -> int:
    return int(np.int64(d.toordinal()))
