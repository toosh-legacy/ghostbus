"""SSE load test with Locust: start the API on a 20x simulator replay, run N concurrent riders headless, and write
evals/reports/load_test.md from Locust's CSV stats.

Run: uv run python -m gbevals.load_locust --users 500 --spawn-rate 50 --duration 90
"""

from __future__ import annotations

import argparse
import csv
import os
import socket
import subprocess
import sys
import time

import httpx
from gbcore.settings import REPO_ROOT

from .run import REPORTS


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=500)
    ap.add_argument("--spawn-rate", type=int, default=50)
    ap.add_argument("--duration", type=int, default=90)
    a = ap.parse_args(argv)
    port = _free_port()
    env = {**os.environ, "PIPELINE": "sim", "REPLAY_SPEED": "20", "SIM_SCENARIO": "normal", "GB_LLM": "fake"}
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "gbapi.app:create_app", "--factory", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning", "--backlog", "4096"], env=env, cwd=REPO_ROOT)
    try:
        for _ in range(120):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/v1/routes", timeout=2).status_code < 500:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        out = REPO_ROOT / "var" / "locust"
        out.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "locust", "-f", str(REPO_ROOT / "evals" / "locust" / "sse_locustfile.py"),
               "--headless", "-u", str(a.users), "-r", str(a.spawn_rate), "-t", f"{a.duration}s",
               "--host", f"http://127.0.0.1:{port}", "--csv", str(out / "sse"), "--only-summary", "--stop-timeout", "45"]
        subprocess.run(cmd, cwd=REPO_ROOT, env={**env, "LOAD_HOLD_S": "40"}, check=False)
        rows = list(csv.DictReader((out / "sse_stats.csv").open()))
    finally:
        api.terminate()
        api.wait(timeout=30)
    pick = {r["Name"]: r for r in rows}
    L = ["# SSE load test (Locust)", "",
         f"{a.users} concurrent riders, spawn rate {a.spawn_rate}/s, {a.duration} s, API on a 20x simulator replay "
         "(one uvicorn worker). Each rider holds one /v1/stream for 40 s.", "",
         "| Measure | Count | Failures | Median (ms) | p95 (ms) | p99 (ms) | Max (ms) |", "|---|---|---|---|---|---|---|"]
    for name in ("SSE connect", "SSE vehicle", "SSE session"):
        r = pick.get(name)
        if r:
            L.append(f"| {name} | {r['Request Count']} | {r['Failure Count']} | {r['50%']} | {r['95%']} | {r['99%']} | "
                     f"{float(r['Max Response Time']):.0f} |")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "load_test.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    s = pick.get("SSE session")
    return 0 if s and int(s["Failure Count"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
