"""Replay engine: recorded lake snapshots (or the simulator) -> stream bus -> processor, at any speed.

Time inside processing always comes from the events. `speed` only scales the wall-clock pacing between
snapshots (1x = real time, 20x = twenty times faster, 0 = as fast as possible). Pacing uses an injectable
async sleep so tests can run "1x" virtually.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import re
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from gbcore.interfaces import MemoryStreamBus, StreamBus
from gbcore.settings import get_settings
from gbschemas.events import Event, event_time

from .decoder import decode
from .processor import Processor

TS_RE = re.compile(r"(\d{8}T\d{6})(\d{6})Z")


def snapshot_ts(path: Path) -> float:
    m = TS_RE.search(path.name)
    if not m:
        raise ValueError(path)
    dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return dt.timestamp() + int(m.group(2)) / 1e6


def list_snapshots(raw_dir: Path, start: float | None = None, end: float | None = None) -> list[tuple[float, str, Path]]:
    out = []
    for feed in ("VehiclePositions", "TripUpdates"):
        for p in (raw_dir / feed).rglob("*.pb.gz"):
            ts = snapshot_ts(p)
            if (start is None or ts >= start) and (end is None or ts < end):
                out.append((ts, feed, p))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def failed_fetches(raw_dir: Path, start: float | None = None, end: float | None = None) -> list[tuple[float, str]]:
    """Failed fetch attempts from the recorder manifest (they advance the event clock during outages)."""
    import json

    out = []
    m = raw_dir / "manifest.jsonl"
    if not m.exists():
        return out
    for line in m.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "error" in r:
            ts = datetime.fromisoformat(r["fetch_ts"]).timestamp()
            if (start is None or ts >= start) and (end is None or ts < end):
                out.append((ts, r["feed"]))
    return out


def iter_lake_events(raw_dir: Path, start: float | None = None, end: float | None = None,
                     route_filter: set[str] | None = None, source: str = "replay") -> Iterator[list[Event]]:
    """Yields one decoded snapshot (list of events) at a time, in fetch order (failed fetches included)."""
    from gbschemas.events import FeedFetchEvent

    items: list[tuple[float, str, Path | None]] = list(list_snapshots(raw_dir, start, end))
    items += [(ts, feed, None) for ts, feed in failed_fetches(raw_dir, start, end)]
    items.sort(key=lambda x: (x[0], x[1]))
    for ts, feed, p in items:
        if p is None:
            yield [FeedFetchEvent(feed, ts, ts, 0, source, ok=False)]
            continue
        try:
            payload = gzip.decompress(p.read_bytes())
        except Exception:  # noqa: BLE001 - a corrupt snapshot is skipped, never fatal
            continue
        yield decode(feed, payload, ts, source, route_filter)


def batches_from_events(events: list[Event]) -> Iterator[list[Event]]:
    """Group a flat (simulator) event list into per-fetch batches."""
    batch: list[Event] = []
    cur = None
    for e in events:
        f = e.fetch_ts
        if cur is not None and f != cur:
            yield batch
            batch = []
        cur = f
        batch.append(e)
    if batch:
        yield batch


async def produce(bus: StreamBus, batches: Iterator[list[Event]], speed: float,
                  sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    n = 0
    prev_ts = None
    for batch in batches:
        if not batch:
            continue
        ts = batch[-1].fetch_ts
        if prev_ts is not None and speed > 0:
            await sleep(max(0.0, (ts - prev_ts) / speed))
        prev_ts = ts
        for e in batch:
            await bus.publish(e)
            n += 1
    await bus.close()
    return n


async def consume(bus: StreamBus, proc: Processor) -> int:
    n = 0
    async for e in bus.subscribe():
        proc.process(e)
        n += 1
    proc.flush()
    return n


async def replay(batches: Iterator[list[Event]], proc: Processor, speed: float = 0.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, bus: StreamBus | None = None) -> dict:
    bus = bus or MemoryStreamBus()
    produced, consumed = await asyncio.gather(produce(bus, batches, speed, sleep), consume(bus, proc))
    return {"produced": produced, "consumed": consumed, "dropped": produced - consumed}


def main(argv: list[str] | None = None) -> int:
    from .network import get_network

    p = argparse.ArgumentParser(description="Replay recorded snapshots through the processor")
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument("--hours", type=float, default=1.0)
    a = p.parse_args(argv)
    s = get_settings()
    raw = s.lake_dir / "raw"
    snaps = list_snapshots(raw)
    if not snaps:
        print("no recorded snapshots")
        return 1
    start = snaps[0][0]
    net = get_network()
    from gbdata.cli import load_pilot_routes

    routes = set(load_pilot_routes())
    proc = Processor(net, route_ids=routes)
    r = asyncio.run(replay(iter_lake_events(raw, start, start + a.hours * 3600, routes), proc, a.speed))
    print(r, proc.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["replay", "iter_lake_events", "batches_from_events", "list_snapshots", "event_time"]
