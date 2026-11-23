"""D2: MBTA Bus Arrival Departure Times (official ground truth) pipeline (spec 9.0 D2).

Observed facts from profiling the 2025 release (see data/reports/arrdep_profile.md):
- `scheduled` / `actual` are LOCAL wall-clock times written on a dummy date with a misleading 'Z':
  '1900-01-01T15:16:00Z' = 15:16 local on service_date; '1900-01-02T00:20:00Z' = 00:20 the next calendar
  day (still the same service date).
- `half_trip_id` is an MBTA-internal one-way trip ID (not the GTFS trip_id).
- Scheduled rows are present even when `actual` is null (no observed event).
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from gbagency.mbta import get_agency
from gbcore.settings import get_settings

from .download import download, sha256_file, update_manifest

MONTHS_DEFAULT = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
EXPECTED_COLUMNS = ["service_date", "route_id", "direction_id", "half_trip_id", "stop_id", "time_point_id",
                    "time_point_order", "point_type", "standard_type", "scheduled", "actual",
                    "scheduled_headway", "headway"]


def raw_dir(year: int) -> Path:
    return get_settings().lake_dir / "raw" / "arrdep" / str(year)


def fetch_year(year: int) -> Path:
    """Fallback chain step 1: ArcGIS item /data endpoint (streams the item's file)."""
    item = get_agency().arrdep_item_ids[year]
    dest = raw_dir(year) / f"MBTA_Bus_Arrival_Departure_Times_{year}.zip"
    url = f"https://www.arcgis.com/sharing/rest/content/items/{item}/data"
    download(url, dest, timeout=600)
    return dest


def extract_months(zpath: Path, months: list[str]) -> list[Path]:
    out = []
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            for m in months:
                if name.endswith(f"_{m}.csv"):
                    target = zpath.parent / name
                    if not target.exists():
                        z.extract(name, zpath.parent)
                    out.append(target)
    return sorted(out)


def parse_offset_sql(col: str) -> str:
    """'1900-01-0DTHH:MM:SSZ' -> seconds since local midnight of the service date (day 02 => +86400)."""
    return (
        f"((CAST(substr({col},9,2) AS INTEGER) - 1) * 86400 + CAST(substr({col},12,2) AS INTEGER) * 3600"
        f" + CAST(substr({col},15,2) AS INTEGER) * 60 + CAST(substr({col},18,2) AS INTEGER))"
    )


def route_case_sql() -> str:
    sl = get_agency().SILVER_LINE  # type: ignore[attr-defined]
    whens = " ".join(f"WHEN upper(route_id) = '{k}' THEN '{v}'" for k, v in sl.items())
    return (f"CASE {whens} WHEN regexp_full_match(route_id, '[0-9]+') THEN CAST(CAST(route_id AS INTEGER) AS VARCHAR)"
            f" ELSE route_id END")


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


def stage_view(con: duckdb.DuckDBPyConnection, csvs: list[Path]) -> None:
    files = ", ".join(f"'{p.as_posix()}'" for p in csvs)
    con.execute(f"CREATE OR REPLACE VIEW raw_arrdep AS SELECT * FROM read_csv([{files}], all_varchar=true, header=true)")
    cols = [r[0] for r in con.execute("DESCRIBE raw_arrdep").fetchall()]
    missing = [c for c in EXPECTED_COLUMNS if c not in cols]
    if missing:
        raise ValueError(f"arrdep schema check failed, missing columns: {missing}")
    con.execute(f"""
        CREATE OR REPLACE TABLE arrdep AS
        SELECT
          CAST(service_date AS DATE) AS service_date,
          {route_case_sql()} AS route_id,
          route_id AS route_id_raw,
          CASE lower(direction_id) WHEN 'outbound' THEN 0 WHEN 'inbound' THEN 1 END AS direction_id,
          half_trip_id, stop_id, time_point_id,
          CAST(time_point_order AS INTEGER) AS time_point_order,
          point_type, standard_type,
          {parse_offset_sql('scheduled')} AS sched_s,
          CASE WHEN actual IS NULL THEN NULL ELSE {parse_offset_sql('actual')} END AS actual_s,
          TRY_CAST(scheduled_headway AS INTEGER) AS scheduled_headway,
          TRY_CAST(headway AS INTEGER) AS headway,
          strftime(CAST(service_date AS DATE), '%Y-%m') AS month
        FROM raw_arrdep
    """)
    con.execute("""
        CREATE OR REPLACE TABLE arrdep AS
        SELECT *,
          actual_s - sched_s AS delay_s,
          timezone('America/New_York', CAST(service_date AS TIMESTAMP) + to_seconds(sched_s)) AS scheduled_utc,
          CASE WHEN actual_s IS NULL THEN NULL ELSE
            timezone('America/New_York', CAST(service_date AS TIMESTAMP) + to_seconds(actual_s)) END AS actual_utc
        FROM arrdep
    """)


def select_pilot_routes(con: duckdb.DuckDBPyConnection, n: int = 10) -> list[str]:
    rows = con.execute(
        "SELECT route_id, count(actual_s) n FROM arrdep GROUP BY 1 ORDER BY n DESC, route_id LIMIT ?", [n]
    ).fetchall()
    return [r[0] for r in rows]


def write_parquet(con: duckdb.DuckDBPyConnection, routes: list[str], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rl = ", ".join(f"'{r}'" for r in routes)
    # Deduplicate exact duplicate events (same trip, timepoint, point type, scheduled time).
    con.execute(f"""
        CREATE OR REPLACE TABLE arrdep_pilot AS
        SELECT * EXCLUDE (rn) FROM (
          SELECT *, row_number() OVER (PARTITION BY service_date, route_id, half_trip_id, time_point_id,
                                       time_point_order, point_type, sched_s ORDER BY actual_s NULLS LAST) rn
          FROM arrdep WHERE route_id IN ({rl})
        ) WHERE rn = 1
    """)
    con.execute(f"COPY arrdep_pilot TO '{out_dir.as_posix()}' (FORMAT PARQUET, PARTITION_BY (route_id, month), "
                "OVERWRITE_OR_IGNORE true)")
    return con.execute("SELECT count(*) FROM arrdep_pilot").fetchone()[0]


def profile(con: duckdb.DuckDBPyConnection, routes: list[str]) -> dict:
    rl = ", ".join(f"'{r}'" for r in routes)
    q = lambda s: con.execute(s).fetchall()  # noqa: E731
    total = q("SELECT count(*) FROM arrdep")[0][0]
    nulls = q("""SELECT avg((half_trip_id IS NULL)::INT), avg((actual_s IS NULL)::INT), avg((stop_id IS NULL)::INT),
                 avg((scheduled_headway IS NULL)::INT), avg((headway IS NULL)::INT) FROM arrdep""")[0]
    dups = q(f"""SELECT count(*) - count(DISTINCT (service_date, route_id, half_trip_id, time_point_id, time_point_order,
                 point_type, sched_s)) FROM arrdep WHERE route_id IN ({rl})""")[0][0]
    impossible = q(f"SELECT count(*) FROM arrdep WHERE route_id IN ({rl}) AND abs(delay_s) > 3*3600")[0][0]
    per_month = q(f"""SELECT month, count(*), count(actual_s), count(DISTINCT service_date) FROM arrdep
                    WHERE route_id IN ({rl}) GROUP BY 1 ORDER BY 1""")
    per_route = q(f"""SELECT route_id, any_value(route_id_raw), count(*), count(actual_s),
                     count(DISTINCT (service_date, half_trip_id)) FROM arrdep WHERE route_id IN ({rl})
                     GROUP BY 1 ORDER BY 3 DESC""")
    after_midnight = q(f"SELECT count(*) FROM arrdep WHERE route_id IN ({rl}) AND sched_s >= 86400")[0][0]
    return {"total_rows_all_routes": total, "null_rates": dict(zip(
        ["half_trip_id", "actual", "stop_id", "scheduled_headway", "headway"], [round(x, 4) for x in nulls])),
        "duplicate_events_pilot": dups, "impossible_times_pilot": impossible, "per_month": per_month,
        "per_route": per_route, "after_midnight_rows_pilot": after_midnight}


def run(months: list[str] | None = None, n_routes: int = 10) -> dict:
    s = get_settings()
    months = months or MONTHS_DEFAULT
    year = int(months[0][:4])
    zpath = None
    errors = []
    for y in (year, year - 1):
        try:
            zpath = fetch_year(y)
            year = y
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{y}: {e}")
    if zpath is None:
        raise RuntimeError("; ".join(errors))
    if year != int(months[0][:4]):
        months = [f"{year}-{m[5:]}" for m in months]
    update_manifest(f"arrdep_{year}", url=f"https://www.arcgis.com/sharing/rest/content/items/"
                    f"{get_agency().arrdep_item_ids[year]}/data", sha256=sha256_file(zpath),
                    bytes=zpath.stat().st_size, downloaded_at=datetime.fromtimestamp(zpath.stat().st_mtime, UTC)
                    .isoformat(), source="real", license="CC0", months=months)
    csvs = extract_months(zpath, months)
    con = connect()
    stage_view(con, csvs)
    routes = select_pilot_routes(con, n_routes)
    out = s.lake_dir / "curated" / "arrdep"
    if out.exists():
        import shutil

        shutil.rmtree(out)
    rows = write_parquet(con, routes, out)
    prof = profile(con, routes)
    update_manifest(f"arrdep_{year}", rows_pilot=rows, schema_ok=True, pilot_routes=routes)
    return {"year": year, "months": months, "routes": routes, "rows": rows, "profile": prof}
