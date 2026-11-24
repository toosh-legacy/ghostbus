"""Feature drift with Evidently: PSI per Model A feature, reference (training weeks) vs current (the latest weeks, or
features logged by live serving). Writes an HTML report to ml/reports/drift.html and JSON to drift.json; the admin
models page and the drift alert read the JSON. PSI ≥ 0.2 on any feature = drift warning.

Run: uv run python -m gbml.drift
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pandas as pd

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
PSI_WARN = 0.2
NUMERIC = ["delay_k", "delay_chg", "horizon", "sched_travel_s", "hour", "dow", "recent_seg_delta", "headway_k"]


def drift(reference: pd.DataFrame, current: pd.DataFrame, columns: list[str] | None = None,
          html_path=None) -> dict:
    from evidently import Report
    from evidently.presets import DataDriftPreset

    cols = [c for c in (columns or NUMERIC) if c in reference and c in current]
    ref, cur = reference[cols].astype(float), current[cols].astype(float)
    snap = Report([DataDriftPreset(method="psi", threshold=PSI_WARN)]).run(current_data=cur, reference_data=ref)
    if html_path is not None:
        snap.save_html(str(html_path))
    per = {}
    share = None
    for m in snap.dict()["metrics"]:
        cfg = m["config"]
        if cfg.get("type", "").endswith("ValueDrift"):
            per[cfg["column"]] = float(m["value"])
        elif cfg.get("type", "").endswith("DriftedColumnsCount"):
            share = m["value"]["share"]
    return {"method": "psi", "threshold": PSI_WARN, "psi": per, "drifted_share": share,
            "drifted": sorted(c for c, v in per.items() if v >= PSI_WARN), "n_reference": len(ref), "n_current": len(cur)}


def main() -> int:
    from gbcore.settings import REPO_ROOT

    from .data import build_model_a_samples

    out_dir = REPO_ROOT / "ml" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = build_model_a_samples()
    last = df["service_date"].max()
    cur = df[df["service_date"] > last - pd.Timedelta(days=14)]
    ref = df[df["service_date"] <= last - pd.Timedelta(days=42)].sample(frac=1.0, random_state=42).head(200_000)
    r = drift(ref, cur.head(200_000), html_path=out_dir / "drift.html")
    r.update({"reference": f"service days up to {(last - pd.Timedelta(days=42)).date()}",
              "current": f"last 14 service days to {last.date()}", "generated_utc": datetime.now(UTC).isoformat()})
    (out_dir / "drift.json").write_text(json.dumps(r, indent=2))
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
