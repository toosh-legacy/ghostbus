"""Locust SSE load test: each simulated rider opens /v1/stream for one route and holds it.

Reported as custom request types so Locust's stats show:
- "SSE connect"      time to the first `hello` event
- "SSE vehicle"      gap between successive vehicle updates on one stream (fan-out latency under load)
- "SSE session"      one entry per stream that stayed open for the hold time (failures = dropped streams)
Run via `uv run python -m gbevals.load_locust` (starts the API on a 20x simulator replay first).
"""

from __future__ import annotations

import os
import random
import time

from locust import HttpUser, between, task

ROUTES = os.environ.get("LOAD_ROUTES", "1,23,28,39,66,111").split(",")
HOLD_S = float(os.environ.get("LOAD_HOLD_S", "40"))


class Rider(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def watch_route(self) -> None:
        route = random.choice(ROUTES)
        ev = self.environment.events.request
        t0 = time.perf_counter()
        last = None
        n = 0
        try:
            with self.client.get(f"/v1/stream?route={route}", stream=True, timeout=HOLD_S + 30,
                                 name="/v1/stream", catch_response=True) as r:
                r.success()
                for raw in r.iter_lines(decode_unicode=True):
                    now = time.perf_counter()
                    if raw.startswith("event: hello"):
                        ev.fire(request_type="SSE", name="SSE connect", response_time=(now - t0) * 1000,
                                response_length=0, exception=None, context={})
                    elif raw.startswith("event: vehicle"):
                        n += 1
                        if last is not None:
                            ev.fire(request_type="SSE", name="SSE vehicle", response_time=(now - last) * 1000,
                                    response_length=0, exception=None, context={})
                        last = now
                    if now - t0 > HOLD_S:
                        break
            ok = n > 0
            ev.fire(request_type="SSE", name="SSE session", response_time=(time.perf_counter() - t0) * 1000,
                    response_length=n, exception=None if ok else RuntimeError("no vehicle events"), context={})
        except Exception as e:  # noqa: BLE001
            ev.fire(request_type="SSE", name="SSE session", response_time=(time.perf_counter() - t0) * 1000,
                    response_length=n, exception=e, context={})
