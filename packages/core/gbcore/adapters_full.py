"""Full-mode adapters (Docker): Redis Streams bus and Redis live state. Same interfaces as the lite ones."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from gbschemas.events import FeedFetchEvent, TripUpdateEvent, VehicleEvent

from .interfaces import LiveState, StreamBus

_KINDS = {"vehicle": VehicleEvent, "trip_update": TripUpdateEvent, "fetch": FeedFetchEvent}


def encode_event(e: Any) -> dict[str, str]:
    from dataclasses import asdict

    return {"e": json.dumps(asdict(e))}


def decode_event(fields: dict) -> Any:
    raw = fields.get(b"e") or fields.get("e")
    d = json.loads(raw)
    return _KINDS[d["kind"]](**d)


class RedisStreamsBus(StreamBus):
    """Redis Streams with a consumer group. Partitioning by vehicle is implicit: one stream, ordered appends.

    Phase C maps this to Event Hubs with partition key = vehicle ID.
    """

    END = "__end__"

    def __init__(self, url: str, stream: str = "gb:events", group: str = "processor", consumer: str = "c1") -> None:
        import redis.asyncio as aioredis

        self.r = aioredis.from_url(url)
        self.stream, self.group, self.consumer = stream, group, consumer
        self._group_ready = False

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as e:  # noqa: BLE001 - BUSYGROUP means it exists
            if "BUSYGROUP" not in str(e):
                raise
        self._group_ready = True

    async def publish(self, event: Any) -> None:
        await self._ensure_group()
        await self.r.xadd(self.stream, encode_event(event))

    async def close(self) -> None:
        await self._ensure_group()
        await self.r.xadd(self.stream, {"e": self.END})

    async def subscribe(self) -> AsyncIterator[Any]:  # type: ignore[override]
        await self._ensure_group()
        while True:
            resp = await self.r.xreadgroup(self.group, self.consumer, {self.stream: ">"}, count=500, block=5000)
            if not resp:
                continue
            for _stream, msgs in resp:
                for mid, fields in msgs:
                    await self.r.xack(self.stream, self.group, mid)
                    raw = fields.get(b"e") or fields.get("e")
                    if raw in (self.END, self.END.encode()):
                        return
                    yield decode_event(fields)


class RedisLiveState(LiveState):
    def __init__(self, url: str, prefix: str = "gb") -> None:
        import redis

        self.r = redis.Redis.from_url(url)
        self.p = prefix

    def set_vehicle(self, vehicle_id: str, data: dict) -> None:
        self.r.hset(f"{self.p}:vehicles", vehicle_id, json.dumps(data))

    def get_vehicle(self, vehicle_id: str) -> dict | None:
        v = self.r.hget(f"{self.p}:vehicles", vehicle_id)
        return json.loads(v) if v else None

    def vehicles(self, route_id: str | None = None) -> list[dict]:
        vs = [json.loads(v) for v in self.r.hvals(f"{self.p}:vehicles")]
        return [v for v in vs if route_id is None or v.get("route_id") == route_id]

    def set_ghost(self, trip_id: str, data: dict) -> None:
        self.r.hset(f"{self.p}:ghosts", trip_id, json.dumps(data))

    def ghosts(self) -> dict[str, dict]:
        return {k.decode(): json.loads(v) for k, v in self.r.hgetall(f"{self.p}:ghosts").items()}

    def set_meta(self, key: str, value: Any) -> None:
        self.r.hset(f"{self.p}:meta", key, json.dumps(value))

    def get_meta(self, key: str, default: Any = None) -> Any:
        v = self.r.hget(f"{self.p}:meta", key)
        return json.loads(v) if v else default


def make_raw_publisher(url: str):
    """Recorder hook (full mode): publish each raw snapshot to a Redis stream."""
    import redis

    r = redis.Redis.from_url(url)

    def pub(feed: str, fetch_ts, payload: bytes) -> None:
        r.xadd("gb:raw", {"feed": feed, "fetch_ts": fetch_ts.timestamp(), "payload": payload}, maxlen=10000)

    return pub
