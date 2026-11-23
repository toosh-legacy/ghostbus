"""SSE load smoke test (spec 17.1): N concurrent /v1/stream clients against a 20x simulator replay.

The API (uvicorn, pipeline off) runs in this process and is fed a simulated half hour at 20x; the N streaming
clients run in a separate process (own event loop), so the test measures the server rather than the harness.
Each client counts vehicle events until a final `done` event, then the client process reports the counts.

Run: uv run python -m gbevals.load_sse --clients 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time

import httpx


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _clients(port: int, n: int) -> None:
    """Client process: open n streams, print 'ready' when all are open, then counts when done."""
    counts = [0] * n
    opened = [0]
    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=0)

    async def one(i: int, http: httpx.AsyncClient) -> None:
        for attempt in range(5):
            try:
                async with http.stream("GET", f"http://127.0.0.1:{port}/v1/stream", params={"route": "1"}) as r:
                    opened[0] += 1
                    if opened[0] == n:
                        print("ready", flush=True)
                    async for line in r.aiter_lines():
                        if line.startswith("event: vehicle"):
                            counts[i] += 1
                        elif line.startswith("event: done"):
                            return
                return
            except httpx.HTTPError:
                await asyncio.sleep(0.2 * (attempt + 1))

    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30), limits=limits) as http:
        tasks = []
        for i in range(n):
            tasks.append(asyncio.create_task(one(i, http)))
            if i % 50 == 49:
                await asyncio.sleep(0.05)  # ramp up
        await asyncio.gather(*tasks)
    print(json.dumps({"opened": opened[0], "counts": counts}), flush=True)


async def run(clients: int = 500, minutes: float = 30.0, speed: float = 20.0) -> dict:
    import uvicorn
    from gbapi.app import create_app
    from gbsim.evaluate import DEFAULT_DATE
    from gbsim.simulator import simulate
    from gbstream.replay import batches_from_events

    app = create_app(pipeline_mode="off")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                                           backlog=2048))
    srv = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    ctx = app.state.ctx
    sim = simulate(ctx.net, "normal", DEFAULT_DATE, hours=1.0, route_ids=["1"])
    w0 = sim.window[0]
    batches = [b for b in batches_from_events(sim.events) if b[-1].fetch_ts < w0 + minutes * 60]
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "gbevals.load_sse", "--client-mode",
                                                "--port", str(port), "--clients", str(clients),
                                                stdout=subprocess.PIPE)
    ready_line = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
    connected = ctx.pipeline.bc.n_subscribers
    expected = [0]
    orig = ctx.pipeline.bc.publish

    def counting_publish(msg, _orig=orig):
        if msg.get("type") == "vehicle" and msg.get("route_id") == "1":
            expected[0] += 1
        _orig(msg)

    ctx.pipeline.bc.publish = counting_publish
    t0 = time.perf_counter()
    prev = None
    lag = 0.0
    for b in batches:
        ts = b[-1].fetch_ts
        if prev is not None:
            target = t0 + (ts - batches[0][-1].fetch_ts) / speed
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            lag = max(lag, time.perf_counter() - target)
        prev = ts
        ctx.pipeline.feed(b)
    elapsed = time.perf_counter() - t0
    await asyncio.sleep(1.0)
    ctx.pipeline.bc.publish({"type": "done", "route_id": "1"})
    out = await asyncio.wait_for(proc.stdout.read(), timeout=120)
    await proc.wait()
    server.should_exit = True
    await srv
    res = json.loads(out.decode().strip().splitlines()[-1])
    got = sorted(res["counts"])
    return {"clients": clients, "connected": connected, "ready": ready_line.decode().strip() == "ready",
            "sim_minutes": minutes, "speed": speed, "wall_s": round(elapsed, 1),
            "expected_wall_s": round(minutes * 60 / speed, 1), "max_feed_lag_s": round(lag, 2),
            "vehicle_msgs_per_client_expected": expected[0], "min_received": got[0],
            "median_received": got[len(got) // 2],
            "all_received_everything": expected[0] > 0 and got[0] >= expected[0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=500)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--client-mode", action="store_true")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    if a.client_mode:
        asyncio.run(_clients(a.port, a.clients))
        return 0
    r = asyncio.run(run(a.clients, a.minutes))
    print(r)
    return 0 if r["all_received_everything"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
