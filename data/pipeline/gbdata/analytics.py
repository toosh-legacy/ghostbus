"""AnalyticsStore (DuckDB over the Parquet lake) and the allowlisted aggregate views for the copilot.

The aggregate views are MATERIALIZED as tables in var/analytics.duckdb so that the copilot connection can be
opened read-only with external access disabled (no file reads possible from SQL at all).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from gbcore.settings import REPO_ROOT, get_settings

ALLOWLISTED_VIEWS = {
    "v_route_month_metrics": "One row per pilot route and month (all days, all time bands).",
    "v_route_timeband_metrics": "Route x month x day_type (weekday/weekend/all) x time_band (am_peak, midday, pm_peak, evening, early_late, all).",
    "v_timepoint_delay_summary": "Delay quantiles per route, direction, timepoint, month and time band.",
    "v_missed_trip_daily": "Missed trips per route and service date (unknown route-days flagged, rate NULL).",
    "v_backtest_results": "Latest ML backtest metrics per model, fold, baseline and horizon.",
}

COLUMN_DOCS = {
    "route_id": "GTFS route id (e.g. '1', '111', '741' = SL1, '743' = SL3)",
    "month": "YYYY-MM",
    "day_type": "weekday | weekend | all",
    "time_band": "am_peak (6-9) | midday (9-15) | pm_peak (15-19) | evening (19-24) | early_late (0-6) | all",
    "on_time_performance": "share 0-1 of schedule-standard timepoint events within -1/+6 min",
    "headway_adherence": "share 0-1 of headway-standard events within +/-50% of scheduled headway",
    "excess_wait_time_s": "seconds of excess wait for headway-standard service",
    "bunching_rate": "share 0-1 of events with headway < 25% of scheduled",
    "missed_trip_rate": "missed trips / scheduled trips over known route-days",
    "missed_trips": "count", "scheduled_trips": "count", "unknown_route_days": "count",
}


def db_path() -> Path:
    return get_settings().var_dir / "analytics.duckdb"


def build(path: Path | None = None) -> Path:
    s = get_settings()
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.duckdb")
    if tmp.exists():
        tmp.unlink()
    m = s.lake_dir / "curated" / "metrics"
    con = duckdb.connect(str(tmp))
    rm = (m / "route_metrics.parquet").as_posix()
    con.execute(f"CREATE TABLE v_route_month_metrics AS SELECT * EXCLUDE (day_type, time_band) FROM read_parquet('{rm}') "
                "WHERE day_type = 'all' AND time_band = 'all'")
    con.execute(f"CREATE TABLE v_route_timeband_metrics AS SELECT * FROM read_parquet('{rm}')")
    con.execute(f"CREATE TABLE v_timepoint_delay_summary AS SELECT * FROM read_parquet('{(m / 'timepoint_delay_summary.parquet').as_posix()}')")
    con.execute(f"CREATE TABLE v_missed_trip_daily AS SELECT * FROM read_parquet('{(m / 'missed_trip_daily.parquet').as_posix()}')")
    bt = REPO_ROOT / "ml" / "reports" / "backtest_results.parquet"
    if bt.exists():
        con.execute(f"CREATE TABLE v_backtest_results AS SELECT * FROM read_parquet('{bt.as_posix()}')")
    else:
        con.execute("CREATE TABLE v_backtest_results (model VARCHAR, fold INTEGER, method VARCHAR, horizon VARCHAR, "
                    "metric VARCHAR, value DOUBLE)")
    # a non-allowlisted raw table also lives here so that red-team tests prove the validator blocks it
    con.execute(f"CREATE TABLE raw_trip_labels AS SELECT * FROM read_parquet('{(m / 'trip_labels.parquet').as_posix()}') LIMIT 1000")
    con.close()
    if path.exists():
        path.unlink()
    tmp.rename(path)
    return path


def read_only_connection(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Copilot connection: read-only, no external access (no file/URL reads), bounded resources."""
    con = duckdb.connect(str(path or db_path()), read_only=True,
                         config={"enable_external_access": False, "threads": 2, "memory_limit": "1GB"})
    return con


def connect_lake() -> duckdb.DuckDBPyConnection:
    """Internal (non-copilot) analytics over the lake."""
    s = get_settings()
    con = duckdb.connect()
    cur = s.lake_dir / "curated"
    for name, sub in (("arrdep", "arrdep"), ("scheduled", "scheduled")):
        p = cur / sub
        if p.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{p.as_posix()}/**/*.parquet', hive_partitioning=true, hive_types_autocast=false)")
    return con
