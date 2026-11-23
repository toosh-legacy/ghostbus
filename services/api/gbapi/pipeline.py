"""In-process stream pipeline for the API (lite mode): tail the recorder's lake, replay, or simulate.

Modes (env PIPELINE):
- tail   : process new raw snapshots as the recorder writes them (lite mode: "the processor tails the lake")
- replay : replay all recorded snapshots at REPLAY_SPEED, then keep tailing
- sim    : run a simulator scenario at REPLAY_SPEED (looping)
- off    : no background processing (tests inject events directly)
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import time
from dataclasses import dataclass, field

from gbcore.interfaces import Broadcaster, MemoryLiveState
from gbcore.settings import get_settings
from gbstream.decoder import decode
from gbstream.network import Network
from gbstream.processor import Processor
from gbstream.replay import batches_from_events, iter_lake_events, list_snapshots
from gbstream.writers import CuratedWriter

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
                 scenario: str = "normal", priors: dict | None = None) -> None:
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

    def _new_proc(self, schedule=None) -> Processor:
        return Processor(self.net, live_state=self.state, broadcaster=self.bc, route_ids=self.routes,
                         schedule=schedule, priors=self.priors)

    def start(self) -> None:
        if self.status.mode != "off":
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
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

    async def _tail(self) -> None:
        raw = get_settings().lake_dir / "raw"
        while True:
            snaps = list_snapshots(raw, start=(self._last_ts + 1e-6) if self._last_ts else None)
            if self._last_ts is None and snaps:
                snaps = snaps[-6:]  # start from the latest couple of minutes
            for ts, feed, p in snaps:
                try:
                    batch = decode(feed, gzip.decompress(p.read_bytes()), ts, "live", self.routes)
                except Exception:  # noqa: BLE001
                    continue
                self.feed(batch)
                self._last_ts = ts
                await asyncio.sleep(0)
            await asyncio.sleep(5)

    async def _sim(self) -> None:
        from gbsim.evaluate import DEFAULT_DATE
        from gbsim.simulator import simulate

        while True:
            sim = await asyncio.to_thread(simulate, self.net, self.status.scenario or "normal", DEFAULT_DATE,
                                          7.0, 2.0, sorted(self.routes))
            self.proc = self._new_proc(schedule=sim.schedule)
            await self._paced(batches_from_events(sim.events))
