"""Published definitions (verbatim from spec 9.4-9.5), versioned. Served by /v1/methodology and the copilot."""

from __future__ import annotations

GHOST_DEF_VERSION = "ghost-def v1.1"
METRIC_DEF_VERSION = "metrics-def v1.0"

METRICS: dict[str, str] = {
    "on_time_performance": (
        "On-time performance (schedule-standard trips): Share of timepoint events with `actual` between 1 minute "
        "early and 6 minutes late of `scheduled` at midpoints/endpoints, and departing no more than 1 minute early at "
        "startpoints. The thresholds are config values; document the ones used. "
        "[Thresholds used: -60 s to +360 s at all point types (startpoints use the same late bound).]"
    ),
    "headway_adherence": (
        "Headway adherence (headway-standard trips): Share of events where the actual headway is within ±50% of the "
        "scheduled headway (configurable)."
    ),
    "excess_wait_time": (
        "Excess wait time: Average wait implied by actual headways minus the wait implied by scheduled headways, for "
        "headway-standard service. Average wait = sum(h²) / (2 · sum(h))."
    ),
    "bunching_rate": "Bunching rate: Share of events where the actual headway is below 25% of the scheduled headway.",
    "missed_trip_rate": "Missed-trip rate: Missed trips / scheduled trips over route-days that are not unknown (section 9.4).",
    "coverage_of_intervals": "Coverage of intervals: Share of observed arrivals that fell inside the predicted 80% range.",
}

GHOST: dict[str, str] = {
    "historical_missed_trip": (
        "Historical missed trip (from the MBTA Bus Arrival Departure Times data): A scheduled trip of a pilot route on a "
        "service date has no timepoint events at all. Route-days whose completeness score is below 70% are marked "
        "unknown, not missed, because the data itself is incomplete. "
        "[Implementation: a trip with no events = a half-trip whose rows all have a null `actual`; completeness = "
        "half-trips with at least one observed event / GTFS scheduled trips for that route and date.]"
    ),
    "high": ("High: Scheduled departure from the trip's first stop was more than 5 minutes ago; no vehicle has been "
             "matched to the trip; the feed is healthy."),
    "elevated": ("Elevated: Departure is within 10 minutes and no vehicle is assigned yet, or the trip appears in trip "
                 "updates with a prediction but no vehicle has been seen for more than 3 minutes."),
    "unknown": ("Unknown: the feed is unhealthy (no successful fetch in 2 minutes, or the message rate is below 30% of "
                "the same hour's typical rate)."),
    "low": "Low: a vehicle is matched and progressing.",
    "reassignment_v1_1": ("v1.1 addition: a trip that would be High is Elevated instead when a vehicle on the same route "
                          "and direction is running its claimed trip more than 10 minutes ahead of schedule and departed "
                          "within 15 minutes of this trip's scheduled departure (a likely wrong trip assignment, not a "
                          "missing bus)."),
    "prior": "The missed-trip classifier supplies the prior probability, which the UI shows with the rule state.",
}

ALIASES = {
    "otp": "on_time_performance", "on-time": "on_time_performance", "on time": "on_time_performance",
    "headway": "headway_adherence", "ewt": "excess_wait_time", "excess wait": "excess_wait_time",
    "bunching": "bunching_rate", "missed": "missed_trip_rate", "missed trip": "missed_trip_rate",
    "ghost": "high", "coverage": "coverage_of_intervals",
}

DATA_SOURCES = [
    {"name": "MBTA static GTFS (current + archive)", "url": "https://cdn.mbta.com/MBTA_GTFS.zip",
     "license": "MassDOT Developers License Agreement", "attribution": "Schedule data courtesy of MassDOT/MBTA."},
    {"name": "MBTA Bus Arrival Departure Times 2025", "url": "https://gis.data.mass.gov/content/924df13d845f4907bb6a6c3ed380d57a",
     "license": "CC0", "attribution": "Massachusetts Bay Transportation Authority (public domain dedication)."},
    {"name": "MBTA GTFS-Realtime (VehiclePositions, TripUpdates)", "url": "https://cdn.mbta.com/realtime/",
     "license": "MassDOT Developers License Agreement", "attribution": "Real-time data courtesy of MassDOT/MBTA."},
    {"name": "OpenStreetMap tiles (map display)", "url": "https://www.openstreetmap.org/copyright",
     "license": "ODbL", "attribution": "© OpenStreetMap contributors"},
]

KNOWN_GAPS = [
    "The MBTA notes arrival/departure data is not guaranteed complete for any stop or date; route-days below 70% "
    "completeness are reported as unknown.",
    "Arrival/departure data covers timepoints only, not every stop.",
    "Live recording started 2026-11-23; replays only cover recorded periods.",
]


def get_definition(name: str) -> dict | None:
    key = name.strip().lower().replace("-", "_")
    key = ALIASES.get(name.strip().lower(), key)
    if key in METRICS:
        return {"name": key, "text": METRICS[key], "version": METRIC_DEF_VERSION}
    if key in GHOST:
        return {"name": key, "text": GHOST[key], "version": GHOST_DEF_VERSION}
    return None
