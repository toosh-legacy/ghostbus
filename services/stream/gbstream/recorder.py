"""Live GTFS-Realtime recorder (D3).

Polls the MBTA VehiclePositions and TripUpdates feeds politely:
- at most every RECORDER_INTERVAL_S (default 20 s, never below 15 s)
- descriptive User-Agent
- If-Modified-Since / Last-Modified honoured
- exponential backoff on errors (capped at 10 min)

Stores raw bytes gzipped at lake/raw/{feed}/date=YYYY-MM-DD/hour=HH/{fetch_ts}.pb.gz,
appends a manifest line per fetch, and writes a heartbeat file every minute.
In lite mode the processor tails the lake; in full mode it also publishes to Redis Streams.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from gbagency.mbta import get_agency
from gbcore.settings import get_settings

log = logging.getLogger("gbstream.recorder")
MIN_INTERVAL_S = 15.0
MAX_BACKOFF_S = 600.0


@dataclass
class FeedState:
    name: str
    url: str
    last_modified: str | None = None
    consecutive_errors: int = 0
    next_due: float = 0.0
    fetches: int = 0
    stored: int = 0
    not_modified: int = 0
    errors: int = 0
    last_ok_utc: str | None = None
    last_error: str | None = None
    last_entities: int | None = None


def snapshot_path(raw_dir: Path, feed: str, fetch_ts: datetime) -> Path:
    d = fetch_ts.strftime("%Y-%m-%d")
    h = fetch_ts.strftime("%H")
    name = fetch_ts.strftime("%Y%m%dT%H%M%S%fZ")
    return raw_dir / feed / f"date={d}" / f"hour={h}" / f"{name}.pb.gz"


def count_entities(payload: bytes) -> int | None:
    try:
        from google.transit import gtfs_realtime_pb2

        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(payload)
        return len(msg.entity)
    except Exception:  # noqa: BLE001 - counting is best effort
        return None


def backoff_seconds(consecutive_errors: int, base: float) -> float:
    """Exponential backoff: base, 2*base, 4*base ... capped at MAX_BACKOFF_S."""
    return min(MAX_BACKOFF_S, base * (2 ** max(0, consecutive_errors - 1)))


class Recorder:
    def __init__(self, raw_dir: Path, var_dir: Path, interval_s: float, user_agent: str,
                 publisher=None, feeds: list[FeedState] | None = None) -> None:
        self.raw_dir = raw_dir
        self.var_dir = var_dir
        self.interval_s = max(MIN_INTERVAL_S, interval_s)
        self.user_agent = user_agent
        self.publisher = publisher
        if feeds is None:
            rt = get_agency().realtime
            feeds = [
                FeedState("VehiclePositions", rt.vehicle_positions),
                FeedState("TripUpdates", rt.trip_updates),
            ]
        self.feeds = feeds
        self.manifest = raw_dir / "manifest.jsonl"
        self.heartbeat = var_dir / "recorder_heartbeat.json"
        self.started = datetime.now(UTC)
        self._last_hb = 0.0
        self._stop = False

    def stop(self, *_args) -> None:
        self._stop = True

    def fetch_once(self, client: httpx.Client, fs: FeedState) -> None:
        fetch_ts = datetime.now(UTC)
        headers = {"User-Agent": self.user_agent}
        if fs.last_modified:
            headers["If-Modified-Since"] = fs.last_modified
        fs.fetches += 1
        rec: dict = {"feed": fs.name, "url": fs.url, "fetch_ts": fetch_ts.isoformat()}
        try:
            r = client.get(fs.url, headers=headers, timeout=20.0)
            rec["status"] = r.status_code
            if r.status_code == 304:
                fs.not_modified += 1
                fs.consecutive_errors = 0
                fs.last_ok_utc = fetch_ts.isoformat()
            elif r.status_code == 200:
                payload = r.content
                path = snapshot_path(self.raw_dir, fs.name, fetch_ts)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                with gzip.open(tmp, "wb") as f:
                    f.write(payload)
                os.replace(tmp, path)
                fs.last_modified = r.headers.get("Last-Modified")
                fs.stored += 1
                fs.consecutive_errors = 0
                fs.last_ok_utc = fetch_ts.isoformat()
                fs.last_entities = count_entities(payload)
                rec.update(path=path.relative_to(self.raw_dir).as_posix(), bytes=len(payload),
                           entities=fs.last_entities, last_modified=fs.last_modified)
                if self.publisher is not None:
                    try:
                        self.publisher(fs.name, fetch_ts, payload)
                    except Exception as e:  # noqa: BLE001
                        log.warning("publish failed: %s", e)
            else:
                raise RuntimeError(f"HTTP {r.status_code}")
            fs.next_due = time.monotonic() + self.interval_s
        except Exception as e:  # noqa: BLE001 - recorder must survive everything
            fs.errors += 1
            fs.consecutive_errors += 1
            fs.last_error = f"{type(e).__name__}: {e}"[:300]
            rec["error"] = fs.last_error
            delay = backoff_seconds(fs.consecutive_errors, self.interval_s)
            rec["backoff_s"] = delay
            fs.next_due = time.monotonic() + delay
            log.warning("%s fetch failed (%s); backing off %.0fs", fs.name, fs.last_error, delay)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as m:
            m.write(json.dumps(rec) + "\n")

    def write_heartbeat(self, stopped: bool = False) -> None:
        hb = {
            "pid": os.getpid(),
            "stopped": stopped,
            "now_utc": datetime.now(UTC).isoformat(),
            "started_utc": self.started.isoformat(),
            "interval_s": self.interval_s,
            "feeds": {
                f.name: {k: v for k, v in f.__dict__.items() if k != "next_due"} for f in self.feeds
            },
        }
        self.heartbeat.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(hb, indent=2), encoding="utf-8")
        os.replace(tmp, self.heartbeat)
        self._last_hb = time.monotonic()

    def run(self, max_cycles: int | None = None, client: httpx.Client | None = None,
            sleep_s: float = 1.0) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.var_dir.mkdir(parents=True, exist_ok=True)
        own = client is None
        client = client or httpx.Client(follow_redirects=True)
        cycles = 0
        try:
            self.write_heartbeat()
            while not self._stop:
                now = time.monotonic()
                for fs in self.feeds:
                    if now >= fs.next_due:
                        self.fetch_once(client, fs)
                if time.monotonic() - self._last_hb >= 60:
                    self.write_heartbeat()
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                time.sleep(sleep_s)
        finally:
            self.write_heartbeat(stopped=True)
            if own:
                client.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="GhostBus live GTFS-RT recorder")
    p.add_argument("--max-cycles", type=int, default=None)
    args = p.parse_args(argv)
    s = get_settings()
    s.var_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(s.var_dir / "recorder.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    publisher = None
    if s.stream_bus == "redis":
        try:
            from gbstream.bus_redis import make_raw_publisher

            publisher = make_raw_publisher(s.redis_url)
        except Exception as e:  # noqa: BLE001
            log.warning("redis publisher unavailable, lake only: %s", e)
    rec = Recorder(s.lake_dir / "raw", s.var_dir, s.recorder_interval_s, s.recorder_user_agent, publisher)
    signal.signal(signal.SIGINT, rec.stop)
    signal.signal(signal.SIGTERM, rec.stop)
    (s.var_dir / "recorder.pid").write_text(str(os.getpid()))
    log.info("recorder starting interval=%ss ua=%s", rec.interval_s, rec.user_agent)
    rec.run(args.max_cycles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
