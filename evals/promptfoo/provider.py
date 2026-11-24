"""promptfoo Python provider: runs a prompt through the GhostBus LangGraph copilot and returns the answer contract as
JSON. Uses the fake chat model unless PROMPTFOO_LIVE=1 (then OpenAI, under the session cost guard)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("copilot", "packages/core", "data/pipeline", "evals"):
    sys.path.insert(0, str(ROOT / p))
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
os.environ.setdefault("GB_RERANKER", "none")


def call_api(prompt: str, options: dict, context: dict) -> dict:
    from gbcopilot.agent import answer

    provider = "live" if os.environ.get("PROMPTFOO_LIVE") == "1" else "fake"
    a = answer(prompt, provider=provider)
    keep = {k: a.get(k) for k in ("answer", "refused", "sql", "rejected_sql", "via", "citations", "table")}
    return {"output": json.dumps(keep, default=str)}
