"""`make data`: the full, idempotent static + historical data pipeline."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from datetime import date

import duckdb
import pandas as pd
import yaml
from gbcore.settings import REPO_ROOT, get_settings

from . import arrdep, gtfs, static
from .download import sha256_file, update_manifest

COMPLETENESS_MIN = 0.70


def pilot_config_path():
    return get_settings().config_dir / "pilot_routes.yaml"


def write_pilot_config(routes: list[str], raw_names: dict[str, str]) -> None:
    p = pilot_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "selection": "Top 10 routes by observed timepoint events in MBTA Bus Arrival Departure Times, Jul-Dec 2025",
        "routes": [{"gtfs_route_id": r, "arrdep_route_id": raw_names.get(r, r)} for r in routes],
    }
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def load_pilot_routes() -> list[str]:
    doc = yaml.safe_load(pilot_config_path().read_text(encoding="utf-8"))
    return [r["gtfs_route_id"] for r in doc["routes"]]


def build_scheduled(routes: list[str], start: date, end: date) -> pd.DataFrame:
    idx = gtfs.load_archive_index()
    cov = gtfs.feeds_covering(idx, start, end)
    by_feed: dict = defaultdict(list)
    for d, f in cov.items():
        by_feed[f].append(d)
    parts = []
    for f, dates in sorted(by_feed.items(), key=lambda kv: kv[0].start):
        z = gtfs.fetch_feed(f)
        df = gtfs.scheduled_trips_for_feed(z, sorted(dates), set(routes))
        df["feed_version"] = f.version
        parts.append(df)
        update_manifest(f"gtfs_archive_{f.key}", url=f.url, sha256=sha256_file(z), bytes=z.stat().st_size,
                        start_date=str(f.start), end_date=str(f.end), source="real",
                        license="MassDOT Developers License Agreement", rows=len(df))
    sched = pd.concat(parts, ignore_index=True)
    from gbcore.timeutil import gtfs_time_to_seconds

    sched["first_dep_s"] = gtfs_time_to_seconds(sched["departure_time"]).astype("Int64")
    sched["direction_id"] = sched["direction_id"].astype(int)
    sched["service_date"] = pd.to_datetime(sched["service_date"])
    local = sched["service_date"] + pd.to_timedelta(sched["first_dep_s"].astype(float), unit="s")
    from gbcore.timeutil import local_naive_to_utc_series

    sched["first_departure_utc"] = local_naive_to_utc_series(local).to_numpy()
    sched["month"] = sched["service_date"].dt.strftime("%Y-%m")
    return sched[["service_date", "trip_id", "route_id", "direction_id", "service_id", "shape_id", "first_dep_s",
                  "first_departure_utc", "n_timepoints", "feed_key", "feed_version", "month"]]


def completeness(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Completeness per route-day = observed D2 trips (>=1 actual event) / scheduled trips (D1)."""
    return con.execute(f"""
        WITH s AS (SELECT CAST(service_date AS DATE) d, route_id, count(*) scheduled FROM sched GROUP BY ALL),
        a AS (SELECT service_date d, route_id, count(DISTINCT half_trip_id) d2_trips,
                     count(DISTINCT CASE WHEN actual_s IS NOT NULL THEN half_trip_id END) observed
              FROM arr GROUP BY ALL)
        SELECT s.d AS service_date, s.route_id, s.scheduled, coalesce(a.d2_trips,0) d2_trips,
               coalesce(a.observed,0) observed,
               least(1.0, coalesce(a.observed,0) / s.scheduled) AS completeness,
               least(1.0, coalesce(a.observed,0) / s.scheduled) < {COMPLETENESS_MIN} AS unknown
        FROM s LEFT JOIN a USING (d, route_id) ORDER BY 1, 2
    """).df()


def write_report(res: dict, comp: pd.DataFrame, static_info: dict, path) -> None:
    prof = res["profile"]
    lines = ["# MBTA Bus Arrival Departure Times — profile and data quality", "",
             f"Source: MBTA Bus Arrival Departure Times {res['year']} (CC0). Months loaded: {', '.join(res['months'])}.",
             f"GROUND_TRUTH_SOURCE=real. Pilot routes (GTFS ids): {', '.join(res['routes'])}.", "",
             f"- Total rows (all routes, loaded months): {prof['total_rows_all_routes']:,}",
             f"- Pilot-route rows after dedupe: {res['rows']:,}",
             f"- Duplicate events (pilot): {prof['duplicate_events_pilot']:,}",
             f"- Impossible times |delay| > 3 h (pilot): {prof['impossible_times_pilot']:,}",
             f"- After-midnight rows (scheduled >= 24:00 on service date, pilot): {prof['after_midnight_rows_pilot']:,}",
             "", "## Null rates (all routes)", "", "| column | null rate |", "|---|---|"]
    lines += [f"| {k} | {v:.2%} |" for k, v in prof["null_rates"].items()]
    lines += ["", "## Rows per month (pilot)", "", "| month | rows | with actual | service days |", "|---|---|---|---|"]
    lines += [f"| {m} | {n:,} | {a:,} | {d} |" for m, n, a, d in prof["per_month"]]
    lines += ["", "## Rows per route (pilot)", "", "| GTFS route | D2 route | rows | with actual | half-trips |",
              "|---|---|---|---|---|"]
    lines += [f"| {r} | {raw} | {n:,} | {a:,} | {h:,} |" for r, raw, n, a, h in prof["per_route"]]
    rd = comp.groupby("route_id").agg(days=("service_date", "count"), mean_completeness=("completeness", "mean"),
                                      unknown_days=("unknown", "sum")).reset_index()
    lines += ["", f"## Completeness per route-day (threshold {COMPLETENESS_MIN:.0%})", "",
              "Completeness = D2 half-trips with at least one observed event / GTFS scheduled trips for that date "
              "(feed version in effect from the archive).", "",
              f"- Route-days: {len(comp):,}; marked unknown: {int(comp['unknown'].sum()):,} "
              f"({comp['unknown'].mean():.1%})", "",
              "| route | days | mean completeness | unknown days |", "|---|---|---|---|"]
    lines += [f"| {r.route_id} | {r.days} | {r.mean_completeness:.1%} | {r.unknown_days} |" for r in rd.itertuples()]
    days = comp.groupby("service_date").agg(unknown=("unknown", "sum"), routes=("route_id", "count"))
    bad = days[days["unknown"] == days["routes"]]
    lines += ["", f"## Days failing completeness on every pilot route (excluded): {len(bad)}", ""]
    lines += [f"- {d.date()}" for d in bad.index[:60]]
    lines += ["", "## Static network (current feed)", "",
              f"- routes {static_info['routes']}, stops {static_info['stops']}, trips {static_info['trips']:,}, "
              f"shapes {static_info['shapes']}, stop-on-shape rows {static_info['stop_on_shape']:,}",
              f"- Stops more than 50 m off their shape: {static_info['stops_off_shape_gt_50m']}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all(months: list[str] | None = None) -> dict:
    s = get_settings()
    res = arrdep.run(months)
    routes = res["routes"]
    raw = {r: rr for r, rr, *_ in res["profile"]["per_route"]}
    write_pilot_config(routes, raw)
    cur = gtfs.fetch_current()
    static_info = static.build(cur, routes)
    m0, m1 = res["months"][0], res["months"][-1]
    start = date.fromisoformat(m0 + "-01")
    end = (pd.Timestamp(m1 + "-01") + pd.offsets.MonthEnd(0)).date()
    sched = build_scheduled(routes, start, end)
    sched_dir = s.lake_dir / "curated" / "scheduled"
    if sched_dir.exists():
        shutil.rmtree(sched_dir)
    con = duckdb.connect()
    con.register("sched_df", sched)
    con.execute("CREATE TABLE sched AS SELECT * FROM sched_df")
    con.execute(f"COPY sched TO '{sched_dir.as_posix()}' (FORMAT PARQUET, PARTITION_BY (month))")
    con.execute(f"CREATE VIEW arr AS SELECT * FROM read_parquet('{(s.lake_dir / 'curated' / 'arrdep').as_posix()}/**/*.parquet', hive_partitioning=true, hive_types_autocast=false)")
    comp = completeness(con)
    comp_path = s.lake_dir / "curated" / "completeness.parquet"
    comp.to_parquet(comp_path, index=False)
    write_report(res, comp, static_info, REPO_ROOT / "data" / "reports" / "arrdep_profile.md")
    summary = {"routes": routes, "months": res["months"], "arrdep_rows": res["rows"], "scheduled_trips": len(sched),
               "route_days": len(comp), "unknown_route_days": int(comp["unknown"].sum()), "static": {
                   k: v for k, v in static_info.items() if k != "off_shape_examples"}}
    update_manifest("scheduled_trips", rows=len(sched), source="real (derived from GTFS archive)")
    from . import analytics, load_db, metrics

    summary["metrics"] = metrics.build()
    analytics.build()
    summary["db"] = load_db.run()
    print(json.dumps(summary, indent=1, default=str))
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("step", nargs="?", default="all", choices=["all", "derived"])
    p.add_argument("--months", nargs="*")
    a = p.parse_args()
    if a.step == "derived":  # metrics + analytics + DB only (no downloads)
        from . import analytics, load_db, metrics

        print(metrics.build(), analytics.build(), load_db.run())
        return 0
    run_all(a.months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
