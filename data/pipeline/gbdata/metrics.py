"""Reliability metrics (spec 9.5) and historical missed-trip labels (spec 9.4) from D2.

Outputs in lake/curated/metrics/ (Parquet):
- route_metrics.parquet        route x month x day_type x time_band (+ 'all' rollups)
- trip_labels.parquet          one row per D2 half-trip: missed / unknown labels (ghost-def v1.0)
- missed_trip_daily.parquet    route x service_date
- timepoint_delay_summary.parquet   route x direction x timepoint x month x time_band delay quantiles
- delay_quantiles.parquet      route x direction x stop x time_band (fallback prediction ranges)
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from gbcore.settings import get_settings

DEF_VERSION = "ghost-def v1.0"
METRIC_DEF_VERSION = "metrics-def v1.0"

# Thresholds (config values; published on the methodology page)
THRESHOLDS = {
    "otp_early_s": -60,  # no more than 1 minute early
    "otp_late_s": 360,  # no more than 6 minutes late (midpoints/endpoints; also applied at startpoints)
    "headway_tolerance": 0.5,  # actual headway within +/-50% of scheduled
    "bunching_fraction": 0.25,  # actual headway below 25% of scheduled
    "completeness_min": 0.70,
}

BAND_SQL = """CASE WHEN h >= 6 AND h < 9 THEN 'am_peak' WHEN h >= 9 AND h < 15 THEN 'midday'
  WHEN h >= 15 AND h < 19 THEN 'pm_peak' WHEN h >= 19 AND h < 24 THEN 'evening' ELSE 'early_late' END"""


def metrics_dir() -> Path:
    return get_settings().lake_dir / "curated" / "metrics"


def connect() -> duckdb.DuckDBPyConnection:
    s = get_settings()
    con = duckdb.connect()
    cur = s.lake_dir / "curated"
    con.execute(f"CREATE VIEW arr AS SELECT * FROM read_parquet('{(cur / 'arrdep').as_posix()}/**/*.parquet', hive_partitioning=true, hive_types_autocast=false)")
    con.execute(f"CREATE VIEW comp AS SELECT * FROM read_parquet('{(cur / 'completeness.parquet').as_posix()}')")
    return con


def build(con: duckdb.DuckDBPyConnection | None = None, out: Path | None = None) -> dict:
    con = con or connect()
    out = out or metrics_dir()
    out.mkdir(parents=True, exist_ok=True)
    t = THRESHOLDS
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ev AS
        SELECT *, CASE WHEN dayofweek(service_date) IN (0, 6) THEN 'weekend' ELSE 'weekday' END AS day_type,
               {BAND_SQL.replace('h ', '((sched_s // 3600) % 24) ').replace('h>', '((sched_s // 3600) % 24)>')} AS time_band
        FROM arr
    """)
    # ---- trip labels (historical missed trip, ghost-def v1.0)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE trips AS
        SELECT e.service_date, e.route_id, e.direction_id, e.half_trip_id, min(e.sched_s) AS first_sched_s,
               count(*) AS n_events, count(e.actual_s) AS n_actual,
               count(e.actual_s) = 0 AS no_events,
               any_value(c.completeness) AS completeness,
               coalesce(any_value(c.completeness), 0) < {t['completeness_min']} AS unknown,
               any_value(e.standard_type) AS standard_type
        FROM ev e LEFT JOIN comp c ON c.service_date = e.service_date AND c.route_id = e.route_id
        GROUP BY ALL
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE trip_labels AS
        SELECT *, (no_events AND NOT unknown) AS missed,
               CASE WHEN unknown THEN 'unknown' WHEN no_events THEN 'missed' ELSE 'operated' END AS label,
               '{DEF_VERSION}' AS def_version,
               {BAND_SQL.replace('h ', '((first_sched_s // 3600) % 24) ').replace('h>', '((first_sched_s // 3600) % 24)>')} AS time_band,
               CASE WHEN dayofweek(service_date) IN (0, 6) THEN 'weekend' ELSE 'weekday' END AS day_type,
               strftime(service_date, '%Y-%m') AS month
        FROM trips
    """)
    con.execute(f"COPY trip_labels TO '{(out / 'trip_labels.parquet').as_posix()}' (FORMAT PARQUET)")
    # ---- daily missed trips
    con.execute(f"""
        COPY (SELECT service_date, route_id, strftime(service_date, '%Y-%m') AS month,
                     count(*) AS scheduled_trips, sum(missed::INT) AS missed_trips, bool_or(unknown) AS unknown,
                     any_value(completeness) AS completeness,
                     CASE WHEN bool_or(unknown) THEN NULL ELSE sum(missed::INT) / count(*) END AS missed_rate,
                     '{DEF_VERSION}' AS def_version
              FROM trip_labels GROUP BY ALL ORDER BY 1, 2)
        TO '{(out / 'missed_trip_daily.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    # ---- route metrics by month x day_type x time_band, plus rollups via GROUPING SETS
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE m_ev AS
        SELECT route_id, month, day_type, time_band,
          count(*) FILTER (WHERE standard_type = 'Schedule' AND actual_s IS NOT NULL) AS otp_n,
          count(*) FILTER (WHERE standard_type = 'Schedule' AND actual_s IS NOT NULL
                           AND delay_s BETWEEN {t['otp_early_s']} AND {t['otp_late_s']}) AS otp_ok,
          count(*) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0) AS hw_n,
          count(*) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0
                           AND abs(headway - scheduled_headway) <= {t['headway_tolerance']} * scheduled_headway) AS hw_ok,
          sum(headway::DOUBLE * headway) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0) AS h2,
          sum(headway::DOUBLE) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0) AS h1,
          sum(scheduled_headway::DOUBLE * scheduled_headway) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0) AS s2,
          sum(scheduled_headway::DOUBLE) FILTER (WHERE standard_type = 'Headway' AND headway > 0 AND scheduled_headway > 0) AS s1,
          count(*) FILTER (WHERE headway > 0 AND scheduled_headway > 0) AS bunch_n,
          count(*) FILTER (WHERE headway > 0 AND scheduled_headway > 0
                           AND headway < {t['bunching_fraction']} * scheduled_headway) AS bunch_k
        FROM ev GROUP BY GROUPING SETS ((route_id, month, day_type, time_band), (route_id, month, day_type),
                                         (route_id, month), (route_id, month, time_band))
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE m_tr AS
        SELECT route_id, month, day_type, time_band,
               count(*) FILTER (WHERE NOT unknown) AS scheduled_trips,
               sum(missed::INT) AS missed_trips,
               count(DISTINCT CASE WHEN unknown THEN (service_date, route_id) END) AS unknown_route_days
        FROM trip_labels GROUP BY GROUPING SETS ((route_id, month, day_type, time_band), (route_id, month, day_type),
                                                 (route_id, month), (route_id, month, time_band))
    """)
    con.execute(f"""
        COPY (
          SELECT e.route_id, e.month, coalesce(e.day_type, 'all') AS day_type, coalesce(e.time_band, 'all') AS time_band,
                 otp_ok / nullif(otp_n, 0) AS on_time_performance, otp_n,
                 hw_ok / nullif(hw_n, 0) AS headway_adherence, hw_n,
                 (h2 / nullif(2 * h1, 0)) - (s2 / nullif(2 * s1, 0)) AS excess_wait_time_s,
                 bunch_k / nullif(bunch_n, 0) AS bunching_rate, bunch_n,
                 t.missed_trips / nullif(t.scheduled_trips, 0) AS missed_trip_rate,
                 t.missed_trips, t.scheduled_trips, t.unknown_route_days,
                 '{METRIC_DEF_VERSION}' AS metric_def_version, '{DEF_VERSION}' AS ghost_def_version
          FROM m_ev e JOIN m_tr t ON e.route_id = t.route_id AND e.month = t.month
               AND e.day_type IS NOT DISTINCT FROM t.day_type AND e.time_band IS NOT DISTINCT FROM t.time_band
          ORDER BY 1, 2, 3, 4)
        TO '{(out / 'route_metrics.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    # ---- timepoint delay summary
    con.execute(f"""
        COPY (SELECT route_id, direction_id, time_point_id, any_value(stop_id) AS stop_id,
                     min(time_point_order) AS time_point_order, month, time_band, count(delay_s) AS n,
                     median(delay_s) AS median_delay_s, quantile_cont(delay_s, 0.9) AS p90_delay_s,
                     avg(delay_s) AS mean_delay_s
              FROM ev WHERE delay_s IS NOT NULL AND abs(delay_s) <= 10800 GROUP BY ALL)
        TO '{(out / 'timepoint_delay_summary.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    # ---- delay quantiles for fallback ranges (all months)
    con.execute(f"""
        COPY (SELECT route_id, direction_id, stop_id, time_band, count(*) AS n,
                     quantile_cont(delay_s, 0.1) AS p10, quantile_cont(delay_s, 0.5) AS p50,
                     quantile_cont(delay_s, 0.9) AS p90
              FROM ev WHERE delay_s IS NOT NULL AND abs(delay_s) <= 10800
              GROUP BY GROUPING SETS ((route_id, direction_id, stop_id, time_band), (route_id, direction_id, time_band)))
        TO '{(out / 'delay_quantiles.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{(out / 'route_metrics.parquet').as_posix()}')").fetchone()[0]
    lab = con.execute("SELECT label, count(*) FROM trip_labels GROUP BY 1").fetchall()
    return {"route_metric_rows": n, "trip_labels": dict(lab)}


if __name__ == "__main__":
    print(build())
