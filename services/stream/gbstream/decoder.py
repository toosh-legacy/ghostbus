"""Decode GTFS-Realtime protobuf snapshots into normalized events (spec 7.4 step 2)."""

from __future__ import annotations

from gbschemas.events import Event, FeedFetchEvent, TripUpdateEvent, VehicleEvent
from google.transit import gtfs_realtime_pb2

STATUS = {0: "INCOMING_AT", 1: "STOPPED_AT", 2: "IN_TRANSIT_TO"}


def decode_vehicle_positions(payload: bytes, fetch_ts: float, source: str = "live",
                             route_filter: set[str] | None = None) -> list[Event]:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(payload)
    out: list[Event] = []
    for ent in msg.entity:
        if not ent.HasField("vehicle"):
            continue
        v = ent.vehicle
        if not v.HasField("position"):
            continue
        rid = v.trip.route_id or None
        if route_filter is not None and rid not in route_filter:
            continue
        out.append(VehicleEvent(
            vehicle_id=v.vehicle.id or ent.id,
            trip_id=v.trip.trip_id or None,
            route_id=rid,
            direction_id=v.trip.direction_id if v.trip.HasField("direction_id") else None,
            lat=v.position.latitude,
            lon=v.position.longitude,
            bearing=v.position.bearing if v.position.HasField("bearing") else None,
            vehicle_ts=float(v.timestamp or msg.header.timestamp or fetch_ts),
            fetch_ts=fetch_ts,
            stop_sequence=v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
            status=STATUS.get(v.current_status),
            source=source,
        ))
    out.append(FeedFetchEvent("VehiclePositions", fetch_ts, float(msg.header.timestamp or fetch_ts),
                              len(msg.entity), source))
    return out


def decode_trip_updates(payload: bytes, fetch_ts: float, source: str = "live",
                        route_filter: set[str] | None = None) -> list[Event]:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(payload)
    out: list[Event] = []
    for ent in msg.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        rid = tu.trip.route_id or None
        if route_filter is not None and rid not in route_filter:
            continue
        first = None
        preds: dict[str, float] = {}
        for stu in tu.stop_time_update:
            t = stu.arrival.time or stu.departure.time
            if t:
                if first is None:
                    first = float(t)
                if stu.stop_id:
                    preds[stu.stop_id] = float(t)
        out.append(TripUpdateEvent(trip_id=tu.trip.trip_id, route_id=rid, fetch_ts=fetch_ts,
                                   vehicle_id=tu.vehicle.id or None, has_prediction=first is not None,
                                   first_pred_ts=first, source=source, stop_preds=preds))
    out.append(FeedFetchEvent("TripUpdates", fetch_ts, float(msg.header.timestamp or fetch_ts), len(msg.entity),
                              source))
    return out


def decode(feed: str, payload: bytes, fetch_ts: float, source: str = "live",
           route_filter: set[str] | None = None) -> list[Event]:
    if feed == "VehiclePositions":
        return decode_vehicle_positions(payload, fetch_ts, source, route_filter)
    if feed == "TripUpdates":
        return decode_trip_updates(payload, fetch_ts, source, route_filter)
    raise ValueError(feed)


def encode_vehicle_positions(events: list[VehicleEvent], header_ts: float) -> bytes:
    """Encode events back into a GTFS-RT FeedMessage (simulator → recorded-like snapshots)."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(header_ts)
    for e in events:
        ent = msg.entity.add()
        ent.id = e.vehicle_id
        v = ent.vehicle
        if e.trip_id:
            v.trip.trip_id = e.trip_id
        if e.route_id:
            v.trip.route_id = e.route_id
        if e.direction_id is not None:
            v.trip.direction_id = e.direction_id
        v.position.latitude = e.lat
        v.position.longitude = e.lon
        v.timestamp = int(e.vehicle_ts)
        v.vehicle.id = e.vehicle_id
    return msg.SerializeToString()


EFFECT = {1: "NO_SERVICE", 2: "REDUCED_SERVICE", 3: "SIGNIFICANT_DELAYS", 4: "DETOUR", 5: "ADDITIONAL_SERVICE",
          6: "MODIFIED_SERVICE", 7: "OTHER_EFFECT", 8: "UNKNOWN_EFFECT", 9: "STOP_MOVED", 10: "NO_EFFECT",
          11: "ACCESSIBILITY_ISSUE"}


def decode_alerts(payload: bytes, route_filter: set[str] | None = None) -> list[dict]:
    """Service alerts -> plain dicts (route ids, stop ids, effect, header text, active periods)."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(payload)
    out = []
    for ent in msg.entity:
        if not ent.HasField("alert"):
            continue
        a = ent.alert
        routes = sorted({ie.route_id for ie in a.informed_entity if ie.route_id})
        if route_filter is not None and not (set(routes) & route_filter):
            continue

        def text(ts) -> str:
            for t in ts.translation:
                if t.language in ("", "en"):
                    return t.text
            return ts.translation[0].text if ts.translation else ""

        out.append({"id": ent.id, "routes": routes,
                    "stops": sorted({ie.stop_id for ie in a.informed_entity if ie.stop_id}),
                    "effect": EFFECT.get(a.effect, str(a.effect)), "header": text(a.header_text),
                    "description": text(a.description_text)[:1000],
                    "active": [[p.start or None, p.end or None] for p in a.active_period]})
    return out
