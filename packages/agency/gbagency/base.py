"""AgencyAdapter interface: every agency-specific detail lives behind this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedUrls:
    vehicle_positions: str
    trip_updates: str
    alerts: str | None = None


class AgencyAdapter(ABC):
    agency_id: str
    timezone: str
    service_day_start_hour: int  # service day rolls over at this local hour

    @property
    @abstractmethod
    def static_gtfs_url(self) -> str: ...

    @property
    @abstractmethod
    def gtfs_archive_index_url(self) -> str: ...

    @property
    @abstractmethod
    def realtime(self) -> FeedUrls: ...

    @abstractmethod
    def direction_label_to_id(self, label: str) -> int: ...

    @property
    @abstractmethod
    def arrdep_item_ids(self) -> dict[int, str]:
        """Historical ground-truth dataset item IDs keyed by year."""
