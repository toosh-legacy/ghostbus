"""Live ghost-risk rules, ghost-def v1.0 (spec 9.4). Published verbatim in docs/methodology.md.

States for an upcoming/in-progress scheduled trip, evaluated every 30 s of event time:
- unknown : feed unhealthy (no successful VehiclePositions fetch in 2 min, or message rate < 30% of the
            typical rate for that hour)
- low     : a vehicle is matched to the trip and progressing
- high    : scheduled first departure > 5 min ago, no vehicle matched, feed healthy
- elevated: departure within 10 min and no vehicle assigned yet, OR the trip has a trip-update prediction
            but no vehicle seen for > 3 min
v1.1 adds: a trip that would be "high" is "elevated" instead when a vehicle on the same route and direction is
           running its claimed trip more than 10 min ahead of schedule and departed within 15 min of this trip's
           scheduled departure (a likely wrong trip assignment, not a missing bus).
"""

from __future__ import annotations

from dataclasses import dataclass

GHOST_DEF_VERSION = "ghost-def v1.1"
FEED_STALE_S = 120.0
RATE_FRACTION = 0.30
HIGH_AFTER_S = 300.0
ELEVATED_BEFORE_S = 600.0
TU_NO_VEHICLE_S = 180.0
REASSIGN_EARLY_S = 600.0  # v1.1: claimed trip is running > 10 min early ...
REASSIGN_WINDOW_S = 900.0  # ... and departed within 15 min of this trip's scheduled departure
VEHICLE_FRESH_S = 600.0  # a matched vehicle seen within this window counts as progressing


@dataclass
class ScheduledTrip:
    trip_id: str
    route_id: str
    direction_id: int
    service_date: str
    first_dep_ts: float
    last_arr_ts: float
    first_stop_id: str


@dataclass
class GhostEval:
    trip_id: str
    route_id: str
    service_date: str
    evaluated_at: float
    state: str
    reason: str
    def_version: str = GHOST_DEF_VERSION
    prior_prob: float | None = None


@dataclass
class FeedHealth:
    healthy: bool
    reason: str
    last_fetch_ts: float | None
    last_rate: int | None


def feed_health(now: float, last_fetch_ts: float | None, last_rate: int | None,
                typical_rate: float | None) -> FeedHealth:
    if last_fetch_ts is None or now - last_fetch_ts > FEED_STALE_S:
        return FeedHealth(False, "no successful vehicle-position fetch in the last 2 minutes", last_fetch_ts,
                          last_rate)
    if typical_rate and last_rate is not None and last_rate < RATE_FRACTION * typical_rate:
        return FeedHealth(False, f"message rate {last_rate} is below 30% of typical {typical_rate:.0f}",
                          last_fetch_ts, last_rate)
    return FeedHealth(True, "ok", last_fetch_ts, last_rate)


def evaluate_trip(now: float, trip: ScheduledTrip, health: FeedHealth, vehicle_last_seen: float | None,
                  tu_last_pred: float | None, prior: float | None = None,
                  possible_reassignment: str | None = None) -> GhostEval:
    def ev(state: str, reason: str) -> GhostEval:
        return GhostEval(trip.trip_id, trip.route_id, trip.service_date, now, state, reason, prior_prob=prior)

    if not health.healthy:
        return ev("unknown", f"feed unhealthy: {health.reason}")
    if vehicle_last_seen is not None and now - vehicle_last_seen <= VEHICLE_FRESH_S:
        return ev("low", "vehicle matched and progressing")
    since_dep = now - trip.first_dep_ts
    if since_dep > HIGH_AFTER_S and possible_reassignment:
        return ev("elevated", f"no vehicle reports this trip, but {possible_reassignment}")
    if since_dep > HIGH_AFTER_S:
        return ev("high", f"scheduled departure was {since_dep / 60:.0f} min ago and no vehicle is matched")
    if tu_last_pred is not None and vehicle_last_seen is None and since_dep > -ELEVATED_BEFORE_S \
            and now - tu_last_pred <= 60:
        # predicted in trip updates but no vehicle; becomes elevated once 3 min have passed without one
        if since_dep > -TU_NO_VEHICLE_S:
            return ev("elevated", "trip has a prediction but no vehicle has been seen for over 3 minutes")
    if -ELEVATED_BEFORE_S <= since_dep:
        return ev("elevated", "not yet tracking: departure within 10 min and no vehicle assigned")
    return ev("low", "departure more than 10 min away")
