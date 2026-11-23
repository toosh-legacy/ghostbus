"""MBTA adapter. All MBTA-specific quirks live here, never in the core."""

from __future__ import annotations

from .base import AgencyAdapter, FeedUrls


class MBTAAdapter(AgencyAdapter):
    agency_id = "mbta"
    timezone = "America/New_York"
    # MBTA service day runs ~02:00 to 01:59:59 local time.
    service_day_start_hour = 2

    @property
    def static_gtfs_url(self) -> str:
        return "https://cdn.mbta.com/MBTA_GTFS.zip"

    @property
    def gtfs_archive_index_url(self) -> str:
        return "https://cdn.mbta.com/archive/archived_feeds.txt"

    @property
    def realtime(self) -> FeedUrls:
        return FeedUrls(
            vehicle_positions="https://cdn.mbta.com/realtime/VehiclePositions.pb",
            trip_updates="https://cdn.mbta.com/realtime/TripUpdates.pb",
            alerts="https://cdn.mbta.com/realtime/Alerts.pb",
        )

    def direction_label_to_id(self, label: str) -> int:
        # In MBTA arrival/departure data, "Outbound" corresponds to GTFS direction_id 0
        # and "Inbound" to direction_id 1.
        s = str(label).strip().lower()
        if s in {"outbound", "0"}:
            return 0
        if s in {"inbound", "1"}:
            return 1
        raise ValueError(f"unknown MBTA direction label: {label!r}")

    # Arrival/departure data names Silver Line routes "SL1" etc.; GTFS uses numeric route_ids.
    SILVER_LINE = {"SL1": "741", "SL2": "742", "SL3": "743", "SLW": "746", "SL5": "749", "SL4": "751"}

    def normalize_route_id(self, rid: str) -> str:
        """D2 route_id -> GTFS route_id ('01' -> '1', 'SL1' -> '741')."""
        s = str(rid).strip()
        if s.upper() in self.SILVER_LINE:
            return self.SILVER_LINE[s.upper()]
        if s.isdigit():
            return str(int(s))
        return s.lstrip("0") or s

    @property
    def arrdep_item_ids(self) -> dict[int, str]:
        return {
            2025: "924df13d845f4907bb6a6c3ed380d57a",
            2024: "96c77138c3144906bce93d0257531b6a",
            2023: "b7b36fdb7b3a4728af2fccc78c2ca5b7",
        }


def get_agency() -> AgencyAdapter:
    return MBTAAdapter()
