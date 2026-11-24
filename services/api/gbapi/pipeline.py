"""In-process stream pipeline for the API (lite mode): tail the recorder's lake, replay, or simulate.

Modes (env PIPELINE):
- tail   : process new raw snapshots as the recorder writes them (lite mode: "the processor tails the lake")
- replay : replay all recorded snapshots at REPLAY_SPEED, then keep tailing
- sim    : run a simulator scenario at REPLAY_SPEED (looping)
- off    : no background processing (tests inject events directly)

Tailing runs on an APScheduler AsyncIOScheduler: `tail_poll` (every 5 s) picks up new raw snapshots, and the
`ghost_staleness` timer (every 30 s) applies the ghost-rule feed check: when no snapshot has arrived for 2 minutes
it broadcasts a feed-health event so every open map shows ghost states as Unknown, and again when the feed recovers.
Processing itself stays on event time; the timers only drive polling and the staleness signal.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from gbcore.interfaces import Broadcaster, MemoryLiveState
from gbcore.settings import get_settings
from gbstream.decoder import decode
from gbstream.network import Network
from gbstream.processor import Processor
from gbstream.replay import batches_from_events, iter_lake_events, list_snapshots
from gbstream.writers import CuratedWriter

STALE_AFTER_S = 120

log = logging.getLogger("gbapi.pipeline")


@dataclass
class PipelineStatus:
    mode: str
    speed: float
    started_wall: float = field(default_factory=time.time)
    snapshots: int = 0
    last_wall_update: float | None = None
    done_replay: bool = False
    error: str | None = None
    scenario: str | None = None


class LivePipeline:
    def __init__(self, net: Network, routes: set[str], mode: str = "tail", speed: float = 20.0,
                 scenario: str = "normal", priors: dict | None = None, persist: bool = False) -> None:
        self.net = net
        self.routes = routes
        self.bc = Broadcaster()
        self.state = MemoryLiveState()
        self.status = PipelineStatus(mode, speed, scenario=scenario if mode == "sim" else None)
        self.priors = priors or {}
        self.proc = self._new_proc()
        self.writer = CuratedWriter(get_settings().lake_dir / "curated", f"{mode}-{int(time.time())}")
        self._task: asyncio.Task | None = None
        self._last_ts: float | None = None
        self.prediction_log: list[dict] | None = None  # set by the API (shared with the stop page)
        self.db_sink = None
        self._last_db_flush = float("-inf")
        self.scheduler: Any = None  # APScheduler AsyncIOScheduler once tailing starts
        self.feed_stale = False
        self.db_flush_every_s = 60.0
        if persist:
            from gbstream.dbsink import DbSink

            try:
                self.db_sink = DbSink()
            except Exception as e:  # noqa: BLE001 - DB is optional for the live pipeline
                log.warning("operational DB sink disabled: %s", e)

    def _new_proc(self, schedule=None) -> Processor:
        return Processor(self.net, live_state=self.state, broadcaster=self.bc, route_ids=self.routes,
                         schedule=schedule, priors=self.priors)

    def start(self) -> None:
        if self.status.mode != "off":
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def feed(self, batch) -> None:
        for e in batch:
            self.proc.process(e)
        self.status.snapshots += 1
        self.status.last_wall_update = time.time()
        self.writer.maybe_flush(self.proc)
        if self.db_sink is not None and self.proc.clock - self._last_db_flush >= self.db_flush_every_s:
            self._last_db_flush = self.proc.clock
            try:
                self.db_sink.flush(self.proc, self.prediction_log)
            except Exception as e:  # noqa: BLE001
                log.warning("DB flush failed: %s", e)

    async def _run(self) -> None:
        try:
            if self.status.mode == "replay":
                await self._replay()
                self.status.done_replay = True
                await self._tail()
            elif self.status.mode == "sim":
                await self._sim()
            else:
                await self._tail()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline failed")
            self.status.error = f"{type(e).__name__}: {e}"

    async def _paced(self, batches) -> None:
        prev = None
        for batch in batches:
            if not batch:
                continue
            ts = batch[-1].fetch_ts
            if prev is not None and self.status.speed > 0:
                await asyncio.sleep(max(0.0, min(30.0, (ts - prev) / self.status.speed)))
            else:
                await asyncio.sleep(0)
            prev = ts
            self.feed(batch)
            self._last_ts = ts

    async def _replay(self) -> None:
        raw = get_settings().lake_dir / "raw"
        await self._paced(iter_lake_events(raw, route_filter=self.routes))

    def _poll_lake(self) -> int:
        raw = get_settings().lake_dir / "raw"
        snaps = list_snapshots(raw, start=(self._last_ts + 1e-6) if self._last_ts else None)
        if self._last_ts is None and snaps:
            snaps = snaps[-6:]  # start from the latest couple of minutes
        n = 0
        for ts, feed, p in snaps:
            try:
                batch = decode(feed, gzip.decompress(p.read_bytes()), ts, "live", self.routes)
            except Exception:  # noqa: BLE001
                continue
            self.feed(batch)
            self._last_ts = ts
            n += 1
        return n

    def _ghost_staleness(self) -> None:
        last = self.status.last_wall_update
        stale = last is None or (time.time() - last) > STALE_AFTER_S
        if stale != self.feed_stale:
            self.feed_stale = stale
            self.bc.publish({"type": "feed_health", "route_id": None, "stale": stale,
                             "ghost_state_override": "unknown" if stale else None,
                             "reason": "no feed snapshot for 2 minutes" if stale else "feed recovered"})

    def start_scheduler(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = AsyncIOScheduler(timezone="UTC")
        sched.add_job(self._poll_lake, "interval", seconds=5, id="tail_poll", max_instances=1, coalesce=True,
                      next_run_time=datetime.now(UTC))
        sched.add_job(self._ghost_staleness, "interval", seconds=30, id="ghost_staleness", max_instances=1,
                      coalesce=True)
        sched.start()
        self.scheduler = sched

    async def _tail(self) -> None:
        self.start_scheduler()
        await asyncio.Event().wait()  # jobs run on the scheduler; this task just keeps the pipeline alive

    async def _sim(self) -> None:
        from gbsim.evaluate import DEFAULT_DATE
        from gbsim.simulator import simulate

        while True:
            sim = await asyncio.to_thread(simulate, self.net, self.status.scenario or "normal", DEFAULT_DATE,
                                          7.0, 2.0, sorted(self.routes))
            self.proc = self._new_proc(schedule=sim.schedule)
            if self.db_sink is not None:
                self.db_sink._arr_i = 0
            await self._paced(batches_from_events(sim.events))
