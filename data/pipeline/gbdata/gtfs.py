"""D1: MBTA static GTFS (current + archive) processing (spec 9.0 D1 and 9.1)."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from gbagency.mbta import get_agency
from gbcore.settings import get_settings

from .download import download, sha256_file, update_manifest

REQUIRED = ["routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "shapes.txt", "calendar.txt",
            "calendar_dates.txt"]


@dataclass(frozen=True)
class ArchivedFeed:
    start: date
    end: date
    version: str
    url: str

    @property
    def key(self) -> str:
        return self.url.rstrip("/").split("/")[-1].removesuffix(".zip")


def _d(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y%m%d").date()


def gtfs_dir() -> Path:
    return get_settings().lake_dir / "raw" / "gtfs"


def load_archive_index(path: Path | None = None) -> list[ArchivedFeed]:
    path = path or gtfs_dir() / "archived_feeds.txt"
    if not path.exists():
        download(get_agency().gtfs_archive_index_url, path)
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    return [ArchivedFeed(_d(r["feed_start_date"]), _d(r["feed_end_date"]), r["feed_version"],
                         r["archive_url"]) for r in rows if r.get("archive_url")]


def feeds_covering(index: list[ArchivedFeed], start: date, end: date) -> dict[date, ArchivedFeed]:
    """For each service date in [start, end], the archived feed in effect.

    Feeds are listed newest first; where ranges overlap, the most recently published feed wins.
    """
    out: dict[date, ArchivedFeed] = {}
    d = start
    while d <= end:
        for f in index:  # newest first
            if f.start <= d <= f.end:
                out[d] = f
                break
        d += timedelta(days=1)
    return out


def fetch_feed(feed: ArchivedFeed) -> Path:
    p = gtfs_dir() / "archive" / f"{feed.key}.zip"
    download(feed.url, p)
    return p


def fetch_current() -> Path:
    p = gtfs_dir() / "current" / "MBTA_GTFS.zip"
    download(get_agency().static_gtfs_url, p)
    update_manifest("gtfs_current", url=get_agency().static_gtfs_url, sha256=sha256_file(p),
                    bytes=p.stat().st_size, downloaded_at=datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                    source="real", license="MassDOT Developers License Agreement")
    return p


def read_table(zpath: Path, name: str, usecols: list[str] | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        with z.open(name) as f:
            return pd.read_csv(io.BytesIO(f.read()), dtype=str, usecols=usecols, keep_default_na=False,
                               na_values=[""])


def validate_feed(zpath: Path) -> list[str]:
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
    return [r for r in REQUIRED if r not in names]


def expand_service(calendar: pd.DataFrame, calendar_dates: pd.DataFrame, start: date, end: date
                   ) -> dict[date, set[str]]:
    """service_id sets active per date: calendar weekday ranges + calendar_dates exceptions."""
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    out: dict[date, set[str]] = {}
    cal = calendar.copy()
    cal["s"] = cal["start_date"].map(_d)
    cal["e"] = cal["end_date"].map(_d)
    d = start
    while d <= end:
        wd = days[d.weekday()]
        act = set(cal.loc[(cal["s"] <= d) & (cal["e"] >= d) & (cal[wd] == "1"), "service_id"])
        out[d] = act
        d += timedelta(days=1)
    if len(calendar_dates):
        cd = calendar_dates.copy()
        cd["d"] = cd["date"].map(_d)
        for row in cd.itertuples():
            if row.d in out:
                if str(row.exception_type) == "1":
                    out[row.d].add(row.service_id)
                elif str(row.exception_type) == "2":
                    out[row.d].discard(row.service_id)
    return out


def scheduled_trips_for_feed(zpath: Path, dates: list[date], route_ids: set[str]) -> pd.DataFrame:
    """Concrete scheduled trips for the given dates (pilot routes) from one feed version."""
    if not dates:
        return pd.DataFrame()
    trips = read_table(zpath, "trips.txt", ["route_id", "service_id", "trip_id", "direction_id", "shape_id"])
    trips = trips[trips["route_id"].isin(route_ids)]
    cal = read_table(zpath, "calendar.txt")
    cd = read_table(zpath, "calendar_dates.txt")
    active = expand_service(cal, cd, min(dates), max(dates))
    st = read_table(zpath, "stop_times.txt", ["trip_id", "stop_sequence", "departure_time", "timepoint"])
    st = st[st["trip_id"].isin(set(trips["trip_id"]))]
    st["seq"] = st["stop_sequence"].astype(int)
    first = st.sort_values("seq").groupby("trip_id", as_index=False).first()[["trip_id", "departure_time"]]
    tp = st[st["timepoint"].fillna("1") == "1"].groupby("trip_id").size().rename("n_timepoints").reset_index()
    trips = trips.merge(first, on="trip_id", how="left").merge(tp, on="trip_id", how="left")
    rows = []
    for d in dates:
        sids = active.get(d, set())
        sub = trips[trips["service_id"].isin(sids)].copy()
        sub["service_date"] = d
        rows.append(sub)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out["feed_key"] = zpath.stem
    return out
