"""ModelRegistry (local adapter): joblib files + model cards under ml/artifacts/<model>/<version>/.

Pinned versions live in ml/artifacts/pinned.json. Promotion rule (spec 10.3): a new version is pinned only if it
does not regress the primary metric (p50 MAE for Model A, Brier for Model B) by more than 1%.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import joblib
from gbcore.settings import REPO_ROOT

ART = REPO_ROOT / "ml" / "artifacts"
PRIMARY = {"model_a": ("p50_mae", "lower"), "model_b": ("brier", "lower")}
REGRESSION_TOLERANCE = 0.01


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return None


class ModelRegistry:
    def __init__(self, root: Path = ART) -> None:
        self.root = root
        self.pinned_path = root / "pinned.json"

    def pinned(self) -> dict:
        return json.loads(self.pinned_path.read_text()) if self.pinned_path.exists() else {}

    def save(self, name: str, version: str, obj, metrics: dict, config: dict, card: str) -> Path:
        d = self.root / name / version
        d.mkdir(parents=True, exist_ok=True)
        joblib.dump(obj, d / "model.joblib", compress=3)
        (d / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
        (d / "training_config.json").write_text(json.dumps({**config, "git_sha": git_sha(),
                                                             "saved_utc": datetime.now(UTC).isoformat()}, indent=2,
                                                            default=str))
        (d / "model_card.md").write_text(card, encoding="utf-8")
        return d

    def load(self, name: str, version: str | None = None):
        version = version or self.pinned().get(name)
        if not version:
            return None, None
        d = self.root / name / version
        return joblib.load(d / "model.joblib"), version

    def metrics(self, name: str, version: str) -> dict:
        return json.loads((self.root / name / version / "metrics.json").read_text())

    def should_promote(self, name: str, new_metrics: dict) -> tuple[bool, str]:
        cur = self.pinned().get(name)
        key, _ = PRIMARY[name]
        if not cur or not (self.root / name / cur / "metrics.json").exists():
            return True, "no pinned version"
        old = self.metrics(name, cur)[key]
        new = new_metrics[key]
        if new <= old * (1 + REGRESSION_TOLERANCE):
            return True, f"{key} {new:.4f} vs pinned {old:.4f} (within 1%)"
        return False, f"{key} regressed: {new:.4f} vs pinned {old:.4f} (> 1%)"

    def pin(self, name: str, version: str) -> None:
        p = self.pinned()
        p[name] = version
        self.root.mkdir(parents=True, exist_ok=True)
        self.pinned_path.write_text(json.dumps(p, indent=2))
