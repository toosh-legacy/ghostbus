"""The single service-day / time-handling module (spec 9.6).

Rules:
1. All stored timestamps are UTC. Local time (America/New_York) only at the edges.
2. Service-day logic lives here and only here.
3. GTFS times may exceed 24:00:00; they are offsets from "noon minus 12h" of the service date
   (the GTFS reference definition), which is DST-correct.
4. MBTA service days run ~02:00 to 01:59:59 local: an event at 00:30 local belongs to the previous date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

AGENCY_TZ = ZoneInfo("America/New_York")
SERVICE_DAY_START_HOUR = 2


def parse_gtfs_time(s: str) -> int:
    """'25:10:05' -> seconds after the service-day reference (may exceed 86400)."""
    h, m, sec = (int(x) for x in str(s).strip().split(":"))
    return h * 3600 + m * 60 + sec


def gtfs_time_to_seconds(series: pd.Series) -> pd.Series:
    """Vectorized parse of GTFS HH:MM:SS strings (hours may be >= 24). NaN-safe."""
    parts = series.astype("string").str.strip().str.split(":", expand=True)
    out = parts[0].astype("float") * 3600 + parts[1].astype("float") * 60 + parts[2].astype("float")
    return out


def service_day_reference(service_date: date, tz: ZoneInfo = AGENCY_TZ) -> datetime:
    """GTFS reference: 'noon minus 12h' local on the service date (handles DST days)."""
    noon = datetime.combine(service_date, time(12, 0), tzinfo=tz)
    # Subtract in UTC: aware-datetime arithmetic in Python is wall-clock, which is wrong on DST days.
    return (noon.astimezone(UTC) - timedelta(hours=12)).astimezone(tz)


def gtfs_to_utc(service_date: date, seconds: int, tz: ZoneInfo = AGENCY_TZ) -> datetime:
    """Absolute UTC instant for a GTFS stop time on a service date.

    The reference is noon-minus-12h expressed as an absolute instant; adding elapsed seconds
    is done in UTC, which is how GTFS defines times on DST-transition days.
    """
    ref = service_day_reference(service_date, tz).astimezone(UTC)
    return ref + timedelta(seconds=int(seconds))


def service_date_for(ts_utc: datetime, tz: ZoneInfo = AGENCY_TZ,
                     start_hour: int = SERVICE_DAY_START_HOUR) -> date:
    """Service date an instant belongs to: local times before 02:00 belong to the previous date."""
    if ts_utc.tzinfo is None:
        raise ValueError("naive datetime: all stored timestamps must be UTC-aware")
    local = ts_utc.astimezone(tz)
    if local.hour < start_hour:
        return (local - timedelta(days=1)).date()
    return local.date()


def to_local(ts_utc: datetime, tz: ZoneInfo = AGENCY_TZ) -> datetime:
    return ts_utc.astimezone(tz)


def local_naive_to_utc_series(local_naive: pd.Series, tz: str = "America/New_York") -> pd.Series:
    """Localize naive local timestamps to UTC (ambiguous fall-back hour -> first occurrence)."""
    return (
        pd.to_datetime(local_naive)
        .dt.tz_localize(tz, ambiguous=np.zeros(len(local_naive), dtype=bool), nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )


def time_band(local_hour: int) -> str:
    """Time bands for reliability reporting (local hour)."""
    if 6 <= local_hour < 9:
        return "am_peak"
    if 9 <= local_hour < 15:
        return "midday"
    if 15 <= local_hour < 19:
        return "pm_peak"
    if 19 <= local_hour < 24:
        return "evening"
    return "early_late"  # 00:00-06:00


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


# US federal holidays observed by MBTA with Sunday/Saturday service (subset is enough for features).
def us_holidays(year: int) -> set[date]:
    def nth_weekday(month: int, weekday: int, n: int) -> date:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def last_weekday(month: int, weekday: int) -> date:
        d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    return {
        date(year, 1, 1),
        nth_weekday(1, 0, 3),  # MLK
        nth_weekday(2, 0, 3),  # Presidents
        nth_weekday(4, 0, 3),  # Patriots' Day (MA)
        last_weekday(5, 0),  # Memorial
        date(year, 6, 19),
        date(year, 7, 4),
        nth_weekday(9, 0, 1),  # Labor
        nth_weekday(10, 0, 2),  # Indigenous Peoples
        date(year, 11, 11),
        nth_weekday(11, 3, 4),  # Thanksgiving
        date(year, 12, 25),
    }


def is_holiday(d: date) -> bool:
    return d in us_holidays(d.year)
