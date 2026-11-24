"""Background jobs for model calls (copilot answers, daily briefs).

A job runs its generator in a worker thread, so a model call never blocks the request path or the event loop.
Every event it yields is kept on the job and fanned out to SSE listeners; a late listener replays from the start.
Jobs expire after JOB_TTL_S.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field

JOB_TTL_S = 900
MAX_JOBS = 500


@dataclass
class Job:
    id: str
    kind: str
    created: float = field(default_factory=time.time)
    status: str = "running"  # running | done | error
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

    def summary(self) -> dict:
        return {"job_id": self.id, "kind": self.kind, "status": self.status, "events_url": f"/v1/jobs/{self.id}/events",
                "result_url": f"/v1/jobs/{self.id}", "error": self.error}

    def push(self, ev: dict) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _gc(self) -> None:
        now = time.time()
        with self._lock:
            for k in [k for k, j in self._jobs.items() if now - j.created > JOB_TTL_S or len(self._jobs) > MAX_JOBS]:
                if self._jobs[k].status != "running":
                    self._jobs.pop(k, None)

    def start(self, kind: str, fn: Callable[[], Iterator[dict]]) -> Job:
        self._gc()
        job = Job(uuid.uuid4().hex, kind)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            try:
                for ev in fn():
                    job.push(ev)
                    if ev.get("type") in ("answer", "brief"):
                        job.result = ev
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.status, job.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
                job.push({"type": "error", "detail": job.error})
            finally:
                job.push({"type": "end", "status": job.status})

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return job

    async def events(self, job_id: str) -> AsyncIterator[dict]:
        """SSE events for a job: replay what happened so far, then follow it until it ends."""
        job = self._jobs[job_id]
        i = 0
        while True:
            def wait(idx: int = i) -> list[dict]:
                with job.cond:
                    job.cond.wait_for(lambda: len(job.events) > idx, timeout=10)
                    return job.events[idx:]

            new = await asyncio.to_thread(wait)
            for ev in new:
                i += 1
                yield {"event": ev.get("type", "message"), "data": json.dumps(ev, default=str)}
                if ev.get("type") == "end":
                    return
