"""Normalized stream event schemas (shared by recorder, simulator, replay and processor).

All times are UTC epoch seconds (float). `vehicle_ts` is event time; `fetch_ts` is when the
snapshot was fetched (or simulated as fetched). Processing never reads the wall clock.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class VehicleEvent:
    vehicle_id: str
    trip_id: str | None
    route_id: str | None
    direction_id: int | None
    lat: float
    lon: float
    vehicle_ts: float
    fetch_ts: float
    bearing: float | None = None
    stop_sequence: int | None = None
    status: str | None = None
    source: str = "live"  # live | replay | sim
    kind: str = "vehicle"

    def key(self) -> tuple[str, float]:
        return (self.vehicle_id, self.vehicle_ts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class TripUpdateEvent:
    trip_id: str
    route_id: str | None
    fetch_ts: float
    vehicle_id: str | None = None
    has_prediction: bool = True
    first_pred_ts: float | None = None
    source: str = "live"
    kind: str = "trip_update"
    stop_preds: dict[str, float] = field(default_factory=dict)  # stop_id -> predicted arrival (UTC epoch s)


@dataclass(slots=True)
class FeedFetchEvent:
    """Marker for one successful feed fetch: drives the event-time clock and feed health."""

    feed: str
    fetch_ts: float
    header_ts: float
    n_entities: int
    source: str = "live"
    kind: str = "fetch"
    vehicle_ids: list[str] = field(default_factory=list)
    ok: bool = True  # False = fetch attempt failed (still advances the event clock)


Event = VehicleEvent | TripUpdateEvent | FeedFetchEvent


def event_time(e: Event) -> float:
    return e.vehicle_ts if isinstance(e, VehicleEvent) else e.fetch_ts
