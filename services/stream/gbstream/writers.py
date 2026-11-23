"""Curated Parquet writers (spec 7.4 step 9): pings, arrivals, ghost evaluations, partitioned by date (+route)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .processor import Processor

FLUSH_EVERY_S = 300.0


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


class CuratedWriter:
    def __init__(self, curated_dir: Path, run_id: str) -> None:
        self.dir = curated_dir
        self.run_id = run_id
        self._i = {"pings": 0, "arrivals": 0, "ghost_eval": 0}
        self._last_flush = float("-inf")
        self._part = 0
        self.files: list[Path] = []

    def maybe_flush(self, proc: Processor) -> None:
        if proc.clock - self._last_flush >= FLUSH_EVERY_S:
            self.flush(proc)

    def flush(self, proc: Processor) -> None:
        self._last_flush = proc.clock
        self._part += 1
        for name, items, tcol in (("pings", proc.pings, "vehicle_ts"), ("arrivals", proc.arrivals, "arrival_ts"),
                                  ("ghost_eval", proc.ghost_evals, "evaluated_at")):
            new = items[self._i[name]:]
            self._i[name] = len(items)
            if not new:
                continue
            df = pd.DataFrame([asdict(x) for x in new])
            df["date"] = df[tcol].map(_date)
            by = ["date", "route_id"] if name != "ghost_eval" else ["date"]
            for keys, g in df.groupby(by, dropna=False):
                keys = keys if isinstance(keys, tuple) else (keys,)
                sub = self.dir / name
                for k, v in zip(by, keys):
                    sub = sub / f"{k}={v}"
                sub.mkdir(parents=True, exist_ok=True)
                p = sub / f"{self.run_id}-{self._part:05d}.parquet"
                g.drop(columns=by).to_parquet(p, index=False)
                self.files.append(p)
