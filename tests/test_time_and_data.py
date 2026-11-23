from __future__ import annotations

import json
from datetime import UTC, date, datetime

import duckdb
import pandas as pd
import pytest
from gbagency.mbta import MBTAAdapter
from gbcore.settings import REPO_ROOT, get_settings
from gbcore.timeutil import (
    gtfs_to_utc,
    is_holiday,
    parse_gtfs_time,
    service_date_for,
    time_band,
)
from gbdata.arrdep import parse_offset_sql
from gbdata.gtfs import ArchivedFeed, expand_service, feeds_covering
from gbdata.static import stop_distances_on_shape
from shapely.geometry import LineString


def test_parse_gtfs_time_past_midnight():
    assert parse_gtfs_time("25:10:05") == 25 * 3600 + 10 * 60 + 5


def test_gtfs_time_rolls_into_next_calendar_day():
    # 25:10 on 2025-12-01 is 01:10 local on 2025-12-02 = 06:10 UTC (EST)
    assert gtfs_to_utc(date(2025, 12, 1), parse_gtfs_time("25:10:00")) == datetime(2025, 12, 2, 6, 10, tzinfo=UTC)


def test_service_day_after_midnight_belongs_to_previous_date():
    # 00:30 local on 2025-12-02 (05:30 UTC) belongs to service date 2025-12-01
    assert service_date_for(datetime(2025, 12, 2, 5, 30, tzinfo=UTC)) == date(2025, 12, 1)
    # 02:30 local belongs to its own date
    assert service_date_for(datetime(2025, 12, 2, 7, 30, tzinfo=UTC)) == date(2025, 12, 2)


def test_service_date_rejects_naive():
    with pytest.raises(ValueError):
        service_date_for(datetime(2025, 12, 2, 5, 30))


def test_dst_spring_forward_noon_minus_12h():
    # 2025-03-09: clocks jump 02:00->03:00. Reference is noon-12h = 23:00 EST previous day (04:00 UTC).
    # 08:00:00 GTFS time => 04:00 UTC + 8h = 12:00 UTC = 08:00 EDT.
    assert gtfs_to_utc(date(2025, 3, 9), 8 * 3600) == datetime(2025, 3, 9, 12, 0, tzinfo=UTC)


def test_dst_fall_back_noon_minus_12h():
    # 2025-11-02: clocks fall back. Reference = 01:00 EDT (05:00 UTC). 08:00 GTFS => 13:00 UTC = 08:00 EST.
    assert gtfs_to_utc(date(2025, 11, 2), 8 * 3600) == datetime(2025, 11, 2, 13, 0, tzinfo=UTC)


def test_service_date_across_fall_back_night():
    # 01:30 EST on 2025-11-02 (06:30 UTC) is still service date 2025-11-01
    assert service_date_for(datetime(2025, 11, 2, 6, 30, tzinfo=UTC)) == date(2025, 11, 1)


def test_arrdep_offset_parse_after_midnight():
    con = duckdb.connect()
    sql = parse_offset_sql("'1900-01-02T00:20:05Z'")
    assert con.execute(f"SELECT {sql}").fetchone()[0] == 86400 + 20 * 60 + 5
    assert con.execute(f"SELECT {parse_offset_sql(chr(39) + '1900-01-01T15:16:00Z' + chr(39))}").fetchone()[0] == 15 * 3600 + 16 * 60


def test_route_normalization():
    a = MBTAAdapter()
    assert a.normalize_route_id("01") == "1"
    assert a.normalize_route_id("SL1") == "741"
    assert a.normalize_route_id("111") == "111"
    assert a.direction_label_to_id("Outbound") == 0 and a.direction_label_to_id("Inbound") == 1


def test_calendar_expansion_with_exceptions():
    cal = pd.DataFrame([{"service_id": "WK", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
                         "friday": "1", "saturday": "0", "sunday": "0", "start_date": "20251201",
                         "end_date": "20251231"}])
    cd = pd.DataFrame([{"service_id": "WK", "date": "20251225", "exception_type": "2"},
                       {"service_id": "HOL", "date": "20251225", "exception_type": "1"}])
    out = expand_service(cal, cd, date(2025, 12, 22), date(2025, 12, 28))
    assert out[date(2025, 12, 22)] == {"WK"}
    assert out[date(2025, 12, 25)] == {"HOL"}
    assert out[date(2025, 12, 27)] == set()


def test_feeds_covering_prefers_newest():
    idx = [ArchivedFeed(date(2025, 12, 5), date(2025, 12, 31), "new", "u/new.zip"),
           ArchivedFeed(date(2025, 12, 1), date(2025, 12, 10), "old", "u/old.zip")]
    cov = feeds_covering(idx, date(2025, 12, 1), date(2025, 12, 6))
    assert cov[date(2025, 12, 1)].version == "old"
    assert cov[date(2025, 12, 6)].version == "new"


def test_stop_distances_monotonic_on_loop():
    # A shape that goes east then comes back west on a parallel street 20 m north
    line = LineString([(0, 0), (1000, 0), (1000, 20), (0, 20)])
    stops = [(100, 0), (900, 0), (900, 20), (100, 20)]
    d = [x for x, _ in stop_distances_on_shape(line, stops)]
    assert d == sorted(d)
    assert abs(d[3] - (1000 + 20 + 900)) < 1e-6


def test_time_band_and_holiday():
    assert time_band(7) == "am_peak" and time_band(12) == "midday" and time_band(17) == "pm_peak"
    assert time_band(21) == "evening" and time_band(3) == "early_late"
    assert is_holiday(date(2025, 11, 27)) and is_holiday(date(2025, 12, 25))


# ---- data tests over the real curated lake (skipped when `make data` has not run) ----
LAKE = get_settings().lake_dir / "curated"
needs_data = pytest.mark.skipif(not (LAKE / "arrdep").exists(), reason="run `make data` first")


@needs_data
def test_arrdep_schema_and_bounds():
    con = duckdb.connect()
    con.execute(f"CREATE VIEW a AS SELECT * FROM read_parquet('{(LAKE / 'arrdep').as_posix()}/**/*.parquet', hive_partitioning=true)")
    cols = {r[0] for r in con.execute("DESCRIBE a").fetchall()}
    assert {"service_date", "route_id", "direction_id", "half_trip_id", "sched_s", "actual_s", "delay_s",
            "scheduled_utc", "actual_utc", "headway", "scheduled_headway"} <= cols
    n, routes = con.execute("SELECT count(*), count(DISTINCT route_id) FROM a").fetchone()
    assert 100_000 < n < 50_000_000 and routes == 10
    assert con.execute("SELECT count(*) FROM a WHERE direction_id IS NULL").fetchone()[0] == 0


@needs_data
def test_completeness_report_and_manifest():
    comp = pd.read_parquet(LAKE / "completeness.parquet")
    assert comp["completeness"].between(0, 1).all()
    assert (REPO_ROOT / "data" / "reports" / "arrdep_profile.md").exists()
    m = json.loads((REPO_ROOT / "data" / "manifest.json").read_text())
    assert all("sha256" in v for k, v in m["sources"].items() if k.startswith(("arrdep", "gtfs")))
