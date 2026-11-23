"""Interfaces for every external dependency (spec 7.3) and their local (lite) adapters.

Full-mode (Redis) adapters live in gbcore.adapters_full; Azure adapters (Phase C) in gbcore.adapters_azure.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any


class StreamBus(ABC):
    @abstractmethod
    async def publish(self, event: Any) -> None: ...

    @abstractmethod
    def subscribe(self) -> AsyncIterator[Any]: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None


class MemoryStreamBus(StreamBus):
    """Lite adapter: in-process asyncio queue (unbounded => no drops)."""

    _SENTINEL = object()

    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.published = 0

    async def publish(self, event: Any) -> None:
        self.published += 1
        await self.q.put(event)

    async def close(self) -> None:
        await self.q.put(self._SENTINEL)

    async def subscribe(self) -> AsyncIterator[Any]:  # type: ignore[override]
        while True:
            e = await self.q.get()
            if e is self._SENTINEL:
                return
            yield e


class LiveState(ABC):
    @abstractmethod
    def set_vehicle(self, vehicle_id: str, data: dict) -> None: ...

    @abstractmethod
    def vehicles(self, route_id: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_vehicle(self, vehicle_id: str) -> dict | None: ...

    @abstractmethod
    def set_ghost(self, trip_id: str, data: dict) -> None: ...

    @abstractmethod
    def ghosts(self) -> dict[str, dict]: ...

    @abstractmethod
    def set_meta(self, key: str, value: Any) -> None: ...

    @abstractmethod
    def get_meta(self, key: str, default: Any = None) -> Any: ...


class MemoryLiveState(LiveState):
    def __init__(self) -> None:
        self._v: dict[str, dict] = {}
        self._g: dict[str, dict] = {}
        self._m: dict[str, Any] = {}

    def set_vehicle(self, vehicle_id: str, data: dict) -> None:
        self._v[vehicle_id] = data

    def vehicles(self, route_id: str | None = None) -> list[dict]:
        return [v for v in self._v.values() if route_id is None or v.get("route_id") == route_id]

    def get_vehicle(self, vehicle_id: str) -> dict | None:
        return self._v.get(vehicle_id)

    def set_ghost(self, trip_id: str, data: dict) -> None:
        self._g[trip_id] = data

    def get_ghost(self, key: str) -> dict | None:
        return self._g.get(key)

    def ghosts(self) -> dict[str, dict]:
        return dict(self._g)

    def set_meta(self, key: str, value: Any) -> None:
        self._m[key] = value

    def get_meta(self, key: str, default: Any = None) -> Any:
        return self._m.get(key, default)


class Broadcaster:
    """Async fan-out to subscribers (SSE). Bounded per-subscriber queues; slow clients drop oldest."""

    def __init__(self, max_queue: int = 1000, max_subscribers: int = 2000) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.max_queue = max_queue
        self.max_subscribers = max_subscribers
        self.sent = 0

    def subscribe(self) -> asyncio.Queue:
        if len(self._subs) >= self.max_subscribers:
            raise RuntimeError("too many subscribers")
        q: asyncio.Queue = asyncio.Queue(self.max_queue)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def n_subscribers(self) -> int:
        return len(self._subs)

    def publish(self, msg: dict) -> None:
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)
            self.sent += 1


class LakeStore(ABC):
    @abstractmethod
    def path(self, *parts: str) -> Path: ...

    @abstractmethod
    def write_bytes(self, rel: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, rel: str) -> bytes: ...


class LocalLakeStore(LakeStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read_bytes(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))
