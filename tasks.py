"""Python task runner mirroring the Makefile targets (works without make).

Usage: uv run python tasks.py <name>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VAR = ROOT / "var"
PY = [sys.executable]


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def recorder_status() -> dict:
    hb = VAR / "recorder_heartbeat.json"
    if not hb.exists():
        return {"running": False, "reason": "no heartbeat file"}
    d = json.loads(hb.read_text())
    age = (datetime.now(UTC) - datetime.fromisoformat(d["now_utc"])).total_seconds()
    return {
        "running": age < 150 and not d.get("stopped") and pid_alive(d.get("pid")),
        "heartbeat_age_s": round(age),
        "pid": d.get("pid"),
        "started_utc": d.get("started_utc"),
        "stored": {k: v["stored"] for k, v in d["feeds"].items()},
        "errors": {k: v["errors"] for k, v in d["feeds"].items()},
    }


def record() -> int:
    """Start the recorder detached (survives this shell). No-op if already running."""
    st = recorder_status()
    if st.get("running"):
        print("recorder already running", st)
        return 0
    VAR.mkdir(exist_ok=True)
    log = open(VAR / "recorder.stdout.log", "ab")  # noqa: SIM115 - handed to the child
    kwargs: dict = {"cwd": ROOT, "stdout": log, "stderr": log, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        if os.name == "nt":
            # Break out of any parent Job Object (uv/terminal) so the recorder outlives this shell.
            kw2 = dict(kwargs, creationflags=kwargs["creationflags"] | 0x01000000)
            p = subprocess.Popen(PY + ["-m", "gbstream.recorder"], **kw2)
        else:
            p = subprocess.Popen(PY + ["-m", "gbstream.recorder"], **kwargs)
    except OSError:
        p = subprocess.Popen(PY + ["-m", "gbstream.recorder"], **kwargs)
    with open(VAR / "recorder_gaps.log", "a", encoding="utf-8") as g:
        g.write(f"{datetime.now(UTC).isoformat()} started pid={p.pid} prev_status={st}\n")
    print("recorder started pid", p.pid)
    return 0


def recorder_check() -> int:
    st = recorder_status()
    print(st)
    if not st.get("running"):
        return record()
    return 0


TASKS = {
    "setup": lambda: run(["uv", "sync"]),
    "up": lambda: run(["docker", "compose", "up", "-d", "postgres", "redis", "azurite"]),
    "down": lambda: run(["docker", "compose", "down"]),
    "record": record,
    "recorder-status": recorder_check,
    "data": lambda: run(PY + ["-m", "gbdata.cli", "all"]),
    "replay": lambda: run(PY + ["-m", "gbstream.replay"]),
    "sim": lambda: run(PY + ["-m", "gbsim.cli"]),
    "train": lambda: run(PY + ["-m", "gbml.train"]),
    "backtest": lambda: run(PY + ["-m", "gbml.train", "--report-only"]),
    "eval": lambda: run(PY + ["-m", "gbevals.run"]),
    "test": lambda: run(PY + ["-m", "pytest"]),
    "demo": lambda: run(PY + ["-m", "gbapi.demo"]),
    "lint": lambda: run(PY + ["-m", "ruff", "check", "."]) or run(PY + ["-m", "mypy"]),
    "eval-live": lambda: run(PY + ["-m", "gbevals.eval_live"]),
    "ablation": lambda: run(PY + ["-m", "gbevals.retrieval_ablation"]),
    "langsmith": lambda: run(PY + ["-m", "gbevals.langsmith_experiments"]),
    "mcp": lambda: run(PY + ["-m", "gbcopilot.mcp_server"]),
    "brief": lambda: run(PY + ["-m", "gbcopilot.brief"]),
    "lock-prompts": lambda: run(PY + ["-m", "gbevals.run", "--fast", "--lock-prompts"]),
    "promptfoo": lambda: run(PY + ["evals/promptfoo/build_config.py"]) or run(
        ["npm", "--prefix", "evals/promptfoo", "run", "eval", "--", "--no-table", "-o", "../reports/promptfoo.json"]),
    "load-test": lambda: run(PY + ["-m", "gbevals.load_locust"]),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TASKS:
        print("tasks:", ", ".join(TASKS))
        sys.exit(2)
    sys.exit(TASKS[sys.argv[1]]() or 0)
